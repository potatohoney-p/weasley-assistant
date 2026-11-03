# Atomic local memory consolidation

The built-in SQLite fact and episode stores now write a consolidation batch and
its chat-log completion flags in one transaction when all three use the same
connection. A storage/FTS-trigger/completion failure rolls back that batch and
leaves the chat rows pending for retry. The model request finishes before the
writer lock is acquired. Only the originally selected chat IDs are marked.

A caller-owned pending transaction is left untouched and consolidation returns
zero, allowing the caller to commit/roll back first. The built-in stores' new
keyword-only `commit=False` option participates in that transaction; ordinary
`add()` calls retain their existing auto-commit behavior.

This guarantee does not extend to remote, custom, or mixed-connection adapters.
Their existing interfaces and error behavior are unchanged. It does not claim
cross-process exactly-once consolidation: two simultaneous summarizers can still
select the same pending rows. A zero return remains ambiguous between not due,
empty output, invalid output, or failed local persistence.

Deterministic tests use real SQLite rejection triggers at the second fact,
episode, and completion update, check both tables and FTS, then retry without
duplicating the first fact. A deferred foreign-key constraint additionally tests
commit-phase rollback and retry. No live model/API or private user data is used.
