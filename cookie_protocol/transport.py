"""Exactly-once HTTP body transport. Caller owns the existing account lock."""
import asyncio
import base64
import json
import math
import secrets
from dataclasses import dataclass

from .capture import capture
from .diagnostics import stage
from .errors import GenerationCancelled, NotSubmitted, SubmissionUncertain, UpstreamErrorCard
from .protocol import ENDPOINT, ResponseState, generation_frames, request_model
from .webchannel_frames import WebChannelFrames


@dataclass(frozen=True)
class GenerationResult:
    text: str
    model: str
    cid: str
    rid: str
    rcid: str

    @property
    def model_id(self):
        return self.model


@dataclass
class Dispatch:
    submitted: bool = False


def emit_dispatch():
    from gemini_webapi.request_diagnostics import emit
    emit('generation_dispatched', transport='cookie', protocol='process-session')


def new_session(bundle, proxy, timeout):
    from curl_cffi.requests import AsyncSession, Cookies
    jar = Cookies()
    for cookie in bundle.cookies:
        domain = cookie['domain'].lstrip('.')
        if domain in {'google.com', 'gemini.google.com', 'geminiweb-pa.clients6.google.com', 'clients6.google.com'}:
            jar.set(cookie['name'], cookie['value'], domain=cookie['domain'], path=cookie['path'])
    return AsyncSession(cookies=jar, impersonate='chrome', proxy=proxy,
                        timeout=min(timeout, 30), allow_redirects=False, trust_env=False)


def check_status(response):
    # Do not surface curl error strings: URLs contain tunneled credentials.
    if response.status_code != 200:
        raise ValueError('HTTP channel rejected')


def check_forward_ack(response):
    check_status(response)
    content = response.content.decode('utf-8')
    # WebChannel ACKs can use the same UTF-16 length prefix as receive frames.
    prefix, sep, rest = content.partition('\n')
    if sep and prefix.isdecimal():
        if len(rest.encode('utf-16-le')) // 2 != int(prefix):
            raise ValueError('Truncated forward ACK')
        content = rest
    value = json.loads(content)
    if (not isinstance(value, list) or len(value) != 3
            or any(type(x) is not int or x < 0 for x in value) or value[0] not in (0, 1)):
        raise ValueError('Invalid forward ACK')


async def _http(bundle, prompt, model_id, proxy, timeout, dispatch):
    deadline = asyncio.get_running_loop().time() + timeout
    payloads = generation_frames(bundle.payloads, prompt)
    if request_model(bundle.query) != model_id:
        raise NotSubmitted('Captured model mismatch; not submitted')
    session = new_session(bundle, proxy, timeout)
    # Match the verified WebChannel flow: independent connection pools and
    # cookie stores for forward and receive channels. Concurrent response-cookie
    # merges must not change the other channel's credentials mid-request.
    receive_session = new_session(bundle, proxy, timeout)
    reader = None
    ready = asyncio.Event()
    completed = asyncio.Event()
    answer = ResponseState()
    aid = -1
    base = None
    headers = {'Origin': 'https://gemini.google.com', 'Referer': 'https://gemini.google.com/',
               'Content-Type': 'application/x-www-form-urlencoded'}
    rid = secrets.randbelow(900000) + 100000

    async def receive():
        nonlocal aid
        first_output = False
        routing_logged = False
        # Resume receive-only EOF on the same SID/AID; never resend generation.
        for number in range(1, 65):
            response = await receive_session.get(ENDPOINT, params={**base, 'RID': 'rpc',
                'AID': str(aid), 'CI': '0', 'TYPE': 'xmlhttp', 't': str(number)},
                headers=headers, stream=True,
                timeout=max(0.001, deadline - asyncio.get_running_loop().time()))
            check_status(response)
            decoder = WebChannelFrames()
            async for chunk in response.aiter_content():
                events = decoder.feed(chunk)
                for frame in events:
                    if frame.aid <= aid:
                        continue
                    value = frame.payload
                    if not isinstance(value, list) or not value or not isinstance(value[0], str):
                        raise ValueError('Invalid channel payload')
                    control = value[0]
                    if control == 'c':
                        if len(value) < 2 or value[1] != base['SID']:
                            raise ValueError('Channel SID changed')
                    elif control == 'noop':
                        pass
                    elif control in ('stop', 'close'):
                        raise ValueError('Channel closed before verified completion')
                    else:
                        raw = base64.b64decode(control, validate=True)
                        if raw == b'\x0a\x00':
                            ready.set()
                        else:
                            answer.consume(raw)
                            if answer.cid and answer.rid and not routing_logged:
                                from gemini_webapi.request_diagnostics import emit
                                emit('routing_received', cid=answer.cid, rid=answer.rid,
                                     transport='cookie', protocol='process-session')
                                routing_logged = True
                            if answer.text and not dispatch.submitted:
                                raise ValueError('Unexpected answer before prompt')
                            if answer.error_card:
                                raise UpstreamErrorCard('Upstream error card; do not automatically resend')
                            if answer.model and answer.model != model_id:
                                raise ValueError('Response model mismatch')
                            if answer.text and not first_output:
                                stage('first_output')
                                first_output = True
                    aid = frame.aid
                if answer.terminal:
                    if not answer.verified(model_id):
                        raise ValueError('Incomplete terminal answer')
                    decoder.finish()
                    stage('terminal')
                    # All envelopes in this chunk were checked before completion.
                    completed.set()
                    return
            decoder.finish()
            await asyncio.sleep(0.05)
        raise ValueError('Receive limit exhausted')

    async def wait_event(event, seconds):
        waiter = asyncio.create_task(event.wait())
        try:
            done, _ = await asyncio.wait({waiter, reader}, timeout=seconds,
                                         return_when=asyncio.FIRST_COMPLETED)
            if reader in done:
                reader.result()
            if not event.is_set():
                raise TimeoutError('Channel event deadline')
        finally:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)

    try:
        # Fresh open: discard ALL browser channel/session routing parameters.
        query = {k: v for k, v in bundle.query.items()
                 if k not in {'SID', 'gsessionid', 'AID', 'RID', 'zx', 't', 'TYPE', 'CI'}}
        query.update(RID=str(rid), zx=secrets.token_hex(12), t='1')
        response = await session.post(ENDPOINT, params=query, data={'count': '0'},
                                      headers=headers, timeout=min(timeout, 20))
        check_status(response)
        decoder = WebChannelFrames()
        decoder.feed(response.content)
        decoder.finish()
        sid, gsession = decoder.sid, response.headers.get('x-http-session-id')
        if not sid or not gsession or sid == bundle.query.get('SID') or gsession == bundle.query.get('gsessionid'):
            raise NotSubmitted('No independent HTTP channel; not submitted')
        aid = max(0, decoder.aid)
        base = {'VER': '8', 'SID': sid, 'gsessionid': gsession}
        reader = asyncio.create_task(receive())
        # Exactly one init POST. The forward ACK alone is not init readiness.
        response = await session.post(ENDPOINT, params={**base, 'RID': str(rid + 1),
            'AID': str(aid), 't': '1'}, data={'count': '1', 'ofs': '0', 'req0___data__': payloads[0]},
            headers=headers, timeout=min(timeout, 20))
        check_forward_ack(response)
        await wait_event(ready, min(timeout, 15))
        stage('channel_initialized')
        if reader.done():
            reader.result()
            raise NotSubmitted('Reader ended before prompt; not submitted')
        # No await between local dispatch marking, parent-budget emission and POST.
        # Even connect failures/cancellation here are uncertain: NEVER replay.
        dispatch.submitted = True
        emit_dispatch()
        response = await session.post(ENDPOINT, params={**base, 'RID': str(rid + 2),
            'AID': str(aid), 't': '1'}, data={'count': '1', 'ofs': '1', 'req0___data__': payloads[1]},
            headers=headers, timeout=min(timeout, 20))
        check_forward_ack(response)
        await wait_event(completed, timeout)
        return GenerationResult(answer.text, answer.model, answer.cid, answer.rid, answer.rcid)
    finally:
        # Closing AsyncCurl cancels network handles. No blocking worker threads,
        # response.aclose() wait on a live backchannel, or fire-and-forget timer.
        if reader is not None:
            reader.cancel()
        try:
            await asyncio.wait_for(asyncio.gather(session.close(), receive_session.close()), 3)
        finally:
            if reader is not None:
                await asyncio.wait_for(asyncio.gather(reader, return_exceptions=True), 3)


async def generate(account, account_dir, prompt, model_id, proxy=None, timeout=300):
    """Generate text under the PARENT account lock and total deadline.

    No retries, fallback, credential persistence or browser body generation.
    timeout includes capture + HTTP; bounded cleanup may add up to 6 seconds.
    Cancellation is a CancelledError subclass with submitted/uncertain flags.
    """
    dispatch = Dispatch()
    try:
        if (not isinstance(prompt, str) or not prompt.strip() or not isinstance(model_id, str)
                or isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0):
            raise NotSubmitted('Invalid generation input; not submitted')
        async with asyncio.timeout(timeout):
            deadline = asyncio.get_running_loop().time() + timeout
            bundle = await capture(account, account_dir, prompt, model_id)
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise NotSubmitted('Capture exhausted the request deadline; not submitted')
            return await _http(bundle, prompt, model_id, proxy, remaining, dispatch)
    except asyncio.CancelledError:
        raise GenerationCancelled(dispatch.submitted) from None
    except UpstreamErrorCard:
        raise
    except NotSubmitted:
        if dispatch.submitted:
            raise SubmissionUncertain('Submission outcome uncertain; do not automatically resend') from None
        raise
    except Exception as exc:
        from gemini_webapi.request_diagnostics import emit
        import traceback
        frames = traceback.extract_tb(exc.__traceback__)
        local = [f for f in frames if 'cookie_protocol' in f.filename]
        emit('protocol_failure', error_type=type(exc).__name__,
             location=(local[-1].name + ':' + str(local[-1].lineno)) if local else 'dependency',
             submitted=dispatch.submitted)
        error = SubmissionUncertain if dispatch.submitted else NotSubmitted
        raise error('Submission outcome uncertain; do not automatically resend' if dispatch.submitted
                    else 'Validation or channel initialization failed; not submitted') from None
