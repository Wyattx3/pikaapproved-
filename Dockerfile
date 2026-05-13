FROM python:3.11-slim

# Install system deps for Playwright Chromium
RUN apt-get update && apt-get install -y \
    wget curl gnupg ca-certificates \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libasound2 \
    libpango-1.0-0 libcairo2 libx11-6 libxext6 \
    fonts-liberation libappindicator3-1 xdg-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium browser
RUN python -m playwright install chromium
RUN python -m playwright install-deps chromium

# Copy app files
COPY bot.py cooper.py bin-list-data.csv ./

# Session restore script — reads SESSION_B64 env var and writes session file
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]
