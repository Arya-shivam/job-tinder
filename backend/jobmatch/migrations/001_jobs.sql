CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE jobs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company text NOT NULL,
    title text NOT NULL,
    description text NOT NULL,
    locations text[] NOT NULL,
    workplace text CHECK (workplace IN ('remote', 'hybrid', 'onsite')),
    employment_type text,
    salary_min integer CHECK (salary_min > 0),
    salary_max integer CHECK (salary_max >= COALESCE(salary_min, 1)),
    salary_currency text,
    content_hash bytea NOT NULL UNIQUE,
    embedding vector(1536) NOT NULL,
    embedding_model text NOT NULL,
    search tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', title), 'A') ||
        setweight(to_tsvector('english', company), 'B') ||
        setweight(to_tsvector('english', description), 'C')
    ) STORED
);
CREATE INDEX jobs_search_idx ON jobs USING gin (search);

CREATE TABLE job_sources (
    board text NOT NULL,
    external_id text NOT NULL,
    job_id bigint NOT NULL REFERENCES jobs(id),
    url text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    PRIMARY KEY (board, external_id)
);
CREATE INDEX job_sources_active_idx ON job_sources (job_id) WHERE active;
