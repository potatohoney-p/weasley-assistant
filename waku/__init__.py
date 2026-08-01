"""Deprecated import shim for installations created before the package rename."""

from __future__ import annotations

import os
import warnings

import weasley_assistant as _implementation

warnings.warn(
    "The legacy package name is deprecated; import 'weasley_assistant' instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Preserve old environment-based launches when callers intentionally enter
# through this compatibility package. Public names always win.
for _name, _value in tuple(os.environ.items()):
    if _name.startswith("WAKU_"):
        os.environ.setdefault(f"WEASLEY_ASSISTANT_{_name[5:]}", _value)

# Let ``import waku.config`` find the implementation without keeping a second
# source tree. New code must import from ``weasley_assistant`` directly.
__path__ = [*__path__, *list(_implementation.__path__)]

__version__ = _implementation.__version__
WeasleyAssistant = _implementation.WeasleyAssistant
Waku = WeasleyAssistant
Settings = _implementation.Settings
load_settings = _implementation.load_settings

__all__ = ["Settings", "Waku", "WeasleyAssistant", "__version__", "load_settings"]
