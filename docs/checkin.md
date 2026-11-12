# STRATTON weekday check-in (unattended, after the US close)

Vision starts you on weekdays at 14:17 Pacific. You are STRATTON's operator for the day: keep
it running, act on what the day produced, and tell the owner only what matters. Keep it cheap:
no subagents, and stop early when there is nothing to do.

## Hard rules

Paper only. Never tune a live pod on recent P&L. Never loosen a gate or a go-live check. No
spending or new accounts. Never show secrets or print .env values. The live spike pod is
redeployed only when the market is closed and only for a fix, never to change its rules.

## Steps

1. From the repo root run `bash scripts/stratton_daily.sh`. Its last line is `TRIGGERS: ...`.
2. If it says `none`: end with a 2-line status (pod and bench up, trades today) and stop.
3. Otherwise handle each trigger:
   - `vps-unreachable`, `pod-down`, `bench-down`, `pod-errors`, `bench-errors`,
     `recorder-errors`: read the container logs over `ssh postiz`, find the cause, and fix it
     if the fix is safe and small (restart, a code bug with a test). Redeploy with the pod's or
     bench's deploy.sh only while the market is closed. Otherwise describe it for the owner.
   - `pod-paused`: the scorecard paused the pod by its fixed rule. Don't unpause; report it.
   - `desk-branch`: review the branch. Merge it to master only if it touches nothing outside
     research/ and scripts/research/, and its verdict follows the pre-registered gate.
   - `desk-pass`: a strategy passed. Check the journal entry: pre-registered before the run,
     the gate met, and a held-out result reported. If all true, prepare it on a branch
     `bench/<strategy name>` (never master): a Strategy in pods/bench/strategies.py at STAGE
     "shadow", with BACKTEST_NET_BPS and HOLD_MIN from the journal and the universe it was tested
     on (WIDE for the 277 names), plus a test in tests/test_bench.py; run the tests; push the
     branch. Do NOT merge it and do NOT deploy the bench: the owner decides (owner, 2026-11-12).
     Say in your final message what passed, the numbers, and that branch `bench/<name>` is ready
     for their yes. If something doesn't check out, don't prepare it; say why.
   - `desk-quiet`: the desk didn't run. Check Vision's task list for the failure and report.
   - `desk-inactive`: a Claude desk (thesis, intraday or flash) has traded too little to ever reach a
     verdict (pods/bench/thesis.py INACTIVE). Report it with the numbers; don't change its instructions.
     Its one rewrite a quarter is the owner's call (pods/bench/registry.py).
   - `desk-futile`: a desk tripped its early stop (day t vs random <= 0 after 100 trades, 60 for
     flash). Report it; the owner decides whether to switch its standing order off.
   - `desk-forward-pass`: a desk passed its registered forward test. Report it first, with n, days and
     day t against the bar; nothing goes live from a virtual pass (docs/stratton-revamp-plan.md).
4. Commit what you changed (never .env) and push to master.

## Final message (sent to the owner on Telegram)

At most 6 plain lines: what happened, what you did, anything waiting on the owner. No jargon
without a short gloss.
