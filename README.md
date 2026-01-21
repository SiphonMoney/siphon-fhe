# Strategies Executor (Off-chain Services)

This folder contains the **off-chain services** that power encrypted strategy creation and evaluation for Siphon Money:

- **Payload Generator** (`siphon-payload-generator-demo`, Rust/Axum): generates FHE keys, encrypts strategy bounds, and forwards an encrypted payload.
- **Trade Executor** (`trade-executor`, Python/Flask): stores encrypted strategies, polls the oracle, orchestrates evaluation, and triggers on-chain execution.
- **FHE Engine** (`fhe`, Rust/Axum + tfhe-rs): performs homomorphic comparisons and returns whether a strategy is triggered.

Optional (external to this repo): **MPC servers** can hold key shares for threshold decryption.

---

## Architecture (How it works)

### Components & ports (local defaults)

- **Frontend (Next.js)**: `http://localhost:3000` (in repo root, outside this folder)
- **Payload Generator**: `http://localhost:5009`
  - `POST /generatePayload`
- **Trade Executor**: `http://localhost:5005`
  - `POST /createStrategy`
  - `GET /health`
- **FHE Engine**: `http://localhost:5001`
  - `POST /evaluateStrategy`

### Data flow

1. **User enters plaintext** strategy parameters in the frontend (upper/lower bounds, amounts, recipient, ZK payload).
2. Frontend calls **Payload Generator** (`/generatePayload`).
3. Payload Generator:
   - Generates FHE keys (optionally using MPC servers for distributed key storage).
   - Encrypts bounds and constructs an **encrypted payload**.
   - Forwards the encrypted payload to **Trade Executor** (`/createStrategy`), including `X-API-TOKEN`.
4. Trade Executor:
   - Persists the strategy (encrypted fields stored in SQLite).
   - A background **scheduler** periodically fetches prices from Pyth Hermes.
   - For each pending strategy, calls **FHE Engine** (`/evaluateStrategy`) with ciphertext + current price.
5. FHE Engine:
   - Runs the homomorphic comparison.
   - Decrypts the comparison result (MPC threshold decryption if `fhe_key_id` is present; legacy direct decryption if `encrypted_client_key` is present).
   - Returns `{ "is_triggered": true|false }`.
6. If triggered, Trade Executor performs the **on-chain execution** (requires RPC URL, contract address, executor key, ABI).

---

## Quick start (recommended): Docker Compose

From `strategies-executor/`:

```bash
docker compose up --build
```

This starts:
- `trade-executor` on `:5005`
- `fhe-engine` on `:5001`
- `payload-generator` on `:5009`

### Required environment variables

Docker Compose reads variables from your shell (or a `.env` file in the same folder as `docker-compose.yml`).

Minimum to run end-to-end:

```bash
# Auth (must match across services)
API_TOKEN="change-me"

# Trade Executor database
DATABASE_URI="sqlite:///strategies.db?timeout=30"

# FHE Engine endpoint used by Trade Executor
FHE_ENGINE_URL="http://fhe-engine:5001/evaluateStrategy"
```

Optional (only needed for on-chain execution after a trigger):

```bash
SEPOLIA_RPC_URL="..."
ENTRYPOINT_CONTRACT_ADDRESS="0x..."
EXECUTOR_PRIVATE_KEY="0x..."
ABI_PATH="Entrypoint.abi.json"
```

Optional (only needed for MPC mode):

```bash
MPC_SERVER_1_URL="http://localhost:8001"
MPC_SERVER_2_URL="http://localhost:8002"
```

### Health checks

```bash
curl http://localhost:5005/health
```

---

## Manual start (dev)

### 1) Start FHE Engine (Rust)

```bash
cd strategies-executor/fhe
cargo run --release
```

Listens on: `http://localhost:5001/evaluateStrategy`

### 2) Start Trade Executor (Python)

```bash
cd strategies-executor/trade-executor
python init_db.py
gunicorn --bind 0.0.0.0:5005 --workers 1 --timeout 3000 "app:app"
```

Requires at least:

```bash
export DATABASE_URI="sqlite:///strategies.db?timeout=20000"
export FHE_ENGINE_URL="http://localhost:5001/evaluateStrategy"
export API_TOKEN="change-me"
```

### 3) Start Payload Generator (Rust)

```bash
cd strategies-executor/siphon-payload-generator-demo
cp env.template .env
# edit .env to set API_TOKEN (and optionally ORCHESTRATOR_URL / MPC URLs)
cargo run --release
```

Listens on: `http://localhost:5009/generatePayload`

---

## Common pitfalls

- **Trade Executor can’t reach the FHE Engine**: set `FHE_ENGINE_URL` (Docker: `http://fhe-engine:5001/evaluateStrategy`, local: `http://localhost:5001/evaluateStrategy`).
- **401 Unauthorized**: ensure the same `API_TOKEN` is configured for **Trade Executor**, **Payload Generator**, and **FHE Engine**.
- **On-chain execution doesn’t happen**: you still need `SEPOLIA_RPC_URL`, `ENTRYPOINT_CONTRACT_ADDRESS`, and `EXECUTOR_PRIVATE_KEY` for actual swaps after a trigger.

