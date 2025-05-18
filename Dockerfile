FROM python:3.10-slim

# Install dependencies for Chrome
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    curl \
    gnupg \
    fonts-liberation \
    libnss3 \
    libxss1 \
    libasound2 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libu2f-udev \
    xdg-utils \
    unzip \
    chromium \
    chromium-driver

# Clean up apt cache
RUN rm -rf /var/lib/apt/lists/*

# Set environment variables for Chrome and ChromeDriver
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver
ENV PATH="${CHROME_BIN}:${CHROMEDRIVER_PATH}:${PATH}"
ENV IS_RENDER="true"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "zealy_bot.py"]