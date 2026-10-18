CREATE TABLE digests (
    account_id uuid NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    day date NOT NULL,
    revision bigint NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (account_id, day)
);

CREATE TABLE digest_exclusions (
    account_id uuid NOT NULL,
    day date NOT NULL,
    job_id bigint NOT NULL,
    title text NOT NULL,
    company text NOT NULL,
    rules jsonb NOT NULL,
    PRIMARY KEY (account_id, day, job_id),
    FOREIGN KEY (account_id, day) REFERENCES digests (account_id, day) ON DELETE CASCADE
);
