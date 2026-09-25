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
| 2 | CRUD endpoints for **schedule, habits, contacts, notes** | ✅ done (habit check-in endpoints still to come) |
| 3 | File watcher (vault + project folders → chunks → embeddings in pgvector) | ✅ done (`python -m app.watcher`) |
| 4 | `GET /knowledge/search?q=` and `GET /me/context` | planned |
| 5 | MCP server wrapping the API | planned |

Each stage works on its own. The CRUD APIs work without pgvector, Ollama or the watcher; the watcher needs pgvector and a local Ollama.

## Repo layout

```
migrations/                 SQL migrations, applied in order by app/migrate.py
  0001_core_schema.sql      pantry_items, schedule, habits, habit_checkins, contacts, notes
  0002_knowledge_vectors.sql  documents + chunks (vector(768)), needs pgvector
app/
  main.py                   FastAPI app
  config.py                 settings from env / .env
  db.py                     SQLAlchemy engine + session
  auth.py                   optional X-API-Key check
  migrate.py                migration runner (python -m app.migrate)
  models/  schemas/  routers/
  watcher/                  file watcher + embedding pipeline (python -m app.watcher)
    chunker.py              Markdown -> ~500-token chunks with heading path + line range
    embedder.py             Ollama client (batching, retries)
    indexer.py              files -> documents/chunks, one file at a time
    watch.py                watchdog events -> debounced, retried jobs
tests/                      pytest suite (unit tests run anywhere; DB tests need TEST_DATABASE_URL)
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

If `API_KEY` is set in `.env`, every endpoint except `/health` requires the header `X-API-Key: <key>`. That covers `/pantry`, `/schedule`, `/habits`, `/contacts` and `/notes`. Leave it empty only on a trusted machine, and set it before exposing the API on your network.

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

```bash
curl -X POST localhost:8000/schedule -H 'Content-Type: application/json' \
  -d '{"title": "Dentist", "starts_at": "2026-10-01T09:00:00-06:00", "ends_at": "2026-10-01T10:00:00-06:00"}'
curl 'localhost:8000/schedule?from=2026-10-01T00:00:00Z&to=2026-10-08T00:00:00Z'

curl -X POST localhost:8000/habits -H 'Content-Type: application/json' -d '{"name": "Read", "target_per_week": 5, "unit": "pages"}'
curl -X POST localhost:8000/contacts -H 'Content-Type: application/json' \
  -d '{"name": "Jane Doe", "relationship": "friend", "dietary_tags": ["gluten_free"], "vault_path": "people/jane-doe.md"}'
curl -X POST localhost:8000/notes -H 'Content-Type: application/json' -d '{"body": "Call the plumber", "tags": ["home"], "source": "chat"}'
```

## File watcher (Stage 3)

`python -m app.watcher` indexes Markdown files into `documents` and `chunks` with local embeddings, then keeps them in sync as files change. It is its own long-running process, separate from the API server. There is no search endpoint yet; that comes in Stage 4.

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
python -m app.watcher --source vault --debounce 5   # one source; wait 5s after the last save
```

The first run embeds every file. For this vault that was 71 notes → 938 chunks in about 75 seconds. Every later start only compares content hashes, which takes about a second when nothing changed. While running, only the file that changed is re-processed.

### What it does

- **Files:** `.md` files under each source folder. It skips `.obsidian`, `.git`, `.trash`, `node_modules`, `.venv`/`venv` and `__pycache__`.
- **Chunking:** about 500 tokens per chunk (estimated as 4 characters per token). Paragraphs, lists and fenced code blocks stay whole unless one is bigger than a chunk. A heading starts a new chunk once the current one has at least ~100 tokens. Chunks within a long section overlap by ~50 tokens; chunks never overlap across sections. YAML frontmatter is left out of chunks but used for the title.
- **Traceability:** each chunk's `metadata` holds `heading_path`, `start_line` and `end_line`, with line numbers from the original file. `documents.path` is relative to the source folder, and `chunks.token_count` is the estimate.
- **Embedding:** each chunk is embedded as `search_document: <title> > <headings>` followed by the chunk text, 16 chunks per Ollama request.
- **Edits:** a new version of a file is embedded first. The document row and all of its chunks are then replaced in one transaction, so no stale chunks are left behind, and a failure keeps the previous version. A file whose content hash and model are unchanged is skipped.
- **Deletes and renames:** deleting a file removes its document and chunks through `ON DELETE CASCADE`. Renaming a file repoints its document without re-embedding. Folder create, delete or move events re-sync just that folder.
- **Debouncing:** rapid saves (Obsidian autosaves every couple of seconds) are collapsed into one reindex, 2 seconds after the last change by default.
- **Failures:** if Ollama is down or the database is unreachable, each embed request is retried 4 times with backoff (1s, 2s, 4s). After that, the file is queued again every 60 seconds. The watcher keeps running.
- **Changing the model:** `documents.metadata.embed_model` records which model embedded each file. Changing `EMBED_MODEL` re-embeds every file on the next sync, but the vector size must still be 768 unless you add a migration.

> **Stage 4 note:** nomic-embed-text expects task prefixes. Documents are stored with `search_document: `, so search queries must be embedded with `search_query: ` (see `QUERY_PREFIX` in `app/watcher/embedder.py`). Without it, results get noticeably worse.

## Schema overview

All tables use UUID primary keys, and `created_at`/`updated_at` are maintained by triggers.

- **pantry_items**: name, qty, unit, location, expiry_estimate, dietary_tags[], notes
- **schedule**: title, starts_at/ends_at, all_day, location, category, recurrence_rule (RRULE), source + external_id (for syncing, e.g. Google Calendar)
- **habits** + **habit_checkins**: habit definitions, and one check-in per habit per day (`done`, optional `value`)
- **contacts**: name, relationship, email, phone, birthday, dietary_tags[] (for guests), tags[], vault_path (link to `people/*.md`)
- **notes**: generic catch-all with tags[] and source (`chat`, `hermes`, `obsidian`, ...)
- **documents** + **chunks**: one row per indexed file with a `content_hash`, so the watcher can skip unchanged files. Chunks store `embedding vector(768)` with an HNSW cosine index.

> **Embedding size:** 768 matches `nomic-embed-text` (runs locally via Ollama, a good fit for Hermes). If you pick a different embedding model, change `vector(768)` in `0002` *before* indexing, or add a migration that alters the column and re-embeds.

## Tests

Unit tests (chunker, embedder, path filtering) run anywhere with plain `pytest`. Tests that need a database are skipped unless `TEST_DATABASE_URL` is set in the environment, not only in `.env`. Point it at a **throwaway** database, because the API tests truncate tables. Without it, the app is pointed at an unreachable address, so tests can never touch your real database.

```bash
pytest   # unit tests only; database tests are skipped
# with docker compose running (it creates personal_api_test):
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/personal_api_test pytest
```

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
