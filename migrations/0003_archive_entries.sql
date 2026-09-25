-- 0003_archive_entries.sql
-- Retention clock for each source's archive folder (OUTDATED by default).
--
-- Windows keeps a file's dates when it is moved, so a file's mtime says nothing about when it
-- was archived. The watcher records each top-level item in the archive folder the first time it
-- sees it and moves it to the Recycle Bin once it has been there ARCHIVE_RETENTION_DAYS.

CREATE TABLE archive_entries (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source          text        NOT NULL,       -- matches documents.source ('vault', ...)
    name            text        NOT NULL,       -- file or folder name directly inside the archive folder
    is_dir          boolean     NOT NULL DEFAULT false,
    first_seen_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, name)
);
