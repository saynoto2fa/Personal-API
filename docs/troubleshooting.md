---
title: Personal API troubleshooting
---

# Personal API troubleshooting

Known problems on this setup (Windows 11, Supabase, Ollama, Claude Desktop and Claude Code) and how
each was fixed. Each entry lists the **symptom**, the **cause**, the **fix** and how to **check** it.
This folder is indexed by the file watcher as source `project:personal-api`, so any chat with the
`personal-api` MCP tools can find an entry with `search_knowledge`.

Quick health check, in order:

1. `Invoke-RestMethod http://127.0.0.1:8000/health` returns `status: ok`: the API is up.
2. `Invoke-RestMethod http://localhost:11434/api/version` answers: Ollama is up (search needs it).
3. `Get-ScheduledTask -TaskPath '\Personal-API\'` shows **API server** and **Watcher** as Running.
4. `logs\api.log` and `logs\watcher.log` in the repo show the latest errors.

## Search returns nothing, or "knowledge: search unavailable", after a reboot

- **Symptom:** search results carry the warning `semantic search unavailable (...); showing
  keyword matches only`, `mode=semantic` returns a 503, or `get_context` has a `knowledge:` warning
  mentioning Ollama "actively refused it". Keyword matches and everything else still work.
- **Cause:** Ollama isn't running. Its startup entry had been disabled, so it didn't start at login.
- **Fix:** start it with `& "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe"`. To start it at login,
  put a shortcut to that exe in the Startup folder (`shell:startup`) and enable "Ollama" under
  Task Manager → Startup apps. Both were done on 2026-09-24.
- **Check:** `Invoke-RestMethod http://localhost:11434/api/version` answers, and a search returns results.

## Search misses an exact name, error code or identifier

- **Symptom:** searching for something like `2147946720`, a tool name or an exact error message
  returns vaguely related notes instead of the one that contains it.
- **Cause:** embeddings capture meaning, not exact strings, so rare tokens barely register.
  Before Stage 7, search was semantic-only.
- **Fix:** use the default `mode=hybrid`, which adds full-text keyword matching and ranks exact
  matches first on ties, or `mode=keyword` to see only chunks containing the words. Keyword search
  follows web-search syntax: `"exact phrase"`, `-exclude`, `or`.
- **Check:** in the results, `keyword_score` is not null for chunks that contain the words.

## A search hit is labeled with the wrong section heading

- **Symptom:** the right text comes back, but `heading_path` names a different, short section just
  above it.
- **Cause:** chunker v1 merged sections under ~100 tokens into the next one and labeled the merged
  chunk with the first section. This was fixed in chunker v2 (Stage 7): H1/H2 always split, and a merged
  chunk takes the heading of the section contributing most of its text.
- **Fix:** nothing to do. Every document records its chunker version in `documents.metadata.chunker`,
  and the watcher re-chunks older ones on its next sync. If labels still look stale, restart the
  Watcher task and check `logs\watcher.log` for `indexed` lines.

## The first search after a while takes 5–7 seconds

- **Cause:** Ollama unloads a model after 5 idle minutes, and reloading `nomic-embed-text` takes a few seconds.
- **Fix:** already in place. The API sends `keep_alive: "1h"` with every query (`app/search.py`), so only the
  first search after an hour of no searches pays the reload.

## Claude Desktop doesn't show the personal-api tools

- **Symptom:** no `personal-api` under **+** → Connectors in a Claude Desktop chat, even after a restart.
- **Cause:** Claude Desktop keeps its settings in memory and rewrites
  `%APPDATA%\Claude\claude_desktop_config.json` while it runs. Any edit made while it's open,
  including through Settings → Developer → Edit Config, gets overwritten by the version in memory, and
  `mcpServers` ends up as `{}` again.
- **Fix:** add the entry while Claude Desktop is **fully quit** (tray icon → Quit, not just closing
  the window). This is the entry:
  ```json
  "mcpServers": {
    "personal-api": {
      "command": "C:\\Users\\Tyler\\Desktop\\Personal-API\\.venv\\Scripts\\python.exe",
      "args": ["-m", "app.mcp_server"]
    }
  }
  ```
  Because a Claude Code session runs inside Claude Desktop, the reliable approach from a session is a
  hidden helper that waits for `Claude.exe` to exit, then edits the JSON with Python after taking a
  backup, then exits.
- **Check:** after reopening, `Get-CimInstance Win32_Process | ? CommandLine -match 'app.mcp_server'`
  shows processes whose parent is `...\WindowsApps\Claude_*\app\Claude.exe`. The log files in
  `%APPDATA%\Claude\logs` were **not** updated by this Store version, so don't rely on them.

## Claude Code doesn't have the tools

- **Fix:** `claude mcp add --scope user --transport stdio personal-api -- "C:\Users\Tyler\Desktop\Personal-API\.venv\Scripts\python.exe" -m app.mcp_server`.
  Run it from Git Bash, because PowerShell swallows the `--`.
- **Check:** `claude mcp get personal-api` shows `✓ Connected`.

## MCP tool says "The Personal API is not reachable"

- **Cause:** the API server isn't running. The MCP server is only a thin wrapper over `http://127.0.0.1:8000`.
- **Fix:** `Start-ScheduledTask -TaskPath '\Personal-API\' -TaskName 'API server'`, or wait: the task
  re-runs every 5 minutes and restarts the API if it crashed. Check `logs\api.log` for why it stopped.

## MCP tool says "The Personal API rejected the API key"

- **Cause:** `API_KEY` in the repo's `.env` differs from the key the running API loaded (for example,
  it was changed without restarting the API).
- **Fix:** restart the API task. The MCP server reads `.env` fresh every time a client starts it.

## A scheduled task didn't restart the watcher or API after a crash

- **Cause:** a repetition attached to an **At log on** trigger only starts after the next logon, so
  tasks registered during a session never repeat until you log out and back in.
- **Fix:** already in place. `scripts\install-autostart.ps1` adds a separate time trigger that
  repeats every 5 minutes (`MultipleInstances IgnoreNew`, so it only starts a process that died).
- **Note:** `LastTaskResult 2147946720` (0x800710E0) just means a repeat run was skipped because the
  task was already running. It isn't an error.
- **Check:** kill the watcher process; it's back within 5 minutes.

## Code changes don't show up in the running API or watcher

- **Cause:** the background tasks load the code once at start.
- **Fix:**
  ```powershell
  Stop-ScheduledTask -TaskPath '\Personal-API\' -TaskName 'API server'
  Start-ScheduledTask -TaskPath '\Personal-API\' -TaskName 'API server'
  ```
  Do the same for 'Watcher'. Stop the API task before running `uvicorn --reload` by hand, because both
  want port 8000.

## uvicorn --reload keeps serving old code on Windows

- **Symptom:** after edits, new endpoints return 404; the log ends at "WatchFiles detected changes... Reloading".
- **Cause:** the reloader on Windows sometimes stalls partway through a reload.
- **Fix:** kill the uvicorn processes and start again without `--reload`, or use the scheduled task.

## Supabase connection string problems

- **Use the Session pooler** URI (Dashboard → Connect → Direct → Session pooler, port **5432**), with
  `?sslmode=require` appended. The Transaction pooler (port 6543) breaks prepared statements, and the
  direct connection needs IPv6, which most home networks don't have.
- **Password symbols must be percent-encoded** in the URL (`@`→`%40`, `#`→`%23`, `/`→`%2F`,
  `?`→`%3F`, `:`→`%3A`, `%`→`%25`). An unencoded `/`, `?` or `#` can still work with one driver and
  fail with another: libpq, SQLAlchemy and `urllib` split the URL differently.
- **Check:** parse the URL with `sqlalchemy.engine.make_url` and `urllib.parse.urlsplit`. Both must
  show the pooler host and port 5432. Never print the password.

## pytest refuses to run: "TEST_DATABASE_URL points at a real database"

- **This is intended.** The tests truncate tables, so a URL that matches `DATABASE_URL` or points at
  Supabase is refused.
- **Fix:** use the local test database, `python -m tests.testdb setup` (one time), then just `pytest`.

## Local test database won't start, or `tests.testdb` hangs

- **Hang on start:** `pg_ctl start` leaves a Postgres process behind that inherits any pipes the caller
  opened, so `subprocess.run(..., capture_output=True)` never returns. `tests/testdb.py` sends
  pg_ctl's output to DEVNULL; keep it that way.
- **"The process cannot access the file because it is being used by another process":** pg_ctl's
  output and the server log (`-l`) can't share one file on Windows.
- **Solver error installing `postgresql=17` + `pgvector`:** conda-forge builds pgvector for Windows
  against Postgres 16 only, so the test DB uses Postgres 16.
- **Check:** `python -m tests.testdb status` should print `postgres 16.x, pgvector available: 0.8.x`.
  Logs are in `.testdb\postgres.log`.

## A date is off by one, or a due time is off by an hour

- **Check-in recorded for tomorrow:** the database's `CURRENT_DATE` is UTC, which is already tomorrow
  on a US evening. Always send dates explicitly from the server's local `date.today()` (the check-in
  endpoint does this).
- **Due time off by an hour around a DST change:** psycopg returns timestamps in the database
  session's timezone (UTC on Supabase, America/Denver on the local test DB). Adding days to a local
  wall-clock time shifts the result by an hour across DST. Convert with `.astimezone(UTC)` before doing
  arithmetic (fixed in `app/watcher/archive.py`).

## Archived (OUTDATED) notes would all be deleted immediately

- **Cause:** Windows keeps a file's modified and created dates when it's moved, so a file's dates say
  nothing about when it was archived.
- **Fix:** already in place. The watcher records `first_seen_at` in `archive_entries` the first time it
  sees each item in `OUTDATED`, and moves it to the Recycle Bin 30 days after that. Check with
  `python -m app.watcher --archive-status`.

## PowerShell breaks commands with quotes (python -c, git commit -m, claude mcp add)

- **Cause:** Windows PowerShell 5.1 re-quotes arguments for native programs and strips or splits
  embedded double quotes. A `git commit -m` message containing `"quotes"` was split into pathspecs.
- **Fix:** put the Python code in a `.py` file and the commit message in a file (`git commit -F msg.txt`),
  and run `claude mcp add ... -- ...` from Git Bash.

## MCP SDK: "No module named mcp.server.fastmcp"

- **Cause:** the `mcp` Python SDK 2.x renamed `FastMCP` to `MCPServer`.
- **Fix:** `from mcp.server.mcpserver import MCPServer` and
  `from mcp.server.mcpserver.exceptions import ToolError`. The project pins `mcp>=2.2,<3`.

## FastAPI dependency override silently ignored in a test script

- **Cause:** `app.dependency_overrides[dep] = lambda e=obj: e`. FastAPI treats `e` as a query
  parameter and passes a *copy* of the default, so the test's object isn't the one used.
- **Fix:** use a closure with no parameters: `app.dependency_overrides[dep] = lambda: obj`.

## "unrecognized configuration parameter hnsw.ef_search"

- **Cause:** pgvector registers its settings only once it's loaded in a session, so `SHOW` fails in a
  fresh connection.
- **Note:** `SET LOCAL hnsw.ef_search = 100` still works. The default of 40 would cap results below
  `limit=50`, which is why search raises it (`app/search.py`).
