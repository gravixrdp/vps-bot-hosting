FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY . .

# Set environment variables (you can override these in Render)
ENV BOTTOKEN=8318430595:AAFtbJVxIbHIQxtmNwZPgXx68wnhVJuDuhk
ENV ALLOWFILE=allowlist.json
ENV PREMIUMFILE=premium.json
ENV USERSFILE=users.json
ENV LOGLEVEL=INFO

# Create necessary JSON files if they don't exist
RUN echo '[]' > allowlist.json && \
    echo '[]' > premium.json && \
    echo '{}' > users.json

# Run the bot
CMD ["python", "bot.py"]
