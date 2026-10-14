import argparse
import os
import asyncio
import base64
import json
import mimetypes
import signal
import sys
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from concurrent.futures import TimeoutError as FutureTimeoutError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
ACCOUNT_DIR = ROOT / "data" / "accounts"
ACCOUNT_ATTEMPT_LOG = ROOT / "openai_account_attempts.jsonl"
ACCOUNT_ATTEMPT_LOG_LOCK = threading.Lock()
sys.path.insert(0, str(ROOT / "src"))

from gemini_webapi.constants import AccountStatus, Model  # noqa: E402
from gemini_webapi.request_diagnostics import diagnostic_sink, emit  # noqa: E402
from local_account_client import create_initialized_client  # noqa: E402


STRICT_PRO_MODEL = Model.BASIC_PRO.model_name
STRICT_PRO_MODEL_ID = Model.BASIC_PRO.model_id


class StrictProUnavailable(RuntimeError):
    """Raised when a request cannot be proven eligible for Gemini Pro."""


def validate_generated_text(text: str) -> None:
    normalized = " ".join(text.split()).casefold().rstrip(".!。")
    if not normalized or normalized in {
        "i seem to be encountering an error. can i try something else for you?",
        "sorry, something went wrong. please try your request again",
    }:
        raise RuntimeError("Gemini returned empty content or a service error message")


class ServerState:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.account_dir = Path(args.account_dir).resolve()
        self.clients: list[tuple[str, Any]] = []
        self.next_index = 0
        self.client_lock = threading.Lock()
        self.account_health: dict[str, dict[str, Any]] = {}
        self.account_locks: dict[str, asyncio.Lock] = {}
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def start(self):
        self.thread.start()
        account_names = self.resolve_accounts()
        for account_name in account_names:
            try:
                client = self.run_async(
                    create_initialized_client(
                        account_name,
                        ignore_proxy=self.args.ignore_proxy,
                        account_dir=self.account_dir,
                        proxy_override=self.args.proxy,
                        timeout=self.args.init_timeout,
                        verify=not self.args.skip_verify,
                        auto_refresh=self.args.auto_refresh,
                    )
                )
            except Exception as exc:
                print(f"[WARN] account {account_name} init failed: {type(exc).__name__}: {exc}")
                continue

            if self.args.strict_pro:
                eligible, reason, _ = self.strict_pro_eligibility(client)
                if not eligible:
                    print(f"[WARN] account {account_name} excluded by strict Pro mode: {reason}")
                    try:
                        self.run_async(client.close(), timeout=10)
                    except Exception:
                        pass
                    continue

            self.clients.append((account_name, client))
            self.account_health[account_name] = {"failures": 0, "cooldown_until": 0.0}
            self.account_locks[account_name] = asyncio.Lock()
            print(f"[OK] account {account_name} initialized")

        if not self.clients:
            raise RuntimeError(
                "No Gemini accounts initialized. Check account JSON files under "
                f"{self.account_dir}"
            )

    def resolve_accounts(self) -> list[str]:
        if self.args.account:
            return [self.args.account]

        raw_accounts = [part.strip() for part in self.args.accounts.split(",") if part.strip()]
        if raw_accounts and raw_accounts != ["all"]:
            return raw_accounts

        return sorted(path.stem for path in self.account_dir.glob("*.json") if path.is_file())

    def strict_pro_eligibility(self, client: Any) -> tuple[bool, str, Any | None]:
        """Return whether the current account can be trusted for strict Pro requests."""

        status = getattr(client, "account_status", None)
        if status != AccountStatus.AVAILABLE:
            status_name = getattr(status, "name", str(status or "UNKNOWN"))
            return False, f"account status is {status_name}, expected AVAILABLE", None

        models = client.list_models() or []
        pro_model = next(
            (
                model
                for model in models
                if getattr(model, "model_id", "") == STRICT_PRO_MODEL_ID
                or getattr(model, "model_name", "") == STRICT_PRO_MODEL
            ),
            None,
        )
        if pro_model is None:
            return False, "Gemini Pro is absent from the account's dynamic model list", None
        if not bool(getattr(pro_model, "is_available", False)):
            return False, "Gemini Pro is listed but marked unavailable", None

        capacity = int(getattr(pro_model, "capacity", 1) or 1)
        if not self.args.allow_free_pro and capacity <= 1:
            return (
                False,
                "paid Pro entitlement is not confirmed (capacity indicates a free account)",
                None,
            )

        return True, "ok", pro_model

    async def prepare_strict_pro_model(self, account_name: str, client: Any) -> Any:
        """Refresh entitlement immediately before generation and return the dynamic Pro model."""

        registry = getattr(client, "_model_registry", None)
        if isinstance(registry, dict):
            registry.clear()
        await client._fetch_user_status()
        eligible, reason, pro_model = self.strict_pro_eligibility(client)
        if not eligible:
            raise StrictProUnavailable(f"{account_name}: {reason}")
        return pro_model

    def request_lock(self, account_name: str) -> asyncio.Lock:
        lock = self.account_locks.get(account_name)
        if lock is None:
            lock = asyncio.Lock()
            self.account_locks[account_name] = lock
        return lock

    def stop(self):
        for _, client in self.clients:
            try:
                self.run_async(client.close(), timeout=10)
            except Exception:
                pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)

    def next_client(self) -> tuple[str, Any]:
        if not self.clients:
            raise RuntimeError("No initialized Gemini accounts are available")

        with self.client_lock:
            now = time.time()
            total = len(self.clients)
            for offset in range(total):
                index = (self.next_index + offset) % total
                account_name, client = self.clients[index]
                health = self.account_health.setdefault(
                    account_name, {"failures": 0, "cooldown_until": 0.0}
                )
                if float(health.get("cooldown_until") or 0) <= now:
                    self.next_index = index + 1
                    return account_name, client

            # Keep the service usable when every account is cooling down.
            index = min(
                range(total),
                key=lambda item: float(
                    self.account_health.get(self.clients[item][0], {}).get("cooldown_until") or 0
                ),
            )
            self.next_index = index + 1
            return self.clients[index]

    def mark_account_success(self, account_name: str) -> None:
        with self.client_lock:
            health = self.account_health.setdefault(
                account_name, {"failures": 0, "cooldown_until": 0.0}
            )
            health["failures"] = 0
            health["cooldown_until"] = 0.0

    def mark_account_failure(self, account_name: str, error: Exception) -> None:
        threshold = max(1, int(getattr(self.args, "account_failure_threshold", 1)))
        cooldown = max(1.0, float(getattr(self.args, "account_cooldown", 900)))
        with self.client_lock:
            health = self.account_health.setdefault(
                account_name, {"failures": 0, "cooldown_until": 0.0}
            )
            health["failures"] = int(health.get("failures") or 0) + 1
            if health["failures"] >= threshold:
                health["cooldown_until"] = time.time() + cooldown
                if not getattr(self.args, "quiet", False):
                    print(
                        f"[WARN] account {account_name} cooling down for {int(cooldown)}s "
                        f"after {health['failures']} failure(s): {str(error)[:240]}"
                    )

    def run_async(self, coro, timeout: float | None = None):
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(timeout=timeout or self.args.request_timeout)
        except FutureTimeoutError:
            future.cancel()
            raise

    @asynccontextmanager
    async def account_request(self, account_name, request_id):
        def sink(fields):
            record = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "request_id": request_id, "account": account_name, **fields}
            with ACCOUNT_ATTEMPT_LOG_LOCK:
                with (ROOT / "openai_request_diagnostics.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
        token = diagnostic_sink.set(sink)
        queued = time.monotonic()
        started = None
        try:
            emit("queued")
            async with self.request_lock(account_name):
                started = time.monotonic()
                emit("account_acquired", wait_sec=round(started - queued, 3))
                try:
                    yield
                finally:
                    emit("account_released", active_sec=round(time.monotonic() - started, 3))
        except asyncio.CancelledError:
            emit("cancelled", during="queued" if started is None else "active")
            raise
        except Exception as exc:
            emit("request_error", error_type=type(exc).__name__)
            raise
        finally:
            diagnostic_sink.reset(token)


STATE: ServerState | None = None


def now_ts() -> int:
    return int(time.time())


def write_account_attempt_log(event: dict[str, Any]) -> None:
    record = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        **event,
    }
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with ACCOUNT_ATTEMPT_LOG_LOCK:
        with ACCOUNT_ATTEMPT_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def model_names() -> list[str]:
    if STATE and STATE.args.strict_pro:
        return [STRICT_PRO_MODEL]

    names = [model.model_name for model in Model]
    if STATE:
        for _, client in STATE.clients:
            dynamic = client.list_models() or []
            for model in dynamic:
                if model.model_name not in names:
                    names.append(model.model_name)
    return names


def normalize_messages(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content", "")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(str(item.get("text") or ""))
                    elif item.get("type") == "image_url":
                        image_url = item.get("image_url") or {}
                        if isinstance(image_url, dict):
                            parts.append(f"[image_url: {image_url.get('url', '')}]")
                else:
                    parts.append(str(item))
            content_text = "\n".join(part for part in parts if part)
        else:
            content_text = str(content)

        if content_text.strip():
            lines.append(f"{role}: {content_text.strip()}")

    return "\n\n".join(lines).strip()


def image_suffix_from_media_type(media_type: str) -> str:
    return mimetypes.guess_extension(media_type.split(";")[0].strip()) or ".png"


def materialize_image_url(url: str, temp_dir: Path) -> str:
    if url.startswith("data:"):
        header, encoded = url.split(",", 1)
        media_type = header[5:].split(";")[0] or "image/png"
        suffix = image_suffix_from_media_type(media_type)
        image_path = temp_dir / f"input_image_{len(list(temp_dir.iterdir()))}{suffix}"
        image_path.write_bytes(base64.b64decode(encoded))
        return str(image_path)

    parsed = urlparse(url)
    if parsed.scheme == "file":
        path = Path(url2pathname(parsed.path.lstrip("/") if parsed.netloc else parsed.path))
        if not path.is_file():
            raise ValueError(f"image file does not exist: {path}")
        return str(path)

    if parsed.scheme in ("http", "https"):
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=60) as response:
            content_type = response.headers.get("content-type", "image/png")
            suffix = image_suffix_from_media_type(content_type)
            image_path = temp_dir / f"remote_image_{len(list(temp_dir.iterdir()))}{suffix}"
            image_path.write_bytes(response.read())
            return str(image_path)

    path = Path(url)
    if path.is_file():
        return str(path)

    raise ValueError(
        "image_url must be a local path, file:// URL, data:image base64 URL, or http(s) URL"
    )


def normalize_messages_with_files(
    messages: list[dict[str, Any]], temp_dir: Path
) -> tuple[str, list[str]]:
    lines: list[str] = []
    files: list[str] = []

    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content", "")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(str(item.get("text") or ""))
                    elif item.get("type") == "image_url":
                        image_url = item.get("image_url") or {}
                        raw_url = (
                            image_url.get("url")
                            if isinstance(image_url, dict)
                            else str(image_url)
                        )
                        if raw_url:
                            files.append(materialize_image_url(str(raw_url), temp_dir))
                            parts.append("[attached image]")
                else:
                    parts.append(str(item))
            content_text = "\n".join(part for part in parts if part)
        else:
            content_text = str(content)

        if content_text.strip():
            lines.append(f"{role}: {content_text.strip()}")

    return "\n\n".join(lines).strip(), files


def require_state() -> ServerState:
    if STATE is None or not STATE.clients:
        raise RuntimeError("Server is not initialized")
    return STATE


def openai_error(message: str, status: int = 500, error_type: str = "server_error"):
    return status, {
        "error": {
            "message": message,
            "type": error_type,
            "param": None,
            "code": None,
        }
    }


MODEL_ALIASES = {
    "gemini-3.1-pro-low": "gemini-3-pro",
    "gemini-3.1-pro-preview": "gemini-3-pro",
    "gemini-3-flash-preview": "gemini-3-flash",
}


def normalize_model_name(model: Any, default_model: str) -> str:
    model_name = str(model or default_model)
    return MODEL_ALIASES.get(model_name, model_name)


def resolve_requested_model(payload: dict[str, Any], state: ServerState) -> tuple[str, str]:
    requested_model = str(payload.get("model") or state.args.default_model)
    normalized_model = normalize_model_name(requested_model, state.args.default_model)
    if state.args.strict_pro and normalized_model != STRICT_PRO_MODEL:
        raise ValueError(
            f"Strict Pro mode only accepts '{STRICT_PRO_MODEL}', got '{requested_model}'"
        )
    return requested_model, normalized_model


async def create_chat_completion(payload: dict[str, Any]) -> dict[str, Any]:
    state = require_state()
    requested_model, model_name = resolve_requested_model(payload, state)
    messages = payload.get("messages") or []
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty array")

    with tempfile.TemporaryDirectory(prefix="gemini_openai_images_") as tmp:
        prompt, files = normalize_messages_with_files(messages, Path(tmp))
        if not prompt:
            raise ValueError("messages did not contain any text content")

        account_name, client = state.next_client()
        model: Any = model_name
        request_started = time.time()
        request_id = f"chat-{uuid.uuid4().hex}"
        write_account_attempt_log({
            "request_id": request_id,
            "endpoint": "/v1/chat/completions",
            "stream": False,
            "event": "begin",
            "account": account_name,
            "requested_model": requested_model,
            "model": model_name,
            "prompt_chars": len(prompt),
            "file_count": len(files or []),
        })
        try:
            async with state.account_request(account_name, request_id):
                if state.args.strict_pro:
                    model = await state.prepare_strict_pro_model(account_name, client)
                output = await client.generate_content(
                    prompt,
                    files=files or None,
                    model=model,
                    current_retry=0,
                )
                emit("output_received", response_chars=len(output.text or ""))
                try:
                    validate_generated_text(output.text or "")
                except Exception:
                    emit("output_rejected", reason="empty_or_service_error")
                    raise
                emit("output_validated", response_chars=len(output.text or ""))
        except Exception as exc:
            state.mark_account_failure(account_name, exc)
            write_account_attempt_log({
                "request_id": request_id,
                "endpoint": "/v1/chat/completions",
                "stream": False,
                "event": "failure",
                "account": account_name,
                "requested_model": requested_model,
                "model": model_name,
                "elapsed_sec": round(time.time() - request_started, 3),
                "error_type": type(exc).__name__,
                "error": str(exc)[:2000],
            })
            raise

    text = output.text or ""
    state.mark_account_success(account_name)
    write_account_attempt_log({
        "request_id": request_id,
        "endpoint": "/v1/chat/completions",
        "stream": False,
        "event": "success",
        "account": account_name,
        "requested_model": requested_model,
        "model": model_name,
        "elapsed_sec": round(time.time() - request_started, 3),
        "response_chars": len(text),
    })
    completion_id = f"chatcmpl-gemini-web-{now_ts()}"

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": now_ts(),
        "model": model_name,
        "account": account_name,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": text,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


async def create_chat_completion_chunks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    state = require_state()
    requested_model, model_name = resolve_requested_model(payload, state)
    messages = payload.get("messages") or []
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty array")

    model: Any = model_name
    account_name, client = state.next_client()
    request_started = time.time()
    request_id = f"stream-{uuid.uuid4().hex}"
    completion_id = f"chatcmpl-gemini-web-{now_ts()}"
    chunks: list[dict[str, Any]] = [
        {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": now_ts(),
            "model": model_name,
            "account": account_name,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant"},
                    "finish_reason": None,
                }
            ],
        }
    ]

    with tempfile.TemporaryDirectory(prefix="gemini_openai_images_") as tmp:
        prompt, files = normalize_messages_with_files(messages, Path(tmp))
        if not prompt:
            raise ValueError("messages did not contain any text content")

        previous_text = ""
        write_account_attempt_log({
            "request_id": request_id,
            "endpoint": "/v1/chat/completions",
            "stream": True,
            "event": "begin",
            "account": account_name,
            "requested_model": requested_model,
            "model": model_name,
            "prompt_chars": len(prompt),
            "file_count": len(files or []),
        })
        try:
            async with state.account_request(account_name, request_id):
                if state.args.strict_pro:
                    model = await state.prepare_strict_pro_model(account_name, client)
                async for output in client.generate_content_stream(
                    prompt,
                    files=files or None,
                    model=model,
                    current_retry=0,
                ):
                    delta = output.text_delta or ""
                    if not delta:
                        text = output.text or ""
                        if text.startswith(previous_text):
                            delta = text[len(previous_text) :]
                        previous_text = text or previous_text

                    if not delta:
                        continue

                    chunks.append(
                        {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": now_ts(),
                            "model": model_name,
                            "account": account_name,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"content": delta},
                                    "finish_reason": None,
                                }
                            ],
                        }
                    )
                validate_generated_text("".join(
                    chunk["choices"][0]["delta"].get("content", "")
                    for chunk in chunks
                ))
        except Exception as exc:
            state.mark_account_failure(account_name, exc)
            write_account_attempt_log({
                "request_id": request_id,
                "endpoint": "/v1/chat/completions",
                "stream": True,
                "event": "failure",
                "account": account_name,
                "requested_model": requested_model,
                "model": model_name,
                "elapsed_sec": round(time.time() - request_started, 3),
                "error_type": type(exc).__name__,
                "error": str(exc)[:2000],
            })
            raise

    chunks.append(
        {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": now_ts(),
            "model": model_name,
            "account": account_name,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
        }
    )
    state.mark_account_success(account_name)
    write_account_attempt_log({
        "request_id": request_id,
        "endpoint": "/v1/chat/completions",
        "stream": True,
        "event": "success",
        "account": account_name,
        "requested_model": requested_model,
        "model": model_name,
        "elapsed_sec": round(time.time() - request_started, 3),
        "response_chars": len(previous_text),
        "chunk_count": len(chunks),
    })
    return chunks


async def create_image_generation(payload: dict[str, Any]) -> dict[str, Any]:
    state = require_state()
    requested_model, model_name = resolve_requested_model(payload, state)
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt is required")

    model: Any = model_name
    n = int(payload.get("n") or 1)
    n = max(1, min(n, 4))
    response_format = payload.get("response_format") or "url"

    gemini_prompt = prompt
    if "generate" not in gemini_prompt.lower():
        gemini_prompt = f"Generate an image: {gemini_prompt}"

    account_name, client = state.next_client()
    request_started = time.time()
    request_id = f"image-{uuid.uuid4().hex}"
    write_account_attempt_log({
        "request_id": request_id,
        "endpoint": "/v1/images/generations",
        "event": "begin",
        "account": account_name,
        "requested_model": requested_model,
        "model": model_name,
        "prompt_chars": len(gemini_prompt),
        "n": n,
        "response_format": response_format,
    })
    try:
        async with state.account_request(account_name, request_id):
            if state.args.strict_pro:
                model = await state.prepare_strict_pro_model(account_name, client)
            output = await client.generate_content(
                gemini_prompt, model=model, current_retry=0
            )
    except Exception as exc:
        state.mark_account_failure(account_name, exc)
        write_account_attempt_log({
            "request_id": request_id,
            "endpoint": "/v1/images/generations",
            "event": "failure",
            "account": account_name,
            "requested_model": requested_model,
            "model": model_name,
            "elapsed_sec": round(time.time() - request_started, 3),
            "error_type": type(exc).__name__,
            "error": str(exc)[:2000],
        })
        raise
    images = output.images or []
    if not images:
        error_message = (
            "Gemini did not return any images. The account/model may not have image generation enabled, "
            "or the prompt may have returned text instead."
        )
        write_account_attempt_log({
            "request_id": request_id,
            "endpoint": "/v1/images/generations",
            "event": "failure",
            "account": account_name,
            "requested_model": requested_model,
            "model": model_name,
            "elapsed_sec": round(time.time() - request_started, 3),
            "error_type": "NoImages",
            "error": error_message,
        })
        raise RuntimeError(error_message)
    state.mark_account_success(account_name)

    data = []
    for image in images[:n]:
        if response_format == "b64_json":
            with tempfile.TemporaryDirectory(prefix="gemini_openai_generated_") as tmp:
                saved_path = await image.save(path=tmp, full_size=True)
                data.append(
                    {
                        "b64_json": base64.b64encode(Path(saved_path).read_bytes()).decode(
                            "ascii"
                        )
                    }
                )
        else:
            data.append({"url": image.url})

        result = {
            "created": now_ts(),
            "account": account_name,
            "data": data,
        }
        write_account_attempt_log({
            "request_id": request_id,
            "endpoint": "/v1/images/generations",
            "event": "success",
            "account": account_name,
            "requested_model": requested_model,
            "model": model_name,
            "elapsed_sec": round(time.time() - request_started, 3),
            "image_count": len(data),
        })
        return result


class OpenAICompatibleHandler(BaseHTTPRequestHandler):
    server_version = "GeminiOpenAICompat/0.1"

    def log_message(self, fmt: str, *args):
        if STATE and STATE.args.quiet:
            return
        super().log_message(fmt, *args)

    def _send_json(self, status: int, data: dict[str, Any]):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_sse(self, chunks: list[dict[str, Any]]):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        for chunk in chunks:
            line = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            self.wfile.write(line.encode("utf-8"))
            self.wfile.flush()

        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _authorized(self) -> bool:
        state = require_state()
        api_key = state.args.api_key
        if not api_key:
            return True

        auth = self.headers.get("Authorization") or ""
        x_api_key = self.headers.get("x-api-key") or ""
        return auth == f"Bearer {api_key}" or x_api_key == api_key

    def do_GET(self):
        try:
            if not self._authorized():
                self._send_json(*openai_error("Unauthorized", 401, "invalid_request_error"))
                return

            if self.path == "/health":
                state = require_state()
                account_details = {}
                for name, client in state.clients:
                    eligible, reason, pro_model = state.strict_pro_eligibility(client)
                    status = getattr(client, "account_status", None)
                    account_details[name] = {
                        "status": getattr(status, "name", str(status or "UNKNOWN")),
                        "strict_pro_eligible": eligible,
                        "strict_pro_reason": reason,
                        "pro_model_id": getattr(pro_model, "model_id", None),
                        "pro_capacity": getattr(pro_model, "capacity", None),
                    }
                self._send_json(
                    200,
                    {
                        "status": "ok",
                        "strict_pro": bool(state.args.strict_pro),
                        "allow_free_pro": bool(state.args.allow_free_pro),
                        "accounts": [name for name, _ in state.clients],
                        "account_count": len(state.clients),
                        "account_details": account_details,
                    },
                )
                return

            if self.path == "/v1/models":
                self._send_json(
                    200,
                    {
                        "object": "list",
                        "data": [
                            {
                                "id": name,
                                "object": "model",
                                "created": 0,
                                "owned_by": "gemini-webapi",
                            }
                            for name in model_names()
                        ],
                    },
                )
                return

            self._send_json(*openai_error("Not found", 404, "invalid_request_error"))
        except Exception as exc:
            self._send_json(*openai_error(str(exc)))

    def do_POST(self):
        try:
            if not self._authorized():
                self._send_json(*openai_error("Unauthorized", 401, "invalid_request_error"))
                return

            if self.path not in ("/v1/chat/completions", "/v1/images/generations"):
                self._send_json(*openai_error("Not found", 404, "invalid_request_error"))
                return

            payload = self._read_json()
            if self.path == "/v1/images/generations":
                response = require_state().run_async(create_image_generation(payload))
                self._send_json(200, response)
                return

            if payload.get("stream"):
                chunks = require_state().run_async(create_chat_completion_chunks(payload))
                self._send_sse(chunks)
                return

            state = require_state()
            response = state.run_async(create_chat_completion(payload))
            self._send_json(200, response)
        except ValueError as exc:
            self._send_json(*openai_error(str(exc), 400, "invalid_request_error"))
        except Exception as exc:
            self._send_json(*openai_error(str(exc)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="OpenAI-compatible local HTTP wrapper for gemini-webapi."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument(
        "--accounts",
        default="all",
        help="Comma-separated account names under ../account, or 'all'",
    )
    parser.add_argument(
        "--account",
        default=None,
        help="Single account name under ../account. Overrides --accounts.",
    )
    parser.add_argument(
        "--account-dir",
        default=str(ACCOUNT_DIR),
        help="Directory containing account JSON files",
    )
    parser.add_argument("--api-key", default=os.environ.get("GEMINI_CONSOLE_API_KEY", ""), help="Local bearer key (or GEMINI_CONSOLE_API_KEY)")
    parser.add_argument("--default-model", default=STRICT_PRO_MODEL)
    strict_group = parser.add_mutually_exclusive_group()
    strict_group.add_argument(
        "--strict-pro",
        dest="strict_pro",
        action="store_true",
        default=True,
        help="Fail closed unless an authenticated, dynamically advertised Pro model is available (default)",
    )
    strict_group.add_argument(
        "--allow-non-pro",
        dest="strict_pro",
        action="store_false",
        help="Disable strict Pro enforcement and restore the original model behavior",
    )
    parser.add_argument(
        "--allow-free-pro",
        action="store_true",
        help="Allow Pro shown on a free-tier account; paid entitlement is required by default",
    )
    parser.add_argument("--ignore-proxy", action="store_true")
    parser.add_argument(
        "--proxy",
        default=None,
        help="Optional proxy override; does not rewrite shared account JSON files",
    )
    parser.add_argument("--skip-verify", action="store_true")
    parser.add_argument("--auto-refresh", action="store_true")
    parser.add_argument("--init-timeout", type=float, default=60)
    parser.add_argument("--request-timeout", type=float, default=300)
    parser.add_argument(
        "--account-failure-threshold",
        type=int,
        default=1,
        help="Consecutive request failures before temporarily cooling down an account",
    )
    parser.add_argument(
        "--account-cooldown",
        type=float,
        default=900,
        help="Seconds to skip an unhealthy account after it reaches the failure threshold",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser


def main() -> int:
    global STATE

    parser = build_parser()
    args = parser.parse_args()
    if not args.api_key:
        parser.error("Set GEMINI_CONSOLE_API_KEY or --api-key; empty authentication is not allowed")
    if args.strict_pro and normalize_model_name(args.default_model, STRICT_PRO_MODEL) != STRICT_PRO_MODEL:
        parser.error(
            f"--default-model must resolve to '{STRICT_PRO_MODEL}' while strict Pro mode is enabled"
        )
    STATE = ServerState(args)
    STATE.start()

    httpd = ThreadingHTTPServer((args.host, args.port), OpenAICompatibleHandler)

    def stop_server(*_):
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop_server)
    signal.signal(signal.SIGTERM, stop_server)

    print(f"OpenAI-compatible Gemini wrapper listening on http://{args.host}:{args.port}/v1")
    print(f"Accounts: {', '.join(name for name, _ in STATE.clients)}")
    print(f"Default model: {args.default_model}")
    print(
        "Strict Pro mode: enabled "
        f"(paid entitlement required: {'no' if args.allow_free_pro else 'yes'})"
        if args.strict_pro
        else "Strict Pro mode: disabled"
    )
    print("Use Authorization: Bearer <api-key>")

    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
        STATE.stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
