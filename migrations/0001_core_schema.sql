-- 0001_core_schema.sql
-- Core structured tables: pantry, schedule, habits, contacts, notes.
-- Plain Postgres only (no pgvector needed), so this stage works anywhere.

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid() on older Postgres

-- Keeps updated_at current on every UPDATE.
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- Pantry
-- ---------------------------------------------------------------------------
CREATE TABLE pantry_items (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text        NOT NULL CHECK (length(trim(name)) > 0),
    qty             numeric(10, 3) NOT NULL DEFAULT 1 CHECK (qty >= 0),
    unit            text,                       -- 'g', 'ml', 'can', 'each', ...
    location        text,                       -- 'pantry', 'fridge', 'freezer', ...
    expiry_estimate date,
    -- Free-form lowercase tags, e.g. {gluten_free, contains_pork, dairy_free}.
    -- See README "Dietary tags" for the recommended vocabulary.
    dietary_tags    text[]      NOT NULL DEFAULT '{}',
    notes           text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX pantry_items_name_idx         ON pantry_items (lower(name));
CREATE INDEX pantry_items_expiry_idx       ON pantry_items (expiry_estimate);
CREATE INDEX pantry_items_dietary_tags_idx ON pantry_items USING gin (dietary_tags);

CREATE TRIGGER pantry_items_updated_at
    BEFORE UPDATE ON pantry_items
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- Schedule (events / time blocks)
-- ---------------------------------------------------------------------------
CREATE TABLE schedule (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title           text        NOT NULL,
    starts_at       timestamptz NOT NULL,
    ends_at         timestamptz,
    all_day         boolean     NOT NULL DEFAULT false,
    location        text,
    category        text,                       -- 'work', 'family', 'focus-block', ...
    recurrence_rule text,                       -- RFC 5545 RRULE, optional
    source          text,                       -- 'manual', 'google_calendar', ...
    external_id     text,                       -- id in the source system, for sync
    notes           text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (ends_at IS NULL OR ends_at >= starts_at)
);

CREATE INDEX schedule_starts_at_idx ON schedule (starts_at);
CREATE UNIQUE INDEX schedule_source_external_idx
    ON schedule (source, external_id) WHERE external_id IS NOT NULL;

CREATE TRIGGER schedule_updated_at
    BEFORE UPDATE ON schedule
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- Habits: definitions + one check-in row per habit per day
-- ---------------------------------------------------------------------------
CREATE TABLE habits (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text        NOT NULL UNIQUE,
    description     text,
    target_per_week smallint CHECK (target_per_week BETWEEN 1 AND 7),
    unit            text,                       -- optional, for measured habits ('min', 'pages')
    active          boolean     NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER habits_updated_at
    BEFORE UPDATE ON habits
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE habit_checkins (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    habit_id        uuid        NOT NULL REFERENCES habits (id) ON DELETE CASCADE,
    checkin_date    date        NOT NULL DEFAULT current_date,
    done            boolean     NOT NULL DEFAULT true,
    value           numeric,                    -- for measured habits
    note            text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (habit_id, checkin_date)
);

CREATE INDEX habit_checkins_date_idx ON habit_checkins (checkin_date);

-- ---------------------------------------------------------------------------
-- Contacts
-- ---------------------------------------------------------------------------
CREATE TABLE contacts (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text        NOT NULL,
    relationship    text,                       -- 'family', 'friend', 'work', ...
    email           text,
    phone           text,
    birthday        date,
    dietary_tags    text[]      NOT NULL DEFAULT '{}',  -- e.g. {no_pork} for guests
    tags            text[]      NOT NULL DEFAULT '{}',
    notes           text,
    vault_path      text,                       -- e.g. 'people/jane-doe.md'
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX contacts_name_idx ON contacts (lower(name));

CREATE TRIGGER contacts_updated_at
    BEFORE UPDATE ON contacts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- Notes (generic catch-all)
-- ---------------------------------------------------------------------------
CREATE TABLE notes (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title           text,
    body            text        NOT NULL,
    tags            text[]      NOT NULL DEFAULT '{}',
    source          text,                       -- 'chat', 'hermes', 'obsidian', 'manual'
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX notes_tags_idx ON notes USING gin (tags);
CREATE INDEX notes_created_at_idx ON notes (created_at DESC);

CREATE TRIGGER notes_updated_at
    BEFORE UPDATE ON notes
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
