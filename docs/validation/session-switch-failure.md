# Failure-safe session switching

`Session.switch` prepares the requested transcript before publishing its identity
and working history. Previously a read failure activated the requested session
with empty history; a failed switch could redirect a subsequent successful turn
into that session. This boundary is separate from `add_exchange` persistence
ordering and durable chat transaction atomicity.

Acceptance: transcript retrieval, lazy iteration or pair-unpacking failures keep
the previous session id and original history list intact, propagate the original
exception, and leave later turns associated with the original conversation.
Successful empty, memory-free and recent-tail switches retain existing behavior.

Ten deterministic regression cases: unchanged main has 7 failures/3 passes;
fixed source has 10 passes. A real SQLite read denial verifies the failed switch,
next synthetic-model response/prompt and durable session rows, then successful
switch retry. No external provider calls or user data are used.

Full deterministic release gate: 433 passed, 13 existing skips; judge tier skipped
without credentials. Ruff and all three skill validations passed. Separate local
composition with Drafts #5, #10 and #15 retains #5's nonpositive-window no-read
rule by adapting that guard around preparation: 466 passed, 13 existing skips.
This is compatibility testing,
not a merge or deployment.

Limits: no concurrent switch/respond serialization, cross-thread atomicity,
backend rollback, streaming transcript limits or new session-id validation.
The existing main-branch zero/negative history-window semantics are not changed
here; #5 addresses them independently. A backend may have its own side effects
before raising, which this in-memory ordering cannot undo.
