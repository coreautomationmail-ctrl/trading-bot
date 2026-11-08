#!/bin/sh
# Deploy/refresh the STRATTON shadow bench on the VPS (run from this folder):
#   ssh postiz 'mkdir -p /opt/stratton-bench/data' && scp Dockerfile bench.py golive.py ops.sh ladder.py strategies.py ../spike/missed_universe.json ../spike/spike_pod.py ../spike/spike_scorecard.py postiz:/opt/stratton-bench/ && ssh postiz 'sh -s' < deploy.sh
# Market data comes through the spike pod's keys (read only). Paper orders, for strategies the
# ladder has promoted, go to the main paper account (the trading listener's keys, exported as
# INCUB_*), idle since ORB paused. Telegram and the paper-URL check as the spike pod.
set -e
cd /opt/stratton-bench
{
  grep -E '^(ALPACA_BASE_URL|TELEGRAM_TOKEN|TELEGRAM_CHAT_ID)=' /opt/trading-listener/.env
  grep -E '^ALPACA_(API|SECRET)_KEY=' /opt/stratton-spike/.env
  sed -n 's/^ALPACA_API_KEY=/INCUB_ALPACA_API_KEY=/p; s/^ALPACA_SECRET_KEY=/INCUB_ALPACA_SECRET_KEY=/p' /opt/trading-listener/.env
} > .env
chmod 600 .env
[ "$(grep -c 'ALPACA_' .env)" = 5 ] || { echo ".env is missing a key"; exit 1; }
docker build -q -t stratton-bench .
docker rm -f stratton-bench 2>/dev/null || true
docker run -d --name stratton-bench --restart unless-stopped --memory 160m \
  --env-file .env -v /opt/stratton-bench/data:/data -v /opt/stratton-spike/data:/spike:ro stratton-bench
sleep 8
docker logs --tail 5 stratton-bench
# Operations record for the go-live case: after each session, the day's errors in the pod and the
# bench (22:55 Berlin, after the US close; ops.sh skips weekends and market holidays).
cat > /etc/cron.d/stratton-ops <<'EOF'
55 22 * * 1-5 root sh /opt/stratton-bench/ops.sh >> /opt/stratton-bench/data/ops.log 2>&1
EOF
