# Copies the spike pod's missed-opportunity rollup and IEX archive from the VPS into scripts/.cache,
# where the research desk reads them. Scheduled task "STRATTON report sync", daily before the desk.
$cache = Join-Path $PSScriptRoot '.cache'
New-Item -ItemType Directory -Force (Join-Path $cache 'iex_live') | Out-Null
scp -q -o BatchMode=yes -o ConnectTimeout=20 postiz:/opt/stratton-spike/data/missed/missed_summary.md (Join-Path $cache 'missed_summary.md')
scp -q -o BatchMode=yes -o ConnectTimeout=20 'postiz:/opt/stratton-spike/data/iex/*' (Join-Path $cache 'iex_live')
