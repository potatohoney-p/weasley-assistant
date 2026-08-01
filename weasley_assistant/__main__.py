"""Entrypoints for ``weasley-assistant`` and ``python -m weasley_assistant``.

Commands: dashboard, voice, telegram, discord, whatsapp, brief, gather, and
``skill install <url>``. With no command, the terminal chat starts.
"""

from __future__ import annotations

import sys


def main() -> None:
    args = sys.argv[1:]
    if not args:
        from weasley_assistant.gateway.cli import main as cli_main

        cli_main()
    elif args[0] == "dashboard":
        from weasley_assistant.ops.dashboard import main as dash_main

        dash_main()
    elif args[0] == "voice":
        from weasley_assistant.gateway.voice import main as voice_main

        voice_main()
    elif args[0] == "telegram":
        from weasley_assistant.gateway.telegram import main as tg_main

        tg_main()
    elif args[0] == "discord":
        from weasley_assistant.gateway.discord import main as discord_main

        discord_main()
    elif args[0] == "whatsapp":
        from weasley_assistant.gateway.whatsapp import main as wa_main

        wa_main()
    elif args[0] == "brief":
        from weasley_assistant.ops.brief import main as brief_main

        brief_main()
    elif args[0] == "gather":
        from weasley_assistant.ops.gather import main as gather_main

        gather_main()
    elif args[0] == "skill" and len(args) >= 3 and args[1] == "install":
        from weasley_assistant.memory.procedural.installer import install

        install(args[2])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
