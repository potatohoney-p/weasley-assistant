"""Weasley Assistant — transparent, local-first agent infrastructure."""

from __future__ import annotations

__version__ = "0.1.1"
__all__ = ["Settings", "WeasleyAssistant", "__version__", "load_settings"]


def __getattr__(name: str):
    """Load the public API lazily so submodule imports stay cycle-free."""
    if name == "WeasleyAssistant":
        from weasley_assistant.app import WeasleyAssistant

        return WeasleyAssistant
    if name in {"Settings", "load_settings"}:
        from weasley_assistant.config import Settings, load_settings

        return {"Settings": Settings, "load_settings": load_settings}[name]
    raise AttributeError(name)
