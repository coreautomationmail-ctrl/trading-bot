"""Intraday book (pods/bench/intraday_book.py): flash files sealed from a git fetch, and the flash screener's
trigger (pods/spike/flash_screener.py)."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "pods", "spike"))
sys.path.insert(0, os.path.join(ROOT, "pods", "bench"))

import flash_screener as fs  # noqa: E402
import intraday_book as ib  # noqa: E402
import thesis  # noqa: E402
from spike_pod import ET  # noqa: E402

SYM = thesis.UNIVERSE[0]


def trade(code, **kw):
    return {"id": f"2026-11-10-{code}-1", "symbol": SYM, "direction": "long", "exit_by": "15:55", "stop_pct": 1.0, **kw}


def test_flash_codes_validate_against_the_flag_minute():
    assert ib.valid(trade("F1032-AMD"), "2026-11-10", "F1032-AMD") == ""
    assert "20+ minutes" in ib.valid(trade("F1032-AMD", exit_by="10:45"), "2026-11-10", "F1032-AMD")
    assert ib.valid(trade("F1032-AMD", exit_by="10:52"), "2026-11-10", "F1032-AMD") == ""
    assert ib.valid(trade("1030"), "2026-11-10", "1030") == ""


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_seal_flash_seals_new_files_once_and_only_today(tmp_path, monkeypatch):
    src = tmp_path / "src"
    (src / "research" / "theses").mkdir(parents=True)
    _git(src, "init", "-q", "-b", "master")
    _git(src, "config", "user.email", "t@t")
    _git(src, "config", "user.name", "t")
    (src / "research" / "theses" / "2026-11-09-F1032-AMD.json").write_text(json.dumps({"theses": [trade("F1032-AMD")]}))
    (src / "README").write_text("x")
    _git(src, "add", ".")
    _git(src, "commit", "-qm", "one")
    monkeypatch.setattr(ib, "REPO_URL", str(src))
    monkeypatch.setattr(ib, "REPO_DIR", str(tmp_path / "clone"))
    monkeypatch.setattr(ib, "DIR", str(tmp_path / "sealed"))
    now = datetime(2026, 11, 10, 10, 40, tzinfo=ET)
    assert ib.seal_flash(now) == ""                                   # yesterday's file is ignored
    body = {"read": "x", "theses": [trade("F1032-AMD"), trade("F1032-AMD", stop_pct=0.1), trade("F1032-AMD")],
            "close": ["2026-11-10-1030-1", "nope"]}
    (tmp_path / "sealed").mkdir(exist_ok=True)
    (tmp_path / "sealed" / "2026-11-10-1030.json").write_text(json.dumps({"theses": [{"id": "2026-11-10-1030-1"}]}))
    (src / "research" / "theses" / "2026-11-10-F1032-AMD.json").write_text(json.dumps(body))
    _git(src, "add", ".")
    _git(src, "commit", "-qm", "two")
    assert ib.seal_flash(now) == "sealed F1032-AMD: 1 of 2 trades"   # MAX_FLASH keeps two; one breaks a rule
    sealed = json.load(open(tmp_path / "sealed" / "2026-11-10-F1032-AMD.json"))
    assert sealed["sealed_at"] == now.isoformat() and sealed["close"] == ["2026-11-10-1030-1"]
    assert len(sealed["theses"]) == 1 and len(sealed["rejected"]) == 1
    assert ib.seal_flash(now + timedelta(minutes=1)) == ""           # once


def _bars(start, n, v=100, c=100.0, jump_at=None, jump_v=0, jump_c=0.0):
    out = []
    for k in range(n):
        t = start + timedelta(minutes=k)
        big = jump_at is not None and t == jump_at
        cc = jump_c if big else c
        out.append({"t": t.astimezone(fs.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "o": c, "h": max(c, cc), "l": min(c, cc),
                    "c": cc, "v": jump_v if big else v})
    return out


def test_screener_needs_both_the_volume_and_the_move(monkeypatch):
    monkeypatch.setattr(fs, "VOL_X", 3.0)
    monkeypatch.setattr(fs, "MOVE_PCT", 0.3)
    head = datetime(2026, 11, 10, 10, 31, 20, tzinfo=ET)
    news = [{"id": 1, "symbols": [SYM], "headline": "Guidance raised", "created_at": head.astimezone(fs.UTC).isoformat()}]
    t0 = datetime(2026, 11, 10, 10, 0, tzinfo=ET)
    jump = datetime(2026, 11, 10, 10, 32, tzinfo=ET)
    now = datetime(2026, 11, 10, 10, 33, 5, tzinfo=ET)
    hit = fs.check(news, {SYM: _bars(t0, 33, jump_at=jump, jump_v=500, jump_c=100.6)}, now)
    assert len(hit) == 1 and hit[0]["hit"]["v"] == 500                # 5x volume and +0.6%: flagged early
    quiet = fs.check(news, {SYM: _bars(t0, 33, jump_at=jump, jump_v=500, jump_c=100.1)}, now)
    assert quiet == []                                               # volume without the move: still waiting
    late = datetime(2026, 11, 10, 10, 35, 5, tzinfo=ET)
    done = fs.check(news, {SYM: _bars(t0, 35, jump_at=jump, jump_v=500, jump_c=100.1)}, late)
    assert len(done) == 1 and done[0]["hit"] is None                 # decided: no flag
    roundup = [{**news[0], "symbols": [SYM, "A", "B", "C"]}]
    assert fs.check(roundup, {SYM: _bars(t0, 33, jump_at=jump, jump_v=500, jump_c=100.6)}, now) == []


def test_every_flag_is_scored_followed_and_faded_and_joined_with_the_ledger():
    t0 = datetime(2026, 11, 10, 10, 0, tzinfo=ET)
    rise = [{"t": (t0 + timedelta(minutes=k)).astimezone(fs.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "o": 100 + k * 0.1, "h": 100 + k * 0.1, "l": 100 + k * 0.1, "c": 100 + k * 0.1, "v": 100}
            for k in range(120)]
    flat = [{**b, "o": 50, "h": 50, "l": 50, "c": 50} for b in rise]
    at = datetime(2026, 11, 10, 10, 10, tzinfo=ET)
    flags = [{"id": "2026-11-10-F1010-AAA", "sym": "AAA", "flagged_at": at.isoformat(), "since_headline_pct": 0.8,
              "volume_x": 4, "headline": "AAA raises guidance"},
             {"id": "2026-11-10-F1010-BBB", "sym": "BBB", "flagged_at": at.isoformat(), "since_headline_pct": -0.5,
              "volume_x": 3, "headline": "BBB price target cut"}]
    ledger = {"2026-11-10-F1010-AAA": {"look": True, "why": "LOOK: guidance", "status": "completed",
                                       "duration_s": 90, "cost_usd_est": 0.5},
              "2026-11-10-F1010-BBB": {"look": False, "why": "SKIP: note"}}
    claude = {"2026-11-10-F1010-AAA": [{"net_bps": "20.0", "vs_random": "12.5"}]}
    rows = ib.score_flag_day("2026-11-10", flags, ledger, claude, {"AAA": rise, "BBB": rise, "SPY": flat})
    aaa, bbb = rows
    assert aaa["follow30"] > 250 and aaa["fade30"] < -250              # the rise pays the follower
    assert aaa["follow30"] + aaa["fade30"] == -4 * ib.thesis.COST_BPS  # each side pays its round trip
    assert bbb["follow30"] < 0 and bbb["fade30"] > 0                   # BBB fell first: following is short
    assert (aaa["triage"], aaa["minutes"], aaa["claude_trades"], aaa["claude_vs_random"]) == ("look", 1.5, 1, 12.5)
    assert bbb["triage"] == "skip" and bbb["session"] == "" and bbb["claude_trades"] == 0
    s = ib.flag_summary([{k: str(v) for k, v in r.items()} for r in rows])
    assert s["look"]["n"] == 1 and s["skip"]["n"] == 1
    assert s["claude"] == {"sessions": 1, "cost_usd_est": 0.5, "flags_traded": 1, "vs_random_per_flag": 12.5}


def test_the_bar_window_ends_at_the_right_utc_time():
    """2026-11-12: 16:45 ET was sent as 16:45Z (11:45 ET), so afternoon trades and flags went unscored."""
    from datetime import datetime
    from spike_pod import ET
    assert ib.utc_z(datetime(2026, 11, 12, 16, 45, tzinfo=ET)) == "2026-11-12T21:45:00Z"


def test_shadow_flags_catch_smaller_moves_without_touching_the_live_flags(monkeypatch, tmp_path):
    import flash_shadow as sh
    head = datetime(2026, 11, 10, 10, 31, 20, tzinfo=ET)
    news = [{"id": 7, "symbols": [SYM], "headline": "Contract win", "created_at": head.astimezone(fs.UTC).isoformat()}]
    t0 = datetime(2026, 11, 10, 10, 0, tzinfo=ET)
    jump = datetime(2026, 11, 10, 10, 32, tzinfo=ET)
    bm = {SYM: _bars(t0, 33, jump_at=jump, jump_v=500, jump_c=100.15), "SPY": _bars(t0, 33), "QQQ": _bars(t0, 33)}
    uni = tmp_path / "u.json"
    uni.write_text(json.dumps([SYM]))
    monkeypatch.setattr(fs, "UNIVERSE_FILE", str(uni))
    monkeypatch.setattr(fs, "FLAG_DIR", str(tmp_path))
    monkeypatch.setattr(fs, "headlines", lambda names, a, b: news)
    monkeypatch.setattr(fs, "bars", lambda syms, tf, a, b: bm)
    monkeypatch.setattr(fs, "VOL_X", 3.0)
    monkeypatch.setattr(fs, "MOVE_PCT", 0.3)
    state, shadow, extra = {"seen": [], "last": {}, "count": 0}, {"seen": [], "last": {}, "count": 0}, []
    now = datetime(2026, 11, 10, 10, 35, 5, tzinfo=ET)
    assert fs.run(now, state, {SYM: 100.0, "SPY": 100.0, "QQQ": 100.0}, shadow, extra) == []   # +0.15%: not live
    assert len(extra) == 1 and extra[0]["sym"] == SYM                                          # but a shadow flag
    assert fs.run(now, state, {SYM: 100.0}, shadow, extra) == [] and len(extra) == 1          # once

    # scored: |move vs SPY| over 60 minutes from 4 minutes after the flag
    t1 = datetime(2026, 11, 10, 10, 30, tzinfo=ET)
    rise = [{"t": (t1 + timedelta(minutes=k)).astimezone(fs.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "o": 100 - k * 0.01, "h": 100, "l": 90, "c": 100, "v": 1} for k in range(120)]
    flat = [{**b, "o": 50} for b in rise]
    flag = {"id": "2026-11-10-F1035-" + SYM, "sym": SYM, "flagged_at": now.isoformat(), "since_headline_pct": 0.15}
    rows = sh.score_shadow_day("2026-11-10", [flag], {flag["id"]: {"v1": "skip", "v2": "look", "category": "contract",
                                                                   "size": "small"}}, {SYM: rise, "SPY": flat})
    assert rows[0]["abs60"] == 60.1 and rows[0]["v2"] == "look"          # fell ~0.6% vs a flat SPY
    assert rows[0]["fade60"] > 0 > rows[0]["follow60"]


def test_shadow_summary_reports_t1_and_t2_only_at_the_sample():
    import flash_shadow as sh
    rows = [{"abs60": 40 + (i % 7), "v1": "look" if i % 3 == 0 else "skip", "v2": "look" if i % 2 else "skip",
             "category": "deal", "follow60": 1, "fade60": -1} for i in range(100)]
    s = sh.shadow_summary(rows)
    assert s["T1"].startswith("running (100") and s["v2"]["look"] == 50
    big = [{**r, "abs60": (80 if r["v2"] == "look" else 20) + (i % 5)} for i, r in enumerate(rows * 15)]
    s = sh.shadow_summary(big)
    assert s["T1"] == "pass" and s["T2"] == "keep v2" and s["v2"]["gap"] == 60.0
