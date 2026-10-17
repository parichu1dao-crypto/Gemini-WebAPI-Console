"""Offline naming/resolution of a client's dynamic Cookie RPC models.

No SDK import, network, enum fallback, model substitution or mutation. Rebuild
the catalog after client reinitialization; it is a snapshot of that session.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable

ARGON_ID = "95a4c0abcba24364"
ARGON_API_NAME = "gemini-4-argon"


class ModelCatalogError(ValueError):
    """Base error for fail-closed model selection."""


class InvalidModelMetadata(ModelCatalogError):
    pass


class UnsupportedModel(ModelCatalogError):
    pass


class UnavailableModel(ModelCatalogError):
    pass


class AmbiguousModel(ModelCatalogError):
    pass


class StrictProRequired(ModelCatalogError):
    pass


class StaleModelCatalog(ModelCatalogError):
    pass


def _metadata(model: Any) -> tuple[str, str, str, bool]:
    model_id = getattr(model, "model_id", None)
    name = getattr(model, "model_name", "")
    display = getattr(model, "display_name", "")
    available = getattr(model, "is_available", None)
    if not isinstance(model_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", model_id):
        raise InvalidModelMetadata("dynamic model requires a nonempty safe model_id")
    if name is None:
        name = ""
    if display is None:
        display = ""
    if not isinstance(name, str) or not isinstance(display, str) or type(available) is not bool:
        raise InvalidModelMetadata("invalid dynamic name, display_name or availability")
    return model_id, name, display, available


def _argon(meta: tuple[str, str, str, bool]) -> bool:
    model_id, name, display, _ = meta
    return (model_id.casefold() == ARGON_ID or "argon" in display.casefold()
            or name.casefold() == ARGON_API_NAME)


def _api_name(meta: tuple[str, str, str, bool]) -> str:
    if _argon(meta):
        return ARGON_API_NAME
    model_id, name, _, _ = meta
    # Preserve existing nonempty API names exactly, not display labels.
    return name if name.strip() else f"web-model-{model_id}"


@dataclass(frozen=True)
class CatalogEntry:
    api_name: str
    model_id: str
    available: bool
    dynamic_model: Any
    _snapshot: tuple[str, str, str, bool]


class CookieModelCatalog:
    """Exact, case-sensitive API names; all resolutions return original objects.

    Duplicate API names are ambiguous even if one entry is unavailable. No raw
    ID aliases or default model are accepted. strict_pro additionally requires
    an existing versioned Gemini Pro API name; display-only 'Pro' and unknown
    IDs are insufficient. This is a naming gate, not paid-tier attestation.
    """

    def __init__(self, models: Iterable[Any] | None):
        entries = []
        for model in (() if models is None else models):
            meta = _metadata(model)
            entries.append(CatalogEntry(_api_name(meta), meta[0], meta[3], model, meta))
        self.entries = tuple(entries)

    @classmethod
    def from_client(cls, client: Any) -> CookieModelCatalog:
        """Reads list_models() once; never initializes the client."""
        return cls(client.list_models())

    @property
    def api_names(self) -> tuple[str, ...]:
        """Includes unavailable entries; availability remains explicit in entries."""
        return tuple(entry.api_name for entry in self.entries)

    def resolve(self, api_name: str, *, strict_pro: bool = False) -> Any:
        if not isinstance(api_name, str) or not api_name.strip():
            raise UnsupportedModel("a nonempty exact API model name is required")
        matches = [entry for entry in self.entries if entry.api_name == api_name]
        if not matches:
            raise UnsupportedModel("requested model is absent from the dynamic catalog")
        if len(matches) != 1:
            raise AmbiguousModel("multiple dynamic entries have the requested API name")
        entry = matches[0]
        if _metadata(entry.dynamic_model) != entry._snapshot:
            raise StaleModelCatalog("dynamic metadata changed; rebuild the catalog")
        if not entry.available:
            raise UnavailableModel("requested dynamic model is unavailable")
        if strict_pro:
            existing_name = entry._snapshot[1]
            if _argon(entry._snapshot) or not re.fullmatch(
                r"gemini-\d+(?:\.\d+)*-pro(?:-(?:plus|advanced))?", existing_name
            ):
                raise StrictProRequired("strict Pro requires an explicit Pro model, never Argon")
        return entry.dynamic_model
