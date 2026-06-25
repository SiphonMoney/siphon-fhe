# siphon-decryptor — runs on GCP Confidential Space / Phala TDX (linux/amd64).
# Holds ClientKeys in enclave/VM memory; decrypts only the FHE result bit.

FROM --platform=linux/amd64 rust:1.83-bookworm AS builder
WORKDIR /build
COPY Cargo.toml Cargo.lock* ./
COPY src ./src
RUN cargo build --release

FROM --platform=linux/amd64 debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /build/target/release/siphon-decryptor /usr/local/bin/siphon-decryptor
ENV PORT=5002
# Confidential VM: keys arrive sealed to attested pubkey (TODO). Until attestation is wired,
# the trade-executor forwards hex ClientKeys over the private VPC — never expose this publicly
# without TLS + network isolation.
ENV SIPHON_DEV_PLAINTEXT_KEY=1
EXPOSE 5002
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f "http://localhost:5002/health" || exit 1
CMD ["siphon-decryptor"]
