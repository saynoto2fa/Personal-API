# Personal API

FastAPI + Supabase Postgres (pgvector) + a file watcher (Ollama embeddings) + an MCP server.
README.md is the reference for setup, endpoints and tools.

- **Stuck on something?** Check `docs/troubleshooting.md` first. It lists problems already solved
  on this machine (Supabase URLs, Ollama not running, Claude Desktop overwriting its config,
  PowerShell quoting, scheduled tasks, the test database). Add an entry when you fix a new one.
- **Tests:** `pytest` uses the local throwaway Postgres in `.testdb/` (`python -m tests.testdb setup`
  once). Never point `TEST_DATABASE_URL` at Supabase; conftest refuses it.
- **Secrets:** `.env` holds the database password and `API_KEY`. Don't print them or commit them.
- **Workflow:** branch off `main`, open a PR, and let the user merge. After merging, restart the
  scheduled tasks (`\Personal-API\API server`, `\Personal-API\Watcher`).
- **Shell:** this is Windows PowerShell 5.1. Pass quoted text through files (`git commit -F`,
  `.py` scripts), not inline.
