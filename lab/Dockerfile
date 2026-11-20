FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl iproute2 nftables nginx openssh-server openssl procps netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
COPY aegis /app/aegis
RUN pip install --no-cache-dir /app \
    && pip install --no-cache-dir flask fastapi uvicorn
COPY lab/nginx.conf /etc/nginx/nginx.conf
WORKDIR /workspace
ENV PYTHONPATH=/workspace:/app
