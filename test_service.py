"""Offline service tests: mocked backend, sockets, clocks, and environment."""
import argparse
import ast
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import service


class StartupError(Exception):
    startup_only = True
    cleanup_complete = True
    retryable = True


class FakeServer:
    StartupError = StartupError
    STRICT_PRO_MODEL = "gemini-3-pro"

    def __init__(self):
        self.main = Mock(return_value=0)

    @staticmethod
    def normalize_model_name(model, default):
        return model or default

    @staticmethod
    def build_parser():
        parser = argparse.ArgumentParser()
        for name in ("host", "port", "accounts", "account-dir", "default-model", "proxy",
                     "init-timeout", "request-timeout", "queue-timeout",
                     "account-failure-threshold", "account-cooldown"):
            parser.add_argument("--" + name)
        parser.add_argument("--api-key", default=os.environ.get(service.KEY_ENV, ""))
        parser.add_argument("--strict-pro", dest="strict_pro", action="store_true")
        parser.add_argument("--allow-non-pro", dest="strict_pro", action="store_false")
        for name in ("allow-free-pro", "ignore-proxy", "skip-verify", "auto-refresh", "quiet"):
            parser.add_argument("--" + name, action="store_true")
        return parser


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix=".service-test-", dir=service.ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        (self.data / "accounts").mkdir()
        self.path = self.data / "settings.json"
        self.key = "unit-test-only-not-a-credential"
        self.raw = {"api_key": self.key}
        self.backend = FakeServer()
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.network = self.enterContext(patch.object(service.socket, "create_connection", side_effect=AssertionError("No real sockets")))
        self.importer = self.enterContext(patch.object(service.importlib, "import_module", return_value=self.backend))
        self.output = io.StringIO()
        self.enterContext(redirect_stdout(self.output))
        self.enterContext(redirect_stderr(self.output))

    def config(self, **updates):
        self.path.write_text(json.dumps({**self.raw, **updates}), encoding="utf-8")
        return service.load_config(self.path)

    def invoke(self, *flags):
        return service.main(["--config", str(self.path), *flags])

    def test_defaults_and_paths(self):
        cfg = self.config()
        self.assertEqual(cfg["port"], 8787)
        self.assertEqual(cfg["host"], "127.0.0.1")
        self.assertTrue(cfg["strict_pro"])
        self.assertEqual(cfg["account_dir"], str(self.data / "accounts"))
        self.assertEqual(cfg["queue_timeout"], 2)

    def test_single_default_source_ignores_service_json(self):
        self.config(model="gemini-3-pro")
        (self.data / "service.json").write_text("not JSON", encoding="utf-8")
        with patch.object(service, "ROOT", self.root):
            self.assertEqual(service.main(["--check"]), 0)
        self.backend.main.assert_not_called()
        self.network.assert_not_called()

    def test_no_implicit_fallback_if_settings_missing(self):
        (self.data / "service.json").write_text(json.dumps(self.raw), encoding="utf-8")
        with patch.object(service, "ROOT", self.root):
            self.assertEqual(service.main([]), 2)
        self.importer.assert_not_called()

    def test_explicit_legacy_file(self):
        self.config()
        other = self.data / "service.json"
        other.write_bytes(self.path.read_bytes())
        self.assertEqual(service.main(["--config", str(other), "--check"]), 0)

    def test_console_metadata_is_not_forwarded(self):
        cfg = self.config(model="gemini-3-pro", proxy="", admin_key="private-admin",
                          transport="managed", browser_key="private-browser",
                          browser_model="other", brower_url="ignored", managed_model="other")
        args = service.server_argv(cfg)
        self.assertEqual(cfg["transport"], "cookie")
        self.assertIsNone(cfg["proxy"])
        self.assertNotIn("admin_key", cfg)
        self.assertNotIn("browser_key", cfg)
        for forbidden in (self.key, "private-admin", "private-browser", "managed", "browser", "transport"):
            self.assertNotIn(forbidden, " ".join(args))

    def test_mapping_options(self):
        cfg = self.config(host="localhost", port=8899, accounts=["alice", "arden+test"],
                          strict_pro=False, model="gemini-3-flash", ignore_proxy=True,
                          allow_free_pro=True, auto_refresh=True, quiet=True,
                          init_timeout=15, request_timeout=20, queue_timeout=3,
                          retry={"attempts": 2, "initial_seconds": 0.5, "max_seconds": 1},
                          proxy_wait_seconds=7)
        parsed = self.backend.build_parser().parse_args(service.server_argv(cfg))
        self.assertEqual(parsed.host, "127.0.0.1")
        self.assertEqual(parsed.accounts, "alice,arden+test")
        self.assertEqual(parsed.queue_timeout, "3")
        self.assertEqual(parsed.default_model, "gemini-3-flash")
        self.assertFalse(parsed.strict_pro)
        self.assertTrue(parsed.ignore_proxy)
        self.assertEqual(cfg["startup_attempts"], 2)
        self.assertEqual(cfg["proxy_wait_timeout"], 7)

    def test_invalid_fields_and_types(self):
        bad = [
            {"port": True}, {"port": 0}, {"port": 65536}, {"port": 2.5},
            {"host": "0.0.0.0"}, {"host": "::1"}, {"host": 123},
            {"strict_pro": "false"}, {"queue_timeout": 0}, {"request_timeout": float("nan")},
            {"init_timeout": float("inf")}, {"startup_attempts": 11},
            {"proxy_poll_interval": 0}, {"proxy_connect_timeout": -1},
            {"proxy_wait_seconds": -1}, {"startup_backoff_initial": 10, "startup_backoff_max": 1},
            {"accounts": ["../secret"]}, {"accounts": ["alice", "alice"]},
            {"accounts": ["all"]}, {"accounts": []}, {"accounts": "alice,,bob"},
            {"accounts": True}, {"account_dir": "missing"}, {"api_key": 5},
            {"transport": "unknown"}, {"unknown": 1},
            {"model": "a", "default_model": "b"},
            {"proxy_wait_seconds": 1, "proxy_wait_timeout": 2},
            {"retry": {"attempts": 2}, "startup_attempts": 3},
            {"retry": {"typo": 3}}, {"retry": False},
            {"proxy": "http://127.0.0.1:1234", "ignore_proxy": True},
            {"proxy": "http://127.0.0.1:0"}, {"proxy": "ftp://127.0.0.1"},
            {"proxy": "http://127.0.0.1/path"}, {"proxy": "http://proxy.invalid:8080"},
            {"proxy": "http://user:private@127.0.0.1:1234"}, {"port": 10 ** 1000},
        ]
        for values in bad:
            with self.subTest(values=values), self.assertRaises(service.ConfigError):
                self.config(**values)

    def test_invalid_json_and_duplicates_are_sanitized(self):
        for text in ('[]', '{"port":1,"port":2}', '{"secret":', 'null'):
            self.path.write_text(text, encoding="utf-8")
            self.assertEqual(self.invoke(), 2)
        self.importer.assert_not_called()

    def test_api_key_environment_override_and_restore(self):
        self.config()
        os.environ[service.KEY_ENV] = "environment-test-value"
        def start(argv):
            self.assertNotIn(self.key, " ".join(argv))
            self.assertNotIn("environment-test-value", " ".join(argv))
            self.assertEqual(os.environ[service.KEY_ENV], "environment-test-value")
            return 0
        self.backend.main.side_effect = start
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(os.environ[service.KEY_ENV], "environment-test-value")
        self.assertNotIn(self.key, self.output.getvalue())

    def test_config_key_is_temporary_environment_only(self):
        self.config()
        self.backend.main.side_effect = lambda argv: 0 if os.environ[service.KEY_ENV] == self.key else 99
        self.assertEqual(self.invoke(), 0)
        self.assertNotIn(service.KEY_ENV, os.environ)

    def test_empty_environment_does_not_fall_back_to_file(self):
        self.config()
        os.environ[service.KEY_ENV] = ""
        self.assertEqual(self.invoke(), 2)
        self.importer.assert_not_called()

    def test_missing_key_fails_before_import(self):
        self.config(api_key=None)
        self.assertEqual(self.invoke(), 2)
        self.importer.assert_not_called()

    def test_old_main_signature_is_rejected_before_start(self):
        self.config()
        self.backend.main = lambda: self.fail("Must not start")
        self.assertEqual(self.invoke(), 2)

    def test_missing_startup_contract_is_fail_closed(self):
        self.config()
        self.backend.StartupError = None
        self.assertEqual(self.invoke(), 2)
        self.backend.main.assert_not_called()
        self.config(startup_attempts=1)
        self.assertEqual(self.invoke(), 0)

    def test_strict_model_mismatch(self):
        self.config(model="gemini-3-flash")
        self.assertEqual(self.invoke(), 2)
        self.backend.main.assert_not_called()

    def test_check_does_not_wait_or_start(self):
        self.config(proxy="http://127.0.0.1:1234")
        self.assertEqual(self.invoke("--check"), 0)
        self.network.assert_not_called()
        self.backend.main.assert_not_called()

    def test_proxy_late_start_tcp_only(self):
        cfg = self.config(proxy="socks5://localhost:1234", proxy_wait_seconds=5)
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        self.network.side_effect = [ConnectionRefusedError(), connection]
        with patch.object(service.time, "monotonic", return_value=0), patch.object(service.time, "sleep") as sleep:
            service.wait_for_proxy(cfg)
        self.assertEqual(self.network.call_count, 2)
        self.network.assert_called_with(("127.0.0.1", 1234), timeout=1)
        sleep.assert_called_once_with(1)
        connection.__exit__.assert_called_once()
        connection.send.assert_not_called()

    def test_proxy_timeout_is_bounded(self):
        cfg = self.config(proxy="http://127.0.0.1:1234", proxy_wait_seconds=1)
        self.network.side_effect = OSError("sensitive upstream text")
        with patch.object(service.time, "monotonic", side_effect=[0, 0, 1]), self.assertRaises(service.ProxyUnavailable):
            service.wait_for_proxy(cfg)
        self.assertEqual(self.network.call_count, 1)

    def test_disabled_proxy_wait(self):
        cfg = self.config(proxy="http://proxy.invalid:8080", proxy_wait_seconds=0)
        service.wait_for_proxy(cfg)
        self.network.assert_not_called()

    def test_finite_exponential_backoff_then_success(self):
        self.config(retry={"attempts": 4, "initial_seconds": 1, "max_seconds": 2})
        self.backend.main.side_effect = [StartupError(self.key), StartupError(self.key), StartupError(self.key), 0]
        with patch.object(service.time, "sleep") as sleep:
            self.assertEqual(self.invoke(), 0)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2, 2])
        self.assertEqual(self.backend.main.call_count, 4)
        self.assertNotIn(self.key, self.output.getvalue())

    def test_retry_exhaustion(self):
        self.config()
        self.backend.main.side_effect = StartupError(self.key)
        with patch.object(service.time, "sleep") as sleep:
            self.assertEqual(self.invoke(), 1)
        self.assertEqual(self.backend.main.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_unmarked_unclean_or_permanent_failure_never_retried(self):
        self.config()
        for attribute in ("startup_only", "cleanup_complete", "retryable"):
            self.backend.main.reset_mock()
            error = StartupError(self.key)
            setattr(error, attribute, False)
            self.backend.main.side_effect = error
            with patch.object(service.time, "sleep") as sleep:
                self.assertEqual(self.invoke(), 1)
            self.backend.main.assert_called_once()
            sleep.assert_not_called()

    def test_generation_or_runtime_failure_never_retried(self):
        self.config()
        self.backend.main.side_effect = RuntimeError(self.key)
        with patch.object(service.time, "sleep") as sleep:
            self.assertEqual(self.invoke(), 1)
        self.backend.main.assert_called_once()
        sleep.assert_not_called()
        self.assertNotIn(self.key, self.output.getvalue())

    def test_nonzero_return_never_restarted(self):
        self.config()
        self.backend.main.return_value = 9
        self.assertEqual(self.invoke(), 9)
        self.backend.main.assert_called_once()

    def test_backend_proxy_failure_is_not_a_preflight_failure(self):
        self.config()
        self.backend.main.side_effect = service.ProxyUnavailable("runtime")
        with patch.object(service.time, "sleep") as sleep:
            self.assertEqual(self.invoke(), 1)
        self.backend.main.assert_called_once()
        sleep.assert_not_called()

    def test_preflight_retry_does_not_call_backend_before_ready(self):
        self.config(proxy="http://127.0.0.1:1234")
        with patch.object(service, "wait_for_proxy", side_effect=[service.ProxyUnavailable(), None]) as wait, patch.object(service.time, "sleep"):
            self.assertEqual(self.invoke(), 0)
        self.assertEqual(wait.call_count, 2)
        self.backend.main.assert_called_once()

    def test_interrupt_during_proxy_wait(self):
        self.config(proxy="http://127.0.0.1:1234")
        self.network.side_effect = KeyboardInterrupt()
        self.assertEqual(self.invoke(), 130)
        self.backend.main.assert_not_called()
        self.assertNotIn(service.KEY_ENV, os.environ)

    def test_interrupt_during_backoff(self):
        self.config()
        self.backend.main.side_effect = StartupError()
        with patch.object(service.time, "sleep", side_effect=KeyboardInterrupt()):
            self.assertEqual(self.invoke(), 130)
        self.backend.main.assert_called_once()

    def test_interrupt_during_server(self):
        self.config()
        self.backend.main.side_effect = KeyboardInterrupt()
        self.assertEqual(self.invoke(), 130)
        self.backend.main.assert_called_once()

    def test_actual_parser_mapping_without_importing_backend(self):
        tree = ast.parse((service.ROOT / "openai_compatible_server.py").read_text(encoding="utf-8"))
        definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "build_parser")
        module = ast.Module(body=[definition], type_ignores=[])
        namespace = {"argparse": argparse, "os": os, "ACCOUNT_DIR": self.data / "accounts",
                     "STRICT_PRO_MODEL": "gemini-3-pro"}
        exec(compile(module, "<isolated-parser>", "exec"), namespace)
        cfg = self.config()
        with service.key_environment(self.key):
            parsed = namespace["build_parser"]().parse_args(service.server_argv(cfg))
        self.assertEqual(parsed.api_key, self.key)
        self.assertEqual(parsed.port, 8787)
        self.assertEqual(parsed.queue_timeout, 2)
        self.assertEqual(parsed.request_timeout, 300)

    def test_opt_in_log_captures_both_streams_and_appends(self):
        self.config()
        log = self.data / "server.log"
        log.write_text("previous\n", encoding="utf-8")
        original_stdout, original_stderr = service.sys.stdout, service.sys.stderr
        def start(argv):
            self.assertTrue(service.sys.stdout.line_buffering)
            print("mock backend ready")
            print("mock diagnostic", file=service.sys.stderr)
            self.assertIn("mock backend ready", log.read_text(encoding="utf-8"))
            return 0
        self.backend.main.side_effect = start
        with patch.object(service, "ROOT", self.root):
            self.assertEqual(self.invoke("--log-file", "data/server.log"), 0)
        self.assertEqual(log.read_text(encoding="utf-8"), "previous\nmock backend ready\nmock diagnostic\n")
        self.assertIs(service.sys.stdout, original_stdout)
        self.assertIs(service.sys.stderr, original_stderr)
        self.assertNotIn(self.key, log.read_text(encoding="utf-8"))

    def test_default_does_not_create_log(self):
        self.config()
        self.assertEqual(self.invoke("--check"), 0)
        self.assertFalse((self.data / "server.log").exists())

    def test_log_failure_does_not_start_backend(self):
        self.config()
        self.assertEqual(self.invoke("--log-file", str(self.root / "missing" / "server.log")), 2)
        self.backend.main.assert_not_called()

    def test_log_errors_do_not_dump_configuration_or_secrets(self):
        self.config(admin_key="admin-private", browser_key="browser-private")
        self.backend.main.side_effect = RuntimeError(self.key + "admin-private browser-private")
        log = self.data / "server.log"
        self.assertEqual(self.invoke("--log-file", str(log)), 1)
        text = log.read_text(encoding="utf-8")
        for secret in (self.key, "admin-private", "browser-private", "api_key"):
            self.assertNotIn(secret, text)


if __name__ == "__main__":
    unittest.main()
