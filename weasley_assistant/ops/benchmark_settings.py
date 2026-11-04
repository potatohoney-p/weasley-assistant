"""Local-data defaults shared by the CLI shootout and dashboard Arena."""

from __future__ import annotations

from pathlib import Path

from weasley_assistant.config import Settings


def benchmark_settings(provider: str, model: str, home: Path, *,
                       apple: bool = False, coding: bool = False) -> Settings:
    """Keep ambient personal integrations out of a fresh benchmark home.

    Provider credentials/endpoints, loop limits and graph configuration still
    use normal Settings defaults. This is configuration isolation, not an OS
    sandbox: model calls and search remain live, and explicit Apple/coding
    opt-ins can reach the real calendar or run pi. Never mutate os.environ;
    Arena contestants share a process with the user's ordinary chat agent.
    """
    return Settings(
        provider=provider,
        model=model,
        small_model="",
        home=home,
        semantic_store="sqlite",
        episodic_store="sqlite",
        apple_calendar=apple,
        google_calendar=False,
        apple_tools=False,
        gh_tool=False,
        experimental=coding,
        otel_endpoint="",
    )
