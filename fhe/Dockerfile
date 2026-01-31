# FHE Engine Dockerfile - Optimized for AWS EC2 (x86_64)
FROM rust:latest AS builder

WORKDIR /usr/src/app

# Copy Cargo.toml first
COPY Cargo.toml ./

# Use a wildcard copy for Cargo.lock.
# This command says "Copy Cargo.lock if it exists, otherwise do nothing."
COPY Cargo.loc[k] ./

# Create a dummy main.rs to build dependencies (caching layer)
RUN mkdir src && echo "fn main() {}" > src/main.rs
RUN cargo build --release
RUN rm -rf src

# Now copy the actual source code
COPY . .

# Target x86-64-v3 for AWS EC2 instances (supports AVX2)
# This ensures compatibility with c5/c6i/m5/m6i instance types
ENV RUSTFLAGS="-C target-cpu=x86-64-v3"
RUN cargo build --release

# --- Runtime Stage ---
FROM debian:bookworm-slim

WORKDIR /usr/local/bin

# Install runtime dependencies and curl for health checks
RUN apt-get update && apt-get install -y \
    libssl-dev \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN groupadd -r siphon && useradd -r -g siphon siphon

# Copy the binary from the builder stage
COPY --from=builder /usr/src/app/target/release/syphon_fhe_rust .

# Set ownership
RUN chown siphon:siphon syphon_fhe_rust

# Switch to non-root user
USER siphon

EXPOSE 5001

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:5001/health || exit 1

# Run the binary
CMD ["./syphon_fhe_rust"]