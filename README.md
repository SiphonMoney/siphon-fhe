# Strategies Executor (Off-chain Services)

This folder contains the **off-chain services** that power encrypted strategy creation and evaluation for Siphon Money:

- **Payload Generator** (`siphon-payload-generator-demo`, Rust/Axum): generates FHE keys, encrypts strategy bounds, and forwards an encrypted payload.
- **Trade Executor** (`trade-executor`, Python/Flask): stores encrypted strategies, polls the oracle, orchestrates evaluation, and triggers on-chain execution.
- **FHE Engine** (`fhe`, Rust/Axum + tfhe-rs): performs homomorphic comparisons and returns whether a strategy is triggered.

---

## Architecture

### Components & ports (local defaults)

| Service | Port | Endpoint |
|---------|------|----------|
| Payload Generator | 5009 | `POST /generatePayload`, `GET /health` |
| Trade Executor | 5005 | `POST /createStrategy`, `GET /health` |
| FHE Engine | 5001 | `POST /evaluateStrategy`, `GET /health` |

### Data flow

1. **User enters plaintext** strategy parameters in the frontend (upper/lower bounds, amounts, recipient, ZK payload).
2. Frontend calls **Payload Generator** (`/generatePayload`).
3. Payload Generator:
   - Generates FHE keys.
   - Encrypts bounds and constructs an **encrypted payload**.
   - Forwards the encrypted payload to **Trade Executor** (`/createStrategy`).
4. Trade Executor:
   - Persists the strategy (encrypted fields stored in SQLite).
   - A background **scheduler** periodically fetches prices from Pyth Hermes.
   - For each pending strategy, calls **FHE Engine** (`/evaluateStrategy`) with ciphertext + current price.
5. FHE Engine:
   - Runs the homomorphic comparison.
   - Decrypts the comparison result.
   - Returns `{ "is_triggered": true|false }`.
6. If triggered, Trade Executor performs the **on-chain execution** on Solana.

---

## Quick start: Docker Compose

From `strategies-executor/`:

```bash
docker compose up --build
```

This starts all three services with the correct networking.

---

## Environment Variables

Create a `.env` file in `strategies-executor/` or `trade-executor/`:

```bash
# Database
DATABASE_URI="sqlite:///instance/strategies.db?timeout=30"

# FHE Engine (Docker uses service names)
FHE_ENGINE_URL="http://fhe-engine:5001/evaluateStrategy"

# Solana RPC
HELIUS_API_KEY="your_helius_key"
# OR: SOLANA_RPC_URL="https://api.devnet.solana.com"
SOLANA_NETWORK="devnet"

# Executor wallet (for on-chain transactions)
EXECUTOR_PRIVATE_KEY="your_base58_private_key"
```

---

## Manual start (dev)

### 1) Start FHE Engine (Rust)

```bash
cd strategies-executor/fhe
cargo run --release
```

Listens on: `http://localhost:5001`

### 2) Start Trade Executor (Python)

```bash
cd strategies-executor/trade-executor
pip install -r requirements.txt
python init_db.py
gunicorn --bind 0.0.0.0:5005 --workers 1 --timeout 3000 "app:app"
```

### 3) Start Payload Generator (Rust)

```bash
cd strategies-executor/siphon-payload-generator-demo
cargo run --release
```

Listens on: `http://localhost:5009`

---

## Health checks

```bash
curl http://localhost:5005/health
curl http://localhost:5001/health
curl http://localhost:5009/health
```

---

## Common pitfalls

- **Trade Executor can't reach the FHE Engine**: set `FHE_ENGINE_URL` (Docker: `http://fhe-engine:5001/evaluateStrategy`, local: `http://localhost:5001/evaluateStrategy`).
- **On-chain execution doesn't happen**: you need `HELIUS_API_KEY` (or `SOLANA_RPC_URL`) and `EXECUTOR_PRIVATE_KEY` for actual transactions.
