"""Offline tests only: no real browser, cookies, identity, or Gemini requests."""
from contextlib import redirect_stderr, redirect_stdout
import copy
import io
import json
import os
import sys
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

import import_browser_session as importer


IDENTITY = "sample@example.test"
LABEL = f"Google Account: Test ({IDENTITY})"
COOKIE = {"name": "__Secure-1PSID", "value": "synthetic-secret", "domain": ".google.com",
          "path": "/", "expires": -1, "secure": True, "httpOnly": True, "sameSite": "None"}


class Page:
    def __init__(self, labels=None, url="https://gemini.google.com/app"):
        self.url = url
        self.labels = [LABEL] if labels is None else labels
        self.reads = 0

    def locator(self, selector):
        assert selector == "[aria-label]"
        return self

    def evaluate_all(self, expression):
        assert "getAttribute('aria-label')" in expression
        self.reads += 1
        return self.labels


class Context:
    def __init__(self, pages=None, cookies=None):
        self.pages = [Page()] if pages is None else pages
        self.jar = [copy.deepcopy(COOKIE)] if cookies is None else cookies

    def cookies(self):
        return self.jar


class Browser:
    def __init__(self, contexts=None):
        self.contexts = [Context()] if contexts is None else contexts
    # Deliberately no close/new_context APIs: accidental mutation fails tests.


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for key, value in (("ROOT", self.root), ("DATA_DIR", self.root / "data")):
            p = patch.object(importer, key, value)
            p.start()
            self.addCleanup(p.stop)

    def test_loopback_urls_only(self):
        for url in ("http://127.0.0.1:9222", "http://localhost:9222", "http://[::1]:9222",
                    "ws://127.0.0.1:9222/devtools/browser/test"):
            self.assertIn(":9222", importer.validate_cdp_url(url))
        self.assertEqual(importer.validate_cdp_url("http://localhost:9222"), "http://127.0.0.1:9222")
        for url in ("http://example.test:9222", "http://192.168.1.1:9222", "http://0.0.0.0:9222",
                    "http://localhost.evil:9222", "http://127.0.0.1", "http://127.0.0.1:0",
                    "http://user:password@127.0.0.1:9222", "file://127.0.0.1:9222",
                    "http://127.0.0.1:9222/?secret=x", "http://127.0.0.1:9222/#x",
                    "http://127.0.0.1:99999", " http://127.0.0.1:9222", "http://127.0.0.1:9222\\x",
                    "http://[::]:9222", "http://127.1:9222", "http://[::1%25lo]:9222"):
            with self.subTest(url=url), self.assertRaises(importer.ImportFailure):
                importer.validate_cdp_url(url)

    def test_identity_exact_case_insensitive_and_double_checked(self):
        page = Page([f"Google Account: Test ({IDENTITY.upper()})"])
        result = importer.collect_session(Browser([Context([page])]), IDENTITY)
        self.assertEqual(result["email"], IDENTITY)
        self.assertEqual(page.reads, 2)

    def test_fail_closed_identity(self):
        for pages in ([], [Page([])], [Page(["Sign in"])], [Page(["synthetic-secret"])],
                      [Page([f"Google Account (prefix{IDENTITY})"])],
                      [Page([f"Google Account ({IDENTITY}.evil)"])],
                      [Page(url="https://gemini.google.com.evil/app")],
                      [Page(url="http://gemini.google.com/app")],
                      [Page(url="https://accounts.google.com/app")],
                      [Page(), Page(["Google Account (other@example.test)"])],
                      [Page([LABEL, "Google Account (other@example.test)"])],
                      [Page(url="https://user@gemini.google.com/app")]):
            with self.subTest(pages=pages), self.assertRaises(importer.ImportFailure):
                importer.collect_session(Browser([Context(pages)]), IDENTITY)

    def test_context_count(self):
        for contexts in ([], [Context(), Context()]):
            with self.assertRaises(importer.ImportFailure):
                importer.collect_session(Browser(contexts), IDENTITY)

    def test_http_discovery_remains_local(self):
        socket_url = "ws://127.0.0.1:9222/devtools/browser/test"
        opener = MagicMock()
        response = opener.open.return_value.__enter__.return_value
        response.read.return_value = json.dumps({"webSocketDebuggerUrl": socket_url}).encode()
        with patch.object(importer, "build_opener", return_value=opener) as build:
            self.assertEqual(importer.resolve_cdp_endpoint("http://localhost:9222"), socket_url)
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1], importer._NoRedirect)
        self.assertEqual(opener.open.call_args.args[0].full_url, "http://127.0.0.1:9222/json/version")
        for discovered in ("ws://example.test:9222/test", "http://127.0.0.1:9222", "ws://user:secret@127.0.0.1:9222/test"):
            response.read.return_value = json.dumps({"webSocketDebuggerUrl": discovered}).encode()
            with patch.object(importer, "build_opener", return_value=opener), self.assertRaises(importer.ImportFailure):
                importer.resolve_cdp_endpoint("http://127.0.0.1:9222")
        with self.assertRaises(importer.ImportFailure):
            importer._NoRedirect().redirect_request(None, None, 302, None, None, "https://example.test")

    def test_context_switch_during_collection(self):
        context = Context()
        browser = Browser([context])
        def cookies():
            browser.contexts = [Context()]
            return context.jar
        context.cookies = cookies
        with self.assertRaises(importer.ImportFailure):
            importer.collect_session(browser, IDENTITY)

    def test_identity_switch_during_cookie_read(self):
        context = Context()
        def cookies():
            context.pages[0].labels = ["Google Account (other@example.test)"]
            return context.jar
        context.cookies = cookies
        with self.assertRaises(importer.ImportFailure):
            importer.collect_session(Browser([context]), IDENTITY)

    def test_complete_cookie_records_exact_domain_scope(self):
        scoped = [dict(COOKIE, domain=d) for d in importer.COOKIE_DOMAINS]
        scoped += [dict(COOKIE, name="OTHER", path="/app", partitionKey="https://google.com")]
        rejected = [dict(COOKIE, domain=d) for d in ("evilgoogle.com", "google.com.evil", "accounts.google.com",
                    ".youtube.com", "..google.com", "gemini.google.com.evil")]
        jar = scoped + rejected
        before = copy.deepcopy(jar)
        result = importer.collect_session(Browser([Context(cookies=jar)]), IDENTITY)
        self.assertEqual(result["cookies"], scoped)
        self.assertEqual(jar, before)

    def test_missing_blank_expired_or_wrong_scope_psid(self):
        jars = [[], [dict(COOKIE, value="")], [dict(COOKIE, value="  ")],
                [dict(COOKIE, domain="accounts.google.com")], [dict(COOKIE, expires=1)],
                [dict(COOKIE, name="__Secure-1PSIDTS")], [dict(COOKIE, path="/other")]]
        for jar in jars:
            with self.subTest(jar=jar), self.assertRaises(importer.ImportFailure):
                importer.collect_session(Browser([Context(cookies=jar)]), IDENTITY)

    def test_invalid_names_and_directories(self):
        for name in ("", ".", "..", "../x", "a/b", "a\\b", "a:stream", "CON", "NUL", "COM1",
                     "name.", "name ", "x\n", "a" * 61):
            with self.subTest(name=name), self.assertRaises(importer.ImportFailure):
                importer.validate_name(name)
        for directory in (self.root / "public", "data/../public", "data/accounts/../accounts"):
            with self.assertRaises(importer.ImportFailure):
                importer.account_directory(directory)
        self.assertEqual(importer.account_directory(), self.root / "data/accounts")
        self.assertEqual(importer.account_directory("data/other"), self.root / "data/other")

    def test_atomic_save_and_timestamp_backup(self):
        path = importer.write_session("test-account", {"email": IDENTITY, "cookies": [COOKIE]})
        original = path.read_bytes()
        updated = {"email": IDENTITY, "cookies": [dict(COOKIE, value="synthetic-replacement")]}
        with patch.object(importer.os, "replace", wraps=os.replace) as replace:
            importer.write_session("test-account", updated)
        replace.assert_called_once()
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), updated)
        backups = list(path.parent.glob("test-account.*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertRegex(backups[0].name, r"^test-account\.\d{8}T\d{6}\.\d{6}Z\.bak$")
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertFalse(list(path.parent.glob("*.tmp")))
        self.assertFalse(list(path.parent.glob("*.lock")))
        if os.name != "nt":
            for f in [path, *backups]:
                self.assertEqual(f.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_failed_replace_keeps_original_and_backup(self):
        path = importer.write_session("a", {"cookies": [COOKIE]})
        original = path.read_bytes()
        with patch.object(importer.os, "replace", side_effect=OSError("synthetic failure")):
            with self.assertRaises(OSError):
                importer.write_session("a", {"cookies": []})
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(next(path.parent.glob("a.*.bak")).read_bytes(), original)
        self.assertFalse(list(path.parent.glob("*.tmp")))
        self.assertFalse(list(path.parent.glob("*.lock")))

    def test_failed_backup_never_replaces_original(self):
        path = importer.write_session("a", {"cookies": [COOKIE]})
        original = path.read_bytes()
        with patch.object(importer, "_private_write", side_effect=OSError("failure")), patch.object(importer.os, "replace") as replace:
            with self.assertRaises(OSError):
                importer.write_session("a", {"cookies": []})
        replace.assert_not_called()
        self.assertEqual(path.read_bytes(), original)

    def test_existing_lock_rejected_not_removed(self):
        directory = self.root / "data/accounts"
        directory.mkdir(parents=True)
        lock = directory / ".a.import.lock"
        lock.touch()
        with self.assertRaises(importer.ImportFailure):
            importer.write_session("a", {})
        self.assertTrue(lock.exists())

    def test_symlinks_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        data = self.root / "data"
        data.mkdir()
        try:
            (data / "accounts").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("OS does not permit creating test symlinks")
        with self.assertRaises(importer.ImportFailure):
            importer.write_session("a", {})
        (data / "accounts").unlink()
        (data / "accounts").mkdir()
        (data / "accounts/a.json").symlink_to(outside / "missing")
        with self.assertRaises(importer.ImportFailure):
            importer.write_session("a", {})

    def test_import_only_attaches_and_reads_existing_objects(self):
        pw = MagicMock()
        browser = Browser()
        pw.chromium.connect_over_cdp.return_value = browser
        manager = MagicMock()
        manager.__enter__.return_value = pw
        sync = types.ModuleType("playwright.sync_api")
        sync.sync_playwright = MagicMock(return_value=manager)
        with patch.dict(sys.modules, {"playwright.sync_api": sync}):
            path = importer.import_session("a", "ws://127.0.0.1:9222/devtools/browser/test", IDENTITY)
        pw.chromium.connect_over_cdp.assert_called_once_with("ws://127.0.0.1:9222/devtools/browser/test", timeout=10000)
        pw.chromium.launch.assert_not_called()
        self.assertEqual(browser.contexts[0].pages[0].reads, 2)
        self.assertEqual(json.loads(path.read_text())["cookies"], [COOKIE])
        manager.__exit__.assert_called_once()

    def test_invalid_input_never_discovers_or_writes(self):
        with patch.object(importer, "resolve_cdp_endpoint") as discover, patch.object(importer, "write_session") as write:
            for name, endpoint, email, directory in (
                ("../bad", "http://127.0.0.1:9222", IDENTITY, None),
                ("a", "http://example.test:9222", IDENTITY, None),
                ("a", "http://127.0.0.1:9222", "not-email", None),
                ("a", "http://127.0.0.1:9222", IDENTITY, self.root / "public"),
            ):
                with self.assertRaises(importer.ImportFailure):
                    importer.import_session(name, endpoint, email, directory)
        discover.assert_not_called()
        write.assert_not_called()
        self.assertFalse((self.root / "data").exists())

    def test_no_output_leaks_on_success_failure_or_argument_error(self):
        for fail in (False, True):
            out, err = io.StringIO(), io.StringIO()
            with patch.object(importer, "import_session", side_effect=RuntimeError(IDENTITY + COOKIE["value"]) if fail else None), redirect_stdout(out), redirect_stderr(err):
                code = importer.main(["--name", "a", "--cdp-url", "http://127.0.0.1:9222", "--expected-email", IDENTITY])
            self.assertEqual(code, 1 if fail else 0)
            self.assertNotIn(IDENTITY, out.getvalue() + err.getvalue())
            self.assertNotIn(COOKIE["value"], out.getvalue() + err.getvalue())
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit):
            importer.main(["--unexpected", IDENTITY + COOKIE["value"]])
        self.assertNotIn(IDENTITY, err.getvalue())
        self.assertNotIn(COOKIE["value"], err.getvalue())


if __name__ == "__main__":
    unittest.main()
