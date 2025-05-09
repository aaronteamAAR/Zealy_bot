FROM python:3.10-slim

# Install Chrome with dependencies
RUN apt-get update && apt-get install -y \
    wget gnupg ca-certificates \
    libnss3 libgdk-pixbuf2.0-0 libgtk-3-0 libxss1 \
    && wget -q https://dl.google.com/linux/linux_signing_key.pub \
    && apt-key add linux_signing_key.pub \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && ln -s /usr/bin/google-chrome-stable /usr/bin/google-chrome \  # Create symlink
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
CMD ["python", "zealy_bot.py"]