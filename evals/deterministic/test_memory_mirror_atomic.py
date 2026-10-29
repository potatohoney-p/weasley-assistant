"""The generated memory mirror is published only as a complete file."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from weasley_assistant.config import Settings
from weasley_assistant.db import connect
from weasley_assistant.memory import Memory


@pytest.fixture
def memory(tmp_path):
    conn = connect(tmp_path)
    obj = Memory(conn, Settings(home=tmp_path), client=None)
    obj.facts.add("Wade", "서울에서 연구합니다", source="test")
    obj.episodes.add("Reviewed memory", happened_at="2026-10-28")
    yield obj
    conn.close()


def leftovers(memory):
    return list(memory.settings.home.glob(".MEMORY.*.tmp"))


def test_complete_unicode_mirror_and_repeat(memory):
    memory.export_markdown()
    target = memory.settings.home / "MEMORY.md"
    first = target.read_bytes()
    assert "서울에서 연구합니다" in first.decode()
    assert "Reviewed memory" in first.decode()
    memory.export_markdown()
    assert target.read_bytes() == first
    assert not leftovers(memory)


def test_old_mirror_visible_until_closed_stage_is_replaced(memory, monkeypatch):
    target = memory.settings.home / "MEMORY.md"
    target.write_text("previous complete mirror")
    replace = os.replace
    seen = []

    def inspect(source, destination):
        assert Path(destination) == target
        assert Path(source).parent == target.parent
        assert target.read_text() == "previous complete mirror"
        data = Path(source).read_text()
        assert "서울에서 연구합니다" in data
        assert data.endswith("\n")
        seen.append(data)
        replace(source, destination)

    monkeypatch.setattr(os, "replace", inspect)
    memory.export_markdown()
    assert seen == [target.read_text()]
    assert not leftovers(memory)


@pytest.mark.parametrize("exists", [False, True])
def test_replace_failure_preserves_previous_and_cleans_stage(memory, monkeypatch, exists):
    target = memory.settings.home / "MEMORY.md"
    if exists:
        target.write_bytes(b"previous")
    failure = OSError("replace unavailable")

    def fail(*args):
        raise failure

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError) as caught:
        memory.export_markdown()
    assert caught.value is failure
    assert target.read_bytes() == b"previous" if exists else not target.exists()
    assert not leftovers(memory)


@pytest.mark.parametrize("phase", ["write", "close"])
def test_stage_failure_preserves_previous_and_cleans_stage(memory, monkeypatch, phase):
    import tempfile
    target = memory.settings.home / "MEMORY.md"
    target.write_bytes(b"previous")
    factory = tempfile.NamedTemporaryFile
    failure = OSError("simulated storage failure")

    class FailingStage:
        def __init__(self, *args, **kwargs):
            self.file = factory(*args, **kwargs)
            self.name = self.file.name

        def __enter__(self):
            return self

        def write(self, data):
            if phase == "write":
                self.file.write(data[:10])
                raise failure
            return self.file.write(data)

        def __exit__(self, *args):
            self.file.close()
            if phase == "close":
                raise failure

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", FailingStage)
    with pytest.raises(OSError) as caught:
        memory.export_markdown()
    assert caught.value is failure
    assert target.read_bytes() == b"previous"
    assert not leftovers(memory)


def test_query_error_does_not_touch_mirror(memory):
    target = memory.settings.home / "MEMORY.md"
    target.write_bytes(b"previous")
    memory.conn.set_authorizer(lambda *args: 1)
    with pytest.raises(Exception, match="not authorized"):
        memory.export_markdown()
    memory.conn.set_authorizer(None)
    assert target.read_bytes() == b"previous"
    assert not leftovers(memory)


def test_encoding_error_does_not_truncate_mirror(memory):
    target = memory.settings.home / "MEMORY.md"
    target.write_bytes(b"previous")
    real = memory.conn

    class InvalidRows:
        def execute(self, query):
            if "FROM facts" in query:
                return SimpleNamespace(fetchall=lambda: [{"subject": "bad", "content": "\ud800"}])
            return real.execute(query)

    memory.conn = InvalidRows()
    with pytest.raises(UnicodeEncodeError):
        memory.export_markdown()
    memory.conn = real
    assert target.read_bytes() == b"previous"
    assert not leftovers(memory)


def test_directory_target_remains_intact(memory):
    target = memory.settings.home / "MEMORY.md"
    target.mkdir()
    (target / "keep").write_text("untouched")
    with pytest.raises(OSError):
        memory.export_markdown()
    assert (target / "keep").read_text() == "untouched"
    assert not leftovers(memory)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux file size limit regression")
def test_real_file_size_failure_keeps_previous_mirror(memory):
    target = memory.settings.home / "MEMORY.md"
    target.write_bytes(b"previous complete mirror")
    code = """
import resource, signal, sys
from pathlib import Path
from weasley_assistant.config import Settings
from weasley_assistant.db import connect
from weasley_assistant.memory import Memory
home = Path(sys.argv[1])
conn = connect(home)
memory = Memory(conn, Settings(home=home), client=None)
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
resource.setrlimit(resource.RLIMIT_FSIZE, (100, 100))
try:
    memory.export_markdown()
except OSError:
    pass
else:
    raise AssertionError('expected actual filesystem limit failure')
assert (home / 'MEMORY.md').read_bytes() == b'previous complete mirror'
assert not list(home.glob('.MEMORY.*.tmp'))
"""
    result = subprocess.run([sys.executable, "-c", code, str(memory.settings.home)],
                            capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr


def test_symlink_target_is_not_replaced_or_followed(memory):
    target = memory.settings.home / "MEMORY.md"
    other = memory.settings.home / "other.txt"
    other.write_bytes(b"unrelated")
    try:
        target.symlink_to(other)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(OSError, match="symlink"):
        memory.export_markdown()
    assert target.is_symlink()
    assert other.read_bytes() == b"unrelated"
    assert not leftovers(memory)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO unavailable")
def test_fifo_target_is_not_replaced(memory):
    import stat
    target = memory.settings.home / "MEMORY.md"
    os.mkfifo(target)
    with pytest.raises(OSError, match="regular"):
        memory.export_markdown()
    assert stat.S_ISFIFO(target.stat().st_mode)
    assert not leftovers(memory)
