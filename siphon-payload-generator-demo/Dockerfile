# Payload Generator Dockerfile - Optimized for AWS EC2 (x86_64)
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
COPY --from=builder /usr/src/app/target/release/syphon-payload-server .

# Set ownership
RUN chown siphon:siphon syphon-payload-server

# Switch to non-root user
USER siphon

# Expose the port (5009 is the chosen port for this service)
EXPOSE 5009

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:5009/health || exit 1

# Run the binary
CMD ["./syphon-payload-server"]