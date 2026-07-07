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

# Entrypoint: wait for Postgres, run Alembic migrations, then start supervisord.
# Keeps "deploy" and "migrate" as one step so a schema change can't ship
# without the migration that makes it work.
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

CMD ["/app/docker-entrypoint.sh"]
