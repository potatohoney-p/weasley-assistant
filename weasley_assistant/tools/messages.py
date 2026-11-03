"""send_message — drafts a message into a local outbox.

Local-first: nothing is actually sent. Each message becomes a file in
~/.weasley/assistant/outbox/ that you can read, edit, and send yourself. Wiring a real
channel (email, Telegram, Slack) is a great community contribution.
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from pathlib import Path

from weasley_assistant.tools.registry import Tool


def make_tool(home: Path) -> Tool:
    def send_message(to: str, body: str) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        safe_to = "".join(c if c.isalnum() else "-" for c in to)[:40]
        # Claim the filename atomically: timestamps and sanitized recipients can
        # collide, including across independent gateway instances.
        for index in count():
            suffix = f"-{index}" if index else ""
            path = home / "outbox" / f"{stamp}-{safe_to}{suffix}.txt"
            try:
                draft = path.open("x", encoding="utf-8")
            except FileExistsError:
                continue
            with draft:
                draft.write(f"To: {to}\n\n{body}\n")
            break
        return f"Message to {to} placed in outbox ({path.name}). Nothing was sent — review it there."

    return Tool(
        name="send_message",
        description=(
            "Draft a message to someone and place it in the local outbox for the user to "
            "review and send. Use when the user asks you to message, tell, or remind someone."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient name or address"},
                "body": {"type": "string", "description": "The message text"},
            },
            "required": ["to", "body"],
        },
        fn=send_message,
    )
