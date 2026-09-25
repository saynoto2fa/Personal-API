# Personal Unified API

One source of truth for personal and project data (pantry, schedule, habits, contacts, notes, and indexed documents). Instead of each tool keeping its own memory, every tool reads and writes the same Postgres database through one API. That includes chat assistants, a local AI server ("Hermes") and Obsidian.

```
Obsidian vault ──► file watcher ──► Postgres + pgvector ◄── FastAPI ◄── MCP server ◄── AI clients
                                                              ▲
                                                          curl / scripts
```

## Build stages

| Stage | What | Status |
|---|---|---|
| 1 | Postgres schema (all tables) + FastAPI with **pantry CRUD** | ✅ done |
| 2 | CRUD endpoints for **schedule, habits, contacts, notes** | ✅ done |
| 6 | Habit check-ins (API + MCP) and a local throwaway test database | ✅ done |
| 3 | File watcher (vault + project folders → chunks → embeddings in pgvector) | ✅ done (`python -m app.watcher`) |
| 4 | `GET /knowledge/search?q=` and `GET /me/context` | ✅ done |
| 5 | MCP server wrapping the API | ✅ done (`python -m app.mcp_server`, stdio) |

Each stage works on its own. The CRUD APIs work without pgvector, Ollama or the watcher; the watcher needs pgvector and a local Ollama.

## Repo layout

```
migrations/                 SQL migrations, applied in order by app/migrate.py
  0001_core_schema.sql      pantry_items, schedule, habits, habit_checkins, contacts, notes
  0002_knowledge_vectors.sql  documents + chunks (vector(768)), needs pgvector
  0003_archive_entries.sql  retention clock for the OUTDATED folder
app/
  main.py                   FastAPI app
  config.py                 settings from env / .env
  db.py                     SQLAlchemy engine + session
  auth.py                   optional X-API-Key check
  migrate.py                migration runner (python -m app.migrate)
  serve.py                  run the API with file logging (used by auto-start)
  search.py                 semantic search over chunks (used by /knowledge/search and /me/context)
  mcp_server.py             MCP tools wrapping the API (stdio; python -m app.mcp_server)
  models/  schemas/  routers/
  watcher/                  file watcher + embedding pipeline (python -m app.watcher)
    chunker.py              Markdown -> ~500-token chunks with heading path + line range
    embedder.py             Ollama client (batching, retries)
    indexer.py              files -> documents/chunks, one file at a time
    watch.py                watchdog events -> debounced, retried jobs
    archive.py              OUTDATED retention: 30 days, then the Recycle Bin
scripts/
  install-autostart.ps1     start the API + watcher at Windows logon (Task Scheduler)
  uninstall-autostart.ps1   remove those tasks
tests/                      pytest suite; DB tests use the local test database (tests/testdb.py)
docker-compose.yml          local Postgres 16 + pgvector
```

## Setup

Requires **Python 3.11+** and a **Postgres 14+** database. pgvector is only needed for migration `0002`.

### 1. Get the code and install

```bash
git clone https://github.com/saynoto2fa/Personal-API.git
cd Personal-API
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
```

### 2. Pick a database

**Option A: local Docker (easiest for development, and what Hermes can run too)**

```bash
docker compose up -d
```

This starts Postgres 16 with pgvector on `localhost:5432` and creates `personal_api` plus a `personal_api_test` database for tests. The defaults in `.env.example` already point at it.

**Option B: Supabase.** pgvector is available out of the box. In the dashboard, go to *Connect* and copy the **Session pooler** URI into `DATABASE_URL` in `.env`, adding `?sslmode=require`. Avoid the transaction pooler (port 6543) because it doesn't play well with prepared statements.

**Option C: Railway.** Create a Postgres service. For stage 3 use the *pgvector* template, since plain Railway Postgres may not include the extension. Copy `DATABASE_URL` from the service's Variables tab into `.env`.

### 3. Run migrations

```bash
python -m app.migrate                # apply all pending migrations
python -m app.migrate --status       # see what's applied
python -m app.migrate --until 0001   # structured tables only (no pgvector yet)
```

Migrations are tracked in a `schema_migrations` table, so re-running is safe. To change the schema, add a new file such as `0003_something.sql`. Never edit a migration that has already been applied.

### 4. Start the API

```bash
uvicorn app.main:app --reload                   # dev, localhost only
uvicorn app.main:app --host 0.0.0.0 --port 8000 # reachable from your LAN
```

- Interactive docs: http://localhost:8000/docs
- Health check: http://localhost:8000/health

## Auth

If `API_KEY` is set in `.env`, every endpoint except `/health` requires the header `X-API-Key: <key>`. That covers `/pantry`, `/schedule`, `/habits`, `/contacts`, `/notes`, `/knowledge/search` and `/me/context`. Leave it empty only on a trusted machine, and set it before exposing the API on your network.

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

The `curl` examples below leave the header out for brevity. Add `-H "X-API-Key: $API_KEY"` when a key is set. In `/docs`, click **Authorize** and paste the key once.

## Pantry API

| Method | Path | Description |
|---|---|---|
| `GET` | `/pantry` | List/filter items |
| `POST` | `/pantry` | Create item |
| `GET` | `/pantry/{id}` | Get one item |
| `PATCH` | `/pantry/{id}` | Partial update (only the fields you send change) |
| `POST` | `/pantry/{id}/adjust` | Change qty by a delta, e.g. `{"delta": -2}` ("used 2 eggs"); clamps at 0 |
| `DELETE` | `/pantry/{id}` | Delete item |

**`GET /pantry` query params**

| Param | Meaning |
|---|---|
| `q` | name contains (case-insensitive) |
| `location` | exact match, case-insensitive (`pantry`, `fridge`, `freezer`, ...) |
| `tag` | must have **all** of these tags (repeatable) |
| `exclude_tag` | must have **none** of these tags (repeatable) |
| `expiring_within_days` | expiry estimate on or before today + N (includes already-expired items) |
| `in_stock` | `true` → qty > 0, `false` → qty = 0 |
| `sort` | `name` (default), `expiry`, `updated` |
| `limit`, `offset` | paging (default 100, max 500) |

**Examples**

```bash
# Add an item
curl -X POST localhost:8000/pantry -H 'Content-Type: application/json' -d '{
  "name": "Rice pasta", "qty": 2, "unit": "bag", "location": "pantry",
  "expiry_estimate": "2027-03-01", "dietary_tags": ["gluten_free", "pork_free"]
}'

# What's safe for everyone? (gluten-free and no pork)
curl 'localhost:8000/pantry?tag=gluten_free&exclude_tag=contains_pork'

# What's expiring this week, soonest first?
curl 'localhost:8000/pantry?expiring_within_days=7&sort=expiry'

# Used 2 of something
curl -X POST localhost:8000/pantry/<id>/adjust -H 'Content-Type: application/json' -d '{"delta": -2}'
```

### Dietary tags

`dietary_tags` is a free-form list, normalized on write to lowercase snake_case (`"Gluten-Free"` → `gluten_free`) and de-duplicated. Recommended vocabulary for this household:

| Tag | Meaning |
|---|---|
| `gluten_free` | Confirmed gluten-free (labelled or naturally) |
| `contains_gluten` | Contains wheat/barley/rye; not safe for the household |
| `may_contain_gluten` | Cross-contamination warning / unverified oats etc. |
| `contains_pork` | Contains pork or pork derivatives (bacon, lard, gelatin) |
| `pork_free` | Confirmed no pork |
| `dairy_free`, `vegetarian`, `vegan`, `nut_free` | as named |

Tag positively (`gluten_free`) when you've checked. An item with no gluten tag is "unknown", not "safe". So `?tag=gluten_free&exclude_tag=contains_pork` is the strict "safe for everyone" query.

## Schedule, habits, contacts and notes APIs

All four follow the pantry conventions:

- `POST` returns `201` with the created row, `DELETE` returns `204`, and an unknown id returns `404`.
- `PATCH` changes only the fields you send. Sending `null` clears an optional field, and `null` on a required field returns `422`.
- List endpoints page with `limit` (default 100, max 500) and `offset`.
- Tag lists are normalized like dietary tags (`"Book Club"` → `book_club`).

| Resource | Endpoints | List params | Default order |
|---|---|---|---|
| Schedule | `GET/POST /schedule`, `GET/PATCH/DELETE /schedule/{id}` | `from`, `to`, `category` | `starts_at` |
| Habits | `GET/POST /habits`, `GET/PATCH/DELETE /habits/{id}` | `active` | name |
| Contacts | `GET/POST /contacts`, `GET/PATCH/DELETE /contacts/{id}` | `q` (name), `tag` (repeatable, must have all) | name |
| Notes | `GET/POST /notes`, `GET/PATCH/DELETE /notes/{id}` | `q` (title or body), `tag` (repeatable), `source` | newest first |

**Validation**

| Resource | Required | Rules |
|---|---|---|
| Schedule | `title`, `starts_at` | Timestamps must include a timezone (`Z` or `-06:00`). `ends_at` can't be before `starts_at`. `recurrence_rule` must be an RRULE containing `FREQ=`. A duplicate `source` + `external_id` returns `409`. |
| Habits | `name` | `name` is unique (duplicates return `409`). `target_per_week` is 1–7. Deleting a habit also deletes its check-ins. |
| Contacts | `name` | `email` must look like an address. `birthday` can't be in the future. |
| Notes | `body` | `body` can't be blank. |

`GET /schedule?from=...&to=...` returns events that overlap the window: those that haven't ended by `from` and start by `to`. Recurring events are returned once; RRULEs are stored but not expanded.

### Habit check-ins

A check-in records that a habit was done, or deliberately skipped with `done: false`, on a given day. There is at most one per habit per day.

| Method | Path | Description |
|---|---|---|
| `POST` | `/habits/{habit_id}/checkins` | record a day. The body fields are optional: `checkin_date` (defaults to today), `done` (default `true`), `value` (for measured habits, e.g. pages), `note`. Returns `201`. |
| `GET` | `/habits/{habit_id}/checkins` | list a habit's check-ins, most recent day first. Filters: `from`, `to` (dates), `limit`, `offset`. |
| `DELETE` | `/habits/{habit_id}/checkins/{checkin_id}` | remove a mistaken entry (`204`) |

- **Errors:** an unknown habit is a `404`. A second check-in for the same habit and day is a `409`; delete the first one to change it. A future date is a `422`.
- **"Today":** means the server's local date. The API always sends the date itself, because the column's `CURRENT_DATE` default is the database's UTC date, which is already tomorrow on a US evening.
- **In `/me/context`:** check-ins with `done: true` count towards `habits_done_today` and `habits_done_this_week`.

```bash
curl -X POST localhost:8000/schedule -H 'Content-Type: application/json' \
  -d '{"title": "Dentist", "starts_at": "2026-10-01T09:00:00-06:00", "ends_at": "2026-10-01T10:00:00-06:00"}'
curl 'localhost:8000/schedule?from=2026-10-01T00:00:00Z&to=2026-10-08T00:00:00Z'

curl -X POST localhost:8000/habits -H 'Content-Type: application/json' -d '{"name": "Read", "target_per_week": 5, "unit": "pages"}'
curl -X POST localhost:8000/habits/<habit_id>/checkins -H 'Content-Type: application/json' -d '{"value": 20}'
curl -X POST localhost:8000/contacts -H 'Content-Type: application/json' \
  -d '{"name": "Jane Doe", "relationship": "friend", "dietary_tags": ["gluten_free"], "vault_path": "people/jane-doe.md"}'
curl -X POST localhost:8000/notes -H 'Content-Type: application/json' -d '{"body": "Call the plumber", "tags": ["home"], "source": "chat"}'
```

## File watcher (Stage 3)

`python -m app.watcher` indexes Markdown files into `documents` and `chunks` with local embeddings, then keeps them in sync as files change. It is its own long-running process, separate from the API server. Search over the index is in Stage 4, below.

### Requirements

- **Ollama** running locally, with the embedding model pulled:
  ```bash
  ollama pull nomic-embed-text   # ~274 MB, 768-dim vectors, 2048-token context
  ```
- Migration `0002` applied (pgvector).
- `WATCH_SOURCES` in `.env`: a JSON object mapping a source name to a folder. Use forward slashes and keep the single quotes.
  ```
  WATCH_SOURCES='{"vault": "C:/Users/you/Obsidian Vault"}'
  ```
  To index more folders later, add entries, e.g. `"project:api": "C:/code/api"`. The name is stored in `documents.source`. `OLLAMA_URL` (default `http://localhost:11434`) and `EMBED_MODEL` (default `nomic-embed-text`) are optional.

### Running it

```bash
python -m app.watcher            # sync everything, then watch for changes (Ctrl+C to stop)
python -m app.watcher --once     # sync and exit (exit code 1 if any file failed)
python -m app.watcher --status   # files and chunks indexed per source
python -m app.watcher --archive-status   # what's in OUTDATED and when each item is cleared
python -m app.watcher --log-file logs/watcher.log   # log to a rotating file instead of the console
python -m app.watcher --source vault --debounce 5   # one source; wait 5s after the last save
```

The first run embeds every file. For this vault that was 71 notes → 938 chunks in about 75 seconds. Every later start only compares content hashes, which takes about a second when nothing changed. While running, only the file that changed is re-processed.

### What it does

- **Files:** `.md` files under each source folder. It skips `.obsidian`, `.git`, `.trash`, `node_modules`, `.venv`/`venv`, `__pycache__`, and the archive folder (`OUTDATED` by default; see below).
- **One at a time:** only one watcher runs at once. A second one sees the lock in `logs/watcher.lock` and exits.
- **Chunking:** about 500 tokens per chunk (estimated as 4 characters per token). Paragraphs, lists and fenced code blocks stay whole unless one is bigger than a chunk. A heading starts a new chunk once the current one has at least ~100 tokens. Chunks within a long section overlap by ~50 tokens; chunks never overlap across sections. YAML frontmatter is left out of chunks but used for the title.
- **Traceability:** each chunk's `metadata` holds `heading_path`, `start_line` and `end_line`, with line numbers from the original file. `documents.path` is relative to the source folder, and `chunks.token_count` is the estimate.
- **Embedding:** each chunk is embedded as `search_document: <title> > <headings>` followed by the chunk text, 16 chunks per Ollama request.
- **Edits:** a new version of a file is embedded first. The document row and all of its chunks are then replaced in one transaction, so no stale chunks are left behind, and a failure keeps the previous version. A file whose content hash and model are unchanged is skipped.
- **Deletes and renames:** deleting a file removes its document and chunks through `ON DELETE CASCADE`. Renaming a file repoints its document without re-embedding. Folder create, delete or move events re-sync just that folder.
- **Debouncing:** rapid saves (Obsidian autosaves every couple of seconds) are collapsed into one reindex, 2 seconds after the last change by default.
- **Failures:** if Ollama is down or the database is unreachable, each embed request is retried 4 times with backoff (1s, 2s, 4s). After that, the file is queued again every 60 seconds. The watcher keeps running.
- **Changing the model:** `documents.metadata.embed_model` records which model embedded each file. Changing `EMBED_MODEL` re-embeds every file on the next sync, but the vector size must still be 768 unless you add a migration.

### Archive folder (OUTDATED)

Move a note or folder into `OUTDATED` at the top of the vault to retire it without deleting it right away:

- **Search:** it disappears from the index immediately. Move it back out and it's indexed again.
- **Clearing:** after **30 days** in `OUTDATED`, the watcher moves it to the **Windows Recycle Bin**, so it can still be restored from there. It checks hourly while running.
- **Clock:** Windows keeps a file's dates when it's moved, so the clock starts when the watcher first sees the item there, not from the file's date. If the watcher was off, the clock starts when it next runs, so the hold is only ever longer.
- **Items:** each direct child of `OUTDATED` is one item; a folder moves to the Recycle Bin as a whole.
- **Resetting:** moving an item out forgets it, and moving it back in starts a new 30 days.
- **Locked files:** if a file is open in another program, it is retried on the next check.
- **Settings:** `ARCHIVE_FOLDER` and `ARCHIVE_RETENTION_DAYS` in `.env`. `0` keeps the folder out of search but never clears it.
- **Checking:** `python -m app.watcher --archive-status` lists each item with its due date.

### Running in the background on Windows

```bash
powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1
```

This registers two per-user Task Scheduler tasks under `\Personal-API\`, called **API server** and **Watcher**. Both start at logon with no console window and log to `logs\api.log` and `logs\watcher.log`. Each task also re-runs every 5 minutes; if the process is still running, the new run is ignored, so this only restarts it after a crash. Other details:

- **Start now without logging out:** `Start-ScheduledTask -TaskPath '\Personal-API\' -TaskName 'Watcher'`
- **Remove:** `powershell -ExecutionPolicy Bypass -File scripts\uninstall-autostart.ps1`
- **Restart after code changes:** run `Stop-ScheduledTask`, then `Start-ScheduledTask`.
- **Port 8000:** stop the API task before running `uvicorn --reload` yourself, or the two will fight over the port.

## Knowledge search and context (Stage 4)

Both endpoints are read-only, need `X-API-Key`, and embed the query with the local Ollama model, so Ollama must be running.

> ⚠️ **Query and document prefixes are different on purpose. Don't "fix" this.**
> nomic-embed-text is trained with task prefixes. Chunks are indexed as `search_document: …` (`DOC_PREFIX`), and queries are embedded as `search_query: …` (`QUERY_PREFIX`, both in `app/watcher/embedder.py`). The mismatch is how the model is meant to be used. Giving queries the document prefix, or dropping the prefixes, makes rankings noticeably worse. If you ever switch models, check which prefixes the new model expects and re-index.

### `GET /knowledge/search`

| Param | Meaning |
|---|---|
| `q` | required: what to look for, in plain language (1–1000 characters, not blank) |
| `limit` | results to return (default 10, max 50) |
| `source` | only search one source, e.g. `vault` (matches `documents.source`) |

Results are ranked by cosine similarity (`<=>` on `chunks.embedding`, HNSW `vector_cosine_ops` index), best first. `score` is `1 - cosine distance`; on this vault a strong match is about 0.7–0.8. Each hit points back to its file and lines. Notes in `OUTDATED` are never returned, because they aren't indexed.

```bash
curl -H "X-API-Key: $API_KEY" 'localhost:8000/knowledge/search?q=windows%20process%20auditing&limit=2'
```
```json
{
  "query": "windows process auditing",
  "source": null,
  "results": [
    {
      "score": 0.789,
      "source": "vault",
      "path": "Windows-Process-Auditor.md",
      "title": "Windows Background Process Auditor",
      "chunk_index": 0,
      "heading_path": ["Windows Background Process Auditor"],
      "start_line": 1,
      "end_line": 7,
      "content": "# Windows Background Process Auditor\n\n..."
    },
    { "score": 0.772, "path": "Windows-Process-Auditor.md", "heading_path": ["Windows Background Process Auditor", "Steps"], "start_line": 9, "end_line": 31, "...": "..." }
  ]
}
```

Errors: `422` for a missing, blank or too-long `q` or an out-of-range `limit`; `503` if Ollama can't embed the query (not running, or model not pulled).

### `GET /me/context`

A fast snapshot of what's going on, for assistants to read at the start of a conversation. **By default it returns summary counts only**, about 0.5 KB, plus notes about `q` if one is given. Ask for full detail per section with `include`. It is not an export; use the resource endpoints for complete lists.

| Param | Meaning |
|---|---|
| `q` | optional topic. Adds the `knowledge` section with the top matching notes. |
| `include` | comma-separated sections to return in full: `pantry`, `schedule`, `habits`, `knowledge`. `include=pantry,schedule,habits` gives the full response that was the default before the summary. `knowledge` requires `q`. |
| `days` | look-ahead window for schedule and expiring pantry items (default 7, max 31) |
| `knowledge_limit` | notes to include for `q` (default 5, max 20) |
| `source` | only search notes in this source |

| Key | Returned | Contents (capped) |
|---|---|---|
| `summary` | always | `schedule_count_today`, `schedule_count_upcoming`, `next_event`, `pantry_total`, `pantry_out_of_stock`, `pantry_expiring_count`, `habits_active`, `habits_done_today`, `habits_done_this_week` |
| `schedule` | `include=schedule` | events not yet over and starting within `days`, soonest first (max 20). All-day events without an end count as lasting that day. |
| `pantry` | `include=pantry` | `total_items`, `out_of_stock`, and `expiring`: in-stock items expiring within `days`, including already-expired ones (max 15) |
| `habits` | `include=habits` | active habits with `done_this_week` (check-ins marked done since Monday) and `done_today` (max 30) |
| `knowledge` | when `q` is given | the same hits as `/knowledge/search`, for `q` |
| `warnings` | always | sections that couldn't be filled. If Ollama is down, the rest is still returned, with a warning instead of a 503. |

Sections that weren't requested are `null`. `include` lists what was actually returned in full.

```bash
curl -H "X-API-Key: $API_KEY" 'localhost:8000/me/context'
```
```json
{
  "generated_at": "2026-09-25T03:40:12Z", "today": "2026-09-24", "q": null, "days": 7, "include": [],
  "summary": {
    "schedule_count_today": 2, "schedule_count_upcoming": 5,
    "next_event": { "id": "…", "title": "Dentist", "starts_at": "2026-09-25T15:00:00Z", "ends_at": "2026-09-25T16:00:00Z", "all_day": false, "location": null, "category": "health" },
    "pantry_total": 12, "pantry_out_of_stock": 1, "pantry_expiring_count": 2,
    "habits_active": 3, "habits_done_today": 1, "habits_done_this_week": 6
  },
  "schedule": null, "pantry": null, "habits": null, "knowledge": null, "warnings": []
}
```

```bash
curl -H "X-API-Key: $API_KEY" 'localhost:8000/me/context?q=tutor%20course%20design&include=schedule,habits&knowledge_limit=3'
```
```json
{
  "q": "tutor course design", "include": ["schedule", "habits", "knowledge"],
  "summary": { "...": "as above" },
  "schedule": [{ "id": "…", "title": "Dentist", "starts_at": "2026-09-26T15:00:00Z", "...": "..." }],
  "habits": [{ "id": "…", "name": "Read", "unit": "pages", "target_per_week": 5, "done_this_week": 2, "done_today": true }],
  "pantry": null,
  "knowledge": [{ "score": 0.74, "source": "vault", "path": "memory/projects/ai-tutor-system.md", "start_line": 12, "end_line": 30, "...": "..." }],
  "warnings": []
}
```

"Today" and "this week" (starting Monday) use the server's local date; event times are compared in UTC. The API asks Ollama to keep the model loaded for an hour, so only the first search after a long idle period pays the ~5-second model load.

Not included yet: keyword or hybrid search, and topic-filtered structured data. Those come later.

## MCP server (Stage 5)

`python -m app.mcp_server` exposes the API as [MCP](https://modelcontextprotocol.io) tools, so MCP clients such as Claude Desktop and Claude Code can read and update your data directly. It speaks **stdio**: the client starts it as a child process when needed, so you never run it yourself.

- **Thin wrapper:** it calls the running HTTP API (`API_URL`, default `http://127.0.0.1:8000`), so the API must be up. The auto-start task handles that. Validation, auth and behaviour are exactly the API's.
- **API key:** read from this repo's `.env` by absolute path, and sent as `X-API-Key`. The AI client never sees or supplies it.
- **Errors:** returned as one readable line, never a stack trace. For example: *Not found: Note not found*, *Invalid input: starts_at: Input should have timezone info*, *The Personal API is not reachable at http://127.0.0.1:8000. Is it running?*, *The Personal API rejected the API key*.
- **Only works on this PC:** the claude.ai website and mobile apps can't launch local stdio servers. Using it from there would need a remote (HTTPS) MCP server exposed to the internet, which is a separate decision with security trade-offs.

### Registering it

Use the virtual environment's Python, by absolute path.

**Claude Code** (available in all your projects):
```bash
claude mcp add --scope user --transport stdio personal-api -- "C:\Users\Tyler\Desktop\Personal-API\.venv\Scripts\python.exe" -m app.mcp_server
```

**Claude Desktop:** Settings → Developer → Edit Config (`%APPDATA%\Claude\claude_desktop_config.json`), add the server under `mcpServers`, then fully quit and restart Claude Desktop:
```json
{
  "mcpServers": {
    "personal-api": {
      "command": "C:\\Users\\Tyler\\Desktop\\Personal-API\\.venv\\Scripts\\python.exe",
      "args": ["-m", "app.mcp_server"]
    }
  }
}
```
No `env` or working directory is needed. Server logs go to stderr, which Claude Desktop writes to `%APPDATA%\Claude\logs\mcp-server-personal-api.log`.

### Tools

Every tool starts with a marker saying whether it changes data. Read tools begin with **Read-only.** and carry `readOnlyHint`. Write tools begin with **WRITES DATA** and delete tools with **DELETES DATA PERMANENTLY**; both are marked `readOnlyHint: false`, and updates and deletes are also marked `destructiveHint: true`, so clients can ask before running them.

| Tool | Calls | What it's for |
|---|---|---|
| `search_knowledge` | `GET /knowledge/search` | find passages in the vault **by meaning**, with file path and line numbers |
| `get_context` | `GET /me/context` | quick situational summary. The default is lightweight counts; pass `include` or `q` for detail. |
| `list_pantry`, `get_pantry_item` | `GET /pantry`, `/pantry/{id}` | inventory, with dietary-tag, expiry and stock filters |
| `create_pantry_item`, `update_pantry_item`, `delete_pantry_item` | `POST`/`PATCH`/`DELETE /pantry` | change inventory |
| `adjust_pantry_quantity` | `POST /pantry/{id}/adjust` | "used 2", "bought 3 more" |
| `list_schedule`, `get_schedule_event` | `GET /schedule`, `/schedule/{id}` | events, optionally within a time window (`from_time`, `to_time`) |
| `create_schedule_event`, `update_schedule_event`, `delete_schedule_event` | `POST`/`PATCH`/`DELETE /schedule` | change the calendar. Created events get `source: "mcp"`. |
| `list_habits`, `get_habit` | `GET /habits`, `/habits/{id}` | habit definitions (use `get_context` with `include=["habits"]` for progress) |
| `create_habit`, `update_habit`, `delete_habit` | `POST`/`PATCH`/`DELETE /habits` | `update_habit` with `active=false` retires a habit but keeps its history |
| `list_habit_checkins` | `GET /habits/{id}/checkins` | the days a habit was done or skipped, optionally within `from_date`/`to_date` |
| `create_habit_checkin`, `delete_habit_checkin` | `POST`/`DELETE /habits/{id}/checkins` | "I read 20 pages today". One per habit per day; delete a check-in to correct it. |
| `list_contacts`, `get_contact` | `GET /contacts`, `/contacts/{id}` | people, with relationship, birthday and dietary needs |
| `create_contact`, `update_contact`, `delete_contact` | `POST`/`PATCH`/`DELETE /contacts` | change contacts |
| `list_notes`, `get_note` | `GET /notes`, `/notes/{id}` | short saved notes, which are separate from the vault |
| `create_note`, `update_note`, `delete_note` | `POST`/`PATCH`/`DELETE /notes` | change notes. Created notes get `source: "mcp"`. |

**Other behaviour:**
- **Updates:** update tools are partial, so only the fields you pass change. List field names in `clear` to erase them.
- **Lists:** list tools return `{"count": n, "items": [...]}`, paged with `limit` and `offset`.
- **Ids:** ids must be UUIDs, and anything else is rejected before the API is called.

## Schema overview

All tables use UUID primary keys, and `created_at`/`updated_at` are maintained by triggers.

- **pantry_items**: name, qty, unit, location, expiry_estimate, dietary_tags[], notes
- **schedule**: title, starts_at/ends_at, all_day, location, category, recurrence_rule (RRULE), source + external_id (for syncing, e.g. Google Calendar)
- **habits** + **habit_checkins**: habit definitions, and one check-in per habit per day (`done`, optional `value`)
- **contacts**: name, relationship, email, phone, birthday, dietary_tags[] (for guests), tags[], vault_path (link to `people/*.md`)
- **notes**: generic catch-all with tags[] and source (`chat`, `hermes`, `obsidian`, ...)
- **documents** + **chunks**: one row per indexed file with a `content_hash`, so the watcher can skip unchanged files. Chunks store `embedding vector(768)` with an HNSW cosine index.
- **archive_entries**: one row per item in a source's archive folder, with `first_seen_at` (the retention clock).

> **Embedding size:** 768 matches `nomic-embed-text` (runs locally via Ollama, a good fit for Hermes). If you pick a different embedding model, change `vector(768)` in `0002` *before* indexing, or add a migration that alters the column and re-embeds.

## Tests

The test suite runs against a **local throwaway Postgres**, never your real database, because the tests truncate tables.

**One-time setup** (no admin rights, Docker or WSL):
```bash
python -m tests.testdb setup
```
This downloads [micromamba](https://mamba.readthedocs.io/) and installs Postgres 16 and pgvector 0.8 from conda-forge into `.testdb/`, which is git-ignored and about 370 MB. conda-forge only builds pgvector for Windows against Postgres 16; Supabase runs 17, and nothing in the migrations differs between them. The server listens on `127.0.0.1:54329` only, with trust auth and fsync off.

**Running the tests:**
```bash
pytest
```
- **Automatic start and stop:** `pytest` starts the test database if needed, applies every migration, runs all tests (99 at the time of writing, in about 7 seconds), and stops the database again if it started it.
- **Manual control:** `python -m tests.testdb start|stop|status|reset`. `reset` wipes the test data folder.
- **Another throwaway database:** set `TEST_DATABASE_URL` in the environment.
- **Safety check:** URLs that point at Supabase, or equal `DATABASE_URL` in `.env`, are refused.
- **No test database at all:** DB tests are skipped, and the app is pointed at an unreachable address so nothing can reach your real data.

On other platforms, `docker compose up -d` gives an equivalent database; set `TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/personal_api_test`.

## Running on Hermes (always-on)

The API and the file watcher need real filesystem access, so they're meant to run on your machine or on Hermes, not in the cloud. The watcher also needs Ollama on the same machine. Example systemd unit (`/etc/systemd/system/personal-api.service`); for the watcher, copy it as `personal-api-watcher.service` with `ExecStart=/home/youruser/Personal-API/.venv/bin/python -m app.watcher` and `After=network-online.target ollama.service`:

```ini
[Unit]
Description=Personal Unified API
After=network-online.target docker.service

[Service]
User=youruser
WorkingDirectory=/home/youruser/Personal-API
ExecStart=/home/youruser/Personal-API/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now personal-api
```

Updating: `git pull && pip install -e . && python -m app.migrate && sudo systemctl restart personal-api`.
