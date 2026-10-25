"""Offline WebChannel response framing; no authentication or business decoding.

Reference: Mag1cFall/AIStudio2API docs/protocol.md at
88b47e136f867995b09591579d5503694fca597d (2026-10-14).
Wire frames are decimal UTF-16 code-unit length + LF + JSON. This parser
accepts the backchannel's arrays of [serverAID, payload] envelopes, not the
forward channel's three-integer ACK. Payloads remain opaque except 'c'/SID.
feed() returns validated envelopes; aid is the highest parsed server AID,
not an acknowledgement that an external consumer has persisted the event.
"""

from __future__ import annotations

import codecs
import json
from dataclasses import dataclass
from typing import Any


class FrameError(ValueError):
    """Invalid or truncated stream. The parser cannot be reused after failure."""


@dataclass(frozen=True)
class Envelope:
    aid: int
    payload: Any


def _reject_constant(value: str) -> None:
    raise FrameError("Non-JSON numeric constant")


class WebChannelFrames:
    """Incremental strict UTF-8 input, UTF-16 framing and envelope validation.

    Feed bytes only, then call finish() at EOF (mandatory for truncation checks).
    Duplicate/out-of-order AIDs are returned unchanged; aid never decreases.
    A SID change within one stream is rejected, rather than silently rebinding.
    State commits atomically per valid frame. Use one instance per stream.
    """

    def __init__(self, *, max_frame_units: int = 16 * 1024 * 1024):
        if type(max_frame_units) is not int or max_frame_units < 1:
            raise ValueError("max_frame_units must be a positive integer")
        self.max_frame_units = max_frame_units
        self.sid: str | None = None
        self.aid = -1
        self._decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self._buffer = ""
        self._expected: int | None = None
        self._pos = 0
        self._units = 0
        self._closed = False
        self._failed = False

    def _check_open(self) -> None:
        if self._failed or self._closed:
            raise FrameError("Parser is failed or closed")

    def feed(self, chunk: bytes) -> list[Envelope]:
        self._check_open()
        if not isinstance(chunk, bytes):
            raise TypeError("feed accepts UTF-8 bytes only")
        try:
            self._buffer += self._decoder.decode(chunk)
            return self._drain()
        except (ValueError, UnicodeError, RecursionError):
            self._failed = True
            raise FrameError("Invalid WebChannel frame or UTF-8 stream") from None

    def finish(self) -> list[Envelope]:
        self._check_open()
        try:
            self._buffer += self._decoder.decode(b"", final=True)
            events = self._drain()
            if self._buffer or self._expected is not None:
                raise FrameError("Incomplete frame at EOF")
            self._closed = True
            return events
        except (ValueError, UnicodeError, RecursionError):
            self._failed = True
            raise FrameError("Invalid or incomplete WebChannel stream at EOF") from None

    def _drain(self) -> list[Envelope]:
        events: list[Envelope] = []
        while True:
            if self._expected is None:
                newline = self._buffer.find("\n")
                prefix = self._buffer if newline < 0 else self._buffer[:newline]
                if len(prefix) > 20 or any(c not in "0123456789" for c in prefix):
                    raise FrameError("Invalid length prefix")
                if newline < 0:
                    return events
                if not prefix:
                    raise FrameError("Empty length prefix")
                expected = int(prefix)
                if not 1 <= expected <= self.max_frame_units:
                    raise FrameError("Frame length out of bounds")
                self._expected = expected
                self._buffer = self._buffer[newline + 1:]
                self._pos = self._units = 0

            while self._pos < len(self._buffer) and self._units < self._expected:
                char = self._buffer[self._pos]
                self._units += 2 if ord(char) > 0xFFFF else 1
                self._pos += 1
            if self._units > self._expected:
                raise FrameError("Length splits a UTF-16 surrogate pair")
            if self._units < self._expected:
                return events

            raw = self._buffer[:self._pos]
            parsed = json.loads(raw, parse_constant=_reject_constant)
            frame_events, sid, aid = self._validate(parsed)
            self.sid, self.aid = sid, aid
            events.extend(frame_events)
            self._buffer = self._buffer[self._pos:]
            self._expected = None
            self._pos = self._units = 0

    def _validate(self, frame: Any) -> tuple[list[Envelope], str | None, int]:
        if not isinstance(frame, list):
            raise FrameError("Expected envelope array")
        events: list[Envelope] = []
        sid, aid = self.sid, self.aid
        for item in frame:
            if (not isinstance(item, list) or len(item) != 2
                    or type(item[0]) is not int or item[0] < 0):
                raise FrameError("Expected [nonnegative integer AID, payload]")
            server_aid, payload = item
            if isinstance(payload, list) and payload and payload[0] == "c":
                if len(payload) < 2 or not isinstance(payload[1], str) or not payload[1]:
                    raise FrameError("Invalid SID control frame")
                if sid is not None and sid != payload[1]:
                    raise FrameError("SID changed within stream")
                sid = payload[1]
            aid = max(aid, server_aid)
            events.append(Envelope(server_aid, payload))
        return events, sid, aid
