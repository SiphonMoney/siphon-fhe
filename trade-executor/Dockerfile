FROM python:3.9-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies for web3.py
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5005

# Initialize DB and start Gunicorn
CMD ["sh", "-c", "python init_db.py && gunicorn --bind 0.0.0.0:5005 --workers 1 --timeout 3000 'app:app'"]