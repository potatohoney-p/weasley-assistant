"""Deprecated ``python -m`` compatibility entrypoint."""

from __future__ import annotations

import warnings

from weasley_assistant.__main__ import main

warnings.warn(
    "The legacy module entrypoint is deprecated; use 'weasley-assistant'.",
    DeprecationWarning,
    stacklevel=2,
)


if __name__ == "__main__":
    main()
