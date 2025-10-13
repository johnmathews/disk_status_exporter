FROM python:3.11-slim

RUN apt-get update && apt-get install -y smartmontools && \
    pip install fastapi uvicorn && \
    apt-get clean

COPY main.py /app/
WORKDIR /app

EXPOSE 9635
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9635"]
