# FIX 1: Use 'latest' to support new dependencies like rayon-core
FROM rust:latest AS builder

WORKDIR /usr/src/app

# FIX 2: Copy Cargo.toml first
COPY Cargo.toml ./

# FIX 3: Use a wildcard copy for Cargo.lock.
# This command says "Copy Cargo.lock if it exists, otherwise do nothing."
# It prevents the "file not found" error while still using the lockfile if available.
COPY Cargo.loc[k] ./

# Create a dummy main.rs to build dependencies (caching layer)
RUN mkdir src && echo "fn main() {}" > src/main.rs
RUN cargo build --release
RUN rm -rf src

# Now copy the actual source code
COPY . .

# Enable native CPU features for TFHE performance
ENV RUSTFLAGS="-C target-cpu=native"
RUN cargo build --release

# --- Runtime Stage ---
FROM debian:bookworm-slim

WORKDIR /usr/local/bin

# Install runtime dependencies
RUN apt-get update && apt-get install -y libssl-dev ca-certificates && rm -rf /var/lib/apt/lists/*

# Copy the binary from the builder stage
# IMPORTANT: Replace 'syphon_fhe_rust' with the actual name from your Cargo.toml!
COPY --from=builder /usr/src/app/target/release/syphon_fhe_rust .

EXPOSE 5001

# Run the binary
CMD ["./syphon_fhe_rust"]