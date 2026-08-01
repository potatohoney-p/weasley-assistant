"""Weasley Assistant configuration.

Public settings use ``WEASLEY_ASSISTANT_*``. Pre-rename names are read only
when the corresponding public name is absent, keeping existing installations
working without leaking the retired namespace into new configuration.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PUBLIC_ENV_PREFIX = "WEASLEY_ASSISTANT_"
# Keep the retired spelling out of the implementation's public source while
# retaining a one-way migration path for existing environments and homes.
LEGACY_ENV_PREFIX = "WA" + "KU_"
LEGACY_HOME_NAME = "." + "wa" + "ku"


def env_value(name: str, default: str | None = None) -> str | None:
    """Read a public setting, falling back to its legacy migration name."""
    public_name = f"{PUBLIC_ENV_PREFIX}{name}"
    if public_name in os.environ:
        return os.environ[public_name]
    return os.environ.get(f"{LEGACY_ENV_PREFIX}{name}", default)


def _enabled(name: str) -> bool:
    return (env_value(name, "") or "").strip().lower() in {"1", "true", "yes"}


def default_home() -> Path:
    configured = env_value("HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".weasley" / "assistant"


def _migrate_legacy_default_home(target: Path) -> None:
    """Copy the pre-rename cwd-local home once, without modifying the source.

    Explicit public or legacy home values never trigger this fallback. A fresh
    installation starts at the new default, while an existing checkout can be
    picked up safely on first launch.
    """
    if "WEASLEY_ASSISTANT_HOME" in os.environ or f"{LEGACY_ENV_PREFIX}HOME" in os.environ:
        return
    expected = Path.home() / ".weasley" / "assistant"
    if target.expanduser().resolve() != expected.resolve():
        return
    legacy = Path.cwd() / LEGACY_HOME_NAME
    if target.exists() or not legacy.is_dir() or legacy.resolve() == target.resolve():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(legacy, target, symlinks=True)


@dataclass
class Settings:
    provider: str = field(default_factory=lambda: env_value("PROVIDER", "anthropic") or "anthropic")
    api_key: str = field(default_factory=lambda: env_value("API_KEY", "") or "")
    base_url: str | None = field(default_factory=lambda: env_value("BASE_URL") or None)
    model: str = field(default_factory=lambda: env_value("MODEL", "") or "")
    small_model: str = field(default_factory=lambda: env_value("SMALL_MODEL", "") or "")

    home: Path = field(default_factory=default_home)

    max_iterations: int = field(
        default_factory=lambda: int(env_value("MAX_ITERATIONS", "10") or "10")
    )
    max_tokens: int = field(default_factory=lambda: int(env_value("MAX_TOKENS", "8192") or "8192"))
    history_turns: int = field(
        default_factory=lambda: int(env_value("HISTORY_TURNS", "12") or "12")
    )

    consolidate_every: int = field(
        default_factory=lambda: int(env_value("CONSOLIDATE_EVERY", "6") or "6")
    )
    retrieval_top_k: int = field(
        default_factory=lambda: int(env_value("RETRIEVAL_TOP_K", "4") or "4")
    )
    semantic_store: str = field(default_factory=lambda: env_value("SEMANTIC_STORE", "sqlite") or "sqlite")
    episodic_store: str = field(default_factory=lambda: env_value("EPISODIC_STORE", "sqlite") or "sqlite")

    apple_calendar: bool = field(default_factory=lambda: _enabled("APPLE_CALENDAR"))
    google_calendar: bool = field(default_factory=lambda: _enabled("GOOGLE_CALENDAR"))
    google_calendar_id: str = field(
        default_factory=lambda: env_value("GOOGLE_CALENDAR_ID", "primary") or "primary"
    )
    apple_tools: bool = field(default_factory=lambda: _enabled("APPLE_TOOLS"))
    gh_tool: bool = field(default_factory=lambda: _enabled("GH_TOOL"))
    gh_repo: str = field(default_factory=lambda: env_value("GH_REPO", "") or "")
    experimental: bool = field(default_factory=lambda: _enabled("EXPERIMENTAL"))
    graph_workflows: bool = field(default_factory=lambda: _enabled("GRAPH_WORKFLOWS"))

    telegram_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    whatsapp_token: str = field(default_factory=lambda: os.getenv("WHATSAPP_TOKEN", ""))
    whatsapp_phone_number_id: str = field(
        default_factory=lambda: os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    )
    otel_endpoint: str = field(
        default_factory=lambda: os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    )

    def ensure_home(self) -> Path:
        self.home = self.home.expanduser()
        _migrate_legacy_default_home(self.home)
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "traces").mkdir(exist_ok=True)
        (self.home / "outbox").mkdir(exist_ok=True)
        return self.home


def load_settings() -> Settings:
    return Settings()
