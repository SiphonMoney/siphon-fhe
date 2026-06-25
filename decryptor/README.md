# Phala Cloud (dstack / Intel TDX) — siphon-decryptor

Runs the Rust decryptor inside a **Phala Cloud confidential VM** (dstack, Intel TDX).
Holds each user's `ClientKey` in enclave memory; decrypts only the FHE engine's encrypted
result bit (`triggered: true|false`). The host/operator never sees decrypted strategy inputs.

> Design + threat model: [`../docs/PHALA_TEE_DECRYPTOR.md`](../docs/PHALA_TEE_DECRYPTOR.md).
> The service code is TEE-agnostic — only deployment changed from GCP Confidential Space to Phala.

## 1. Build & push the image (from repo root)

Phala pulls from any public registry — use Docker Hub or GHCR (no GCP Artifact Registry).

```bash
cd siphon-fhe/decryptor
# Docker Hub
docker build --platform linux/amd64 -t docker.io/<your-namespace>/siphon-decryptor:latest .
docker push docker.io/<your-namespace>/siphon-decryptor:latest
# (or GHCR: ghcr.io/<your-org>/siphon-decryptor:latest)
```

Pin by digest for a reproducible measurement: `docker buildx imagetools inspect ...` → use the
`@sha256:...` digest in `docker-compose.yml`.

## 2. Deploy to Phala Cloud

Point `image:` in `docker-compose.yml` at the tag/digest you pushed, then:

```bash
npm install -g phala
phala login                                   # paste API key from https://cloud.phala.network → API Tokens
phala deploy --compose ./docker-compose.yml --name siphon-decryptor --wait
# inspect / get the public gateway URL + the measurement to pin:
phala cvms list
phala cvms attestation siphon-decryptor       # TDX quote + RTMRs
```

Phala exposes each mapped port at a public TLS gateway URL, e.g.
`https://<app-id>-5002.dstack-prod5.phala.network`. **No VPC peering needed** (unlike the old
GCP setup) — `trade-executor` on AWS EC2 reaches it directly over HTTPS.

## 3. Wire trade-executor (AWS EC2)

```bash
DECRYPTOR_URL=https://<app-id>-5002.dstack-prod5.phala.network
```

Restart trade-executor. Scheduler logs should show `Starting worker loop (TEE auto-execute)`.

## 4. Frontend (Vercel)

```bash
NEXT_PUBLIC_TEE_AUTONOMOUS=true
# After attestation is wired (below), also pin the enclave measurement:
# NEXT_PUBLIC_DECRYPTOR_MEASUREMENT=<RTMR/compose-hash from `phala cvms attestation`>
```

Browser uploads the client key via `POST /uploadClientKey` (proxied through trade-executor to
the decryptor). No browser decrypt loop when this is enabled.

## API

| Method | Path | Caller |
|--------|------|--------|
| GET | `/health` | ops |
| GET | `/attestation` | browser (TODO: return dstack TDX quote) |
| GET | `/hasClientKey/:userId` | trade-executor proxy |
| POST | `/clientKey` | trade-executor proxy |
| POST | `/decrypt` | scheduler only (internal) |

## Security note & remaining work (attestation)

`SIPHON_DEV_PLAINTEXT_KEY=1` accepts hex `ClientKey`s over the connection — acceptable for a
**Base Sepolia PoC** because the key still only ever lives in TDX enclave memory and the gateway
is TLS, but it is **not** the full guarantee. Before mainnet, wire the attestation flow (see
`../docs/PHALA_TEE_DECRYPTOR.md` §Components and the `TODO(attestation)` markers in `src/main.rs`):

1. `GET /attestation` → fetch the **TDX quote from the dstack guest agent** (socket mounted at
   `/var/run/dstack.sock`, via the `dstack-sdk` crate) with `report_data = sha256(enclave_x25519_pubkey)`.
2. Browser **verifies the quote + pinned measurement** (`phala cvms attestation`), then **HPKE-seals**
   the `ClientKey` to the attested pubkey.
3. `POST /clientKey` **unseals** with the enclave private key; then disable `SIPHON_DEV_PLAINTEXT_KEY`.
