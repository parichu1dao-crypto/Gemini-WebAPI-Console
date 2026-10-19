"""Offline only: synthetic packets, fake browser routes and fake HTTP sessions."""
import asyncio
import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

from cookie_protocol import (generate, NotSubmitted, SubmissionUncertain,
                             UpstreamErrorCard, GenerationCancelled)
from cookie_protocol.capture import Bundle, CaptureGuard
from cookie_protocol.protocol import (ENDPOINT, ResponseState, generation_frames,
                                      request_model, is_error_card)
from cookie_protocol.webchannel_frames import WebChannelFrames, FrameError

MODEL = 'e6fa609c3fa255c0'
PROMPT = '  exact\r\n中文😀\ntext  '


def varint(value):
    out = bytearray()
    while value > 127:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def field(n, value):
    if isinstance(value, str):
        value = value.encode()
    return varint(n * 8 + 2) + varint(len(value)) + value


def encoded(raw):
    return base64.b64encode(raw).decode()


def framed(value):
    text = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return str(len(text.encode('utf-16-le')) // 2).encode() + b'\n' + text.encode()


def response(text='answer', terminal=True, model=MODEL, cid='c_test', rid='r_test', rcid='rc_test'):
    body = field(2, field(1, cid) + field(2, rid))
    if text is not None:
        body += field(5, field(1, rcid) + field(2, field(1, text)))
    if terminal:
        body += varint(15 * 8) + b'\x01'
    if model:
        body += field(40, model)
    for n in (4, 4, 1, 7, 2):
        body = field(n, body)
    return body


def bundle():
    query = {'VER': '8', 'SID': 'browser-sid', 'gsessionid': 'browser-gsession',
             'AID': '999', '$httpHeaders': 'x-goog-ext-525001261-bin:' + encoded(field(5, MODEL))}
    payloads = (encoded(field(1, b'')), encoded(field(2, field(9, field(1, PROMPT)))))
    return Bundle(query, payloads, [])


class FakeResponse:
    def __init__(self, content=b'[1,0,0]', status=200, headers=None):
        self.content, self.status_code, self.headers = content, status, headers or {}


class FakeSession:
    def __init__(self, mode='success'):
        self.mode = mode
        self.calls = []
        self.trace = []
        self.queue = asyncio.Queue()
        self.closed = False
        self.gets = 0
        self.prompt_seen = asyncio.Event()

    async def post(self, url, **kw):
        self.calls.append(('POST', kw))
        if kw['data'] == {'count': '0'}:
            self.trace.append('open')
            return FakeResponse(framed([[0, ['c', 'http-sid', '', 8]]]),
                                headers={'x-http-session-id': 'http-gsession'})
        if kw['data']['ofs'] == '0':
            self.trace.append('init')
            if self.mode == 'init-fail':
                raise RuntimeError('private credential detail')
            if self.mode == 'bad-forward':
                return FakeResponse(b'not an ACK')
            if self.mode != 'no-init':
                await self.queue.put(framed([[1, [encoded(b'\x0a\x00')]]]))
            return FakeResponse()
        self.trace.append('prompt')
        self.prompt_seen.set()
        if self.mode == 'prompt-fail':
            raise RuntimeError('private prompt and URL')
        if self.mode == 'hang':
            return FakeResponse()
        if self.mode == 'eof':
            await self.queue.put(None)
        if self.mode == 'partial':
            raw = response(terminal=False)
        elif self.mode == 'wrong-model':
            raw = response(model='wrong')
        elif self.mode == 'error-card':
            raw = response(text='Sorry, something went wrong. Please try your request again.')
        elif self.mode == 'no-text':
            raw = response(text=None)
        else:
            raw = response()
        await self.queue.put(framed([[2, [encoded(raw)]]]))
        return FakeResponse()

    async def get(self, url, **kw):
        self.calls.append(('GET', kw))
        self.gets += 1
        owner = self

        class Stream(FakeResponse):
            async def aiter_content(self):
                while True:
                    value = await owner.queue.get()
                    if value is None:
                        return
                    yield value
        return Stream()

    async def close(self):
        self.closed = True


class ProtocolTests(unittest.TestCase):
    def test_only_observed_markdown_list_escape_is_accepted(self):
        prompt='Instructions:\n- keep the meaning\n1. Keep order'
        p=(encoded(field(1,b'')),encoded(field(2,field(9,field(1,'Instructions:\n\u200b- keep the meaning\n\u200b1. Keep order')))))
        self.assertEqual(generation_frames(p,prompt),p)
        bad=(p[0],encoded(field(2,field(9,field(1,prompt+'\u200b')))))
        with self.assertRaises(ValueError):generation_frames(bad,prompt)

    def test_forward_ack_accepts_length_framing_rejects_truncation(self):
        from cookie_protocol.transport import check_forward_ack
        from types import SimpleNamespace
        for body in (b'[1,0,0]', b'7\n[1,0,0]'):
            check_forward_ack(SimpleNamespace(status_code=200, content=body))
        with self.assertRaises(ValueError):
            check_forward_ack(SimpleNamespace(status_code=200, content=b'9\n[1,0,0]'))

    def test_exact_prompt_no_normalization(self):
        self.assertEqual(generation_frames(bundle().payloads, PROMPT), bundle().payloads)
        for changed in (PROMPT.strip(), PROMPT.replace('\r\n', '\n'), PROMPT + '\n'):
            with self.assertRaises(ValueError):
                generation_frames(bundle().payloads, changed)

    def test_reject_close_reorder_and_malformed(self):
        p = bundle().payloads
        for value in ((p[1], p[0]), (*p, encoded(field(4, b''))), (p[0], encoded(field(4, b''))), ('!', p[1])):
            with self.assertRaises(ValueError):
                generation_frames(value, PROMPT)

    def test_binary_and_json_model_headers(self):
        q = bundle().query
        self.assertEqual(request_model(q), MODEL)
        q['$httpHeaders'] += '\nx-goog-ext-525001261-jspb:' + json.dumps([1, None, None, None, MODEL])
        self.assertEqual(request_model(q), MODEL)
        q['$httpHeaders'] = q['$httpHeaders'].replace('"' + MODEL + '"', '"wrong"')
        with self.assertRaises(ValueError):
            request_model(q)

    def test_missing_duplicate_bad_model_headers(self):
        for value in ('', 'x-goog-ext-525001261-bin:!',
                      'x-goog-ext-525001261-bin:' + encoded(field(5, MODEL) * 2),
                      bundle().query['$httpHeaders'] + '\n' + bundle().query['$httpHeaders']):
            with self.assertRaises(ValueError):
                request_model({'$httpHeaders': value})

    def test_answer_requires_completion_and_identity(self):
        a = ResponseState()
        a.consume(b'\x0a\x00')
        self.assertFalse(a.verified(MODEL))
        a.consume(response('an', False))
        self.assertFalse(a.verified(MODEL))
        a.consume(response('answer', False))
        self.assertEqual(a.text, 'answer')
        a.consume(response(None, True))
        self.assertTrue(a.verified(MODEL))

    def test_changed_identity_and_model_rejected(self):
        for key in ('cid', 'rid', 'rcid', 'model'):
            a = ResponseState()
            a.consume(response(terminal=False))
            with self.assertRaises(ValueError):
                a.consume(response(**{key: 'changed'}))

    def test_empty_routing_identity_is_not_a_replacement(self):
        a = ResponseState()
        a.consume(response(None,False,model='',cid='c_test',rid=''))
        self.assertEqual(a.cid,'c_test')
        self.assertFalse(a.verified(MODEL))
        a.consume(response('answer',True))
        self.assertTrue(a.verified(MODEL))

    def test_error_cards(self):
        for value in ('http://googleusercontent.com/card_content/123',
                      'Sorry, something went wrong. Please try your request again.',
                      "I'm having a hard time fulfilling your request. Can I help you with something else instead?"):
            self.assertTrue(is_error_card(value))
            a = ResponseState()
            a.consume(response(value))
            self.assertFalse(a.verified(MODEL))

    def test_unicode_every_split(self):
        data = framed([[0, ['c', 'sid']], [1, ['中文😀']]])
        for split in range(len(data) + 1):
            d = WebChannelFrames()
            events = d.feed(data[:split]) + d.feed(data[split:]) + d.finish()
            self.assertEqual(events[-1].payload, ['中文😀'])

    def test_truncation_and_ack_are_not_envelopes(self):
        for data in (framed([[0, ['noop']]])[:-1], framed([1, 0, 0]), b'999999999999999999999\n'):
            with self.assertRaises(FrameError):
                d = WebChannelFrames()
                d.feed(data)
                d.finish()


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def run_generate(self, session, timeout=2):
        async def fake_capture(*args):
            return bundle()
        with patch('cookie_protocol.transport.capture', fake_capture), \
             patch('cookie_protocol.transport.new_session', return_value=session), \
             patch('cookie_protocol.transport.emit_dispatch', side_effect=lambda: session.trace.append('dispatch')), \
             patch('cookie_protocol.transport.stage'):
            return await generate('synthetic', '/not-used/accounts', PROMPT, MODEL, timeout=timeout)

    async def test_success_order_and_independent_sid(self):
        s = FakeSession()
        answer = await self.run_generate(s, 100)
        self.assertEqual((answer.text, answer.model, answer.cid, answer.rid, answer.rcid),
                         ('answer', MODEL, 'c_test', 'r_test', 'rc_test'))
        self.assertEqual(s.trace, ['open', 'init', 'dispatch', 'prompt'])
        posts = [kw for method, kw in s.calls if method == 'POST']
        self.assertNotIn('SID', posts[0]['params'])
        self.assertNotIn('gsessionid', posts[0]['params'])
        self.assertNotIn('AID', posts[0]['params'])
        self.assertEqual(posts[-1]['data']['req0___data__'], bundle().payloads[1])
        for method, kw in s.calls[1:]:
            self.assertEqual(kw['params']['SID'], 'http-sid')
        reads = [kw for method, kw in s.calls if method == 'GET']
        self.assertGreater(reads[0]['timeout'], 90)  # Not a 30s whole-stream timeout.
        self.assertTrue(s.closed)

    async def test_clean_eof_resume_does_not_resend(self):
        s = FakeSession('eof')
        await self.run_generate(s)
        self.assertEqual(s.gets, 2)
        self.assertEqual(s.trace.count('prompt'), 1)
        reads = [kw for method, kw in s.calls if method == 'GET']
        self.assertEqual(reads[1]['params']['AID'], '1')

    async def test_pre_dispatch_failure_is_not_submitted(self):
        for mode in ('init-fail', 'bad-forward', 'no-init'):
            s = FakeSession(mode)
            with self.assertRaises(NotSubmitted) as caught:
                await self.run_generate(s, .03)
            self.assertFalse(caught.exception.submitted)
            self.assertNotIn('private', str(caught.exception))
            self.assertNotIn('prompt', s.trace)
            self.assertTrue(s.closed)

    async def test_post_dispatch_failures_never_retry(self):
        for mode in ('prompt-fail', 'wrong-model', 'partial', 'no-text', 'hang'):
            s = FakeSession(mode)
            with self.assertRaises(SubmissionUncertain) as caught:
                await self.run_generate(s, .03)
            self.assertTrue(caught.exception.submitted)
            self.assertEqual(s.trace.count('prompt'), 1)
            self.assertNotIn('private', str(caught.exception))
            self.assertTrue(s.closed)

    async def test_error_card_is_exported_submitted_failure(self):
        s = FakeSession('error-card')
        with self.assertRaises(UpstreamErrorCard):
            await self.run_generate(s)
        self.assertEqual(s.trace.count('prompt'), 1)
        self.assertTrue(s.closed)

    async def test_cancellation_preserves_submitted_flag(self):
        for mode, submitted in (('no-init', False), ('hang', True)):
            s = FakeSession(mode)
            task = asyncio.create_task(self.run_generate(s))
            if submitted:
                await s.prompt_seen.wait()
            else:
                await asyncio.sleep(.01)
            task.cancel()
            with self.assertRaises(GenerationCancelled) as caught:
                await task
            self.assertEqual(caught.exception.submitted, submitted)
            self.assertTrue(s.closed)

    async def test_capture_uses_total_budget_and_never_opens_http(self):
        async def slow_capture(*args):
            await asyncio.sleep(1)
        with patch('cookie_protocol.transport.capture', slow_capture), \
             patch('cookie_protocol.transport.new_session') as factory:
            with self.assertRaises(NotSubmitted):
                await generate('synthetic', '/unused', PROMPT, MODEL, timeout=.01)
            factory.assert_not_called()

    async def test_invalid_inputs_do_not_capture(self):
        for prompt, timeout in (('', 1), (PROMPT, 0), (PROMPT, float('nan')), (PROMPT, True)):
            with patch('cookie_protocol.transport.capture') as capture:
                with self.assertRaises(NotSubmitted):
                    await generate('synthetic', '/unused', prompt, MODEL, timeout=timeout)
                capture.assert_not_called()


class FakeRoute:
    def __init__(self, path=ENDPOINT, form='', method='POST'):
        self.request = SimpleNamespace(url=path, post_data=form, method=method)
        self.action = None

    async def abort(self):
        self.action = 'abort'

    async def continue_(self):
        self.action = 'continue'

    async def fulfill(self, **kwargs):
        self.action = 'fulfill'


class CaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_generation_posts_intercepted(self):
        guard = CaptureGuard(armed=True)
        for index, payload in enumerate(bundle().payloads):
            route = FakeRoute(form=urlencode({'count': '1', 'ofs': str(index), 'req0___data__': payload}))
            await guard.route(route)
            self.assertEqual(route.action, 'fulfill')
        self.assertTrue(guard.done.is_set())
        self.assertFalse(guard.failed)
        self.assertEqual(guard.payloads[1], bundle().payloads[1])
        for route in (FakeRoute(path='https://gemini.google.com/StreamGenerate'),
                      FakeRoute(form='broken'), FakeRoute(form='count=0&req0___data__=secret'),
                      FakeRoute(path='https://gemini.google.com/unknown-generation')):
            await guard.route(route)
            self.assertEqual(route.action, 'abort')

    async def test_empty_handshake_and_receive_only_allowed(self):
        guard = CaptureGuard(armed=True)
        for route in (FakeRoute(form='count=0'), FakeRoute(method='GET')):
            await guard.route(route)
            self.assertEqual(route.action, 'continue')

    async def test_duplicate_changed_or_close_frames_fail_closed(self):
        guard = CaptureGuard()
        for value in (bundle().payloads[0], 'changed'):
            route = FakeRoute(form=urlencode({'count': '1', 'ofs': '0', 'req0___data__': value}))
            await guard.route(route)
        self.assertTrue(guard.failed)
        self.assertEqual(route.action, 'abort')


if __name__ == '__main__':
    unittest.main()
