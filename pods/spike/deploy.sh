#!/bin/sh
# Deploy/refresh the STRATTON spike pod on the VPS (run from this folder):
#   ssh postiz 'mkdir -p /opt/stratton-spike/data' && scp Dockerfile spike_pod.py spike_scorecard.py missed_report.py missed_universe.json postiz:/opt/stratton-spike/ && ssh postiz 'sh -s' < deploy.sh
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
# Missed-opportunity report: its own container after the close (21:40 UTC is 16:40 or 17:40 ET).
cat > /etc/cron.d/stratton-missed <<'EOF'
40 21 * * 1-5 root docker run --rm --memory 192m --env-file /opt/stratton-spike/.env -v /opt/stratton-spike/data:/data stratton-spike python missed_report.py >> /opt/stratton-spike/data/missed.log 2>&1
EOF
