FROM python:3.12-slim

# Install dependencies for Playwright browsers
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libxcomposite1 \
    libnss3 \
    libxdamage1 \
    libnspr4 \
    libdbus-1-3 \
    libxext6 \
    libatk1.0-0 \
    libxfixes3 \
    libatk-bridge2.0-0 \
    libxrandr2 \
    libcups2 \
    libgbm1 \
    libxcb1 \
    libpango-1.0-0 \
    libxkbcommon0 \
    libcairo2 \
    libatspi2.0-0 \
    libasound2 \
    libx11-6 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy your code and requirements
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Install Playwright browsers
RUN python -m playwright install

CMD ["python", "main.py"]
