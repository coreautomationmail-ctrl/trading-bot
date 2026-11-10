# Copies what the morning thesis session reads from the VPS into scripts/.cache/thesis: its scored
# record (theses_summary.json, theses_scores.csv) and today's 08:20 ET pre-market snapshot. The
# session runs in the repo profile, which can't ssh, so this runs first: scheduled task
# "STRATTON thesis inputs", weekdays 05:25 PT, five minutes before the stratton-thesis order.
$dir = Join-Path $PSScriptRoot '.cache\thesis'
New-Item -ItemType Directory -Force $dir | Out-Null
Remove-Item (Join-Path $dir '*') -Force -ErrorAction SilentlyContinue
$ssh = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20')
scp -q @ssh postiz:/opt/stratton-bench/data/theses_summary.json postiz:/opt/stratton-bench/data/theses_scores.csv postiz:/opt/stratton-bench/data/theses_intraday_summary.json $dir
scp -q @ssh postiz:/opt/stratton-spike/data/missed/missed_summary.md $dir
$pre = ssh @ssh postiz 'ls -t /opt/stratton-spike/data/world/*-pre.json.gz | head -1'
if ($pre) { scp -q @ssh "postiz:$pre" (Join-Path $dir 'pre.json.gz') }
Get-Date -Format o | Set-Content -Encoding utf8 (Join-Path $dir 'synced_at.txt')
