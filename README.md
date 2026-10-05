# Agent History Viewer (ClaudeViewer)

A local, **read-only** web viewer for coding-agent history. Browse Claude Code,
Codex, GitHub Copilot CLI, pi, and VS Code Chat in the existing three-panel UI.
No model API, agent CLI, cloud account, or frontend build is required.

## Quick start

Python **3.10+**:

```bash
pip install -r requirements.txt
python server.py
```

The server **automatically syncs local history before listening**, as the original
ClaudeViewer did. Open **http://localhost:8000** after the startup sync completes.
The console prints scan/import counts and the current file. The first import can
take a while for large archives; previously committed sessions are preserved.
Later reloads reuse unchanged projections; changed sessions are rebuilt transactionally.

**Reload** starts a background job and displays live phase, counts, current file
and elapsed time. Refreshing the page reconnects to that job instead of starting
another one. Already committed history remains browsable during a reload.

```bash
python server.py --port 8001  # choose another port if 8000 is occupied
python server.py --no-sync    # explicitly skip startup sync and browse the existing archive
python server.py --debug      # development mode, without the double-import reloader
```

The current archive is **`viewer.db` (schema v2)**. Your existing **`claude.db` is
preserved and is not automatically migrated. An older schema-v1 `viewer.db`
requires the explicit rebuild below; it is never silently overwritten.
Agent history files are never modified, and
all source deletion endpoints are disabled, including Claude deletion.
The UI reports import errors without discarding the last successful projection.
Active JSONL files may keep appending: the importer validates the exact byte prefix
it read, commits that snapshot, and picks up later appended records on the next
sync. Rewrites or truncation of that prefix are still rejected.

## Rebuilding an older viewer index

Stop the viewer, then run:

```bash
python rebuild.py
```

The command builds a separate v2 archive, checks SQLite/foreign keys/search-index
integrity and existing session-ID coverage, then switches the database. The old
viewer index is retained as `viewer.backup-<timestamp>.db` for rollback. Agent
files and the old Claude-only database are untouched. Failed builds keep the
original database and the candidate for inspection.

```bash
python rebuild.py --discard-backup  # delete the old viewer index only after successful validation/switch
```

If source files were deliberately removed, inspect missing sessions before using
`--allow-missing`. Rebuilding from current sources cannot recover deleted source
history; do not discard a backup that is your only copy.

## Storage and search

- Raw records and normalized messages use versioned, lossless JSON blobs with
  zlib compression; decoding is transparent to the API.
- Identical search text is stored/indexed once, with separate message/session
  associations. This is **storage deduplication**, not deletion/merging of
  repeated questions, turns, forks or sessions.
- FTS5 trigram uses `detail=none, columnsize=0` to avoid positional-posting bloat.
  Queries select candidates by three-character grams and verify the exact literal
  substring. Short queries/SQLite builds without trigram fall back to literal LIKE.
- Binary attachments, Base64 data, opaque reasoning/signatures and embedded data
  URIs are retained in raw records, but replaced by explicit references/markers in
  normalized content and excluded from search. Structured JSON embedded in tool
  output is inspected for typed binary/opaque fields as well.
- VS Code result metadata is summarized (status, timing, usage, error). Tool output
  is extracted once into tool-result records, paired only by stable call IDs.
  Unpaired outputs remain separate and explicitly marked; the full original
  metadata/rounds/rendered context stays in the raw archive.
- Changed projections are parsed/replayed once and spooled in a private,
  compressed, per-job cache. Only authenticated cache bytes produced by that
  job are deserialized; the cache is removed on completion/failure.

## Supported sources

| Source | Default location | Notes |
| --- | --- | --- |
| Claude Code | `~/.claude/projects/**/*.jsonl` | Main sessions and separate agent/subagent transcripts |
| Codex | `$CODEX_HOME/{sessions,archived_sessions}/**/*.jsonl` (`~/.codex` by default) | Legacy/paginated rollouts; explicit thread/turn ownership preserves historical subagent work even before migrated context boundaries |
| Copilot CLI | `~/.copilot/session-state/**/events.jsonl` and legacy flat JSONL | Conversation and tool lifecycle events; model snapshots are not duplicated as chat |
| pi | `~/.pi/agent/sessions/**/*.jsonl` | v1–v3, typed content, tree parent IDs and saved-branch view |
| VS Code Chat | Platform `Code/User` and `Code - Insiders/User` | Workspace JSON/JSONL and global empty-window sessions; official mutation-log replay |

On Windows, VS Code defaults to `%APPDATA%/Code/User`; on macOS,
`~/Library/Application Support/Code/User`; on Linux, `~/.config/Code/User`.
`PI_CODING_AGENT_DIR` and `PI_CODING_AGENT_SESSION_DIR` are respected.

### Custom roots

`VIEWER_SOURCE_ROOTS` is a JSON object mapping source IDs to arrays of paths.
Omitted sources keep their defaults. An empty array disables a source. A root
can be a directory or an explicitly selected transcript file. VS Code directory
roots should point to the editor's **User** directory; roots are trusted local
configuration and are not accepted from HTTP requests.

PowerShell:

```powershell
$env:VIEWER_SOURCE_ROOTS = '{"pi":["D:/History/pi"],"codex":["D:/History/codex/sessions"],"vscode_chat":[]}'
$env:VIEWER_DB = 'D:/History/viewer.db'
python server.py
```

Bash:

```bash
export VIEWER_SOURCE_ROOTS='{"pi":["/mnt/history/pi"],"vscode_chat":[]}'
export VIEWER_DB=/mnt/history/viewer.db
python server.py
```

Do **not** point `VIEWER_DB` at any agent-owned database. Recognized legacy
archive schemas are refused instead of migrated.

## Browsing

- Source/project filters, session names, modification times and availability.
- User/assistant text is shown by default; enable tool, thinking, summary and
  system filters to inspect other records. A message containing text and tools
  retains both instead of losing one content type.
- Cross-session search covers names, projects and extracted message/tool text.
  Position-free FTS5 trigram substring search is used when SQLite supports it;
  short queries and installations without that tokenizer use escaped literal `LIKE`, including
  two-character Chinese queries.
- Messages load **200 records per page**. Use **Load next 200 records** to load
  more. The in-conversation search explicitly searches **loaded messages only**.
- pi can switch between all physical records and the current saved branch.
- Details show normalized content/metadata; **Load original record** lazily
  retrieves the unmodified JSON and tool-related records.
- Missing source files do not delete the archive; sessions are marked unavailable.

## Current limits

This is the first multi-source implementation, not a complete replacement for
all agents' native history tools:

- Original records are retained losslessly, but some source-specific rich blocks
  display as structured JSON. Images are not rendered and opaque reasoning is not
  decrypted/indexed. Large VS Code status/error fields are summarized in the UI;
  their complete originals remain in raw records.
- Codex `history_base` inherited prefixes are **not expanded**. Such sessions
  show a warning. Paginated completed items are preferred for visible messages;
  raw response/context records remain accessible in the raw-record API. An
  unfinished paginated turn may only exist in raw records until completion.
- Raw Codex `.jsonl.zst` compression is not supported yet.
- Copilot `session-store.db` and Codex `state_*.sqlite` are not yet used for
  metadata enrichment or missing-transcript recovery.
- VS Code CLI/IDE records are not automatically merged across sources. Duplicate
  storage copies within VS Code are grouped by native session ID; the newest
  nonempty copy wins. Source records for a VS Code message are state operations,
  not necessarily a standalone chat message; `sourceLocator` identifies the
  reconstructed request/chunk. The full operation log is paginated via API.
- Project paths are displayed as recorded; worktree aliases/path-case mapping,
  branch pickers for arbitrary pi leaves, watcher/SSE and export are follow-ups.
- Synchronization runs automatically before startup and on Reload, not through a
  continuous watcher. Per-file content hashes detect rewrites, including
  equal-size/equal-mtime edits. A first import captures candidate projections,
  then imports winners from the private spool without replaying them a second time.
- Schema v2 is independent of the old Claude-only database. V1 viewer archives
  require explicit rebuilding; future schema changes likewise must not silently
  overwrite existing archives.

## API

```text
GET  /api/sources
GET  /api/sessions?source=&project=&q=&cursor=0&limit=100
GET  /api/sessions/<id>/messages?cursor=0&limit=200&branch=active
GET  /api/messages/<message-id>/raw
GET  /api/sessions/<id>/records?cursor=0&limit=50
POST /api/sync/start   # returns 202 immediately; attaches to an existing job if busy
GET  /api/sync/status  # phase, counts, current file, elapsed time, final result
POST /api/sync        # blocking compatibility endpoint for scripts
```

Pages return `items`, `total`, and `nextCursor` (`null` at the end). Message
pages also include session metadata. IDs include the source namespace.
Timestamps carry `timestampUnit: "ms"`.

Completed sync results contain `added`, `updated`, `unchanged`, `failed`, `errors`,
and `warnings`. The asynchronous start endpoint returns a status snapshot; poll
`/api/sync/status` until `running` is false. The `progress` object distinguishes
scanning source files from importing logical sessions, rather than claiming a
misleading overall percentage. Concurrent async requests share the current job.
Mutation requests require **`X-Viewer-Request: 1`** and, if present, a same-origin
`Origin` header. No CORS is enabled. Host headers are restricted to loopback;
trusted proxy hostnames can be explicitly added with comma-separated
`VIEWER_ALLOWED_HOSTS`. This is not authentication: **do not expose the server
to an untrusted network**.

Legacy `/sessions`, `/sessions/search`, `/sessions/sync`, and `/session/<id>`
read routes remain available, but the old message endpoint is bounded to 500
records. New clients should use the paginated API. Source DELETE returns 405.

## Development and tests

```bash
python -m unittest discover -s tests -v
node --experimental-default-type=module tests/test_utils.mjs
node --experimental-default-type=module tests/test_browser.mjs  # optional: Node 22+ and Edge/Chromium
```

Tests use synthetic transcripts and temporary databases only. They cover
source identity, idempotence, atomic rollback, torn/repaired lines, content
rewrites, raw preservation, pi trees, Codex completed-item/tool deduplication,
VS Code truncation/deep mutations, substring/CJK search, pagination and local
HTTP protections. No agent binaries or user history are used by tests. The optional browser test
launches an isolated browser profile and a synthetic local fixture server, then
checks source filters, mixed blocks, raw records, paging, saved branches,
escaping and Reload. Set `EDGE_BIN` when the browser is not at the default
Windows Edge path.

See [`docs/multi-agent-history-research.md`](docs/multi-agent-history-research.md)
for the research, reference projects and planned follow-up work.
