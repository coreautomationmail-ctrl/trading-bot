#!/bin/sh
# Deploy/refresh the STRATTON shadow bench on the VPS (run from this folder):
#   ssh postiz 'mkdir -p /opt/stratton-bench/data' && scp Dockerfile bench.py golive.py ops.sh ladder.py strategies.py thesis.py intraday_book.py flash_shadow.py registry.py forward_registry.json ../spike/missed_universe.json ../spike/spike_pod.py ../spike/spike_scorecard.py postiz:/opt/stratton-bench/ && ssh postiz 'sh -s' < deploy.sh
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
# Thesis pod (thesis.py): seal the morning's theses from GitHub every 5 min before the open (the
# script checks the 08:00-09:29 ET window itself, so the weeks when Berlin and New York change
# clocks apart don't matter), and score them after the close. Intraday book (intraday_book.py): seal
# each check-in's file when it lands (every 2 min; the script checks its windows), flash-desk files every
# minute from a git fetch (09:35-15:55 ET, checked by the script), score after the close, then every
# screener flag against Vision's flash ledger (score_flags).
cat > /etc/cron.d/stratton-thesis <<'EOF'
*/5 13-15 * * 1-5 root docker run --rm --memory 128m --env-file /opt/stratton-bench/.env -v /opt/stratton-bench/data:/data stratton-bench python thesis.py seal >> /opt/stratton-bench/data/thesis.log 2>&1
5 23 * * 1-5 root docker run --rm --memory 192m --env-file /opt/stratton-bench/.env -v /opt/stratton-bench/data:/data -v /opt/stratton-spike/data:/spike:ro -v /opt/vision/data/stratton/flash:/vision:ro stratton-bench sh -c "python thesis.py score; python intraday_book.py score; python intraday_book.py score_flags; python flash_shadow.py" >> /opt/stratton-bench/data/thesis.log 2>&1
*/2 15-21 * * 1-5 root docker run --rm --memory 128m --env-file /opt/stratton-bench/.env -v /opt/stratton-bench/data:/data stratton-bench python intraday_book.py seal >> /opt/stratton-bench/data/thesis.log 2>&1
* 14-22 * * 1-5 root docker run --rm --memory 128m --env-file /opt/stratton-bench/.env -v /opt/stratton-bench/data:/data stratton-bench python intraday_book.py seal_flash >> /opt/stratton-bench/data/thesis.log 2>&1
EOF
