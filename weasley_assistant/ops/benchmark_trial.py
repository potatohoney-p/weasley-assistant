"""Own the throwaway home and core resources of one benchmark trial."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from weasley_assistant.config import Settings


@contextmanager
def benchmark_trial(settings: Settings, *, prefix: str):
    """Assign a fresh home, then close resources before removing it.

    The caller owns the Settings object; as with get_client, resolved fields
    are filled in place. Read ledgers and build result payloads inside the
    context. Published reports/history live outside this disposable home.
    """
    from weasley_assistant import app as app_mod

    resources = ExitStack()
    try:
        settings.home = Path(resources.enter_context(TemporaryDirectory(prefix=prefix)))
        settings.ensure_home()
        # Own these before constructing the assistant so constructor failures
        # (including a missing provider key) still close an allocated database.
        conn = app_mod.connect(settings.home)
        resources.callback(conn.close)
        client = app_mod.get_client(settings)
        resources.callback(client.close)
        app = app_mod.WeasleyAssistant(settings=settings, conn=conn, client=client)
        resources.callback(app.close)
        yield app
    except BaseException as error:
        # ExitStack attempts every cleanup, even when an earlier close fails.
        # A cleanup failure must not replace the trial's original exception.
        try:
            resources.close()
        except BaseException as cleanup_error:
            error.add_note(f"Benchmark cleanup also failed: {type(cleanup_error).__name__}")
        raise
    else:
        # A cleanup error on an otherwise successful trial remains visible.
        resources.close()
