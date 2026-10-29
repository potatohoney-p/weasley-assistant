# Complete memory mirror publication

`Memory.export_markdown()` renders and encodes its generated view before creating a
same-directory temporary file. It closes that file before replacing `MEMORY.md`.
Readers of the path therefore see the prior complete view or the new complete view,
not a partially rewritten mirror. Database queries, encoding, staging, close or
replacement failures propagate; ordinary failure cleanup removes the stage on a
best-effort basis without masking the original error. No immediate retry is added.

The SQLite database remains authoritative. Both table reads now share one SQLite read
snapshot, using a nested read-only savepoint released before rendering/file I/O.
This does not change remote-store mirror contents, consolidation, or post-commit
maintenance policy. PR #17's optional-maintenance handling is a separate change.

## Compatibility and limits

- Same-directory `os.replace` is used; its atomic replacement contract depends on
  the filesystem. Native Windows/network filesystems have not been tested.
- Publication changes the file inode. Existing open handles can keep reading the
  old inode. Hard-linked aliases do not receive the new generated view.
- A symbolic-link or existing non-regular `MEMORY.md` is rejected rather than replacing the link or
  overwriting its target. This assumes a stable, trusted home directory; it is not
  protection against concurrent hostile filesystem changes.
- The replacement uses the private temporary file's permissions; old custom modes,
  ownership, ACLs and other inode metadata are not copied. No chmod is performed.
- There is no fsync or power-loss durability guarantee, multi-writer publication lock, cross-store
  snapshot, disk quota or bounded render-memory guarantee. A killed
  process or failed cleanup can leave a `.MEMORY.*.tmp` file. No automatic sweep
  deletes such files.
- A successful replacement followed by an external interruption is not rolled back.

## Deterministic verification

`evals/deterministic/test_memory_mirror_atomic.py` covers Unicode/repeat exports,
old-view visibility up to publication, absent/existing destination replacement
failure, staged short-write and close failures, query/encoding failures, directory
and symlink destinations, cleanup, and a Linux subprocess with a real file-size
limit. That subprocess ignores SIGXFSZ only within itself, then uses a 100-byte
RLIMIT_FSIZE; the old code truncates the previous mirror before the write fails.
The replacement preserves it. Tests use synthetic content in actual SQLite and
local files; injected stages model additional write/close failures. No provider
calls or credentials are required.

## Coherent SQLite view

`test_memory_mirror_snapshot.py` commits a second connection's paired fact/episode
update, deletion or insertion between the two table reads in WAL mode. The export
contains the old complete pair; a later export sees the new committed state.
Read failures release the export's savepoint without replacing the previous file.
A pre-existing caller transaction (including a savepoint with the same name) is
neither committed nor rolled back. Its own uncommitted data is visible and may
therefore appear in the mirror even if the caller later rolls back. Normal
post-commit export avoids that ambiguity. Connection/savepoint cleanup errors
still propagate; an unusable connection requires caller recovery.

This is snapshot coherence, not freshness at publication time, serialized writers,
a bound on database rows/memory, or a globally consistent view of remote stores.
Sharing a single SQLite connection concurrently is outside this contract.
