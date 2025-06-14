FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

# Install system dependencies for Playwright browsers
RUN apt-get update && apt-get install -y \
    libx11-xcb1 \
    libxcursor1 \
    libgtk-3-0 \
    libgtk-4-1 \
    libpangocairo-1.0-0 \
    libcairo-gobject2 \
    libgdk-pixbuf2.0-0 \
    libx11-6 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libnss3 \
    libxss1 \
    libasound2 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libgbm1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libpango-1.0-0 \
    libxcb1 \
    libnspr4 \
    libdbus-1-3 \
    libxfixes3 \
    libgstreamer1.0-0 \
    libgstreamer-plugins-base1.0-0 \
    libatomic1 \
    libxslt1.1 \
    libvpx7 \
    libopus0 \
    libwebpdemux2 \
    libharfbuzz-icu0 \
    libenchant-2-2 \
    libsecret-1-0 \
    libhyphen0 \
    libmanette-0.2-0 \
    libgles2 \
    wget \
    curl \
    ca-certificates \
    fonts-liberation \
    fonts-noto-color-emoji \
    fonts-freefont-ttf \
    && rm -rf /var/lib/apt/lists/*

# Install Playwright browsers
RUN pip install --no-cache-dir playwright
RUN playwright install

# Copy your bot code
WORKDIR /app
COPY . /app

# Install python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Run your bot
CMD ["python", "main.py"]
