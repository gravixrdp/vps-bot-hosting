# Telegram Bot Hosting Platform

A Docker-based Telegram bot that allows users to host their own bots through a web interface.

## Features
- Host multiple Telegram bots using Docker containers
- Admin panel for user management
- Premium user system
- Linux shell access through Telegram
- Bot deployment automation

## Deployment on Render

1. Upload this repository to GitHub
2. Connect your GitHub repo to Render
3. Choose "Web Service" deployment type
4. Select "Docker" as environment
5. Set your bot token in environment variables
6. Deploy!

## Environment Variables
- BOTTOKEN: Your Telegram bot token
- ALLOWFILE: Path to allowed users file (default: allowlist.json)
- PREMIUMFILE: Path to premium users file (default: premium.json)
- USERSFILE: Path to users data file (default: users.json)
- LOGLEVEL: Logging level (default: INFO)

## Usage
Send /start to your bot to begin using the hosting platform.
