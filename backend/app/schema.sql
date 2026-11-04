-- Tables for live data. Run automatically when the API starts; safe to run again.
-- Amounts are integer satoshis. Times are UTC.

-- One row per address lookup, so balances can be compared over time.
CREATE TABLE IF NOT EXISTS address_snapshots (
    id                        BIGSERIAL PRIMARY KEY,
    entity_id                 TEXT NOT NULL,
    network                   TEXT NOT NULL,
    address                   TEXT NOT NULL,
    confirmed_tx_count        INTEGER NOT NULL,
    confirmed_funded_sats     BIGINT NOT NULL,
    confirmed_spent_sats      BIGINT NOT NULL,
    confirmed_balance_sats    BIGINT NOT NULL,
    unconfirmed_tx_count      INTEGER NOT NULL,
    unconfirmed_funded_sats   BIGINT NOT NULL,
    unconfirmed_spent_sats    BIGINT NOT NULL,
    unconfirmed_balance_sats  BIGINT NOT NULL,
    provider                  TEXT NOT NULL,
    source_url                TEXT NOT NULL,
    retrieved_at              TIMESTAMPTZ NOT NULL,
    data_mode                 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS address_snapshots_address_idx ON address_snapshots (address, retrieved_at DESC);

-- One row per transaction seen. Refreshed when seen again (e.g. it confirms).
CREATE TABLE IF NOT EXISTS transactions (
    txid               TEXT PRIMARY KEY,
    entity_id          TEXT NOT NULL,
    network            TEXT NOT NULL,
    status             TEXT NOT NULL,
    block_height       INTEGER,
    block_time         TIMESTAMPTZ,
    fee_sats           BIGINT,
    input_count        INTEGER NOT NULL,
    output_count       INTEGER NOT NULL,
    total_output_sats  BIGINT NOT NULL,
    inputs             JSONB NOT NULL,
    outputs            JSONB NOT NULL,
    provider           TEXT NOT NULL,
    source_url         TEXT NOT NULL,
    first_seen_at      TIMESTAMPTZ NOT NULL,
    last_retrieved_at  TIMESTAMPTZ NOT NULL,
    data_mode          TEXT NOT NULL
);

-- Each input and output with an address, so you can ask "which transactions touched this address?".
CREATE TABLE IF NOT EXISTS transaction_flows (
    txid        TEXT NOT NULL REFERENCES transactions (txid) ON DELETE CASCADE,
    direction   TEXT NOT NULL CHECK (direction IN ('input', 'output')),
    position    INTEGER NOT NULL,
    address     TEXT NOT NULL,
    value_sats  BIGINT,
    PRIMARY KEY (txid, direction, position)
);
CREATE INDEX IF NOT EXISTS transaction_flows_address_idx ON transaction_flows (address);

-- Every investigation run from the dashboard, with the full result the browser received.
CREATE TABLE IF NOT EXISTS investigations (
    investigation_id  TEXT PRIMARY KEY,
    created_at        TIMESTAMPTZ NOT NULL,
    mode              TEXT NOT NULL,
    method            TEXT NOT NULL,
    input_type        TEXT NOT NULL,
    input_value       TEXT NOT NULL,
    data_mode         TEXT NOT NULL,
    finding_count     INTEGER NOT NULL,
    node_count        INTEGER NOT NULL,
    edge_count        INTEGER NOT NULL,
    graph_truncated   BOOLEAN NOT NULL,
    result            JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS investigations_created_idx ON investigations (created_at DESC);
CREATE INDEX IF NOT EXISTS investigations_input_idx ON investigations (input_value);

-- Results of provider health checks (is mempool.space up, and what block is it on).
CREATE TABLE IF NOT EXISTS provider_health_checks (
    id          BIGSERIAL PRIMARY KEY,
    provider    TEXT NOT NULL,
    reachable   BOOLEAN NOT NULL,
    tip_height  INTEGER,
    checked_at  TIMESTAMPTZ NOT NULL,
    detail      TEXT NOT NULL
);

-- ---- Accounts, cases and the sources page -----------------------------------------------
-- Passwords are stored only as scrypt hashes (app/auth/passwords.py). Session tokens are stored
-- only as SHA-256 hashes, so a copy of this database cannot be used to sign in.

CREATE TABLE IF NOT EXISTS app_users (
    id             BIGSERIAL PRIMARY KEY,
    email          TEXT NOT NULL UNIQUE,            -- always lower case
    display_name   TEXT NOT NULL,
    password_hash  TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'viewer' CHECK (role IN ('viewer', 'analyst', 'admin')),
    disabled       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS app_sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES app_users (id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    user_agent  TEXT
);
CREATE INDEX IF NOT EXISTS app_sessions_user_idx ON app_sessions (user_id);

-- Cases group the addresses and transactions an analyst wants to follow up.
CREATE TABLE IF NOT EXISTS cases (
    id           BIGSERIAL PRIMARY KEY,
    title        TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    priority     TEXT NOT NULL DEFAULT 'medium' CHECK (priority IN ('low', 'medium', 'high')),
    created_by   BIGINT REFERENCES app_users (id) ON DELETE SET NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS case_items (
    id         BIGSERIAL PRIMARY KEY,
    case_id    BIGINT NOT NULL REFERENCES cases (id) ON DELETE CASCADE,
    item_type  TEXT NOT NULL CHECK (item_type IN ('address', 'transaction', 'domain', 'payment', 'other')),
    value      TEXT NOT NULL,
    network    TEXT NOT NULL DEFAULT 'bitcoin',
    label      TEXT,
    note       TEXT NOT NULL DEFAULT '',
    source     TEXT,                                -- where it was added from, e.g. an investigation ID
    added_by   BIGINT REFERENCES app_users (id) ON DELETE SET NULL,
    added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (case_id, item_type, value)
);
CREATE INDEX IF NOT EXISTS case_items_value_idx ON case_items (value);

-- The acknowledgements shown on the Sources page. Seeded once; admins edit it.
CREATE TABLE IF NOT EXISTS data_sources (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,
    url         TEXT NOT NULL DEFAULT '',
    used_for    TEXT NOT NULL DEFAULT '',
    licence     TEXT NOT NULL DEFAULT '',
    citation    TEXT NOT NULL DEFAULT '',
    position    INTEGER NOT NULL DEFAULT 0,
    updated_by  BIGINT REFERENCES app_users (id) ON DELETE SET NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One-off markers, e.g. "the sources list has been seeded".
CREATE TABLE IF NOT EXISTS app_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

-- Who did what: sign-ups, role changes, source edits, case changes, BigQuery estimates.
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id     BIGINT REFERENCES app_users (id) ON DELETE SET NULL,
    user_email  TEXT,
    action      TEXT NOT NULL,
    target      TEXT,
    detail      JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS audit_log_at_idx ON audit_log (at DESC);

-- Calls this API made to each outside data source, per day (see app/usage.py).
-- units: compute units for Alchemy, calls for the others.
CREATE TABLE IF NOT EXISTS api_usage (
    day     DATE NOT NULL,
    source  TEXT NOT NULL,
    kind    TEXT NOT NULL,
    calls   BIGINT NOT NULL DEFAULT 0,
    units   DOUBLE PRECISION NOT NULL DEFAULT 0,
    errors  BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (day, source, kind)
);
