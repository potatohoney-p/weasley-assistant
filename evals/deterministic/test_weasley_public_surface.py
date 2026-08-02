from __future__ import annotations

import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_fresh_default_home_uses_weasley_namespace(monkeypatch):
    from weasley_assistant.config import Settings

    monkeypatch.delenv("WEASLEY_ASSISTANT_HOME", raising=False)
    assert Settings().home == Path.home() / ".weasley" / "assistant"


def test_public_cli_and_package_are_shipped():
    with (REPO / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)
    assert project["project"]["scripts"] == {
        "weasley-assistant": "weasley_assistant.__main__:main"
    }
    assert project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "weasley_assistant"
    ]
    assert (REPO / "weasley_assistant" / "app.py").is_file()


def test_public_package_exports_version_consistently():
    import weasley_assistant

    assert weasley_assistant.__version__ == "0.2.0"
