from __future__ import annotations

import sys
import tomllib
import types
from pathlib import Path

import pytest

from weasley_assistant import __version__
from weasley_assistant.__main__ import main

REPO = Path(__file__).resolve().parents[2]

SIDE_EFFECT_COMMANDS = [
    ("dashboard", "weasley_assistant.ops.dashboard"),
    ("voice", "weasley_assistant.gateway.voice"),
    ("telegram", "weasley_assistant.gateway.telegram"),
    ("discord", "weasley_assistant.gateway.discord"),
    ("whatsapp", "weasley_assistant.gateway.whatsapp"),
    ("brief", "weasley_assistant.ops.brief"),
    ("gather", "weasley_assistant.ops.gather"),
]


def _module_that_must_not_run(module_name: str) -> types.ModuleType:
    module = types.ModuleType(module_name)

    def fail() -> None:
        raise AssertionError(f"{module_name}.main() ran while rendering help")

    module.main = fail
    return module


def _stub_installer(monkeypatch, install) -> None:
    module_name = "weasley_assistant.memory.procedural.installer"
    module = types.ModuleType(module_name)
    module.install = install
    monkeypatch.setitem(sys.modules, module_name, module)


@pytest.mark.parametrize("flag", ["-h", "--help", "help"])
def test_help_is_a_successful_lightweight_command(flag, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["weasley-assistant", flag])

    main()

    captured = capsys.readouterr()
    assert "Usage:" in captured.out
    assert "skill install <url>" in captured.out
    assert captured.err == ""


@pytest.mark.parametrize("flag", ["-V", "--version", "version"])
def test_version_is_a_successful_lightweight_command(flag, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["weasley-assistant", flag])

    main()

    captured = capsys.readouterr()
    assert captured.out.strip() == f"weasley-assistant {__version__}"
    assert captured.err == ""


def test_runtime_version_matches_project_metadata():
    with (REPO / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)

    assert __version__ == project["project"]["version"]


def test_unknown_command_fails_with_actionable_help(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["weasley-assistant", "not-a-command"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Unknown command: not-a-command" in captured.err
    assert "Usage:" in captured.err


@pytest.mark.parametrize(("command", "module_name"), SIDE_EFFECT_COMMANDS)
@pytest.mark.parametrize("flag", ["-h", "--help", "help"])
def test_subcommand_help_never_starts_the_command(
    command,
    module_name,
    flag,
    monkeypatch,
    capsys,
):
    monkeypatch.setitem(sys.modules, module_name, _module_that_must_not_run(module_name))
    monkeypatch.setattr(sys, "argv", ["weasley-assistant", command, flag])

    main()

    captured = capsys.readouterr()
    assert f"weasley-assistant {command}" in captured.out
    assert "Usage:" in captured.out
    assert captured.err == ""


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["skill", "-h"], "Manage procedural skills."),
        (["skill", "--help"], "Manage procedural skills."),
        (["skill", "help"], "Manage procedural skills."),
        (["skill", "install", "-h"], "Download and install one procedural skill."),
        (["skill", "install", "--help"], "Download and install one procedural skill."),
        (["skill", "install", "help"], "Download and install one procedural skill."),
    ],
)
def test_skill_help_never_calls_the_installer(argv, expected, monkeypatch, capsys):
    def fail(_url: str) -> None:
        raise AssertionError("skill installer ran while rendering help")

    _stub_installer(monkeypatch, fail)
    monkeypatch.setattr(sys, "argv", ["weasley-assistant", *argv])

    main()

    captured = capsys.readouterr()
    assert expected in captured.out
    assert "Usage:" in captured.out
    assert captured.err == ""


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["skill"], "Missing skill command."),
        (["skill", "remove"], "Unknown skill command: remove"),
        (["skill", "install"], "skill install requires exactly one <url>."),
        (
            ["skill", "install", "https://example.test/SKILL.md", "extra"],
            "skill install requires exactly one <url>.",
        ),
    ],
)
def test_invalid_skill_arity_is_exit_two_without_installing(
    argv,
    message,
    monkeypatch,
    capsys,
):
    calls = []
    _stub_installer(monkeypatch, calls.append)
    monkeypatch.setattr(sys, "argv", ["weasley-assistant", *argv])

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 2
    assert calls == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err
    assert "Usage:" in captured.err


def test_skill_install_accepts_exactly_one_url(monkeypatch, capsys):
    calls = []
    _stub_installer(monkeypatch, calls.append)
    url = "https://example.test/SKILL.md"
    monkeypatch.setattr(sys, "argv", ["weasley-assistant", "skill", "install", url])

    main()

    assert calls == [url]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
