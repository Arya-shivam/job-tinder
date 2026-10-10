CREATE TABLE accounts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    profile jsonb,
    preferences jsonb NOT NULL DEFAULT '{}',
    revision bigint NOT NULL DEFAULT 0
);

CREATE TABLE auth_sessions (
    token_hash bytea PRIMARY KEY CHECK (octet_length(token_hash) = 32),
    account_id uuid NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    expires_at timestamptz NOT NULL
);
CREATE INDEX auth_sessions_account_expiry_idx ON auth_sessions (account_id, expires_at);

-- Uploads insert a new source version. Edits only write to resume_edits.
CREATE TABLE resumes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id uuid NOT NULL UNIQUE REFERENCES accounts (id) ON DELETE CASCADE,
    filename text NOT NULL,
    pdf bytea NOT NULL CHECK (octet_length(pdf) BETWEEN 1 AND 5242880),
    original_text text NOT NULL CHECK (length(original_text) BETWEEN 1 AND 60000)
);

CREATE TABLE resume_edits (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    resume_id uuid NOT NULL REFERENCES resumes (id) ON DELETE CASCADE,
    job_id bigint NOT NULL,
    start_offset integer NOT NULL CHECK (start_offset >= 0),
    end_offset integer NOT NULL CHECK (end_offset > start_offset),
    before_text text NOT NULL CHECK (length(before_text) = end_offset - start_offset),
    after_text text NOT NULL CHECK (length(after_text) > 0 AND after_text <> before_text),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX resume_edits_resume_span_idx ON resume_edits (resume_id, start_offset, end_offset);
