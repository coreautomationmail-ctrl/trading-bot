#!/bin/sh
# Deploy/refresh the STRATTON spike pod on the VPS (run from this folder):
#   ssh postiz 'mkdir -p /opt/stratton-spike/data' && scp Dockerfile spike_pod.py spike_scorecard.py postiz:/opt/stratton-spike/ && ssh postiz 'sh -s' < deploy.sh
# Trades its own paper account (keys in /opt/stratton-spike/keys.env as
# SPIKE_ALPACA_API_KEY / SPIKE_ALPACA_SECRET_KEY, written by the user), so its
# positions and P&L never mix with the ORB bot's. Base URL + Telegram come
# from the trading listener's .env; state in /opt/stratton-spike/data.
set -e
cd /opt/stratton-spike
{
  grep -E '^(ALPACA_BASE_URL|TELEGRAM_TOKEN|TELEGRAM_CHAT_ID)=' /opt/trading-listener/.env
  sed -n 's/^SPIKE_ALPACA_API_KEY=/ALPACA_API_KEY=/p; s/^SPIKE_ALPACA_SECRET_KEY=/ALPACA_SECRET_KEY=/p' keys.env
} > .env
chmod 600 .env
[ "$(grep -c '^ALPACA_' .env)" = 3 ] || { echo "keys.env missing a key"; exit 1; }
docker build -q -t stratton-spike .
docker rm -f stratton-spike 2>/dev/null || true
docker run -d --name stratton-spike --restart unless-stopped --memory 192m \
  --env-file .env -v /opt/stratton-spike/data:/data stratton-spike
sleep 5
docker logs --tail 5 stratton-spike
