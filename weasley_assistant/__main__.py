"""Entrypoints for ``weasley-assistant`` and ``python -m weasley_assistant``."""

from __future__ import annotations

import sys

from weasley_assistant import __version__

HELP_FLAGS = {"-h", "--help", "help"}

COMMAND_DESCRIPTIONS = {
    "dashboard": "Start the local dashboard.",
    "voice": "Start the voice gateway.",
    "telegram": "Start the Telegram gateway.",
    "discord": "Start the Discord gateway.",
    "whatsapp": "Start the WhatsApp gateway.",
    "brief": "Generate an operations brief.",
    "gather": "Run the gather workflow.",
}

HELP = """Weasley Assistant — transparent, local-first AI assistant

Usage:
  weasley-assistant [command]
  python -m weasley_assistant [command]

Commands:
  dashboard             Start the local dashboard
  voice                 Start the voice gateway
  telegram              Start the Telegram gateway
  discord               Start the Discord gateway
  whatsapp              Start the WhatsApp gateway
  brief                 Generate an operations brief
  gather                Run the gather workflow
  skill install <url>   Install a procedural skill

With no command, the terminal chat starts.

Options:
  -h, --help            Show this help message
  -V, --version         Show the installed version
"""

SKILL_HELP = """Manage procedural skills.

Usage:
  weasley-assistant skill install <url>
  python -m weasley_assistant skill install <url>

Commands:
  install <url>         Download and install one procedural skill

Options:
  -h, --help            Show this help message
"""

SKILL_INSTALL_HELP = """Download and install one procedural skill.

Usage:
  weasley-assistant skill install <url>
  python -m weasley_assistant skill install <url>

Arguments:
  <url>                 GitHub, Gist, or raw SKILL.md URL

Options:
  -h, --help            Show this help message
"""


def _command_help(command: str) -> str:
    return (
        f"{COMMAND_DESCRIPTIONS[command]}\n\n"
        "Usage:\n"
        f"  weasley-assistant {command}\n"
        f"  python -m weasley_assistant {command}\n\n"
        "Options:\n"
        "  -h, --help            Show this help message\n"
    )


def _usage_error(message: str, usage: str) -> None:
    print(message, file=sys.stderr)
    print(usage, file=sys.stderr)
    raise SystemExit(2)


def _run_skill_command(args: list[str]) -> None:
    if args and args[0] in HELP_FLAGS:
        print(SKILL_HELP)
        return
    if not args:
        _usage_error("Missing skill command.", SKILL_HELP)
    if args[0] != "install":
        _usage_error(f"Unknown skill command: {args[0]}", SKILL_HELP)

    install_args = args[1:]
    if install_args and install_args[0] in HELP_FLAGS:
        print(SKILL_INSTALL_HELP)
        return
    if len(install_args) != 1:
        _usage_error("skill install requires exactly one <url>.", SKILL_INSTALL_HELP)

    from weasley_assistant.memory.procedural.installer import install

    install(install_args[0])


def main() -> None:
    args = sys.argv[1:]
    if not args:
        from weasley_assistant.gateway.cli import main as cli_main

        cli_main()
    elif args[0] in HELP_FLAGS:
        print(HELP)
    elif args[0] in {"-V", "--version", "version"}:
        print(f"weasley-assistant {__version__}")
    elif (
        args[0] in COMMAND_DESCRIPTIONS
        and len(args) >= 2
        and args[1] in HELP_FLAGS
    ):
        print(_command_help(args[0]))
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
    elif args[0] == "skill":
        _run_skill_command(args[1:])
    else:
        print(f"Unknown command: {args[0]}", file=sys.stderr)
        print(HELP, file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
