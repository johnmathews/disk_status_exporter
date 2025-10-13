# Use the explicit Debian variant to avoid tag drift
FROM python:3.11-slim-bookworm

# Prevent tz/interactive prompts in CI
ENV DEBIAN_FRONTEND=noninteractive

# Enable contrib (and non-free/non-free-firmware for future safety),
# then install zfsutils + smartmontools
RUN set -eux; \
    sed -Ei 's/ main$/ main contrib non-free non-free-firmware/' /etc/apt/sources.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        zfsutils-linux \
        smartmontools; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY main.py /app/

RUN pip install --no-cache-dir fastapi uvicorn

EXPOSE 9635
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9635"]
