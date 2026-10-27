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
