"""Synthetic offline tests: no credentials, SDK, browser or production imports."""
import socket
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cookie_model_catalog import (
    ARGON_API_NAME, ARGON_ID, CookieModelCatalog, AmbiguousModel,
    InvalidModelMetadata, StaleModelCatalog, StrictProRequired,
    UnavailableModel, UnsupportedModel,
)


def model(mid="abc123", name="", display="", available=True):
    return SimpleNamespace(model_id=mid, model_name=name, display_name=display,
                           is_available=available, capacity=2, capacity_field=12)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "socket.create_connection", "socket.getaddrinfo"):
            guard = patch(target, side_effect=AssertionError("network forbidden in offline tests"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_reads_client_once_and_returns_exact_object(self):
        dynamic = model(ARGON_ID)
        client = SimpleNamespace(list_models=Mock(return_value=[dynamic]))
        catalog = CookieModelCatalog.from_client(client)
        client.list_models.assert_called_once_with()
        self.assertIs(catalog.resolve(ARGON_API_NAME), dynamic)

    def test_argon_display_case_insensitive(self):
        dynamic = model(display="Gemini 4 ARGoN")
        self.assertIs(CookieModelCatalog([dynamic]).resolve(ARGON_API_NAME), dynamic)

    def test_observed_id_case_insensitive(self):
        self.assertEqual(CookieModelCatalog([model(ARGON_ID.upper())]).api_names, (ARGON_API_NAME,))

    def test_argon_overrides_stale_pro_name(self):
        dynamic = model(ARGON_ID, "gemini-3-pro", "Pro")
        catalog = CookieModelCatalog([dynamic])
        self.assertEqual(catalog.api_names, (ARGON_API_NAME,))
        with self.assertRaises(StrictProRequired):
            catalog.resolve(ARGON_API_NAME, strict_pro=True)

    def test_keep_nonempty_names_and_metadata_untouched(self):
        dynamic = model(name="vendor-existing-name")
        before = vars(dynamic).copy()
        catalog = CookieModelCatalog([dynamic])
        self.assertIs(catalog.resolve("vendor-existing-name"), dynamic)
        self.assertEqual(vars(dynamic), before)

    def test_unknown_empty_name(self):
        for name in ("", "   ", None):
            with self.subTest(name=name):
                dynamic = model(name=name)
                catalog = CookieModelCatalog([dynamic])
                self.assertEqual(catalog.api_names, ("web-model-abc123",))
                self.assertIs(catalog.resolve("web-model-abc123"), dynamic)

    def test_no_default_or_raw_id_alias(self):
        catalog = CookieModelCatalog([model(ARGON_ID), model(name="gemini-3-flash")])
        for name in ("", " ", None, ARGON_ID, "missing", "GEMINI-4-ARGON"):
            with self.subTest(name=name), self.assertRaises(UnsupportedModel):
                catalog.resolve(name)

    def test_unavailable_never_falls_back(self):
        catalog = CookieModelCatalog([model(ARGON_ID, available=False), model(name="gemini-3-flash")])
        with self.assertRaises(UnavailableModel):
            catalog.resolve(ARGON_API_NAME)

    def test_duplicate_argon_is_ambiguous_even_if_unavailable(self):
        for available in (False, True):
            catalog = CookieModelCatalog([model(ARGON_ID), model("other", display="Argon", available=available)])
            with self.subTest(available=available), self.assertRaises(AmbiguousModel):
                catalog.resolve(ARGON_API_NAME)

    def test_generic_and_fallback_name_collisions(self):
        for models in ([model(name="same"), model("other", name="same")],
                       [model(), model("other", name="web-model-abc123")]):
            catalog = CookieModelCatalog(models)
            with self.assertRaises(AmbiguousModel):
                catalog.resolve(catalog.api_names[0])

    def test_strict_pro_accepts_actual_pro_object(self):
        for name in ("gemini-3-pro", "gemini-3.1-pro", "gemini-3-pro-plus", "gemini-3-pro-advanced"):
            dynamic = model(name=name)
            with self.subTest(name=name):
                self.assertIs(CookieModelCatalog([dynamic]).resolve(name, strict_pro=True), dynamic)

    def test_strict_pro_rejects_argon_flash_unknown_and_display_only(self):
        for dynamic in (model(ARGON_ID), model(display="Argon"), model(name=ARGON_API_NAME),
                        model(name="gemini-3-flash"), model(display="Pro"), model(name="not-pro")):
            catalog = CookieModelCatalog([dynamic])
            with self.subTest(name=catalog.api_names[0]), self.assertRaises(StrictProRequired):
                catalog.resolve(catalog.api_names[0], strict_pro=True)

    def test_strict_pro_does_not_select_other_available_model(self):
        catalog = CookieModelCatalog([model(ARGON_ID)])
        with self.assertRaises(UnsupportedModel):
            catalog.resolve("gemini-3-pro", strict_pro=True)

    def test_empty_catalog(self):
        for models in (None, []):
            catalog = CookieModelCatalog(models)
            self.assertEqual(catalog.api_names, ())
            with self.assertRaises(UnsupportedModel):
                catalog.resolve(ARGON_API_NAME)

    def test_invalid_metadata_fails_closed(self):
        for dynamic in (model(""), model("bad id"), model(available=None), model(name=42)):
            with self.assertRaises(InvalidModelMetadata):
                CookieModelCatalog([dynamic])

    def test_changed_metadata_requires_rebuild(self):
        dynamic = model(ARGON_ID)
        catalog = CookieModelCatalog([dynamic])
        dynamic.is_available = False
        with self.assertRaises(StaleModelCatalog):
            catalog.resolve(ARGON_API_NAME)
        with self.assertRaises(UnavailableModel):
            CookieModelCatalog([dynamic]).resolve(ARGON_API_NAME)


if __name__ == "__main__":
    unittest.main()
