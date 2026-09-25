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
| 3 | File watcher (vault + project folders → chunks → embeddings in pgvector) | tables exist (`documents`, `chunks`) |
| 4 | `GET /knowledge/search?q=` and `GET /me/context` | planned |
| 5 | MCP server wrapping the API | planned |

Each stage works on its own. The CRUD APIs are usable today without pgvector, the watcher or MCP.

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
tests/                      pytest suite (runs against a real Postgres)
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

The tests run against a real Postgres. Point them at a **throwaway** database, because tables are truncated.

```bash
# with docker compose running (it creates personal_api_test):
pytest
# or explicitly:
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/personal_api_test pytest
```

## Running on Hermes (always-on)

The API and the future file watcher need real filesystem access, so they're meant to run on your machine or on Hermes, not in the cloud. Example systemd unit (`/etc/systemd/system/personal-api.service`):

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
