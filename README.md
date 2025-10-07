# Telegram Bot Hosting Platform

A Docker-based Telegram bot that allows users to host their own bots through a Telegram interface. It builds and runs user-submitted Python bots inside Docker containers and provides management tools (logs/stop/remove), a premium access system, and an optional Linux shell.

## Features
- Host multiple Python bots using Docker containers
- Admin panel for user management (allowlist and premium)
- Premium user system with expiry
- Linux shell access through Telegram (ephemeral, resource-limited)
- Automatic requirements detection from bot.py (AST + regex)
- Inline UI with copy support (fallback provided)

## Important Deployment Notes

This bot requires access to a Docker daemon at runtime to build and run user containers. Typical managed PaaS (like Render Web Service) does not expose a Docker daemon to your app. Use one of the following:
- Self-hosted VPS/VM/Bare-metal with Docker installed and mount `/var/run/docker.sock` into this bot container, or
- Configure a remote Docker host and expose it via `DOCKER_HOST` environment variable.

If you deploy on a platform without Docker daemon access, the hosting features (build/run containers) will not work.

## Quick Start (Self-hosted)

1. Clone this repository
2. Build the image: `docker build -t hostbot .`
3. Run with Docker socket mounted:
   ```
   docker run --name hostbot --restart unless-stopped -d \
     -e BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN \
     -e LOG_LEVEL=INFO \
     -v /var/run/docker.sock:/var/run/docker.sock \
     -v $(pwd)/data:/app \
     hostbot
   ```
   The mapping to `/app` will persist the JSON files.

## Environment Variables
- BOT_TOKEN: Your Telegram bot token (required)
- ALLOW_FILE: Path to allowed users file (default: allowlist.json)
- PREMIUM_FILE: Path to premium users file (default: premium.json)
- USERS_FILE: Path to users data file (default: users.json)
- LOG_LEVEL: Logging level (default: INFO)

## Usage
Send `/start` to your bot to begin using the hosting platform.
