"""Single-dispatch Cookie generation; no browser dependency or hidden fallback."""
import asyncio
from contextlib import contextmanager

from gemini_webapi.request_diagnostics import emit
from gemini_webapi.exceptions import UsageLimitExceeded, ModelInvalid, TemporarilyBlocked, AuthError
from gemini_webapi.constants import AccountStatus


class SubmissionUncertain(RuntimeError):
    """Generation was dispatched; callers must reconcile before retrying."""


class UpstreamErrorCard(RuntimeError):
    """Google returned an explicit error card, not generated chapter content."""

class ModelVerificationFailed(SubmissionUncertain):
    """Do not accept unverified or downgraded Pro output, or automatically resend."""


def verify_response_model(output, requested):
    expected = getattr(requested, "model_id", None)
    if expected != "e6fa609c3fa255c0":
        return
    actual = getattr(output, "model_id", None)
    if actual != expected:
        emit("response_model_rejected", expected_model_id=expected,
             actual_model_id=actual if isinstance(actual, str) and len(actual) == 16 else None)
        raise ModelVerificationFailed(
            "Pro response model is missing or mismatched; output rejected; do not automatically retry")
    emit("response_model_verified", model_id=actual, transport="cookie")


@contextmanager
def single_dispatch(client):
    session = client.client
    original = session.stream
    submitted = False

    def stream(method, url, *args, **kwargs):
        nonlocal submitted
        if 'StreamGenerate' in str(url):
            if submitted:
                raise SubmissionUncertain('submission outcome unknown; duplicate generation blocked')
            submitted = True
            emit('generation_dispatched', transport='cookie')
        return original(method, url, *args, **kwargs)

    session.stream = stream
    try:
        yield
    except BaseException as exc:
        if submitted and not isinstance(exc, (UsageLimitExceeded, ModelInvalid, TemporarilyBlocked, SubmissionUncertain, UpstreamErrorCard)):
            # Keep useful causes without logging response bodies or credentials.
            message = str(exc).lower()
            reason = ('stream_aborted' if 'silently aborted' in message else
                      'timeout' if 'timed out' in message or isinstance(exc, TimeoutError) else
                      'connection' if 'connect' in message else 'upstream_error')
            emit('submission_uncertain', error_type=type(exc).__name__, reason=reason)
            raise SubmissionUncertain('submission outcome unknown; generation failed after dispatch; do not automatically retry') from exc
        raise
    finally:
        session.stream = original


async def generate_stream(client, prompt, *, files=None, model, timeout=300):
    # This is used under the server's per-account lock. Never monkeypatch a
    # shared/global SDK class: the dispatch guard belongs to one session only.
    if getattr(client, '_running', True) is False:
        await client.init(timeout=timeout, supplied_cookies_only=True,
                          auto_refresh=False, auto_close=False, verbose=False)
    if getattr(client, 'account_status', None) == AccountStatus.UNAUTHENTICATED:
        raise AuthError('Account session is unauthenticated; generation was not submitted')
    # The outer deadline is the generation budget. SDK initialization may have
    # installed a much shorter timeout; do not let it terminate a live long
    # generation at its default 120-second watchdog threshold.
    old_timeout = getattr(client, 'timeout', timeout)
    old_watchdog = getattr(client, 'watchdog_timeout', timeout)
    session = client.client
    old_session_timeout = getattr(session, 'timeout', timeout)
    client.timeout = timeout
    client.watchdog_timeout = timeout
    session.timeout = timeout
    try:
        async for output in _generate_stream_once(client, prompt, files=files, model=model, timeout=timeout):
            yield output
    finally:
        client.timeout = old_timeout
        client.watchdog_timeout = old_watchdog
        session.timeout = old_session_timeout


async def _generate_stream_once(client, prompt, *, files, model, timeout):
    with single_dispatch(client):
        async with asyncio.timeout(timeout):
            # Use the public SDK workflow for text as well as attachments:
            # it sends the activity RPC and initializes per-turn parsing state.
            iterator = client.generate_content_stream(
                prompt, files=files, model=model, temporary=True, current_retry=0)
            first=True
            async for output in iterator:
                candidates = getattr(output, 'candidates', ())
                chosen = getattr(output, 'chosen', 0)
                if candidates and getattr(candidates[chosen], 'is_error_card', False):
                    emit('upstream_error_card', transport='cookie')
                    raise UpstreamErrorCard('Google returned an error card; no usable generation received; no automatic resend')
                verify_response_model(output, model)
                if first and (getattr(output,'text',None) or getattr(output,'text_delta',None)):
                    emit('first_output')
                    first=False
                yield output


async def generate(client, prompt, *, files=None, model, timeout=300):
    output = None
    async for output in generate_stream(client, prompt, files=files, model=model, timeout=timeout):
        pass
    if output is None:
        raise RuntimeError('Gemini returned no generation output')
    return output
