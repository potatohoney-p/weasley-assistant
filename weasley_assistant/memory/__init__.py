"""Memory facade — the three pillars behind one small interface.

    procedural  SKILL.md files      how to act
    semantic    facts table (FTS5)  what is durably true
    episodic    episodes table      what happened, when

Plus the two agents that manage them:
    retrieval_gate   decides IF a turn needs memory   (hero moment #1)
    consolidation    distills chats into facts, every N exchanges
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import anthropic

from weasley_assistant.config import Settings
from weasley_assistant.memory import consolidation, retrieval_gate
from weasley_assistant.memory.episodic.store import SqliteEpisodeStore
from weasley_assistant.memory.procedural.loader import SkillLoader
from weasley_assistant.memory.semantic.store import SqliteFactStore


def bundled_skill_dirs() -> list[Path]:
    """Where the skills that SHIP with WeasleyAssistant live — and why there are two answers.

    Contributors add skills to `skills/` at the repo root: that is what
    CONTRIBUTING.md documents, what CI validates, and what a checkout has. But
    the wheel only packages the `assistant/` directory, so a `pip install weasley-assistant`
    would have found nothing there and silently started with zero skills —
    procedural memory, one of the four pillars, quietly missing. (It did, until
    2026-07-31.) pyproject force-includes the same folder into the wheel at
    `assistant/skills`, so an installed WeasleyAssistant finds it next to the code.

    Exactly one of these exists at a time — the package copy only in a built
    wheel, the repo copy only in a checkout — so returning both is not a
    double-load, it is "wherever you installed from, the skills came too".
    """
    here = Path(__file__).resolve()
    return [p for p in (here.parents[1] / "skills", here.parents[2] / "skills") if p.is_dir()]


class Memory:
    def __init__(self, conn: sqlite3.Connection, settings: Settings, client: anthropic.Anthropic,
                 episode_store=None):
        # episode_store: inject an already-built store (the dashboard caches ONE
        # NotionEpisodeStore process-wide — its constructor hits the network,
        # so building one per Memory would re-query Notion on every poll).
        self.conn = conn
        self.settings = settings
        self.client = client
        self.facts = self._make_fact_store(conn, settings)
        self.episodes = episode_store if episode_store is not None else self._make_episode_store(conn, settings)
        self.skills = SkillLoader([*bundled_skill_dirs(), settings.home / "skills"])

    @staticmethod
    def _make_fact_store(conn, settings):
        if settings.semantic_store == "supabase":
            from weasley_assistant.memory.semantic.supabase_store import SupabaseFactStore

            return SupabaseFactStore(settings)
        return SqliteFactStore(conn)

    @staticmethod
    def _make_episode_store(conn, settings):
        if settings.episodic_store == "notion":
            from weasley_assistant.memory.episodic.notion_store import NotionEpisodeStore

            return NotionEpisodeStore()
        return SqliteEpisodeStore(conn)

    # ---- retrieval (gated — see retrieval_gate.py for why)
    def gated_retrieve(self, message: str, notify=None) -> str:
        retrieve, query, reason = retrieval_gate.should_retrieve(
            self.client, self.settings.small_model, message
        )
        if notify:
            notify("gate", {"decision": "retrieve" if retrieve else "skip", "reason": reason})
        if not retrieve:
            return ""
        found = self.facts.search(query, self.settings.retrieval_top_k)
        found += self.episodes.search(query, top_k=3)
        return "\n".join(found)

    # ---- procedural
    def matching_skills(self, message: str) -> str:
        matched = self.skills.match(message)
        return "\n\n".join(f"### {s.name}\n{s.body}" for s in matched)

    # ---- write paths
    def log_chat(self, user_message: str, reply: str, session_id: str = "default",
                 source: str = "cli", meta: dict | None = None) -> None:
        import json as _json
        # Serialize before writing either row. A malformed metadata value must
        # not leave a user-only turn for the next successful call to commit.
        encoded_meta = _json.dumps(meta) if meta else None
        # A savepoint keeps the pair atomic even on autocommit connections and
        # preserves unrelated pending writes on ordinary statement failures.
        self.conn.execute("SAVEPOINT log_chat_exchange")
        try:
            self.conn.execute(
                "INSERT INTO chat_log (role, content, session_id, source) VALUES ('user', ?, ?, ?)",
                (user_message, session_id, source),
            )
            # Telemetry rides on the assistant row for reopened turn cards.
            self.conn.execute(
                "INSERT INTO chat_log (role, content, session_id, source, meta) "
                "VALUES ('assistant', ?, ?, ?, ?)",
                (reply, session_id, source, encoded_meta),
            )
        except Exception:
            # SQLite may already have rolled back the whole transaction (for
            # example, a RAISE(ROLLBACK) trigger). Preserve that original error.
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK TO SAVEPOINT log_chat_exchange")
                self.conn.execute("RELEASE SAVEPOINT log_chat_exchange")
            raise
        self.conn.execute("RELEASE SAVEPOINT log_chat_exchange")
        self.conn.commit()

    # ---- sessions (for the dashboard's chat history + "New chat")
    def session_history(self, session_id: str) -> list[tuple[str, str]]:
        """The (user, assistant) exchanges of one past session, in order — used
        to reload working memory when the user switches back to a conversation."""
        rows = self.conn.execute(
            "SELECT role, content FROM chat_log WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        pairs, pending = [], None
        for r in rows:
            if r["role"] == "user":
                pending = r["content"]
            elif pending is not None:
                pairs.append((pending, r["content"]))
                pending = None
        return pairs

    def list_sessions(self) -> list[dict]:
        """One row per conversation: id, first user message (the title), message
        count, and when it started — newest first."""
        rows = self.conn.execute(
            """SELECT session_id,
                      COUNT(*) AS messages,
                      MIN(created_at) AS started_at,
                      MAX(created_at) AS last_at
               FROM chat_log GROUP BY session_id ORDER BY last_at DESC"""
        ).fetchall()
        out = []
        for r in rows:
            first = self.conn.execute(
                "SELECT content FROM chat_log WHERE session_id = ? AND role = 'user' ORDER BY id LIMIT 1",
                (r["session_id"],),
            ).fetchone()
            out.append({
                "id": r["session_id"],
                "title": (first["content"][:60] if first else "(empty)"),
                "messages": r["messages"],
                "started_at": r["started_at"],
                "last_at": r["last_at"],
            })
        return out

    def export_markdown(self) -> None:
        """Mirror memory to a human-readable MEMORY.md next to state.db — so the
        whiteboard's `~/~/.weasley/assistant/MEMORY.md` box is literally real, and "your memory
        is a file you can open" is true. state.db stays the queryable source of
        truth; this file is a generated view, refreshed after each turn."""
        # One read snapshot spans both tables, including in WAL mode where a
        # writer can commit between SELECTs. A savepoint nests inside a caller's
        # transaction without committing/rolling it back. Release before file I/O.
        self.conn.execute("SAVEPOINT memory_mirror")
        try:
            facts = self.conn.execute(
                "SELECT subject, content FROM facts ORDER BY subject, id"
            ).fetchall()
            eps = self.conn.execute(
                "SELECT happened_at, summary FROM episodes ORDER BY happened_at DESC, id DESC"
            ).fetchall()
        finally:
            self.conn.execute("RELEASE memory_mirror")
        lines = [
            "# Weasley Assistant memory",
            "",
            ("_A human-readable mirror of what Weasley Assistant remembers. The source of truth is "
            "`state.db` (the `facts` and `episodes` tables, keyword-searchable via FTS5); "
            "this file is regenerated after every turn._"),
            "",
            f"## Facts — semantic memory ({len(facts)})",
            "",
        ]
        lines += [f"- **{f['subject']}** — {f['content']}" for f in facts] or ["_none yet_"]
        lines += ["", f"## Episodes — episodic memory ({len(eps)})", ""]
        lines += [f"- **{e['happened_at']}** — {e['summary']}" for e in eps] or ["_none yet_"]
        # Build/encode before opening a file. Publish only after the stage has
        # closed successfully, so write/close errors leave the previous view.
        import os
        import stat
        import tempfile

        payload = ("\n".join(lines) + "\n").encode("utf-8")
        target = self.settings.home / "MEMORY.md"
        if target.is_symlink():
            raise OSError("MEMORY.md must be a regular generated file, not a symlink")
        try:
            mode = target.stat().st_mode
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(mode):
                raise OSError("MEMORY.md must be a regular generated file")
        staged = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=target.parent, prefix=".MEMORY.", suffix=".tmp", delete=False
            ) as stream:
                staged = Path(stream.name)
                stream.write(payload)
            os.replace(staged, target)
        except BaseException:
            if staged is not None:
                try:
                    staged.unlink(missing_ok=True)
                except OSError:
                    pass  # Best effort; do not hide the publication failure.
            raise

    def maybe_consolidate(self, notify=None) -> None:
        new_facts = consolidation.consolidate_if_due(
            self.conn,
            self.client,
            self.settings.small_model,
            self.settings.consolidate_every,
            self.facts,
            self.episodes,
        )
        if new_facts and notify:
            notify("consolidation", {"new_facts": new_facts})

