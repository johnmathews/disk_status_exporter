FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends smartmontools udev \
 && pip install --no-cache-dir fastapi uvicorn \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY main.py /app/

EXPOSE 9635
# Healthcheck makes debugging deploys easier
HEALTHCHECK --interval=30s --timeout=2s --retries=3 CMD wget -qO- http://127.0.0.1:9635/metrics >/dev/null || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9635", "--workers", "1"]
