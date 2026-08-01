"""Migration checks for the deprecated package and environment names."""

from __future__ import annotations

import importlib
import sys

import pytest

from weasley_assistant import WeasleyAssistant
from weasley_assistant.config import Settings


def test_public_environment_wins_and_legacy_environment_is_fallback(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy"
    public = tmp_path / "public"
    monkeypatch.setenv("WAKU_HOME", str(legacy))
    assert Settings().home == legacy

    monkeypatch.setenv("WEASLEY_ASSISTANT_HOME", str(public))
    assert Settings().home == public


def test_deprecated_import_is_a_thin_alias():
    sys.modules.pop("waku", None)
    with pytest.warns(DeprecationWarning):
        legacy = importlib.import_module("waku")
    assert legacy.Waku is WeasleyAssistant
