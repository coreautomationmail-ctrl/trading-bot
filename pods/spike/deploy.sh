#!/bin/sh
# Deploy/refresh the STRATTON spike pod on the VPS (run from this folder):
#   ssh postiz 'mkdir -p /opt/stratton-spike/data' && scp Dockerfile spike_pod.py postiz:/opt/stratton-spike/ && ssh postiz 'sh -s' < deploy.sh
# Reuses the trading listener's paper keys + Telegram (.env); state in /opt/stratton-spike/data.
set -e
cd /opt/stratton-spike
grep -E '^(ALPACA_API_KEY|ALPACA_SECRET_KEY|ALPACA_BASE_URL|TELEGRAM_TOKEN|TELEGRAM_CHAT_ID)=' /opt/trading-listener/.env > .env
chmod 600 .env
docker build -q -t stratton-spike .
docker rm -f stratton-spike 2>/dev/null || true
docker run -d --name stratton-spike --restart unless-stopped --memory 192m \
  --env-file .env -v /opt/stratton-spike/data:/data stratton-spike
sleep 5
docker logs --tail 5 stratton-spike
