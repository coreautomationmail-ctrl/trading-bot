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
