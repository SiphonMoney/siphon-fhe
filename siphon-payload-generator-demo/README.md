# Siphon Payload Generator

The **Payload Generator** is a local Rust service that:

- receives **plaintext** strategy inputs (from the frontend),
- generates FHE keys and **encrypts** the strategy bounds,
- forwards the encrypted payload to the **Trade Executor** (`/createStrategy`).

It exists to keep heavy cryptography out of the browser, while ensuring plaintext bounds never reach the Trade Executor.

---

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/generatePayload` | POST | Generate encrypted payload from plaintext strategy |
| `/health` | GET | Health check |

Default port: `5009`

---

## Configuration

Environment variables (optional):

```bash
# Where to forward the encrypted payload (defaults to localhost)
ORCHESTRATOR_URL="http://localhost:5005/createStrategy"
```

---

## Run (local)

```bash
cd strategies-executor/siphon-payload-generator-demo
cargo run --release
```

Listens on `0.0.0.0:5009`.

---

## Run (Docker Compose)

From `strategies-executor/`:

```bash
docker compose up --build
```

In Docker, the orchestrator URL uses the service name:
- `ORCHESTRATOR_URL=http://trade-executor:5005/createStrategy` (set in `docker-compose.yml`)
