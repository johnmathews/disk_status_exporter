FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends smartmontools ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir fastapi uvicorn

WORKDIR /app
COPY main.py /app/

EXPOSE 9635
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9635"]
