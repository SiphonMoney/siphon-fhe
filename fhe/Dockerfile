# --- Build Stage ---
FROM rust:1.77 as builder

WORKDIR /usr/src/app

# Copy manifests first to cache dependencies
COPY Cargo.toml Cargo.lock ./

# Create a dummy main.rs to force cargo to build dependencies only
# This speeds up future builds
RUN mkdir src && echo "fn main() {}" > src/main.rs
RUN cargo build --release
RUN rm -rf src

# Now copy the actual source code
COPY . .

# Build the application
# CRITICAL: We enable native CPU features for TFHE performance (AVX-512/AVX2)
ENV RUSTFLAGS="-C target-cpu=native"
RUN cargo build --release

# --- Runtime Stage ---
FROM debian:bookworm-slim

WORKDIR /usr/local/bin

# Install runtime dependencies (OpenSSL is commonly needed)
RUN apt-get update && apt-get install -y libssl-dev ca-certificates && rm -rf /var/lib/apt/lists/*

# Copy the binary from the builder stage
# REPLACE 'syphon_fhe_rust' with the actual name of your binary from Cargo.toml
COPY --from=builder /usr/src/app/target/release/syphon_fhe_rust .

# Expose the port
EXPOSE 5001

# Run the binary
CMD ["./syphon_fhe_rust"]