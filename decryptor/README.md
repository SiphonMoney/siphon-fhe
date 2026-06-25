# GCP Confidential Space — siphon-decryptor

Runs the Rust decryptor on a SEV Confidential VM. Holds each user's `ClientKey` in VM memory;
decrypts only the FHE engine's encrypted result bit (`triggered: true|false`).

## Build & push (from repo root)

```bash
cd siphon-fhe/decryptor
docker build --platform linux/amd64 -t asia-south1-docker.pkg.dev/siphon-500509/siphon/decryptor:latest .
docker push asia-south1-docker.pkg.dev/siphon-500509/siphon/decryptor:latest
```

## VM (existing: `siphon-decryptor`, `10.160.0.3`)

Replace the placeholder hello-world container with the real image via Confidential Space
workload metadata, or run directly over SSH (IAP):

```bash
gcloud compute ssh siphon-decryptor --zone=asia-south1-c --tunnel-through-iap -- \
  'sudo docker pull asia-south1-docker.pkg.dev/siphon-500509/siphon/decryptor:latest && \
   sudo docker rm -f siphon-decryptor 2>/dev/null; \
   sudo docker run -d --name siphon-decryptor --restart unless-stopped \
     -p 5002:5002 \
     -e SIPHON_DEV_PLAINTEXT_KEY=1 \
     asia-south1-docker.pkg.dev/siphon-500509/siphon/decryptor:latest'
```

Health: `curl http://localhost:5002/health` (from inside the VM).

## Wire trade-executor

On the host that runs `trade-executor`, set:

```bash
DECRYPTOR_URL=http://10.160.0.3:5002   # same VPC / VPN only
```

**AWS EC2 cannot reach `10.160.0.3` without VPC peering.** Options:

1. Run trade-executor on GCP in the same VPC as the confidential VM, or
2. Cloud VPN / VPC peering between AWS and GCP.

Restart trade-executor after setting `DECRYPTOR_URL`. Scheduler logs should show
`Starting worker loop (TEE auto-execute)`.

## Frontend (Vercel)

```bash
NEXT_PUBLIC_TEE_AUTONOMOUS=true
```

Browser uploads client key via `POST /uploadClientKey` (proxied to decryptor). No browser
decrypt loop when this is enabled.

## API

| Method | Path | Caller |
|--------|------|--------|
| GET | `/health` | ops |
| GET | `/attestation` | browser (TODO: TDX quote) |
| GET | `/hasClientKey/:userId` | trade-executor proxy |
| POST | `/clientKey` | trade-executor proxy |
| POST | `/decrypt` | scheduler only (internal) |

## Security note

`SIPHON_DEV_PLAINTEXT_KEY=1` accepts hex ClientKeys over HTTP inside the private VPC.
Before mainnet: wire TDX attestation + HPKE sealing (`/attestation`), disable plaintext mode,
and never expose port 5002 to the public internet.
