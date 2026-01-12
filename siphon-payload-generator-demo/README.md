# Siphon Payload Generator (`siphon-payload-generator-demo`)

The **Payload Generator** is a local Rust service that:

- receives **plaintext** strategy inputs (from the frontend),
- generates FHE keys and **encrypts** the strategy bounds,
- forwards the encrypted payload to the **Trade Executor** (`/createStrategy`).

It exists to keep heavy cryptography out of the browser, while ensuring plaintext bounds never reach the Trade Executor.

---

## API

- **`POST /generatePayload`**: `http://localhost:5009/generatePayload`

---

## Configuration

Copy the template and edit the values:

```bash
cp env.template .env
```

- **`API_TOKEN` (required)**: must match the Trade Executor `API_TOKEN`
- **`ORCHESTRATOR_URL` (recommended)**: defaults to `http://localhost:5005/createStrategy`
- **`MPC_SERVER_1_URL`, `MPC_SERVER_2_URL` (optional)**: enable MPC key-share storage if your MPC servers are running

---

## Run (local)

```bash
cargo run --release
```

Listens on `0.0.0.0:5009`.

---

## Run (Docker Compose)

From `strategies-executor/`:

```bash
docker compose up --build
```

In Docker, the orchestrator URL must be the service name:
- `ORCHESTRATOR_URL=http://trade-executor:5005/createStrategy` (set in `../docker-compose.yml`)

