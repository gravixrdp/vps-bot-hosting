FROM python:3.12-slim

WORKDIR /app

# Install minimal OS deps (kept empty for slim image)
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY . .

# Optional environment variables (override at runtime)
# BOT_TOKEN must be provided at runtime; do NOT bake tokens into images.
ENV LOG_LEVEL=INFO
ENV ALLOW_FILE=allowlist.json
ENV PREMIUM_FILE=premium.json
ENV USERS_FILE=users.json

# Ensure data files exist with correct schema if not mounted
RUN [ -f allowlist.json ] || echo '{"allow": []}' > allowlist.json; \
    [ -f premium.json ] || echo '{"premium": {}}' > premium.json; \
    [ -f users.json ] || echo '{"users": {}}' > users.json

# Run the bot
CMD ["python", "main.py"]
