# STRATTON weekday check-in: health check only (unattended, after the US close)

Since 2026-11-14 (owner): STRATTON research is closed and the Claude desks (thesis, intraday, flash,
research) are switched off. What still runs is code only: the paper spike pod and the bench logs. This
check-in only keeps those healthy. Be brief and cheap: no subagents, no research, no strategy work.

## Hard rules

Paper only. Never tune a pod on recent P&L. Never loosen a gate. No spending or new accounts. Never show
secrets or print .env values. Redeploy the pod or bench only while the market is closed and only for a fix.

## Steps

1. From the repo root run `bash scripts/stratton_daily.sh`. Its last line is `TRIGGERS: ...`.
2. Ignore every `desk-*` trigger (desk-quiet, desk-inactive, desk-futile, desk-branch, desk-pass,
   desk-forward-pass): the desks are off on purpose.
3. If nothing else is listed: end with one line, "STRATTON ok: pod and bench up", and stop.
4. For `vps-unreachable`, `pod-down`, `bench-down`, `pod-errors`, `bench-errors`, `recorder-errors`: read
   the container logs over `ssh postiz`, find the cause, and fix it only if the fix is safe and small (a
   restart, or a code bug with a test), redeploying only while the market is closed. Otherwise describe it.
   For `pod-paused`: the scorecard paused the pod by its fixed rule; don't unpause, report it.
5. Commit what you changed (never .env) and push to master.

## Final message (sent to the owner on Telegram)

At most 3 plain lines: what was wrong, what you did, anything waiting on the owner.
