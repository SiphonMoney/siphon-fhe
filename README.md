# Strategies Executor (Off-chain Services)

This folder contains the **off-chain services** that power encrypted strategy creation and evaluation for Siphon Money:

- **Browser FHE (`siphon-app/fhe-wasm`, Rust→WASM + tfhe-rs)**: generates the user's FHE keypair, encrypts strategy bounds, and decrypts results — all in the browser. The client (secret) key never leaves the device.
- **Trade Executor** (`trade-executor`, Python/Flask): stores encrypted strategies + the per-user server key, polls the oracle, asks the engine to compute the encrypted result, and runs browser-authorized on-chain execution.
- **FHE Engine** (`fhe`, Rust/Axum + tfhe-rs): performs homomorphic comparisons and returns the **encrypted** trigger result (it can no longer decrypt — it has no client key).
- **Payload Generator** (`siphon-payload-generator-demo`): **deprecated**, replaced by browser FHE. Kept for reference only.

---

## Architecture

### Components & ports (local defaults)

| Service | Port | Endpoint |
|---------|------|----------|
| Payload Generator | 5009 | `POST /generatePayload`, `GET /health` |
| Trade Executor | 5005 | `POST /createStrategy`, `GET /health` |
| FHE Engine | 5001 | `POST /evaluateStrategy`, `GET /health` |

### Data flow

1. **Browser** generates the FHE keypair (once, ~3s), stores the client key locally (IndexedDB), and uploads the server key once via `POST /uploadServerKey`.
2. User enters strategy parameters. The **browser encrypts the bounds locally** and posts ciphertext (no keys) to **Trade Executor** (`/createStrategy`). Plaintext bounds and the client key never leave the device.
3. Trade Executor persists the strategy. A background **scheduler** fetches prices from Pyth Hermes and, for each pending strategy, calls **FHE Engine** (`/evaluateStrategy` or `/evaluateTree`) with ciphertext + current price.
4. FHE Engine runs the homomorphic comparison and returns the **encrypted result** (it cannot decrypt). Trade Executor stores it and marks the strategy `ARMED`.
5. The **browser polls** the encrypted result, **decrypts it locally**, and if triggered calls `POST /executeStrategy`.
6. Trade Executor performs the **on-chain execution** on Solana.

> **Trade-off:** because the client key stays in the browser, the trigger is only acted on while the user's browser is open (browser-in-the-loop). The server never learns the bounds or the trigger bit.

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
