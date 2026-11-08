#!/bin/sh
# One line per US session in /opt/stratton-bench/data/ops.csv: date, errors = pod + bench log
# errors since the open, plus 1 if the pod saved no IEX archive that day. Read by golive.py.
D=$(TZ=America/New_York date +%F)
[ "$(TZ=America/New_York date +%u)" -gt 5 ] && exit 0
F=/opt/stratton-bench/data/ops.csv
[ -f "$F" ] || echo "date,errors,pod_errors,bench_errors,archived" > "$F"
grep -q "^$D," "$F" && exit 0
# a market holiday isn't a session
open=$(docker exec stratton-spike python -c "import spike_pod as p; print(int(any(c['date'] == '$D' for c in p.api('GET', '/v2/calendar?start=$D&end=$D'))))" 2>/dev/null || echo 1)
[ "$open" = 0 ] && exit 0
pod=$(docker logs --since 10h stratton-spike 2>&1 | grep -ciE "error|traceback")
bench=$(docker logs --since 10h stratton-bench 2>&1 | grep -ciE "error|traceback")
arch=0; [ -f /opt/stratton-spike/data/iex/$D.csv.gz ] && arch=1
echo "$D,$((pod + bench + 1 - arch)),$pod,$bench,$arch" >> "$F"
