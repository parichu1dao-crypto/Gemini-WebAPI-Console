"""JSON-configured, Cookie-only service entry point. No request replay."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import importlib
import inspect
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import sys
import time
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
KEY_ENV = "GEMINI_CONSOLE_API_KEY"
DEFAULTS = {
    "host": "127.0.0.1", "port": 8787, "transport": "cookie",
    "proxy": None, "ignore_proxy": False, "account_dir": "accounts",
    "accounts": "all", "default_model": "gemini-3-pro", "strict_pro": True,
    "allow_free_pro": False, "skip_verify": False, "auto_refresh": False,
    "init_timeout": 60.0, "request_timeout": 300.0, "queue_timeout": 2.0,
    "account_failure_threshold": 1, "account_cooldown": 900.0, "quiet": False,
    "proxy_wait_timeout": 60.0, "proxy_poll_interval": 1.0,
    "proxy_connect_timeout": 1.0, "startup_attempts": 3,
    "startup_backoff_initial": 1.0, "startup_backoff_max": 8.0,
    "api_key": None,
    "cookie_protocol": "legacy",
}


class ConfigError(ValueError):
    """Safe-to-display configuration error (never contains supplied values)."""


class ProxyUnavailable(RuntimeError):
    pass


class InterfaceError(RuntimeError):
    pass


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("Duplicate JSON field.")
        result[key] = value
    return result


def _number(config, name, low, high, integer=False):
    value = config[name]
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or (integer and not isinstance(value, int))
            or not low <= value <= high or not math.isfinite(value)):
        raise ConfigError(f"Invalid {name}; expected {'integer' if integer else 'number'} in [{low}, {high}].")


def proxy_endpoint(config):
    """Explicit proxy only; never reads account files or inherited proxy secrets."""
    proxy = config["proxy"]
    if proxy is None:
        return None
    if not isinstance(proxy, str) or not proxy or any(ch.isspace() for ch in proxy):
        raise ConfigError("Invalid proxy URL.")
    try:
        parsed = urlsplit(proxy)
        if (parsed.scheme not in {"http", "https", "socks5", "socks5h"}
                or not parsed.hostname or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment or parsed.username is not None
                or parsed.password is not None):
            raise ValueError
        port = parsed.port or {"http": 80, "https": 443, "socks5": 1080, "socks5h": 1080}[parsed.scheme]
        if parsed.port == 0:
            raise ValueError
        host = parsed.hostname
        if config["proxy_wait_timeout"] > 0:
            # Avoid unbounded OS DNS lookups during a bounded startup wait.
            if host.lower() == "localhost":
                host = "127.0.0.1"
            else:
                ipaddress.ip_address(host)
        return host, port
    except ValueError:
        raise ConfigError("Invalid proxy URL; omit credentials, and use an IP address or localhost for TCP waiting.") from None


def load_config(path):
    path = Path(path).resolve()
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError("Cannot read a valid UTF-8 JSON configuration.") from None
    if not isinstance(raw, dict):
        raise ConfigError("Configuration must be a JSON object.")
    metadata = {name for name in raw if name in {"admin_key", "managed_model"}
                or name.startswith(("browser_", "brower_"))}
    raw = {name: value for name, value in raw.items() if name not in metadata}
    for alias, canonical in (("model", "default_model"), ("proxy_wait_seconds", "proxy_wait_timeout")):
        if alias in raw:
            if canonical in raw and raw[canonical] != raw[alias]:
                raise ConfigError(f"Conflicting {alias} and {canonical} fields.")
            raw[canonical] = raw.pop(alias)
    if "retry" in raw:
        retry = raw.pop("retry")
        mapping = {"attempts": "startup_attempts", "initial_seconds": "startup_backoff_initial", "max_seconds": "startup_backoff_max"}
        if not isinstance(retry, dict) or set(retry) - set(mapping):
            raise ConfigError("retry must contain only attempts, initial_seconds, max_seconds.")
        for name, value in retry.items():
            canonical = mapping[name]
            if canonical in raw and raw[canonical] != value:
                raise ConfigError("Conflicting retry settings.")
            raw[canonical] = value
    if set(raw) - set(DEFAULTS):
        raise ConfigError("Configuration must be an object containing only documented fields.")
    config = {**DEFAULTS, **raw}
    if config['cookie_protocol'] not in ('legacy', 'process-session'):
        raise ConfigError('cookie_protocol must be legacy or process-session.')
    if config["proxy"] == "":
        config["proxy"] = None  # Existing console settings use an empty string.
    for name in ("strict_pro", "allow_free_pro", "ignore_proxy", "skip_verify", "auto_refresh", "quiet"):
        if type(config[name]) is not bool:
            raise ConfigError(f"{name} must be a JSON boolean.")
    if config["transport"] not in ("cookie", "browser", "managed"):
        raise ConfigError("Unknown console transport metadata.")
    # Console owns transport dispatch. This executable always runs Cookie only.
    config["transport"] = "cookie"
    host = config["host"]
    try:
        address = ipaddress.ip_address("127.0.0.1" if host == "localhost" else host)
        if not isinstance(host, str) or address.version != 4 or not address.is_loopback:
            raise ValueError
    except (ValueError, TypeError):
        raise ConfigError("host must be localhost or an IPv4 loopback address.") from None
    config["host"] = str(address)
    for name, low, high, integer in (
        ("port", 1, 65535, True), ("init_timeout", 0.1, 3600, False),
        ("request_timeout", 0.1, 86400, False),
        ("queue_timeout", 0.01, 3600, False),
        ("account_failure_threshold", 1, 100, True), ("account_cooldown", 0, 86400, False),
        ("proxy_wait_timeout", 0, 900, False), ("proxy_poll_interval", 0.01, 60, False),
        ("proxy_connect_timeout", 0.01, 30, False), ("startup_attempts", 1, 10, True),
        ("startup_backoff_initial", 0.01, 60, False), ("startup_backoff_max", 0.01, 300, False),
    ):
        _number(config, name, low, high, integer)
    if config["startup_backoff_initial"] > config["startup_backoff_max"]:
        raise ConfigError("startup_backoff_initial must not exceed startup_backoff_max.")
    if config["ignore_proxy"] and config["proxy"] is not None:
        raise ConfigError("proxy and ignore_proxy cannot be enabled together.")
    proxy_endpoint(config)
    directory = config["account_dir"]
    if not isinstance(directory, str) or not directory.strip() or "\x00" in directory:
        raise ConfigError("account_dir must be a nonempty path.")
    directory = Path(directory)
    directory = (path.parent / directory).resolve() if not directory.is_absolute() else directory.resolve()
    if not directory.is_dir():
        raise ConfigError("account_dir must be an existing directory.")
    config["account_dir"] = str(directory)
    accounts = config["accounts"]
    if accounts != "all":
        if isinstance(accounts, str):
            accounts = [s.strip() for s in accounts.split(",")]
        if (not isinstance(accounts, list) or not accounts
                or any(not isinstance(s, str) or not re.fullmatch(r"[A-Za-z0-9_+@.-]+", s)
                       or s in {".", "..", "all"} or s.startswith("-") for s in accounts)
                or len(set(accounts)) != len(accounts)):
            raise ConfigError("accounts must be 'all' or unique safe account names (list or comma-separated).")
        accounts = ",".join(accounts)
    config["accounts"] = accounts
    model = config["default_model"]
    if not isinstance(model, str) or not model.strip() or model != model.strip() or any(ord(c) < 32 for c in model):
        raise ConfigError("default_model must be a nonempty model name.")
    key = config["api_key"]
    if key is not None and not isinstance(key, str):
        raise ConfigError("api_key must be a string or null.")
    return config


def resolve_key(config):
    # An environment value takes precedence, including an invalid empty value.
    key = os.environ.get(KEY_ENV, config["api_key"])
    if not isinstance(key, str) or not key or key != key.strip() or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ConfigError("Set a nonempty printable ASCII API key in the environment or private configuration.")
    return key


@contextmanager
def key_environment(key):
    previous = os.environ.get(KEY_ENV)
    os.environ[KEY_ENV] = key
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(KEY_ENV, None)
        else:
            os.environ[KEY_ENV] = previous


def server_argv(config):
    """In-process argv only. API keys never appear in argv or subprocesses."""
    names = ("host", "port", "accounts", "account_dir", "default_model", "init_timeout",
             "request_timeout", "queue_timeout", "account_failure_threshold", "account_cooldown", "cookie_protocol")
    argv = [f"--{name.replace('_', '-')}={config[name]}" for name in names]
    argv.append("--strict-pro" if config["strict_pro"] else "--allow-non-pro")
    for name in ("allow_free_pro", "ignore_proxy", "skip_verify", "auto_refresh", "quiet"):
        if config[name]:
            argv.append("--" + name.replace("_", "-"))
    if config["proxy"] is not None:
        argv.append("--proxy=" + config["proxy"])
    return argv


def validate_server(server, config, argv):
    try:
        inspect.signature(server.main).bind(argv)
    except (AttributeError, TypeError, ValueError):
        raise InterfaceError("Server must provide main(argv=None); see docs/DEPLOYMENT.md.") from None
    startup_error = getattr(server, "StartupError", None)
    if config["startup_attempts"] > 1 and not (
            isinstance(startup_error, type) and issubclass(startup_error, Exception)):
        raise InterfaceError("Server must export StartupError for safe startup retries; see docs/DEPLOYMENT.md.")
    # This is parsing only, never ServerState.start() or a network health probe.
    parsed = server.build_parser().parse_args(argv)
    if parsed.api_key != os.environ[KEY_ENV]:
        raise InterfaceError("Server parser must read the API key from GEMINI_CONSOLE_API_KEY.")
    if config["strict_pro"] and server.normalize_model_name(parsed.default_model, server.STRICT_PRO_MODEL) != server.STRICT_PRO_MODEL:
        raise ConfigError("default_model is incompatible with strict_pro.")


def wait_for_proxy(config):
    endpoint = proxy_endpoint(config)
    if endpoint is None or config["ignore_proxy"] or config["proxy_wait_timeout"] == 0:
        return
    deadline = time.monotonic() + config["proxy_wait_timeout"]
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProxyUnavailable("Proxy TCP wait expired.")
        try:
            with socket.create_connection(endpoint, timeout=min(config["proxy_connect_timeout"], remaining)):
                return  # TCP only: no HTTP, TLS, credentials, or generation payload.
        except OSError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProxyUnavailable("Proxy TCP wait expired.") from None
            time.sleep(min(config["proxy_poll_interval"], remaining))


def run_server(server, config, argv):
    for attempt in range(1, config["startup_attempts"] + 1):
        waiting_for_proxy = True
        try:
            wait_for_proxy(config)
            waiting_for_proxy = False
            result = server.main(argv)
            return 0 if result is None else int(result)  # Never restart on a return code.
        except Exception as exc:
            error_type = getattr(server, "StartupError", None)
            safe = (waiting_for_proxy and isinstance(exc, ProxyUnavailable)) or (
                isinstance(error_type, type) and isinstance(exc, error_type)
                and getattr(exc, "startup_only", False) is True
                and getattr(exc, "cleanup_complete", False) is True
                and getattr(exc, "retryable", False) is True
            )
            if not safe or attempt == config["startup_attempts"]:
                # Never echo raw exceptions: upstream errors may contain credentials.
                print("Service stopped; startup unavailable or failure is not safe to retry.", file=sys.stderr)
                return 1
            delay = min(config["startup_backoff_initial"] * 2 ** (attempt - 1), config["startup_backoff_max"])
            print(f"Startup attempt {attempt} failed; retrying in {delay:g}s.", file=sys.stderr)
            time.sleep(delay)
    return 1


def _launch(args):
    try:
        config = load_config(args.config)
        with key_environment(resolve_key(config)):
            server = importlib.import_module("openai_compatible_server")
            converted = server_argv(config)
            validate_server(server, config, converted)
            if args.check:
                print("Configuration and server interface valid; no service was started.")
                return 0
            return run_server(server, config, converted)
    except KeyboardInterrupt:
        print("Service interrupted.", file=sys.stderr)
        return 130
    except (ConfigError, InterfaceError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception:
        print("Service setup failed; check installed dependencies and server interface.", file=sys.stderr)
        return 1


@contextmanager
def log_output(path):
    """Opt-in UTF-8 append log; relative paths are stable across scheduler cwd."""
    if path is None:
        yield
        return
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    # Do not auto-create directories or touch a default log without explicit opt-in.
    with path.open("a", encoding="utf-8", buffering=1) as stream:
        with redirect_stdout(stream), redirect_stderr(stream):
            yield


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "data" / "settings.json")
    parser.add_argument("--log-file", type=Path, default=None,
                        help="Opt-in UTF-8 line-buffered append log; relative to the project directory")
    parser.add_argument("--check", action="store_true", help="Validate configuration/interface only; no TCP or server start")
    args = parser.parse_args(argv)
    try:
        with log_output(args.log_file):
            return _launch(args)
    except KeyboardInterrupt:
        print("Service interrupted.", file=sys.stderr)
        return 130
    except OSError:
        print("Cannot open or write the service log; check its directory and permissions.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
