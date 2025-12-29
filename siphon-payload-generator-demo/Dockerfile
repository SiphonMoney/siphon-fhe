# --- Build Stage ---
FROM rust:latest AS builder

WORKDIR /usr/src/app

# Copy manifest first to cache dependencies
COPY Cargo.toml ./

# Create a dummy main.rs to build dependencies and generate Cargo.lock
RUN mkdir src && echo "fn main() {}" > src/main.rs
RUN cargo build --release
RUN rm -rf src

# Now copy the actual source code
COPY . .

# Enable native CPU features for TFHE performance (AVX-512/AVX2)
ENV RUSTFLAGS="-C target-cpu=native"
RUN cargo build --release

# --- Runtime Stage ---
FROM debian:bookworm-slim

WORKDIR /usr/local/bin

# Install runtime dependencies (OpenSSL is common)
RUN apt-get update && apt-get install -y libssl-dev ca-certificates && rm -rf /var/lib/apt/lists/*

# Copy the binary from the builder stage
# IMPORTANT: Replace 'syphon-payload-server' with the exact name from your Cargo.toml [package] section!
COPY --from=builder /usr/src/app/target/release/syphon-payload-server .

# Expose the port (5009 is the chosen port for this service)
EXPOSE 5009

# Run the binary
CMD ["./syphon-payload-server"]