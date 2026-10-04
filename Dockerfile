FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

WORKDIR /app

# Install system dependencies for ddddocr
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Ensure Playwright Chromium installed
RUN playwright install chromium && playwright install-deps chromium

# Copy app
COPY main.py .

# Environment
ENV PYTHONUNBUFFERED=1
ENV PORT=10000

EXPOSE 10000

CMD gunicorn main:app --bind 0.0.0.0:$PORT --timeout 300 --workers 1 --threads 4 --preload
