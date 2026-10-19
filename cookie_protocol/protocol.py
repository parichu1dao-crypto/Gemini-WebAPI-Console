"""Observed processSession schema. Unknown/ambiguous answer shapes fail closed."""
import base64
import json
import re
from dataclasses import dataclass
from .wire import fields

ENDPOINT = 'https://geminiweb-pa.clients6.google.com/v1/processSession'
MODEL_HEADER = 'x-goog-ext-525001261-jspb'
# Only this UI/model pairing has processSession evidence in the lab reports.
# Adding an ID requires observed picker + request + terminal response evidence.
SUPPORTED_MODELS = {'e6fa609c3fa255c0': r'\b(?:\d+(?:\.\d+)*\s+)?Pro\b'}


def nested(raw, path):
    for key in path:
        values = [v for n, k, v in fields(raw) if n == key and k == 2]
        if len(values) != 1:
            raise ValueError('Missing or ambiguous protocol field')
        raw = values[0]
    return raw


def generation_frames(payloads, prompt):
    if len(payloads) != 2:
        raise ValueError('Expected initialization and prompt only')
    raw = [base64.b64decode(p, validate=True) for p in payloads]
    if [(n, k) for n, k, _ in fields(raw[0])] != [(1, 2)]:
        raise ValueError('Invalid initialization')
    if [(n, k) for n, k, _ in fields(raw[1])] != [(2, 2)]:
        raise ValueError('Invalid prompt frame')
    actual = nested(raw[1], [2, 9, 1]).decode('utf-8')
    # Gemini's rich-query serializer prefixes Markdown list markers with U+200B
    # to retain plain-text input. Accept ONLY that observed escaping, while
    # forwarding the original captured bytes/proof untouched. No content edits.
    unescaped = re.sub(r'(?m)^\u200b(?=[*+-] |\d+[.)] )', '', actual)
    if actual != prompt and ('\u200b' in prompt or unescaped != prompt):
        from gemini_webapi.request_diagnostics import emit
        emit('prompt_serialization_mismatch', expected_chars=len(prompt), actual_chars=len(actual),
             whitespace_only=actual.split()==prompt.split(),
             nbsp_only=actual.replace('\u00a0',' ')==prompt,
             doubled_newlines=actual==prompt.replace('\n','\n\n'),
             trailing_only=actual.strip()==prompt.strip(),
             zero_width_only=actual.replace('\u200b','').replace('\ufeff','')==prompt)
        raise ValueError('Exact prompt binding mismatch')
    return tuple(payloads)


def request_model(query):
    headers = {}
    for line in query.get('$httpHeaders', '').splitlines():
        key, sep, value = line.partition(':')
        if not sep or key.lower() in headers:
            raise ValueError('Invalid tunneled headers')
        headers[key.lower()] = value.strip()
    models = []
    if MODEL_HEADER in headers:
        value = json.loads(headers[MODEL_HEADER])
        if not isinstance(value, list) or len(value) < 5 or not isinstance(value[4], str):
            raise ValueError('No observed model ID')
        models.append(value[4])
    binary = headers.get('x-goog-ext-525001261-bin')
    if binary is not None:
        # processSession captures carry binary protobuf header: field 5 is ID.
        models.append(nested(base64.b64decode(binary, validate=True), [5]).decode('utf-8'))
    if not models or len(set(models)) != 1 or not models[0]:
        raise ValueError('Missing or conflicting observed model ID')
    return models[0]


def is_error_card(text):
    value = text.strip()
    return bool(re.match(r'https?://googleusercontent\.com/card_content/', value) or
                value.casefold() in {
                    'sorry, something went wrong. please try your request again.',
                    "i'm having a hard time fulfilling your request. can i help you with something else instead?",
                })


@dataclass
class ResponseState:
    text: str = ''
    terminal: bool = False
    model: str = ''
    cid: str = ''
    rid: str = ''
    rcid: str = ''
    error_card: bool = False

    def _identity(self, key, incoming):
        value = incoming.decode('utf-8')
        # Routing frames may omit an identity with an empty protobuf string;
        # this is not a replacement of the identity learned from an earlier frame.
        if not value:
            return
        if getattr(self, key) and getattr(self, key) != value:
            raise ValueError('Response identity changed')
        setattr(self, key, value)

    def consume(self, raw):
        # Routing and init ACK are not answers. Validate wire even when ignored.
        fields(raw)
        try:
            body = nested(raw, [2, 7, 1, 4, 4])
        except ValueError:
            return False
        entries = fields(body)
        for key in (2, 5, 15, 40):
            if sum(n == key for n, _, _ in entries) > 1:
                raise ValueError('Ambiguous answer')
        for n, k, v in entries:
            if n == 2 and k == 2:
                for number, key in ((1, 'cid'), (2, 'rid')):
                    values = [x for a, b, x in fields(v) if a == number and b == 2]
                    if len(values) > 1:
                        raise ValueError('Ambiguous identity')
                    if values:
                        self._identity(key, values[0])
            elif n == 40 and k == 2:
                self._identity('model', v)
            elif n == 5 and k == 2:
                self._identity('rcid', nested(v, [1]))
                text = nested(v, [2, 1]).decode('utf-8')
                if self.terminal and text != self.text:
                    raise ValueError('Answer changed after completion')
                self.text = text  # Cumulative snapshot, NOT a delta.
                self.error_card |= is_error_card(text)
            elif n == 15:
                if k != 0 or v not in (0, 1):
                    raise ValueError('Invalid completion')
                self.terminal |= v == 1
        return True

    def verified(self, model):
        return bool(self.terminal and self.text.strip() and self.cid and self.rid
                    and self.rcid and self.model == model and not self.error_card)
