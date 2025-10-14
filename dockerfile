FROM python:3.11-slim

ARG VERSION=dev
ENV VERSION=${VERSION}
# Optional: reduce Python noise in containers
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.source="https://github.com/johnmathews/disk_status_exporter" \
      org.opencontainers.image.title="disk-status-exporter" \
      org.opencontainers.image.version="${VERSION}"

RUN apt-get update \
 && apt-get install -y --no-install-recommends smartmontools \
 && pip install --no-cache-dir fastapi uvicorn \
 && rm -rf /var/lib/apt/lists/*

COPY main.py /app/
WORKDIR /app

EXPOSE 9635
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9635"]
