"""STRATTON's forward-test registry (revamp review G1/G2, 2026-11-12).

Every forward test is written into forward_registry.json BEFORE its first scored trade: what is
tested, the exact pass line, and the instruction-file version it covers. Entries are never removed;
a retired or replaced candidate still counts. The pass bar every desk and the go-live check use
rises with the number of registered tests, so five tests running side by side can't hand one of
them a lucky pass:

  bar(K) = the one-sided z for a 2.5% family-wise error split K ways (Bonferroni), at least 2.0
  K: 1-2 -> 2.0-2.24, 3 -> 2.39, 5 -> 2.58, 8 -> 2.73, 12 -> 2.87, 20 -> 3.02

A change to a desk's instruction file is a new version: a new entry (K goes up by one), and the
desk's evidence restarts at n = 0 (its older rows stay in the score files, marked with their
version, and are reported but never judged). At most one rewrite per desk per quarter.
"""
import json
import os
from statistics import NormalDist

FAMILY_ALPHA = 0.025
MIN_BAR = 2.0
PATH = os.getenv("FORWARD_REGISTRY", os.path.join(os.path.dirname(os.path.abspath(__file__)), "forward_registry.json"))


def load(path: str = PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["tests"]


def bar(k: int | None = None) -> float:
    """The day-t a forward test must reach, given K tests registered so far."""
    k = max(1, k if k is not None else len(load()))
    return round(max(MIN_BAR, NormalDist().inv_cdf(1 - FAMILY_ALPHA / k)), 2)


def current_version(desk: str, tests: list[dict] | None = None) -> int:
    """The newest registered version of a desk ('thesis', 'intraday', 'flash'); 1 if none is."""
    versions = [t.get("version", 1) for t in (tests if tests is not None else load()) if t.get("desk") == desk]
    return max(versions) if versions else 1
