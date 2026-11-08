#!/bin/bash
# STRATTON's daily check: everything worth knowing in under 40 lines, no LLM needed.
# Ends with a TRIGGERS line; "none" means there is nothing to think about today.
cd "$(dirname "$0")/.." || exit 1
triggers=""

echo "== VPS"
vps=$(ssh -o ConnectTimeout=15 postiz 'cd /opt/stratton-spike/data
docker ps --filter name=stratton-spike --format "pod: {{.Status}}"
D=$(TZ=America/New_York date +%F)
echo "errors today: $(docker logs --since 24h stratton-spike 2>&1 | grep -ciE "error|traceback")"
echo "shadow timeouts today: $(docker logs --since 24h stratton-spike 2>&1 | grep -c "^shadow:")"
echo "pod trades today: $(grep -c "$D" spike_trades.csv)  all: $(($(wc -l < spike_trades.csv) - 1))"
echo "shadow signals today: $(grep -c "^$D" spike_shadow.csv)  all: $(($(wc -l < spike_shadow.csv) - 1))"
[ -f spike_control.json ] && echo "control: $(cat spike_control.json)"
ls iex | tail -1 | sed "s/^/last archive: /"
tail -2 world.log 2>/dev/null
tail -1 missed.log 2>/dev/null | cut -c1-120
docker ps --filter name=stratton-bench --format "bench: {{.Status}}"
echo "bench errors today: $(docker logs --since 24h stratton-bench 2>&1 | grep -ciE "error|traceback")"
[ -f /opt/stratton-bench/data/bench_virtual.csv ] && echo "bench virtual today: $(grep -c ",$D," /opt/stratton-bench/data/bench_virtual.csv)  all: $(($(wc -l < /opt/stratton-bench/data/bench_virtual.csv) - 1))"
[ -f /opt/stratton-bench/data/bench_paper.csv ] && echo "bench paper trades all: $(($(wc -l < /opt/stratton-bench/data/bench_paper.csv) - 1))"
python3 -c "import json; print(\"ladder: \" + \", \".join(k + \" \" + v[\"stage\"] for k, v in json.load(open(\"/opt/stratton-bench/data/ladder.json\")).items()))" 2>/dev/null') || { vps="VPS UNREACHABLE"; triggers="$triggers vps-unreachable"; }
echo "$vps"
echo "$vps" | grep -q "^pod: Up" || triggers="$triggers pod-down"
echo "$vps" | grep -q "^bench: Up" || triggers="$triggers bench-down"
echo "$vps" | grep -Eq "^bench errors today: [1-9]" && triggers="$triggers bench-errors"
echo "$vps" | grep -q '"paused": true' && triggers="$triggers pod-paused"
echo "$vps" | grep -Eq "^errors today: [1-9]" && triggers="$triggers pod-errors"
echo "$vps" | grep -E "errors \{" | head -2 | grep -q . && triggers="$triggers recorder-errors"

echo "== Research desk"
git fetch -q origin 2>/dev/null
unmerged=$(git branch -a --no-merged master --list 'vision/task-*' '*/vision/task-*' | sed 's/^[*+ ]*//')
echo "unmerged desk branches: ${unmerged:-none}"
[ -n "$unmerged" ] && triggers="$triggers desk-branch"
grep -nE '^## ' research/journal.md | tail -1 | cut -c1-110
last=$(git log -1 --format=%cr -- research/journal.md)
echo "journal last changed: $last"
git log --since=30.hours --format=%s -- research/journal.md | grep -q . || triggers="$triggers desk-quiet"
git log --since=30.hours --format=%s -- research/journal.md | grep -q 'PASS' && triggers="$triggers desk-pass"
[ -n "$unmerged" ] && git log --format=%s "$(echo "$unmerged" | head -1)" -3 -- research/journal.md | grep -q 'PASS' && triggers="$triggers desk-pass"

echo "== Repo"
git status --short | head -5
echo "TRIGGERS:${triggers:- none}"
