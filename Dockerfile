FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y \
    supervisor \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install all Python deps in one layer
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all packages
COPY packages/ packages/

# Supervisord config
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
