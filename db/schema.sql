CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TYPE lifecycle_state AS ENUM ('CREATED', 'PROVISIONING', 'ACTIVE', 'SUSPENDED', 'DEPRECATED', 'ARCHIVED');
CREATE TYPE listing_status AS ENUM ('ACTIVE', 'SOLD', 'CANCELLED');
CREATE TYPE transaction_type AS ENUM ('SWAP', 'BRIDGE', 'YIELD_DEPOSIT', 'YIELD_WITHDRAW', 'NFT_TRADE', 'LENDING');
CREATE TYPE transaction_status AS ENUM ('PENDING', 'SUBMITTED', 'CONFIRMED', 'FAILED', 'REVERTED');
CREATE TYPE verifier_type AS ENUM ('NONE', 'TEE', 'ZKP');
CREATE TYPE agent_role AS ENUM ('PLANNER', 'MEMORY', 'SIMULATOR', 'EXECUTOR', 'CRITIC');
CREATE TYPE step_status AS ENUM ('STARTED', 'SUCCEEDED', 'FAILED', 'RETRIED');

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    wallet_address VARCHAR(42) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE,
    role VARCHAR(20) DEFAULT 'user',
    is_active BOOLEAN DEFAULT TRUE,
    siwe_nonce VARCHAR(32),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(64) UNIQUE NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    revoked BOOLEAN DEFAULT FALSE,
    ip_address VARCHAR(45),
    user_agent VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nft_id BIGINT UNIQUE NOT NULL,
    chain_id INTEGER DEFAULT 8453,
    owner_id UUID NOT NULL REFERENCES users(id),
    creator_wallet VARCHAR(42) NOT NULL,
    name VARCHAR(100) NOT NULL,
    persona TEXT,
    model_version VARCHAR(50) NOT NULL,
    metadata_uri VARCHAR(500) NOT NULL,
    endpoint VARCHAR(255),
    token_bound_account VARCHAR(42),
    capabilities BIGINT DEFAULT 0,
    state lifecycle_state DEFAULT 'CREATED',
    last_synced_block BIGINT,
    last_synced_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS nft_metadata (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id UUID UNIQUE NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    encrypted_data_hash VARCHAR(66) NOT NULL,
    token_uri VARCHAR(500) NOT NULL,
    verifier_contract VARCHAR(42),
    verifier_type verifier_type DEFAULT 'NONE',
    last_attestation_hash VARCHAR(66),
    last_attested_at TIMESTAMP,
    royalty_receiver VARCHAR(42),
    royalty_bps INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS orders_listings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id UUID NOT NULL REFERENCES agents(id),
    seller_id UUID NOT NULL REFERENCES users(id),
    buyer_id UUID REFERENCES users(id),
    price NUMERIC(36, 18) NOT NULL,
    status listing_status DEFAULT 'ACTIVE',
    protocol_fee_bps INTEGER DEFAULT 250,
    tx_hash VARCHAR(66),
    listed_at TIMESTAMP DEFAULT NOW(),
    sold_at TIMESTAMP,
    cancelled_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS transactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id UUID NOT NULL REFERENCES agents(id),
    tx_hash VARCHAR(66),
    chain_id INTEGER NOT NULL,
    tx_type transaction_type NOT NULL,
    status transaction_status DEFAULT 'PENDING',
    from_address VARCHAR(42) NOT NULL,
    to_address VARCHAR(42) NOT NULL,
    token_symbol VARCHAR(20),
    amount NUMERIC(36, 18),
    amount_usd NUMERIC(18, 2),
    gas_used INTEGER,
    gas_price_gwei NUMERIC(18, 9),
    policy_check_passed BOOLEAN,
    submitted_at TIMESTAMP,
    confirmed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS execution_history (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id UUID NOT NULL,
    agent_id UUID NOT NULL REFERENCES agents(id),
    transaction_id UUID REFERENCES transactions(id),
    role agent_role NOT NULL,
    status step_status DEFAULT 'STARTED',
    sequence INTEGER NOT NULL,
    retry_count INTEGER DEFAULT 0,
    input_summary TEXT,
    output_summary TEXT,
    error_message TEXT,
    estimated_gas INTEGER,
    estimated_slippage_bps INTEGER,
    risk_score INTEGER,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agents_owner ON agents(owner_id);
CREATE INDEX IF NOT EXISTS idx_agents_nft_id ON agents(nft_id);
CREATE INDEX IF NOT EXISTS idx_agents_state ON agents(state);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_transactions_agent ON transactions(agent_id);
CREATE INDEX IF NOT EXISTS idx_transactions_tx_hash ON transactions(tx_hash);
CREATE INDEX IF NOT EXISTS idx_transactions_status ON transactions(status);
CREATE INDEX IF NOT EXISTS idx_listings_agent ON orders_listings(agent_id);
CREATE INDEX IF NOT EXISTS idx_listings_status ON orders_listings(status);
CREATE INDEX IF NOT EXISTS idx_execution_history_task ON execution_history(task_id);
CREATE INDEX IF NOT EXISTS idx_execution_history_agent ON execution_history(agent_id);