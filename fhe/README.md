# Syphon FHE Engine

This is the off-chain, Rust-based FHE (Fully Homomorphic Encryption) server for the Siphon Money protocol. It acts as a specialized co-processor responsible for privately evaluating user-defined trading strategies against live market data.

This server works with the Trade Executor backend, which handles user requests, manages strategies, fetches prices, and triggers on-chain execution.

## Features

- **Private Strategy Evaluation:** Uses the `tfhe-rs` library to homomorphically check if trading conditions are met without decrypting the user's secret price targets.
- **High Performance:** Built with Rust, Tokio, and Axum for a fast, safe, and concurrent architecture.
- **Modular Design:** Code is separated by concern into handlers and a cryptographic core.

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/evaluateStrategy` | POST | Evaluate encrypted strategy against current price |
| `/health` | GET | Health check |

## How to Run

### Using Cargo (for development)

1.  Navigate to this folder:
    ```bash
    cd strategies-executor/fhe
    ```

2.  Build and run the server in release mode for optimal performance:
    ```bash
    cargo run --release
    ```

3.  The server will start and listen on `http://localhost:5001`

### Using Docker

From `strategies-executor/`:

```bash
docker compose up --build fhe-engine
```
