"""The belief ledger is written by unattended sessions, so its shape is checked here."""
import json
import os

PATH = os.path.join(os.path.dirname(__file__), "..", "research", "beliefs.json")
FIELDS = {"id", "claim", "status", "evidence", "confidence", "reopen_if", "journal", "updated"}


def test_beliefs_are_well_formed():
    with open(PATH, encoding="utf-8") as f:
        beliefs = json.load(f)
    assert len({b["id"] for b in beliefs}) == len(beliefs)
    for b in beliefs:
        assert FIELDS <= set(b), b["id"]
        assert b["status"] in {"open", "live", "rejected", "fact"}, b["id"]
        assert b["confidence"] in {"low", "medium", "high"}, b["id"]
        assert all(b[k].strip() for k in FIELDS), b["id"]
