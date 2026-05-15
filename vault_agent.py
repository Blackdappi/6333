"""
vault_agent.py — Second Brain Analyse-Framework for Polymarket bot.

Kører 2x dagligt (08:00 og 20:00). Læser closed trades fra polymarket-bots,
analyserer patterns, og skriver markdown til ObsidianVault.
"""

import json
import os
import re
import csv
import glob
import math
import logging
import schedule
import time
from collections import defaultdict
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional

# ── Paths ─────────────────────────────────────────────────────────────────────
VAULT_PATH = Path(r"C:\Users\bebob\ObsidianVault\Polly")
BOT_PATH   = Path(r"C:\Users\bebob\polymarket-bots")

WIKI        = VAULT_PATH / "wiki"
TRADE_DIR   = WIKI / "trade-analysis"
WEEKLY_DIR  = WIKI / "weekly-brain"
CITIES_MD   = WIKI / "cities.md"
EDGE_MD     = WIKI / "edge-system.md"
ML_MD       = WIKI / "ml-agent.md"
LOG_MD      = VAULT_PATH / "log.md"

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(BOT_PATH / "vault_agent.log", encoding="utf-8", errors="replace"),
    ],
)
log = logging.getLogger("vault_agent")

# ── State: hvornår kørte vi sidst ─────────────────────────────────────────────
STATE_FILE = BOT_PATH / "vault_agent_state.json"


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_run": None}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, default=str, indent=2), encoding="utf-8")


# ── Trade data loader ─────────────────────────────────────────────────────────

TRADE_FILE_CANDIDATES = [
    "closed_trades.json",
    "trades.json",
    "trade_log.json",
    "trades_closed.json",
    "closed_trades.csv",
    "trades.csv",
]

REQUIRED_FIELDS = {
    "trade_id", "city", "entry_price", "exit_price", "pnl",
    "outcome", "forecast_margin", "vc_om_diff", "ml_score",
    "timestamp_entry", "timestamp_exit", "volume", "days_to_expiry",
}


def _find_trade_file() -> Optional[Path]:
    for name in TRADE_FILE_CANDIDATES:
        p = BOT_PATH / name
        if p.exists():
            return p
    # wildcard fallback
    matches = list(BOT_PATH.glob("*trade*.*"))
    if matches:
        return sorted(matches, key=lambda p: p.stat().st_mtime, reverse=True)[0]
    return None


def _parse_timestamp(val) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        try:
            return datetime.fromtimestamp(val)
        except Exception:
            return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(val), fmt)
        except ValueError:
            continue
    return None


def _load_trades() -> list[dict]:
    path = _find_trade_file()
    if path is None:
        log.warning("Ingen trade-fil fundet i %s", BOT_PATH)
        return []

    if path.suffix == ".csv":
        with path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            raw = list(reader)
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw = raw.get("trades", raw.get("data", list(raw.values())))

    trades = []
    for row in raw:
        t = {}
        for field in REQUIRED_FIELDS:
            t[field] = row.get(field)
        # type coercions
        for num_field in ("entry_price", "exit_price", "pnl", "forecast_margin",
                           "vc_om_diff", "ml_score", "volume", "days_to_expiry"):
            try:
                t[num_field] = float(t[num_field]) if t[num_field] is not None else None
            except (ValueError, TypeError):
                t[num_field] = None
        t["timestamp_entry"] = _parse_timestamp(t["timestamp_entry"])
        t["timestamp_exit"]  = _parse_timestamp(t["timestamp_exit"])
        t["is_win"] = str(t.get("outcome", "")).lower() in ("win", "won", "correct", "true", "1", "yes")
        trades.append(t)

    log.info("Loadede %d trades fra %s", len(trades), path.name)
    return trades


def _trades_since(trades: list[dict], since: Optional[datetime]) -> list[dict]:
    if since is None:
        return trades
    return [t for t in trades if t["timestamp_exit"] and t["timestamp_exit"] >= since]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe(val, default=0.0):
    return val if val is not None else default


def _pct(wins, total):
    return (wins / total * 100) if total else 0.0


def _bucket(val, edges: list) -> str:
    if val is None:
        return "N/A"
    for i, edge in enumerate(edges):
        if val < edge:
            lo = edges[i - 1] if i > 0 else "-∞"
            return f"{lo}–{edge}"
    return f"{edges[-1]}+"


def _week_tag(dt: datetime) -> str:
    return f"{dt.year}-W{dt.isocalendar()[1]:02d}"


def _ensure_dirs() -> None:
    for d in (WIKI, TRADE_DIR, WEEKLY_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ── Markdown helpers ──────────────────────────────────────────────────────────

def _write_md(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    log.info("Skrev %s", path)


def _append_log(section: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    line  = f"\n\n## {stamp}\n{section}"
    with LOG_MD.open("a", encoding="utf-8") as f:
        f.write(line)


# ══════════════════════════════════════════════════════════════════════════════
# 2. KOMPLET TRADE ANALYSE
# ══════════════════════════════════════════════════════════════════════════════

def _factor_summary(trades: list[dict]) -> str:
    if not trades:
        return "_Ingen trades_"
    ep   = [_safe(t["entry_price"]) for t in trades]
    fm   = [_safe(t["forecast_margin"]) for t in trades]
    vcom = [_safe(t["vc_om_diff"]) for t in trades]
    ml   = [_safe(t["ml_score"]) for t in trades]
    n    = len(trades)
    avg  = lambda lst: sum(lst) / len(lst) if lst else 0
    return (
        f"  - Antal: **{n}**\n"
        f"  - Avg entry price: `{avg(ep):.3f}`\n"
        f"  - Avg forecast margin: `{avg(fm):.4f}`\n"
        f"  - Avg vc_om_diff: `{avg(vcom):.4f}`\n"
        f"  - Avg ML score: `{avg(ml):.3f}`\n"
    )


def _trade_table(trades: list[dict], limit=20) -> str:
    if not trades:
        return "_Ingen trades_\n"
    header = "| Trade ID | City | Entry | FM | VC_OM | ML | PnL | Outcome |\n"
    sep    = "|---|---|---|---|---|---|---|---|\n"
    rows   = []
    for t in trades[:limit]:
        rows.append(
            f"| {t.get('trade_id','?')} "
            f"| {t.get('city','?')} "
            f"| {_safe(t['entry_price']):.3f} "
            f"| {_safe(t['forecast_margin']):.4f} "
            f"| {_safe(t['vc_om_diff']):.4f} "
            f"| {_safe(t['ml_score']):.3f} "
            f"| {_safe(t['pnl']):+.4f} "
            f"| {'✅ WIN' if t['is_win'] else '❌ LOSS'} |"
        )
    return header + sep + "\n".join(rows) + "\n"


def _what_went_right(winners: list[dict]) -> str:
    if not winners:
        return "_Ingen winners_\n"
    fm_vals   = [_safe(t["forecast_margin"]) for t in winners]
    ml_vals   = [_safe(t["ml_score"]) for t in winners]
    vcom_vals = [_safe(t["vc_om_diff"]) for t in winners]
    avg = lambda lst: sum(lst) / len(lst) if lst else 0

    insights = []
    if avg(fm_vals) > 0.05:
        insights.append("- Høj forecast margin → markedet underpricede sandsynlighed")
    if avg(ml_vals) > 0.65:
        insights.append("- Stærkt ML signal (>0.65) → modellen forudsagde korrekt")
    if avg(vcom_vals) < 0.05:
        insights.append("- Lav vc_om_diff → VC og OM var enige → trygt entry")
    if not insights:
        insights.append("- Faktorer viste ingen klar dominerende pattern")
    return "\n".join(insights) + "\n"


def _what_went_wrong(losers: list[dict]) -> str:
    if not losers:
        return "_Ingen losers_\n"
    fm_vals   = [_safe(t["forecast_margin"]) for t in losers]
    ml_vals   = [_safe(t["ml_score"]) for t in losers]
    vcom_vals = [_safe(t["vc_om_diff"]) for t in losers]
    avg = lambda lst: sum(lst) / len(lst) if lst else 0

    issues = []
    if avg(fm_vals) < 0.02:
        issues.append("- Lav forecast margin → dårlig edge ved entry")
    if avg(ml_vals) < 0.55:
        issues.append("- Svagt ML signal (<0.55) → modellen var usikker")
    if avg(vcom_vals) > 0.10:
        issues.append("- Høj vc_om_diff → VC og OM var uenige → risikabelt entry")
    if not issues:
        issues.append("- Ingen klar årsag — kan være tilfældig udfald")
    return "\n".join(issues) + "\n"


def run_trade_analysis(trades: list[dict], since: datetime) -> None:
    new_trades = _trades_since(trades, since)
    if not new_trades:
        log.info("Ingen nye lukkede trades siden %s", since)
        return

    winners = [t for t in new_trades if t["is_win"]]
    losers  = [t for t in new_trades if not t["is_win"]]
    total   = len(new_trades)
    win_pct = _pct(len(winners), total)

    now      = datetime.now()
    filename = now.strftime("%Y-%m-%d-%H") + ".md"
    out_path = TRADE_DIR / filename

    content = f"""# Trade Analyse — {now.strftime("%Y-%m-%d %H:%M")}

**Periode:** {since.strftime("%Y-%m-%d %H:%M")} → {now.strftime("%Y-%m-%d %H:%M")}
**Trades:** {total} | **Win rate:** {win_pct:.1f}% ({len(winners)}W / {len(losers)}L)

---

## ✅ Winners ({len(winners)})

{_factor_summary(winners)}

### Hvad gik rigtigt

{_what_went_right(winners)}

{_trade_table(winners)}

---

## ❌ Losers ({len(losers)})

{_factor_summary(losers)}

### Hvad gik galt

{_what_went_wrong(losers)}

{_trade_table(losers)}
"""
    _write_md(out_path, content)


# ══════════════════════════════════════════════════════════════════════════════
# 3. PATTERN DETECTION
# ══════════════════════════════════════════════════════════════════════════════

def update_cities_md(trades: list[dict]) -> None:
    by_city: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        city = str(t.get("city") or "Ukendt").strip()
        by_city[city].append(t)

    rows = []
    for city, city_trades in sorted(by_city.items()):
        wins    = sum(1 for t in city_trades if t["is_win"])
        total   = len(city_trades)
        wr      = _pct(wins, total)
        avg_pnl = sum(_safe(t["pnl"]) for t in city_trades) / total if total else 0
        rows.append((city, total, wr, avg_pnl))

    rows.sort(key=lambda r: r[2], reverse=True)

    table = "| By | Trades | Win Rate | Avg PnL |\n|---|---|---|---|\n"
    for city, total, wr, avg_pnl in rows:
        table += f"| {city} | {total} | {wr:.1f}% | {avg_pnl:+.4f} |\n"

    top3 = rows[:3]
    top3_str = "\n".join(f"- **{r[0]}** — {r[2]:.1f}% win rate, avg PnL {r[3]:+.4f}" for r in top3)

    content = f"""# Cities — Performance Wiki

_Sidst opdateret: {datetime.now().strftime("%Y-%m-%d %H:%M")}_

## Samlet overblik

{table}

## Top 3 byer (win rate)

{top3_str}

## Analyse

Tabellen viser historisk performance pr. by. Byer med lav win rate og negativ avg PnL
bør udvises ekstra forsigtighed ved fremtidige entries.
"""
    _write_md(CITIES_MD, content)


def update_edge_system_md(trades: list[dict]) -> None:
    ep_edges = [0.3, 0.5, 0.7, 0.9]
    fm_edges = [0.02, 0.05, 0.10, 0.20]

    ep_buckets: dict[str, list[dict]] = defaultdict(list)
    fm_buckets: dict[str, list[dict]] = defaultdict(list)

    for t in trades:
        ep_buckets[_bucket(_safe(t["entry_price"]), ep_edges)].append(t)
        fm_buckets[_bucket(_safe(t["forecast_margin"]), fm_edges)].append(t)

    def bucket_table(buckets):
        header = "| Bucket | Trades | Win Rate | Avg PnL |\n|---|---|---|---|\n"
        rows   = ""
        for bkt, bt in sorted(buckets.items()):
            wins    = sum(1 for t in bt if t["is_win"])
            total   = len(bt)
            wr      = _pct(wins, total)
            avg_pnl = sum(_safe(t["pnl"]) for t in bt) / total if total else 0
            rows   += f"| {bkt} | {total} | {wr:.1f}% | {avg_pnl:+.4f} |\n"
        return header + rows

    content = f"""# Edge System — Wiki

_Sidst opdateret: {datetime.now().strftime("%Y-%m-%d %H:%M")}_

## Entry Price Buckets

{bucket_table(ep_buckets)}

## Forecast Margin Buckets

{bucket_table(fm_buckets)}

## Fortolkning

- **Entry price** bør helst være i zoner med positiv avg PnL
- **Forecast margin** > 0.05 korrelerer historisk med bedre resultater
- Buckets med <5 trades er statistisk usikre — hold øje med dem

## Edge-regler (auto-genereret)

{_generate_edge_rules(trades)}
"""
    _write_md(EDGE_MD, content)


def _generate_edge_rules(trades: list[dict]) -> str:
    if not trades:
        return "_Ingen data endnu_"
    rules = []

    fm_winners = [_safe(t["forecast_margin"]) for t in trades if t["is_win"]]
    fm_losers  = [_safe(t["forecast_margin"]) for t in trades if not t["is_win"]]
    avg = lambda lst: sum(lst) / len(lst) if lst else 0
    if fm_winners and fm_losers and avg(fm_winners) > avg(fm_losers) * 1.2:
        rules.append(f"- **FM regel:** Entries med FM > {avg(fm_winners):.3f} (winner-avg) har bedre odds")

    vcom_winners = [_safe(t["vc_om_diff"]) for t in trades if t["is_win"]]
    vcom_losers  = [_safe(t["vc_om_diff"]) for t in trades if not t["is_win"]]
    if vcom_winners and vcom_losers and avg(vcom_winners) < avg(vcom_losers) * 0.8:
        rules.append(f"- **VC/OM regel:** Lav vc_om_diff ({avg(vcom_winners):.3f} vs {avg(vcom_losers):.3f}) → bedre entries")

    if not rules:
        rules.append("- Ikke nok data til at generere regler endnu")
    return "\n".join(rules)


def update_ml_agent_md(trades: list[dict]) -> None:
    bins = [0.0, 0.4, 0.5, 0.6, 0.7, 0.8, 1.01]
    bin_labels = ["<0.40", "0.40–0.50", "0.50–0.60", "0.60–0.70", "0.70–0.80", "≥0.80"]

    bucket_data: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        ml = _safe(t["ml_score"])
        for i in range(len(bins) - 1):
            if bins[i] <= ml < bins[i + 1]:
                bucket_data[bin_labels[i]].append(t)
                break

    table = "| ML Score Bucket | Trades | Faktisk Win Rate | Kalibrerings-fejl |\n|---|---|---|---|\n"
    for label in bin_labels:
        bt    = bucket_data.get(label, [])
        total = len(bt)
        if total == 0:
            table += f"| {label} | 0 | N/A | N/A |\n"
            continue
        wins     = sum(1 for t in bt if t["is_win"])
        fact_wr  = _pct(wins, total)
        mid_ml   = sum(_safe(t["ml_score"]) for t in bt) / total
        cal_err  = fact_wr / 100 - mid_ml
        table   += f"| {label} | {total} | {fact_wr:.1f}% | {cal_err:+.3f} |\n"

    # Pearson korrelation ml_score vs outcome
    pairs = [(t["ml_score"], 1 if t["is_win"] else 0) for t in trades
             if t["ml_score"] is not None]
    corr_str = "N/A"
    if len(pairs) >= 5:
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        mx = sum(xs) / len(xs)
        my = sum(ys) / len(ys)
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        den = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
        corr_str = f"{num / den:.3f}" if den else "N/A"

    content = f"""# ML Agent — Wiki

_Sidst opdateret: {datetime.now().strftime("%Y-%m-%d %H:%M")}_

## ML Score vs Faktisk Outcome

{table}

## Pearson Korrelation (ML score ↔ outcome)

`r = {corr_str}`

- r > 0.3 = god korrelation
- r 0.1–0.3 = svag korrelation
- r < 0.1 = ingen korrelation

## Kalibrering

Positiv kalibrerings-fejl = ML er for pessimistisk (undervurderer win-sandsynlighed)
Negativ kalibrerings-fejl = ML er for optimistisk (overvurderer win-sandsynlighed)
"""
    _write_md(ML_MD, content)


# ══════════════════════════════════════════════════════════════════════════════
# 4. LÆRINGS-LOOP
# ══════════════════════════════════════════════════════════════════════════════

def _split_by_4weeks(trades: list[dict]):
    now   = datetime.now()
    cutoff = now - timedelta(weeks=4)
    recent = [t for t in trades if t["timestamp_exit"] and t["timestamp_exit"] >= cutoff]
    older  = [t for t in trades if t["timestamp_exit"] and t["timestamp_exit"] < cutoff]
    return recent, older


def _city_win_rates(trades: list[dict]) -> dict[str, float]:
    by_city: dict[str, list] = defaultdict(list)
    for t in trades:
        by_city[str(t.get("city") or "Ukendt")].append(t["is_win"])
    return {city: _pct(sum(wins), len(wins)) for city, wins in by_city.items()}


def _vc_om_agreement_analysis(trades: list[dict]) -> str:
    AGREE_THRESHOLD = 0.05
    agreed    = [t for t in trades if abs(_safe(t["vc_om_diff"])) < AGREE_THRESHOLD]
    disagreed = [t for t in trades if abs(_safe(t["vc_om_diff"])) >= AGREE_THRESHOLD]

    if not trades:
        return "_Ingen data_"

    wr_agreed    = _pct(sum(1 for t in agreed if t["is_win"]), len(agreed)) if agreed else 0
    wr_disagreed = _pct(sum(1 for t in disagreed if t["is_win"]), len(disagreed)) if disagreed else 0

    verdict = ""
    if agreed and disagreed:
        if wr_agreed > wr_disagreed + 5:
            verdict = "✅ **Vi vinder mere når VC og OM er enige** — overvej at filtrere på vc_om_diff < 0.05"
        elif wr_disagreed > wr_agreed + 5:
            verdict = "⚠️ **Vi vinder faktisk mere ved uenighed** — uenighed kan signalere mis-priset marked"
        else:
            verdict = "🔄 Ingen klar forskel — VC/OM enighed er neutral faktor i denne periode"

    return (
        f"- **Enig** (|vc_om_diff| < {AGREE_THRESHOLD}): {len(agreed)} trades, win rate {wr_agreed:.1f}%\n"
        f"- **Uenig**: {len(disagreed)} trades, win rate {wr_disagreed:.1f}%\n\n"
        f"{verdict}"
    )


def _time_of_day_analysis(trades: list[dict]) -> str:
    slots = {"Morgen (06-12)": [], "Eftermiddag (12-18)": [], "Aften (18-24)": [], "Nat (00-06)": []}
    for t in trades:
        ts = t["timestamp_entry"]
        if ts is None:
            continue
        h = ts.hour
        if 6  <= h < 12: slots["Morgen (06-12)"].append(t)
        elif 12 <= h < 18: slots["Eftermiddag (12-18)"].append(t)
        elif 18 <= h < 24: slots["Aften (18-24)"].append(t)
        else:              slots["Nat (00-06)"].append(t)

    rows = ""
    best_wr = 0.0
    best_slot = ""
    for slot, st in slots.items():
        if not st:
            rows += f"| {slot} | 0 | N/A | N/A |\n"
            continue
        wins    = sum(1 for t in st if t["is_win"])
        wr      = _pct(wins, len(st))
        avg_pnl = sum(_safe(t["pnl"]) for t in st) / len(st)
        rows   += f"| {slot} | {len(st)} | {wr:.1f}% | {avg_pnl:+.4f} |\n"
        if wr > best_wr:
            best_wr, best_slot = wr, slot

    return (
        "| Tidspunkt | Trades | Win Rate | Avg PnL |\n|---|---|---|---|\n"
        + rows
        + (f"\n**Bedste tidspunkt:** {best_slot} ({best_wr:.1f}%)" if best_slot else "")
    )


def _days_to_expiry_analysis(trades: list[dict]) -> str:
    short = [t for t in trades if t["days_to_expiry"] is not None and t["days_to_expiry"] <= 7]
    medium = [t for t in trades if t["days_to_expiry"] is not None and 7 < t["days_to_expiry"] <= 30]
    long_  = [t for t in trades if t["days_to_expiry"] is not None and t["days_to_expiry"] > 30]

    def row(label, group):
        if not group:
            return f"| {label} | 0 | N/A | N/A |\n"
        wins    = sum(1 for t in group if t["is_win"])
        wr      = _pct(wins, len(group))
        avg_pnl = sum(_safe(t["pnl"]) for t in group) / len(group)
        return f"| {label} | {len(group)} | {wr:.1f}% | {avg_pnl:+.4f} |\n"

    return (
        "| Horisont | Trades | Win Rate | Avg PnL |\n|---|---|---|---|\n"
        + row("Kort (≤7 dage)", short)
        + row("Mellem (8–30 dage)", medium)
        + row("Lang (>30 dage)", long_)
    )


def _volume_analysis(trades: list[dict]) -> str:
    vols = [_safe(t["volume"]) for t in trades if t["volume"] is not None]
    if not vols:
        return "_Ingen volumen-data_"
    median_vol = sorted(vols)[len(vols) // 2]
    high_vol = [t for t in trades if _safe(t["volume"]) >= median_vol]
    low_vol  = [t for t in trades if _safe(t["volume"]) <  median_vol]

    def row(label, group):
        if not group:
            return f"| {label} | 0 | N/A | N/A |\n"
        wins    = sum(1 for t in group if t["is_win"])
        wr      = _pct(wins, len(group))
        avg_pnl = sum(_safe(t["pnl"]) for t in group) / len(group)
        return f"| {label} | {len(group)} | {wr:.1f}% | {avg_pnl:+.4f} |\n"

    return (
        f"_(Median volumen: {median_vol:.0f})_\n\n"
        "| Volumen | Trades | Win Rate | Avg PnL |\n|---|---|---|---|\n"
        + row(f"Høj (≥{median_vol:.0f})", high_vol)
        + row(f"Lav (<{median_vol:.0f})", low_vol)
    )


def _period_compare(recent: list[dict], older: list[dict]) -> str:
    def stats(group):
        if not group:
            return {"n": 0, "wr": 0.0, "avg_pnl": 0.0, "avg_ml": 0.0}
        wins    = sum(1 for t in group if t["is_win"])
        avg_pnl = sum(_safe(t["pnl"]) for t in group) / len(group)
        avg_ml  = sum(_safe(t["ml_score"]) for t in group) / len(group)
        return {"n": len(group), "wr": _pct(wins, len(group)), "avg_pnl": avg_pnl, "avg_ml": avg_ml}

    r = stats(recent)
    o = stats(older)

    if o["n"] == 0:
        return "_Ikke nok historisk data til sammenligning_"

    wr_delta  = r["wr"] - o["wr"]
    pnl_delta = r["avg_pnl"] - o["avg_pnl"]

    trend_wr  = "📈 forbedret" if wr_delta > 2 else ("📉 forværret" if wr_delta < -2 else "🔄 stabilt")
    trend_pnl = "📈 bedre" if pnl_delta > 0 else "📉 dårligere"

    return (
        "| Metrik | Denne periode | Forrige periode | Trend |\n|---|---|---|---|\n"
        f"| Trades | {r['n']} | {o['n']} | — |\n"
        f"| Win Rate | {r['wr']:.1f}% | {o['wr']:.1f}% | {trend_wr} |\n"
        f"| Avg PnL | {r['avg_pnl']:+.4f} | {o['avg_pnl']:+.4f} | {trend_pnl} |\n"
        f"| Avg ML | {r['avg_ml']:.3f} | {o['avg_ml']:.3f} | — |\n"
    )


def _city_forecast_trend(trades: list[dict]) -> str:
    now    = datetime.now()
    cutoff = now - timedelta(weeks=4)
    recent = [t for t in trades if t["timestamp_exit"] and t["timestamp_exit"] >= cutoff]
    older  = [t for t in trades if t["timestamp_exit"] and t["timestamp_exit"] < cutoff]

    recent_rates = _city_win_rates(recent)
    older_rates  = _city_win_rates(older)

    all_cities = set(recent_rates) | set(older_rates)
    if not all_cities:
        return "_Ingen data_"

    rows = ""
    for city in sorted(all_cities):
        r = recent_rates.get(city, 0)
        o = older_rates.get(city, 0)
        delta = r - o
        trend = "↑" if delta > 5 else ("↓" if delta < -5 else "→")
        rows += f"| {city} | {o:.1f}% | {r:.1f}% | {delta:+.1f}% {trend} |\n"

    return (
        "| By | Forrige 4 uger | Seneste 4 uger | Δ |\n|---|---|---|---|\n"
        + rows
    )


def _hvad_laerte_vi(trades: list[dict], recent: list[dict], older: list[dict]) -> str:
    insights = []

    # Insight 1: VC/OM enighed
    agreed = [t for t in recent if abs(_safe(t["vc_om_diff"])) < 0.05]
    if agreed:
        wr_a = _pct(sum(1 for t in agreed if t["is_win"]), len(agreed))
        insights.append(f"**VC/OM enighed:** {len(agreed)} entries med |vc_om_diff| < 0.05 → {wr_a:.1f}% win rate")

    # Insight 2: ML kalibrering
    high_ml = [t for t in recent if _safe(t["ml_score"]) >= 0.70]
    if high_ml:
        wr_ml = _pct(sum(1 for t in high_ml if t["is_win"]), len(high_ml))
        insights.append(f"**Høj ML (≥0.70):** {len(high_ml)} entries → {wr_ml:.1f}% faktisk win rate")

    # Insight 3: Periode-trend
    if recent and older:
        wr_r = _pct(sum(1 for t in recent if t["is_win"]), len(recent))
        wr_o = _pct(sum(1 for t in older if t["is_win"]), len(older))
        delta = wr_r - wr_o
        direction = "forbedret" if delta > 0 else "forværret"
        insights.append(f"**Performance trend:** Win rate {direction} med {abs(delta):.1f}% ift. forrige periode")

    if not insights:
        return "- Ikke nok data til at generere lærings-indsigter\n"
    return "\n".join(f"- {i}" for i in insights) + "\n"


def run_learning_loop(trades: list[dict]) -> None:
    recent, older = _split_by_4weeks(trades)

    section = f"""### Lærings-Loop — {datetime.now().strftime("%Y-%m-%d %H:%M")}

#### Forecast fejl pr. by (forbedring over 4 uger)

{_city_forecast_trend(trades)}

#### VC vs OM enighed

{_vc_om_agreement_analysis(trades)}

#### Tid på dagen

{_time_of_day_analysis(trades)}

#### Dage til udløb

{_days_to_expiry_analysis(trades)}

#### Volumen analyse

{_volume_analysis(trades)}

#### Periode sammenligning

{_period_compare(recent, older)}

#### Hvad lærte vi

{_hvad_laerte_vi(trades, recent, older)}
"""
    _append_log(section)


# ══════════════════════════════════════════════════════════════════════════════
# 5. WEEKLY BRAIN UPDATE (kører kun søndag)
# ══════════════════════════════════════════════════════════════════════════════

def _top3_insights(trades: list[dict]) -> str:
    insights = []

    # 1 — Bedste by
    by_city: dict[str, list] = defaultdict(list)
    for t in trades:
        by_city[str(t.get("city") or "Ukendt")].append(t)
    city_wr = {c: _pct(sum(1 for t in ts if t["is_win"]), len(ts)) for c, ts in by_city.items() if len(ts) >= 3}
    if city_wr:
        best = max(city_wr, key=city_wr.get)
        insights.append(f"**Bedste by:** {best} med {city_wr[best]:.1f}% win rate (min. 3 trades)")

    # 2 — ML kalibrering
    high_ml = [t for t in trades if _safe(t["ml_score"]) >= 0.70]
    if high_ml:
        wr = _pct(sum(1 for t in high_ml if t["is_win"]), len(high_ml))
        gap = wr - 70
        if abs(gap) > 5:
            direction = "undervurderer" if gap > 0 else "overvurderer"
            insights.append(f"**ML kalibrering:** ML {direction} med {abs(gap):.1f}% (high-confidence bucket)")

    # 3 — VC/OM signal
    agreed = [t for t in trades if abs(_safe(t["vc_om_diff"])) < 0.05]
    disagreed = [t for t in trades if abs(_safe(t["vc_om_diff"])) >= 0.05]
    if agreed and disagreed:
        wr_a = _pct(sum(1 for t in agreed if t["is_win"]), len(agreed))
        wr_d = _pct(sum(1 for t in disagreed if t["is_win"]), len(disagreed))
        if abs(wr_a - wr_d) > 5:
            better = "enig" if wr_a > wr_d else "uenig"
            insights.append(f"**VC/OM signal:** Vi vinder mere ved {better} signal ({max(wr_a,wr_d):.1f}% vs {min(wr_a,wr_d):.1f}%)")

    if not insights:
        insights.append("Ikke nok data til top-3 indsigter denne uge")
    return "\n".join(f"{i+1}. {ins}" for i, ins in enumerate(insights)) + "\n"


def _top3_improvements(trades: list[dict]) -> str:
    improvements = []
    recent, _ = _split_by_4weeks(trades)

    # 1 — Dårlig by
    by_city: dict[str, list] = defaultdict(list)
    for t in recent:
        by_city[str(t.get("city") or "Ukendt")].append(t)
    bad_cities = {c: _pct(sum(1 for t in ts if t["is_win"]), len(ts))
                  for c, ts in by_city.items() if len(ts) >= 3 and
                  _pct(sum(1 for t in ts if t["is_win"]), len(ts)) < 40}
    if bad_cities:
        worst = min(bad_cities, key=bad_cities.get)
        improvements.append(f"Overvej at reducere eksponering i **{worst}** ({bad_cities[worst]:.1f}% win rate)")

    # 2 — Lav FM entries
    low_fm = [t for t in recent if _safe(t["forecast_margin"]) < 0.02]
    if len(low_fm) > 3:
        improvements.append(f"**{len(low_fm)} entries** med FM < 0.02 — sæt minimums-FM filter")

    # 3 — Kalibrering
    high_ml_loses = [t for t in recent if _safe(t["ml_score"]) >= 0.70 and not t["is_win"]]
    if len(high_ml_loses) > 2:
        improvements.append(f"**{len(high_ml_loses)} high-ML tabs** — revurder ML threshold eller feature importance")

    if not improvements:
        improvements.append("Ingen specifikke forbedringer identificeret — hold kursen")
    return "\n".join(f"{i+1}. {imp}" for i, imp in enumerate(improvements)) + "\n"


def run_weekly_brain(trades: list[dict]) -> None:
    now      = datetime.now()
    week_tag = _week_tag(now)
    out_path = WEEKLY_DIR / f"{week_tag}.md"

    # Filtrér til indeværende uge
    week_start = now - timedelta(days=now.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    week_trades = [t for t in trades if t["timestamp_exit"] and t["timestamp_exit"] >= week_start]

    total   = len(week_trades)
    winners = [t for t in week_trades if t["is_win"]]
    losers  = [t for t in week_trades if not t["is_win"]]
    wr      = _pct(len(winners), total)
    total_pnl = sum(_safe(t["pnl"]) for t in week_trades)
    avg_ml  = sum(_safe(t["ml_score"]) for t in week_trades) / total if total else 0

    content = f"""# Weekly Brain — {week_tag}

_Genereret: {now.strftime("%Y-%m-%d %H:%M")}_

## Uge-opsummering

| Metrik | Værdi |
|---|---|
| Trades | {total} |
| Win Rate | {wr:.1f}% |
| Winners | {len(winners)} |
| Losers | {len(losers)} |
| Total PnL | {total_pnl:+.4f} |
| Avg ML Score | {avg_ml:.3f} |

---

## Top 3 Indsigter

{_top3_insights(week_trades)}

---

## Top 3 Forbedringer til næste uge

{_top3_improvements(week_trades)}

---

## Detaljeret analyse

### Byer denne uge

{_city_forecast_trend(week_trades) if len(week_trades) > 5 else "_For få trades til by-trend_"}

### VC/OM enighed

{_vc_om_agreement_analysis(week_trades)}

### ML kalibrering

{_ml_calibration_summary(week_trades)}

---

_Næste weekly brain: {(_week_tag(now + timedelta(weeks=1)))}_
"""
    _write_md(out_path, content)


def _ml_calibration_summary(trades: list[dict]) -> str:
    if not trades:
        return "_Ingen data_"
    high = [t for t in trades if _safe(t["ml_score"]) >= 0.65]
    low  = [t for t in trades if _safe(t["ml_score"]) <  0.65]
    if not high and not low:
        return "_Ingen data_"
    wr_h = _pct(sum(1 for t in high if t["is_win"]), len(high)) if high else 0
    wr_l = _pct(sum(1 for t in low  if t["is_win"]), len(low))  if low  else 0
    return (
        f"- **ML ≥ 0.65:** {len(high)} trades → {wr_h:.1f}% win rate\n"
        f"- **ML < 0.65:** {len(low)} trades → {wr_l:.1f}% win rate\n"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Hoved-job der kører 2x dagligt
# ══════════════════════════════════════════════════════════════════════════════

def run_analysis_job() -> None:
    log.info("=== Vault Agent analyse-job starter ===")
    _ensure_dirs()

    state = _load_state()
    since = _parse_timestamp(state.get("last_run")) if state.get("last_run") else None
    now   = datetime.now()

    trades = _load_trades()
    if not trades:
        log.warning("Ingen trades fundet — springer over.")
        return

    # 2. Trade analyse (kun nye trades siden sidst)
    run_trade_analysis(trades, since or (now - timedelta(hours=12)))

    # 3. Pattern detection (altid på alle trades)
    update_cities_md(trades)
    update_edge_system_md(trades)
    update_ml_agent_md(trades)

    # 4. Lærings-loop
    run_learning_loop(trades)

    # 5. Weekly brain — kun om søndagen
    if now.weekday() == 6:  # 6 = søndag
        run_weekly_brain(trades)
        log.info("Weekly brain skrevet for %s", _week_tag(now))

    # Gem state
    state["last_run"] = now.isoformat()
    _save_state(state)
    log.info("=== Analyse-job færdig — næste kørsel: %s ===",
             "20:00" if now.hour < 14 else "08:00 i morgen")


# ══════════════════════════════════════════════════════════════════════════════
# Scheduler — 08:00 og 20:00
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    log.info("Vault Agent starter. Kører kl. 08:00 og 20:00.")
    _ensure_dirs()

    schedule.every().day.at("08:00").do(run_analysis_job)
    schedule.every().day.at("20:00").do(run_analysis_job)

    # Kør straks ved opstart så vi ikke venter til næste schedulede tidspunkt
    log.info("Kører initial analyse ved opstart...")
    run_analysis_job()

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
