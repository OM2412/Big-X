# syntax=docker/dockerfile:1
FROM python:3.10-slim AS base
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev libffi-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --retries 10 --timeout 60 -r requirements.txt
