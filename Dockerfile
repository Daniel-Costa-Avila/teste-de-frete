FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    HOST=0.0.0.0 \
    DEBUG=0 \
    HEADLESS=1 \
    CHROME_BINARY_PATH=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver

# Chromium + driver (Debian)
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    chromium chromium-driver \
    ca-certificates fonts-liberation \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080
CMD ["gunicorn", "-b", "0.0.0.0:8080", "app.web.server:create_app()", "--workers", "1", "--threads", "8", "--timeout", "300", "--access-logfile", "-", "--error-logfile", "-"]
