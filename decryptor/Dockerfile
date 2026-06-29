# siphon-decryptor — runs in a Phala Cloud confidential VM (dstack / Intel TDX, linux/amd64).
# Holds ClientKeys in enclave memory; decrypts only the FHE result bit.
#
# Platform: builds for the HOST platform by default (native arm64 on Apple-Silicon dev machines,
# amd64 on CI/EC2). The Phala TDX runtime is linux/amd64 — build the deployment image with
# `docker build --platform=linux/amd64 .` (or `docker buildx build --platform=linux/amd64`).
# Do NOT hardcode --platform here, or local arm64 dev falls back to slow QEMU emulation.

FROM rust:1.83-bookworm AS builder
WORKDIR /build
COPY Cargo.toml Cargo.lock* ./
COPY src ./src
RUN cargo build --release

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /build/target/release/siphon-decryptor /usr/local/bin/siphon-decryptor
ENV PORT=5002
# TDX enclave: keys should arrive sealed to the attested pubkey (TODO — see README §attestation).
# Until that's wired, the trade-executor forwards hex ClientKeys; the key still only lives in
# enclave memory and Phala's gateway is TLS. Disable this once /clientKey unseals.
ENV SIPHON_DEV_PLAINTEXT_KEY=1
EXPOSE 5002
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f "http://localhost:5002/health" || exit 1
CMD ["siphon-decryptor"]
