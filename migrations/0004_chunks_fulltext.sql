-- 0004_chunks_fulltext.sql
-- Keyword (full-text) search over chunks, used with the vector search for hybrid /knowledge/search.
--
-- A generated column: Postgres computes it for existing rows when this runs and keeps it current
-- on every insert/update, so the watcher needs no changes and can't forget to fill it.
-- Heading words (from metadata.heading_path) get weight A, body text weight B, so a term in a
-- section heading ranks above the same term in passing.

ALTER TABLE chunks
    ADD COLUMN content_tsv tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(metadata ->> 'heading_path', '')), 'A') ||
        setweight(to_tsvector('english', content), 'B')
    ) STORED;

CREATE INDEX chunks_content_tsv_idx ON chunks USING gin (content_tsv);
