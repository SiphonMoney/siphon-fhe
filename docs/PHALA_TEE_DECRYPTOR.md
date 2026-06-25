# Siphon — Phala TEE Decryptor (autonomous + private execution)

**Goal:** strategies execute **autonomously server-side** while the **plaintext strategy
parameters never leave the user's control** — the operator only ever learns a 1-bit
`true/false` trigger result. Achieved by moving the FHE **result decryption** into a Phala
TEE (Intel TDX via `dstack`); the heavy FHE comparison stays homomorphic on the untrusted host.

> ⚠️ **Mainnet prerequisites (outrank this feature):**
> 1. **Production Groth16 ceremony** — current `circuit_final.zkey` is a *dev* contribution over
>    Hermez `pot16`. Toxic waste is known → forged withdrawal proofs → total fund loss. Run a
>    real multi-party ceremony, regenerate `WithdrawalVerifier.sol`, redeploy. **Hard gate.**
> 2. Registry → NF-fixed Base entrypoint `0x3f931B3b52dcCf515F3eAfeE30c2442B12978F8A`.
> 3. External audit of contracts + this TEE/FHE execution path.
> 4. Constrain the executor hot key (`EVM_EXECUTOR_KEY`).

---

## Why the TEE is required (the core constraint)

Single-key TFHE: the key that decrypts the **result** also decrypts the **inputs**. So to act on
the result autonomously, *some* component must hold the `ClientKey` and decrypt — and that
component must be trusted not to read the inputs. The TEE is that trusted component:

- `ServerKey` (eval key) → on the untrusted host's `fhe-engine`; computes `price ≥ trigger`
  homomorphically; **cannot decrypt** (unchanged from today).
- `ClientKey` (secret key) → **only inside the attested TEE**; decrypts **only the result bit**.

Operator sees: the encrypted inputs (opaque) + the boolean. Never the trigger/amounts.

---

## Components

### 1. `siphon-decryptor` (new) — runs in Phala TDX (dstack)
A small Rust (tfhe-rs `integer`) service. TCB = "hold ClientKey, decrypt one RadixCiphertext bit".

**API**
| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/attestation` | Returns the TDX quote. `report_data = sha256(enclave_x25519_pubkey)`. Client verifies the quote + measurement, then trusts `pubkey`. |
| `POST` | `/clientKey` | Body: `{ userId, sealedClientKey }` — ClientKey encrypted (e.g. HPKE/x25519) to the enclave's attested pubkey. Enclave decrypts → stores in-memory (optionally sealed to dstack KMS for persistence across restarts). Never written to host disk in plaintext. |
| `POST` | `/decrypt` | Body: `{ userId, encryptedResultHex }` — enclave deserializes the `RadixCiphertext`, decrypts with that user's `ClientKey`, returns `{ "triggered": true|false }`. **Only output is the bit.** |
| `GET`  | `/health` | liveness |

**Hardening rules**
- The enclave program decrypts **only** result ciphertexts via `/decrypt`. It exposes **no**
  endpoint that returns decrypted *inputs*. (Even though the key technically could, the code path
  doesn't exist → minimal surface.)
- ClientKey held in memory only; if persistence needed, seal via dstack KMS (key bound to the
  enclave measurement), never to the host FS.
- Rate-limit `/decrypt` per user; reject malformed ciphertexts.

### 2. `fhe-engine` (existing) — unchanged
Still does the homomorphic compare and returns the **encrypted** result. No key. Stays on the
untrusted host. (Defense in depth: even an enclave compromise can't see inputs from *this* path.)

### 3. `trade-executor` scheduler — change
Today it only **arms** (stores `encrypted_result`). New flow per cycle for ARMED strategies:
1. `enc_result = fhe_engine.evaluate(encrypted_bounds, price, server_key)` (as now)
2. `triggered = decryptor.decrypt(userId, enc_result)`  ← NEW (calls the TEE)
3. `if triggered:` submit the **stored ZK proof** (withdraw+swap) via `evm_executor` using
   `EVM_EXECUTOR_KEY`; set status `EXECUTED`, store `tx_hash`. Else keep ARMED.

No browser required after submit. The decryptor URL is internal config
(`DECRYPTOR_URL`), called over the Docker/VPC network or mTLS to the Phala app.

### 4. Frontend — change
- On strategy submit (or first run), **fetch `/attestation`**, **verify** the TDX quote +
  expected measurement (pin the enclave image digest), then **encrypt the ClientKey to the
  enclave pubkey** and `POST /clientKey`. The ClientKey leaves the device **only** sealed to a
  verified enclave — never to the host.
- Remove the browser auto-execute/poll path (already done). Submit → status-only Runs view.

---

## Deployment (Phala Cloud / dstack)

1. **Containerize** `siphon-decryptor` (Dockerfile, tfhe-rs release build).
2. **dstack app**: a `docker-compose.yml` deployed to Phala Cloud (TDX). The dstack guest agent
   exposes attestation (TDX quote) over the in-VM socket; the service binds `report_data` to its
   x25519 pubkey.
3. **Pin the measurement**: record the enclave image digest / RTMRs; the frontend verifies the
   quote against this pin before sending the key. (Reproducible build so the measurement is
   auditable.)
4. **Networking**: expose only `/attestation`, `/clientKey`, `/decrypt`, `/health` over TLS.
   `trade-executor` calls `/decrypt`; browser calls `/attestation` + `/clientKey`.
5. **Config**: `trade-executor` env `DECRYPTOR_URL=https://<phala-app>`; frontend env
   `NEXT_PUBLIC_DECRYPTOR_URL` + pinned `NEXT_PUBLIC_DECRYPTOR_MEASUREMENT`.

---

## Threat model (what this does / doesn't protect)

| Adversary | Protected? |
|-----------|-----------|
| Operator / host root reading memory | ✅ key + inputs are inside TDX, not host-readable |
| Operator reading DB / logs | ✅ DB holds only ciphertext + the boolean outcome |
| Public chain observer | ✅ on-chain sees only the swap; trigger never on-chain |
| Enclave compromise (TDX break / side-channel) | ❌ key leaks → inputs exposed. Mitigate: attestation pin, reproducible build, keep TCB tiny, rotate keys, monitor advisories |
| Malicious/forged proofs | ❌ **out of scope here** — that's the ZK ceremony (mainnet gate #1) |

**Upgrade path (max trust-min):** replace the single-enclave decryptor with **threshold
decryption** (Zama TKMS / MPC) or **threshold-across-TEEs** so no single enclave/key holder
exists. Bigger build; do after launch if hardware trust becomes unacceptable.

---

## Build order
1. `siphon-decryptor` service (Rust/tfhe-rs) + Dockerfile + local test against a sample
   ClientKey/result ciphertext. *(no Phala account needed yet)*
2. dstack compose + deploy to Phala Cloud; capture measurement.
3. Frontend: attestation verify + sealed ClientKey upload.
4. Scheduler: call `/decrypt`, execute on `true`.
5. End-to-end on **Base Sepolia** first; only then mainnet (after the ceremony + audit).
