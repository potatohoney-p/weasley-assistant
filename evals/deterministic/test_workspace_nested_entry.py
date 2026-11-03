"""The script selected recursively must be the script that autorun executes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from weasley_assistant.tools import experimental
from weasley_assistant.tools import workspace as ws


@pytest.fixture(autouse=True)
def _enable_autorun(monkeypatch):
    monkeypatch.setenv("WEASLEY_ASSISTANT_DELEGATE_AUTORUN", "1")


@pytest.mark.parametrize("relative", [
    "src/main.py",
    "src/deep/app.py",
    "project space/entry script.py",
    "소스/main.py",
    "-scripts/main.py",
    "standalone.py",
])
def test_runs_selected_path_and_records_it(tmp_path, relative):
    entry = tmp_path / relative
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(
        "from pathlib import Path\n"
        "print('selected script')\n"
        "print(Path('input.txt').read_text())\n"
        "Path('executed.txt').write_text('workspace root')\n",
        encoding="utf-8",
    )
    (tmp_path / "input.txt").write_text("root data", encoding="utf-8")

    run = ws.autorun(tmp_path)
    expected = str(Path(relative))
    assert run[0] == expected
    assert run[1] == 0, run[2]
    assert run[2] == "selected script\nroot data"
    assert (tmp_path / "executed.txt").read_text() == "workspace root"
    assert f"{Path(sys.executable).name} {expected}\n" in (
        tmp_path / "run.log"
    ).read_text(encoding="utf-8")
    ws.write_manifest(tmp_path, "test", "test", "nested entry", ws.created_files(tmp_path), run)
    assert f"{Path(sys.executable).name} {expected}`" in (
        tmp_path / "MANIFEST.md"
    ).read_text(encoding="utf-8")


def test_selected_nested_file_does_not_run_same_named_root_file(tmp_path, monkeypatch):
    root = tmp_path / "main.py"
    root.write_text("raise RuntimeError('wrong root script')\n", encoding="utf-8")
    nested = tmp_path / "src" / "main.py"
    nested.parent.mkdir()
    nested.write_text("print('correct nested script')\n", encoding="utf-8")
    # Isolate dispatch from the unchanged entry-selection heuristic.
    monkeypatch.setattr(ws, "_pick_entry", lambda files: nested)

    entry, code, output, _ = ws.autorun(tmp_path)
    assert code == 0, output
    assert entry == str(Path("src/main.py"))
    assert output == "correct nested script"


def test_main_guard_selection_and_sibling_import(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "helper.py").write_text("ANSWER = 'sibling import works'\n", encoding="utf-8")
    (source / "launch.py").write_text(
        "from helper import ANSWER\n"
        "if __name__ == '__main__':\n"
        "    print(ANSWER)\n", encoding="utf-8",
    )
    entry, code, output, _ = ws.autorun(tmp_path)
    assert code == 0, output
    assert entry == str(Path("src/launch.py"))
    assert output == "sibling import works"


def test_relative_workspace_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = Path("relative workspace")
    (folder / "src").mkdir(parents=True)
    (folder / "src/main.py").write_text("print('relative folder works')\n", encoding="utf-8")
    entry, code, output, _ = ws.autorun(folder)
    assert code == 0, output
    assert entry == str(Path("src/main.py"))
    assert output == "relative folder works"


@pytest.mark.parametrize("failure", ["timeout", "launch"])
def test_failure_receipts_keep_the_selected_path(tmp_path, monkeypatch, failure):
    entry = tmp_path / "src/main.py"
    entry.parent.mkdir()
    entry.write_text("print('not launched')\n", encoding="utf-8")

    def fail(argv, **kwargs):
        assert Path(argv[1]) == entry.absolute()
        assert kwargs["cwd"] == tmp_path
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        raise OSError("launch failed")

    monkeypatch.setattr(ws.subprocess, "run", fail)
    run = ws.autorun(tmp_path)
    assert run[0] == str(Path("src/main.py"))
    assert run[1] == (None if failure == "timeout" else -1)
    assert str(Path("src/main.py")) in (tmp_path / "run.log").read_text(encoding="utf-8")


def test_nested_script_failure_keeps_its_output_and_exit_code(tmp_path):
    entry = tmp_path / "src/main.py"
    entry.parent.mkdir()
    entry.write_text(
        "import sys\nprint('before failure')\n"
        "print('failure detail', file=sys.stderr)\nsys.exit(7)\n", encoding="utf-8",
    )
    path, code, output, _ = ws.autorun(tmp_path)
    assert path == str(Path("src/main.py"))
    assert code == 7
    assert output == "before failure\nfailure detail"


def test_disabled_autorun_does_not_launch_nested_script(tmp_path, monkeypatch):
    entry = tmp_path / "src/main.py"
    entry.parent.mkdir()
    entry.write_text("raise RuntimeError('must not run')\n", encoding="utf-8")
    monkeypatch.setenv("WEASLEY_ASSISTANT_DELEGATE_AUTORUN", "0")
    monkeypatch.setattr(ws.subprocess, "run", lambda *a, **kw: pytest.fail("must not launch"))
    assert ws.autorun(tmp_path) is None
    assert not (tmp_path / "run.log").exists()


def test_delegate_result_reports_nested_execution(tmp_path, monkeypatch):
    from weasley_assistant.config import Settings

    fake = tmp_path / "fake_pi.py"
    fake.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "if '--help' in sys.argv:\n"
        "    print('legacy print mode'); sys.exit(0)\n"
        "Path('src').mkdir()\n"
        "Path('src/main.py').write_text(\"print('nested delegation ran')\\n\")\n"
        "print('Created src/main.py')\n", encoding="utf-8",
    )
    monkeypatch.setattr(experimental.shutil, "which", lambda name: str(fake))
    monkeypatch.setattr(experimental, "_PI_JSON_MODE", None)
    monkeypatch.setenv("WEASLEY_ASSISTANT_WORKSPACE", str(tmp_path / "workspace"))
    tool = experimental.make_delegate_tool(Settings(home=tmp_path / "home"))

    output = tool.fn(task="make a nested entry script")
    assert f"Auto-ran {Path('src/main.py')}: ran clean" in output
    assert "nested delegation ran" in output
    manifests = list((tmp_path / "workspace").rglob("MANIFEST.md"))
    assert len(manifests) == 1
    assert f"{Path(sys.executable).name} {Path('src/main.py')}`" in manifests[0].read_text()
