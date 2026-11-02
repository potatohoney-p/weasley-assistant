"""A procedural-memory refresh must not certify stale or partial instructions."""

import os

import pytest

from weasley_assistant.memory.procedural import loader as module


def write_skill(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: meeting-plan\ndescription: meeting planning\n---\n{body}")
    # Explicitly advance mtime: these are deterministic cache invalidation tests.
    stamp = path.stat().st_mtime_ns + 2_000_000_000
    os.utime(path, ns=(stamp, stamp))


def test_edit_during_read_is_reloaded_on_next_match(tmp_path, monkeypatch):
    path = tmp_path / "a" / "SKILL.md"
    write_skill(path, "old")
    loader = module.SkillLoader([tmp_path])
    original_parse = module._parse
    edited = False

    def parse_then_edit(p):
        nonlocal edited
        result = original_parse(p)
        if not edited:
            edited = True
            write_skill(p, "new")
        return result

    monkeypatch.setattr(module, "_parse", parse_then_edit)
    loader.refresh()
    assert loader.match("meeting planning")[0].body == "new"


@pytest.mark.parametrize("error", [OSError("read failed"), UnicodeDecodeError(
    "utf-8", b"\xff", 0, 1, "invalid"), KeyboardInterrupt()])
def test_failed_refresh_preserves_previous_snapshot(tmp_path, monkeypatch, error):
    a, b = tmp_path / "a" / "SKILL.md", tmp_path / "b" / "SKILL.md"
    write_skill(a, "old-a")
    write_skill(b, "old-b")
    loader = module.SkillLoader([tmp_path])
    old_skills, old_sig = loader.skills, loader._sig
    write_skill(a, "new-a")
    original_parse = module._parse

    def fail_second(p):
        if p == b:
            raise error
        return original_parse(p)

    monkeypatch.setattr(module, "_parse", fail_second)
    with pytest.raises(type(error)) as caught:
        loader.refresh()
    assert caught.value is error
    assert loader.skills is old_skills
    assert [s.body for s in loader.skills] == ["old-a", "old-b"]
    assert loader._sig == old_sig
    monkeypatch.setattr(module, "_parse", original_parse)
    assert [s.body for s in loader.match("meeting planning")] == ["new-a", "old-b"]


def test_added_file_during_read_is_not_certified_without_loading(tmp_path, monkeypatch):
    a, b = tmp_path / "a" / "SKILL.md", tmp_path / "b" / "SKILL.md"
    write_skill(a, "a")
    loader = module.SkillLoader([tmp_path])
    original_parse = module._parse

    def add_after_read(p):
        result = original_parse(p)
        if not b.exists():
            write_skill(b, "b")
        return result

    monkeypatch.setattr(module, "_parse", add_after_read)
    loader.refresh()
    assert [s.body for s in loader.match("meeting planning")] == ["a", "b"]


def test_deletion_and_invalid_skill_remain_supported(tmp_path):
    path = tmp_path / "SKILL.md"
    write_skill(path, "old")
    loader = module.SkillLoader([tmp_path])
    path.write_text("not a skill")
    loader.refresh()
    assert loader.match("meeting planning") == []
    write_skill(path, "new")
    assert loader.match("meeting planning")[0].body == "new"
    path.unlink()
    assert loader.match("meeting planning") == []
