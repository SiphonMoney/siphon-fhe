# Trade Executor (`trade-executor`)

The **Trade Executor** is the Python backend that:

- receives **encrypted** strategies from the Payload Generator (`POST /createStrategy`)
- stores strategies in **SQLite** (encrypted/compressed fields handled in the model layer)
- runs a background **scheduler** that fetches live prices and evaluates pending strategies via the Rust **FHE Engine**
- when a strategy triggers, optionally performs **on-chain execution** (requires RPC + contract config)

For the full multi-service architecture, see `../README.md`.

---

## Service API

- `GET /health` (no auth)
- `POST /createStrategy` (requires `X-API-TOKEN`)

Default port: `5005`

---

## Running the full stack (recommended)

From `strategies-executor/`:

```bash
docker compose up --build
```

This starts:
- Trade Executor: `http://localhost:5005`
- FHE Engine: `http://localhost:5001/evaluateStrategy`
- Payload Generator: `http://localhost:5009/generatePayload`

---

## Run locally (dev)

### Prereqs

- Python 3.9+
- Rust toolchain

### 1) Trade Executor (Python)

```bash
cd strategies-executor/trade-executor
pip install -r requirements.txt
python init_db.py
gunicorn --bind 0.0.0.0:5005 --workers 1 --timeout 3000 "app:app"
```

Minimum environment:

```bash
export DATABASE_URI="sqlite:///strategies.db?timeout=20000"
export FHE_ENGINE_URL="http://localhost:5001/evaluateStrategy"
export API_TOKEN="change-me"
```

### 2) FHE Engine (Rust)

```bash
cd strategies-executor/fhe
export API_TOKEN="change-me"
cargo run --release
```

### 3) Payload Generator (Rust)

```bash
cd strategies-executor/siphon-payload-generator-demo
cp env.template .env
# set API_TOKEN=change-me (and optionally ORCHESTRATOR_URL / MPC URLs)
cargo run --release
```

---

## On-chain execution (optional)

If you want the Trade Executor to actually submit transactions after a trigger, you must configure:

```bash
export SEPOLIA_RPC_URL="..."
export ENTRYPOINT_CONTRACT_ADDRESS="0x..."
export EXECUTOR_PRIVATE_KEY="0x..."
export ABI_PATH="Entrypoint.abi.json"
```

Without these, the service can still accept/store strategies and run evaluations, but trade execution will be skipped/fail when triggered.

---

## Quick checks

```bash
curl http://localhost:5005/health
```

If you see `401 Unauthorized` on `/createStrategy`, make sure:
- Payload Generator sends `X-API-TOKEN`
- `API_TOKEN` matches across Trade Executor + Payload Generator + FHE Engine