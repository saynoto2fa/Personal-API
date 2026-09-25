-- 0002_knowledge_vectors.sql
-- Semantic search storage for the file watcher (stage 2).
-- Requires the pgvector extension (enabled by default on Supabase; use the
-- pgvector/pgvector Docker image or install `postgresql-16-pgvector` locally).
--
-- Embedding dimension is 768 (e.g. nomic-embed-text via Ollama, which suits a
-- local Hermes box). If you pick a model with a different size, change
-- vector(768) below BEFORE indexing anything, or add a later migration.

CREATE EXTENSION IF NOT EXISTS vector;

-- One row per indexed file (vault note, source file, ...).
CREATE TABLE documents (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source          text        NOT NULL,       -- 'vault', 'project:<name>', ...
    path            text        NOT NULL,       -- path relative to the source root
    title           text,
    content_hash    text        NOT NULL,       -- sha256 of file contents; skip re-embed if unchanged
    mtime           timestamptz,
    metadata        jsonb       NOT NULL DEFAULT '{}',
    indexed_at      timestamptz NOT NULL DEFAULT now(),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, path)
);

CREATE TRIGGER documents_updated_at
    BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Chunks of a document with their embeddings. Re-indexing a file replaces its chunks.
CREATE TABLE chunks (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     uuid        NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    chunk_index     integer     NOT NULL,
    content         text        NOT NULL,
    token_count     integer,
    embedding       vector(768),
    metadata        jsonb       NOT NULL DEFAULT '{}',  -- heading path, line range, language, ...
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index)
);

-- Approximate nearest-neighbour index for cosine distance (pgvector >= 0.5).
CREATE INDEX chunks_embedding_hnsw_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);
