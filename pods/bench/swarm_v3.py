"""STRATTON swarm-v3 core: an evolving economy of intraday rule bots (registry `swarm-v3`,
docs/swarm-plan.md section 9; the owner chose on 2026-11-18 to start the core now instead of late
December, so its 60-day clock runs beside v1 and v2).

Two economies start from the same 98 genomes as swarm-v1's intraday bots (swarm_bots.ALL) and trade
the live IEX feed virtually, through the bench engine like every swarm bot:

  e (eco)  selection. Capital is life: each bot starts with 1.0; each day its capital grows or
           shrinks by EXPOSURE x its mean net bps; it pays RENT. Under FLOOR (after MIN_TRADES
           trades or MIN_DAYS_ALIVE days) it dies. At BIRTH or more (after MIN_TRADES trades since
           its own birth) it pays STAKE for a mutated child, which starts with no record.
  c (ctl)  random churn, the control: the same capital rules and, each day, the same number of
           deaths and births as e, but who dies and who breeds is drawn at random.
  f (frz)  the 98 seed genomes frozen at equal weight: swarm-v1's own intraday trades, re-costed.

All three pay the same honest cost: per side, by time of day (the open costs more), instead of the
bench's flat 2 bps. A book's daily return is the capital-weighted mean of its bots' daily mean net
bps (a bot with no trades counts 0); f is equal weight.

Test (registered before the first scored trade): from 2026-11-19, >= 60 trading days, day t of
(e - f) >= bar(K) AND day t of (e - c) >= bar(K) AND e's mean > 0 AND both halves of both diffs
positive; KILL at >= 120 days if either t < 1 or e's mean <= 0; else INCONCLUSIVE.

Not in the core (a later, separately registered test, swarm-v3.1): rule grammar (GP), swarm-aware
inputs, new data senses, the hedger, daily bots. Mutation here changes settings and the mode only.
"""
import csv
import json
import os
import random
import re

import spike_pod
import swarm
import swarm_bots

TEST_ID = "swarm-v3"
START = "2026-11-19"
STATE_FILE = os.path.join(spike_pod.STATE_DIR, "swarm_v3_state.json")
TRADES_CSV = os.path.join(spike_pod.STATE_DIR, "swarm_v3_virtual.csv")
V1_CSV = os.path.join(spike_pod.STATE_DIR, "swarm_virtual.csv")
SUMMARY = os.path.join(spike_pod.STATE_DIR, "swarm_v3_summary.json")
PREFIX = "sw3-"
ECOS = ("e", "c")
STEP_AFTER_ET = 16 * 60 + 10

EXPOSURE = 10.0         # capital change per day = EXPOSURE x mean net bps / 1e4 (+-20 bps -> +-2%)
RENT = 0.002            # of the starting stake, per day
FLOOR, BIRTH, STAKE = 0.6, 1.3, 0.3
MIN_TRADES, MIN_DAYS_ALIVE = 40, 20
MIN_POP, MAX_POP, MAX_BIRTHS_DAY = 49, 196, 10
MIN_DAYS, KILL_DAYS = 60, 120

HOLDS = (15, 30, 45, 60, 90, 120)
ORB_R = (3, 5, 10, 15, 20, 30, 45, 60)
LOOKBACKS = (10, 15, 20, 30, 45, 60, 90)
MODES = {"gap": ("fade", "go"), "orb": ("go", "fade"), "vwap": ("revert", "follow"),
         "trend": ("follow", "fade"), "late": ("fade", "follow")}
END = swarm_bots.END

LIVE: dict = {}         # name -> the running bot object


# ---------- honest cost ----------

def side_cost(hm: int) -> float:
    """bps per side at minute-of-day hm (ET). An assumption fixed before any v3 trade, not a
    measurement: the open's wide spreads, a little extra into the close."""
    if hm < 9 * 60 + 45:
        return 5.0
    if hm < 10 * 60:
        return 3.5
    if hm < 10 * 60 + 30:
        return 2.5
    if hm >= 15 * 60 + 45:
        return 2.5
    return 2.0


def recost(gross: float, et: str, hold: int) -> float:
    """Net bps for a trade signalled at `et` (HH:MM): entry 2 minutes later, exit hold + 1 after."""
    h, m = map(int, et.split(":"))
    sig = h * 60 + m
    return round(gross - side_cost(sig + 2) - side_cost(sig + hold + 1), 2)


def hold_of(name: str) -> int:
    m = re.search(r"-h(\d+)$", name)
    return int(m.group(1)) if m else 0


# ---------- genomes ----------

def genome_of(bot) -> dict:
    g = {"fam": bot.NAME.split("-")[1], "mode": bot.mode, "hold": bot.HOLD_MIN}
    if isinstance(bot, swarm_bots.Gap):
        g.update(g_pct=bot.g, at=bot.at)
    elif isinstance(bot, swarm_bots.Orb):
        g.update(r=bot.r)
    elif isinstance(bot, swarm_bots.Vwap):
        g.update(d=bot.d)
    elif isinstance(bot, swarm_bots.Trend):
        g.update(lookback=bot.lookback, x=bot.x)
    elif isinstance(bot, swarm_bots.Late):
        g.update(at=bot.at)
    return g


def make(g: dict, name: str):
    f = g["fam"]
    if f == "gap":
        b = swarm_bots.Gap(g["g_pct"], g["mode"], g["at"], g["hold"])
    elif f == "orb":
        b = swarm_bots.Orb(g["r"], g["mode"], g["hold"])
    elif f == "vwap":
        b = swarm_bots.Vwap(g["d"], g["mode"], g["hold"])
    elif f == "trend":
        b = swarm_bots.Trend(g["lookback"], g["x"], g["mode"], g["hold"])
    elif f == "late":
        b = swarm_bots.Late(g["at"], g["mode"], g["hold"])
    else:
        raise ValueError(f"unknown family {f}")
    b.NAME = name
    b.V3 = True
    return b


def _step(seq: tuple, v, rng) -> int:
    i = min(range(len(seq)), key=lambda j: abs(seq[j] - v))
    return seq[max(0, min(len(seq) - 1, i + rng.choice((-1, 1))))]


def _scale(v: float, lo: float, hi: float, rng) -> float:
    return round(max(lo, min(hi, v * 2 ** rng.gauss(0, 0.4))), 2)


def mutate(g: dict, rng) -> dict:
    """A child: the mode flips with p 0.1, then one or two settings move a step. Always differs."""
    for _ in range(10):
        c = dict(g)
        if rng.random() < 0.1:
            a, b = MODES[c["fam"]]
            c["mode"] = b if c["mode"] == a else a
        keys = [k for k in c if k not in ("fam", "mode")]
        for k in rng.sample(keys, min(len(keys), 1 + (rng.random() < 0.3))):
            if k == "hold":
                c[k] = _step(HOLDS, c[k], rng)
            elif k == "r":
                c[k] = _step(ORB_R, c[k], rng)
            elif k == "lookback":
                c[k] = _step(LOOKBACKS, c[k], rng)
            elif k == "g_pct":
                c[k] = _scale(c[k], 0.2, 5.0, rng)
            elif k == "d":
                c[k] = _scale(c[k], 0.3, 5.0, rng)
            elif k == "x":
                c[k] = _scale(c[k], 0.2, 3.0, rng)
            elif k == "at":
                c[k] = c[k] + rng.choice((-10, -5, 5, 10))
        if c["fam"] == "gap":
            c["at"] = max(9 * 60 + 35, min(10 * 60 + 15, c["at"]))
        if c["fam"] == "late":
            c["at"] = max(13 * 60, min(END - 8 - c["hold"], c["at"]))
        if c != g:
            return c
    return c


def name_of(g: dict, eco: str, i: int) -> str:
    return f"{PREFIX}{g['fam']}-{g['mode']}-{eco}{i}-h{g['hold']}"


# ---------- state ----------

def seed() -> dict:
    pop = {}
    for eco in ECOS:
        pop[eco] = {}
        for i, b in enumerate(swarm_bots.ALL):
            g = genome_of(b)
            pop[eco][name_of(g, eco, i)] = {"g": g, "cap": 1.0, "born": START, "trades": 0, "days": 0,
                                            "parent": b.NAME, "gen": 0}
    n = len(swarm_bots.ALL)
    return {"test": TEST_ID, "start": START, "day_done": None, "checked": None,
            "next_id": {e: n for e in ECOS}, "pop": pop, "deaths": {e: 0 for e in ECOS},
            "births": {e: 0 for e in ECOS}, "history": []}


def load(path: str = None) -> dict:
    try:
        with open(path or STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return seed()


def save(state: dict, path: str = None):
    path = path or STATE_FILE
    with open(path + ".tmp", "w") as f:
        json.dump(state, f)
    os.replace(path + ".tmp", path)


def is_v3(name: str) -> bool:
    return name.startswith(PREFIX)


def bots(state: dict) -> list:
    """The running bot objects for both economies' living populations."""
    out = []
    for eco in ECOS:
        for name, p in state["pop"][eco].items():
            if name not in LIVE:
                LIVE[name] = make(p["g"], name)
            out.append(LIVE[name])
    return out


def sync(strats: list, state: dict):
    """In place: drop dead v3 bots from the engine's list, add the newborn."""
    alive = {n for eco in ECOS for n in state["pop"][eco]}
    strats[:] = [s for s in strats if not is_v3(s.NAME) or s.NAME in alive]
    have = {s.NAME for s in strats}
    strats.extend(b for b in bots(state) if b.NAME not in have)
    for n in list(LIVE):
        if n not in alive:
            del LIVE[n]


def route(rows: list) -> list:
    """The bench's settled rows for v3 bots, re-costed honestly."""
    out = []
    for r in rows:
        if is_v3(r["strategy"]):
            out.append({**r, "net_bps": recost(float(r["gross_bps"]), r["et"], hold_of(r["strategy"]))})
    return out


# ---------- the economy ----------

def _mean(xs: list) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _read_day(path: str, day: str, names=None, honest: bool = False) -> dict:
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if r.get("date") != day or (names is not None and r.get("strategy") not in names):
                continue
            if r.get("gross_bps") in ("", None):
                continue
            net = recost(float(r["gross_bps"]), r["et"], hold_of(r["strategy"])) if honest else float(r["net_bps"])
            out.setdefault(r["strategy"], []).append(net)
    return out


def _book(pop: dict, by_bot: dict) -> float:
    tot = sum(p["cap"] for p in pop.values())
    return sum(p["cap"] * _mean(by_bot.get(n, [])) for n, p in pop.items()) / tot if tot else 0.0


def _child(state: dict, eco: str, parent: str, day: str, rng):
    pop = state["pop"][eco]
    p = pop[parent]
    g = mutate(p["g"], rng)
    i = state["next_id"][eco]
    state["next_id"][eco] = i + 1
    p["cap"] -= STAKE
    pop[name_of(g, eco, i)] = {"g": g, "cap": STAKE, "born": day, "trades": 0, "days": 0,
                               "parent": parent, "gen": p["gen"] + 1}


def step(state: dict, day: str, v3_rows: dict, v1_rows: dict) -> dict:
    """One trading day: book returns (before any capital moves), capital, then deaths and births."""
    names_v1 = sorted(swarm_bots.NAMES)
    h = {"date": day, "f": round(sum(_mean(v1_rows.get(n, [])) for n in names_v1) / len(names_v1), 3)}
    for eco in ECOS:
        pop = state["pop"][eco]
        h[eco] = round(_book(pop, v3_rows), 3)
        for n, p in pop.items():
            xs = v3_rows.get(n, [])
            p["cap"] = max(0.0, p["cap"] * (1 + EXPOSURE * _mean(xs) / 1e4) - RENT)
            p["trades"] += len(xs)
            p["days"] += 1

    def can_die(p):
        return p["trades"] >= MIN_TRADES or p["days"] >= MIN_DAYS_ALIVE

    # e: selection
    e = state["pop"]["e"]
    dying = sorted((n for n, p in e.items() if p["cap"] < FLOOR and can_die(p)), key=lambda n: e[n]["cap"])
    dying = dying[:max(0, len(e) - MIN_POP)]
    for n in dying:
        del e[n]
    rng = random.Random(f"{TEST_ID}-{day}-e")
    parents = sorted((n for n, p in e.items() if p["cap"] >= BIRTH and p["trades"] >= MIN_TRADES),
                     key=lambda n: -e[n]["cap"])[:max(0, min(MAX_BIRTHS_DAY, MAX_POP - len(e)))]
    for n in parents:
        _child(state, "e", n, day, rng)
    # c: the same counts, chosen at random
    c = state["pop"]["c"]
    rng = random.Random(f"{TEST_ID}-{day}-c")
    elig = sorted(n for n, p in c.items() if can_die(p))
    die_c = rng.sample(elig, max(0, min(len(dying), len(elig), len(c) - MIN_POP)))
    for n in die_c:
        del c[n]
    elig = sorted(n for n, p in c.items() if p["trades"] >= MIN_TRADES and p["cap"] >= STAKE + FLOOR)
    born_c = rng.sample(elig, max(0, min(len(parents), len(elig), MAX_POP - len(c))))
    for n in born_c:
        _child(state, "c", n, day, rng)

    h.update(deaths_e=len(dying), births_e=len(parents), deaths_c=len(die_c), births_c=len(born_c),
             n_e=len(e), n_c=len(c))
    for eco, d, b in (("e", dying, parents), ("c", die_c, born_c)):
        state["deaths"][eco] += len(d)
        state["births"][eco] += len(b)
    state["history"].append(h)
    state["day_done"] = day
    return h


def maybe_step(now_et, strats: list, path: str = None) -> dict | None:
    """After the close, once per trading day: run the economy and update the engine's bots."""
    today = now_et.date().isoformat()
    if today < START or now_et.hour * 60 + now_et.minute < STEP_AFTER_ET:
        return None
    state = load(path)
    if state.get("day_done") == today or state.get("checked") == today:
        return None
    v3 = _read_day(TRADES_CSV, today)
    v1 = _read_day(V1_CSV, today, swarm_bots.NAMES, honest=True)
    if not v3 and not v1:               # a holiday, or the bench was down all day
        state["checked"] = today
        save(state, path)
        return None
    h = step(state, today, v3, v1)
    save(state, path)
    sync(strats, state)
    write_summary(state)
    return h


# ---------- verdict and report ----------

def verdict(state: dict, bar: float) -> tuple[str, dict]:
    hist = [h for h in state["history"] if h["date"] >= START]
    n = len(hist)
    d_f = [h["e"] - h["f"] for h in hist]
    d_c = [h["e"] - h["c"] for h in hist]
    t_f, t_c = swarm.day_t(d_f), swarm.day_t(d_c)
    mean_e = _mean([h["e"] for h in hist])
    half = n // 2
    halves = lambda xs: [sum(xs[:half]), sum(xs[half:])] if n >= 2 else [0.0, 0.0]  # noqa: E731
    facts = {"days": n, "t_frozen": t_f, "t_control": t_c, "mean_e": mean_e, "bar": bar,
             "mean_f": _mean([h["f"] for h in hist]), "mean_c": _mean([h["c"] for h in hist])}
    if (n >= MIN_DAYS and t_f is not None and t_c is not None and t_f >= bar and t_c >= bar and mean_e > 0
            and min(halves(d_f)) > 0 and min(halves(d_c)) > 0):
        return "PASS", facts
    if n >= KILL_DAYS and (t_f is None or t_c is None or t_f < 1 or t_c < 1 or mean_e <= 0):
        return "KILL", facts
    return ("INCONCLUSIVE" if n >= KILL_DAYS else "RUNNING"), facts


def write_summary(state: dict, path: str = None):
    """For Vision's phone view: the books, the verdict and the living population. Data only."""
    import registry

    k = len(registry.load())
    status, f = verdict(state, registry.bar(k))
    pops = {}
    for eco in ECOS:
        pop = state["pop"][eco]
        pops[eco] = sorted(({"name": n, "cap": round(p["cap"], 4), "gen": p["gen"], "trades": p["trades"],
                             "born": p["born"], "parent": p["parent"]} for n, p in pop.items()),
                           key=lambda x: -x["cap"])
    out = {"test": TEST_ID, "start": START, "status": status, "min_days": MIN_DAYS, "kill_days": KILL_DAYS,
           "k": k, **f, "history": state["history"], "deaths": state["deaths"], "births": state["births"],
           "pop": pops, "rules": {"exposure": EXPOSURE, "rent": RENT, "floor": FLOOR, "birth": BIRTH,
                                  "stake": STAKE, "min_trades": MIN_TRADES, "max_pop": MAX_POP}}
    path = path or SUMMARY
    with open(path + ".tmp", "w") as fh:
        json.dump(out, fh)
    os.replace(path + ".tmp", path)


def report(state_dir: str = None) -> str:
    import registry

    state = load(os.path.join(state_dir, os.path.basename(STATE_FILE)) if state_dir else None)
    status, f = verdict(state, registry.bar(len(registry.load())))
    if not f["days"]:
        return f"STRATTON {TEST_ID} (economy, {len(state['pop']['e'])} + {len(state['pop']['c'])} bots): no day scored yet."
    e, c = state["pop"]["e"], state["pop"]["c"]
    top = sorted(e.items(), key=lambda kv: -kv[1]["cap"])[:3]
    fmt = lambda x: "n/a" if x is None else f"{x:+.2f}"   # noqa: E731
    return (f"STRATTON {TEST_ID} economy, day {f['days']} of {MIN_DAYS}+: {status}\n"
            f"Daily mean bps: eco {f['mean_e']:+.2f}, control {f['mean_c']:+.2f}, frozen {f['mean_f']:+.2f}\n"
            f"Eco minus frozen: day t {fmt(f['t_frozen'])}; minus control: day t {fmt(f['t_control'])} (bar {f['bar']:.2f})\n"
            f"Population eco {len(e)} (born {state['births']['e']}, died {state['deaths']['e']}), "
            f"control {len(c)} (born {state['births']['c']}, died {state['deaths']['c']})\n"
            "Richest: " + ", ".join(f"{n} {p['cap']:.2f} g{p['gen']}" for n, p in top))
