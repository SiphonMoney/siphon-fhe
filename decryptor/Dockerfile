# siphon-decryptor — runs in a Phala Cloud confidential VM (dstack / Intel TDX, linux/amd64).
# Holds ClientKeys in enclave memory; decrypts only the FHE result bit.

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
# TDX enclave: keys should arrive sealed to the attested pubkey (TODO — see README §attestation).
# Until that's wired, the trade-executor forwards hex ClientKeys; the key still only lives in
# enclave memory and Phala's gateway is TLS. Disable this once /clientKey unseals.
ENV SIPHON_DEV_PLAINTEXT_KEY=1
EXPOSE 5002
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f "http://localhost:5002/health" || exit 1
CMD ["siphon-decryptor"]
