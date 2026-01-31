FROM python:3.9-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies for web3.py/cryptography builds
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc-dev \
    libffi-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN groupadd -r siphon && useradd -r -g siphon siphon

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Make entrypoint script executable and set ownership
RUN chmod +x entrypoint.sh && chown -R siphon:siphon /app

# Switch to non-root user
USER siphon

EXPOSE 5005

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5005/health || exit 1

# Use entrypoint script for DB init + start
ENTRYPOINT ["./entrypoint.sh"]