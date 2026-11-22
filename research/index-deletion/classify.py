"""index-deletion-auction-v1: classify removals from the Wikipedia snapshot (events_raw.csv) using ONLY the
reason text and the two change tables. Runs before any price is fetched; output events_classified.csv.

Classes (registry entry):
  exclude        the removed company's own sentence mentions acquisition/merger/bankruptcy/delisting/
                 spin-off/liquidation/share class (removal not driven by index selling alone)
  migration-down removed from the 400 and added to the 600 within 3 days
  migration-up   removed from the 600 and added to the 400 within 3 days, or a 400 removal not added to the
                 600 (the 400 page can't show S&P 500 additions; most such removals are promotions). Reported only.
  exit           removed from the 600 and not added to the 400 (leaves the S&P 1500)
"""
import re
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
BAD = re.compile(r"acquir|merg|bankrupt|chapter 11|delist|spin|spun|liquidat|share class|class [abc] |reorgani|going private|taken private|tender offer", re.I)
START, END = "2020-01-01", "2026-11-13"


def own_sentence(reason: str, ticker: str, name: str) -> str:
    """The sentences of `reason` that talk about the removed company (by ticker or first name word)."""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z&'.-]+", str(name)) if len(w) > 2][:1]
    keys = [re.escape(str(ticker))] + [re.escape(w) for w in words]
    sents = re.split(r"(?<=[.;])\s+", str(reason))
    hit = [s for s in sents if any(re.search(rf"\b{k}\b", s) for k in keys)]
    return " ".join(hit) if hit else str(reason)


def main():
    raw = pd.read_csv(HERE / "events_raw.csv")
    raw["d"] = pd.to_datetime(raw["date"], errors="coerce")
    adds = {idx: raw[(raw["index"] == idx) & raw.add_ticker.notna()] for idx in (400, 600)}

    def added_to(idx, ticker, day):
        a = adds[idx]
        return bool(((a.add_ticker == ticker) & ((a.d - day).abs() <= pd.Timedelta(days=3))).any())

    rem = raw[(raw.d >= START) & (raw.d <= END) & raw.rem_ticker.notna()].copy()
    out = []
    for r in rem.itertuples():
        s = own_sentence(r.reason, r.rem_ticker, r.rem_name)
        if BAD.search(s):
            cls = "exclude"
        elif r.index == 400:
            cls = "migration-down" if added_to(600, r.rem_ticker, r.d) else "migration-up"
        else:
            cls = "migration-up" if added_to(400, r.rem_ticker, r.d) else "exit"
        out.append({"effective": r.d.date().isoformat(), "index": r.index, "symbol": r.rem_ticker,
                    "name": r.rem_name, "cls": cls, "reason": str(r.reason)[:300]})
    df = pd.DataFrame(out).drop_duplicates(["effective", "symbol", "index"])
    df.to_csv(HERE / "events_classified.csv", index=False)
    print(df.cls.value_counts().to_string())


if __name__ == "__main__":
    main()
