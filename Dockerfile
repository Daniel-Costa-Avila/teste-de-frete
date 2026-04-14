FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=10000 \
    HOST=0.0.0.0 \
    DEBUG=0 \
    HEADLESS=1

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

EXPOSE 10000
CMD ["gunicorn", "-b", "0.0.0.0:10000", "wsgi:app", "--workers", "1", "--threads", "8", "--timeout", "300"]

