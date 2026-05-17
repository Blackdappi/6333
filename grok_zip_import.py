"""
grok_zip_import.py — Polly: Importer Grok-eksport ZIP til Obsidian vault.

Kørsel:
    python grok_zip_import.py

Gør automatisk:
  1. Finder og udpakker grok_export.zip
  2. Analyserer struktur og filindhold
  3. Konverterer hver samtale til .md med frontmatter
  4. Bygger index.md med Polly-relevante samtaler øverst
"""

import hashlib
import json
import os
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from textwrap import dedent

# ── Paths ─────────────────────────────────────────────────────────────────────
ZIP_PATH    = Path(r"C:\Users\bebob\Downloads\grok_export.zip")
EXTRACT_DIR = Path(r"C:\Users\bebob\Downloads\grok_export")
VAULT_GROK  = Path(r"C:\Users\bebob\ObsidianVault\Polly\grok")
INDEX_FILE  = VAULT_GROK / "index.md"

POLLY_KEYWORDS = [
    "polymarket", "edge", "forecast", "ml", "trading", "weather", "polly",
    "bot", "temperature", "staging", "live", "bankroll", "position",
    "win rate", "trade", "market", "probability", "pnl", "city",
    "volume", "resolution", "entry", "exit", "score",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    print(f"{datetime.now().strftime('%H:%M:%S')}  {msg}", flush=True)


def _slug(text: str, maxlen: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", str(text)).strip()
    return re.sub(r"\s+", "-", s).lower()[:maxlen]


def _is_polly(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in POLLY_KEYWORDS)


def _auto_tags(text: str) -> list[str]:
    t    = text.lower()
    tags = ["grok"]
    if any(k in t for k in ["polymarket", "polly", "market", "trade", "bet"]):
        tags.append("polly")
    if any(k in t for k in ["forecast", "probability", "prediction"]):
        tags.append("forecasting")
    if any(k in t for k in ["python", "code", "script", "def ", "class "]):
        tags.append("code")
    if any(k in t for k in ["weather", "temperature", "rain"]):
        tags.append("weather")
    if any(k in t for k in ["ml", "machine learning", "model", "score", "feature"]):
        tags.append("ml")
    if any(k in t for k in ["edge", "strategy", "bankroll"]):
        tags.append("strategy")
    return list(dict.fromkeys(tags))


def _parse_ts(val) -> str:
    """Returner YYYY-MM-DD streng fra diverse timestamp-formater."""
    if not val:
        return datetime.now().strftime("%Y-%m-%d")
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(str(val)[:26], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Prøv unix timestamp
    try:
        return datetime.fromtimestamp(float(val)).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


# ── STEP 1: Udpak ZIP ─────────────────────────────────────────────────────────

def step1_extract() -> bool:
    if not ZIP_PATH.exists():
        log(f"❌ ZIP ikke fundet: {ZIP_PATH}")
        log("   Sørg for at filen ligger i Downloads og hedder grok_export.zip")
        return False

    log(f"📦 Fandt ZIP: {ZIP_PATH} ({ZIP_PATH.stat().st_size / 1024:.0f} KB)")
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(ZIP_PATH, "r") as zf:
        names = zf.namelist()
        log(f"   Indeholder {len(names)} filer/mapper")
        zf.extractall(EXTRACT_DIR)

    log(f"✅ Udpakket til: {EXTRACT_DIR}")
    return True


# ── STEP 2: Analyser struktur ─────────────────────────────────────────────────

def step2_analyze() -> dict:
    log("\n📊 ANALYSERER INDHOLD:")
    log("-" * 50)

    all_files = list(EXTRACT_DIR.rglob("*"))
    files     = [f for f in all_files if f.is_file()]

    by_ext: dict[str, list[Path]] = {}
    for f in files:
        ext = f.suffix.lower() or "(ingen extension)"
        by_ext.setdefault(ext, []).append(f)

    for ext, flist in sorted(by_ext.items(), key=lambda x: -len(x[1])):
        log(f"   {ext:20s}  {len(flist)} filer")

    # Vis de første 20 filnavne
    log(f"\n   Første filer:")
    for f in files[:20]:
        rel = f.relative_to(EXTRACT_DIR)
        log(f"   • {rel}")
    if len(files) > 20:
        log(f"   ... og {len(files) - 20} flere")

    # Prøv at læse JSON-struktur
    json_files = by_ext.get(".json", [])
    structure  = {"files": files, "json_files": json_files, "by_ext": by_ext}

    if json_files:
        log(f"\n   JSON-filer fundet: {len(json_files)}")
        for jf in json_files[:3]:
            try:
                data  = json.loads(jf.read_text(encoding="utf-8"))
                keys  = list(data.keys()) if isinstance(data, dict) else f"liste med {len(data)} elementer"
                log(f"   {jf.name}: {keys}")
            except Exception as exc:
                log(f"   {jf.name}: Kunne ikke parse ({exc})")

    log("-" * 50)
    return structure


# ── STEP 3: Parse samtaler ────────────────────────────────────────────────────

def _parse_grok_json(path: Path) -> list[dict]:
    """
    Håndterer alle kendte Grok JSON-eksport formater.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))

    # Format A: { "conversations": [...] }
    if isinstance(raw, dict) and "conversations" in raw:
        return raw["conversations"]

    # Format B: direkte liste af samtaler
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        if "messages" in raw[0] or "entries" in raw[0] or "turns" in raw[0]:
            return raw

    # Format C: enkelt samtale
    if isinstance(raw, dict) and any(k in raw for k in ("messages", "entries", "turns")):
        return [raw]

    # Format D: { "data": { "conversations": [...] } }
    if isinstance(raw, dict) and "data" in raw:
        inner = raw["data"]
        if isinstance(inner, dict) and "conversations" in inner:
            return inner["conversations"]
        if isinstance(inner, list):
            return inner

    # Format E: flat dict af samtale-id → samtale
    if isinstance(raw, dict):
        candidates = [v for v in raw.values() if isinstance(v, dict) and "messages" in v]
        if candidates:
            return candidates

    return []


def _extract_messages(conv: dict) -> list[dict]:
    """Udtræk beskeder uanset nøgle-navn."""
    msgs_raw = (
        conv.get("messages")
        or conv.get("entries")
        or conv.get("turns")
        or conv.get("history")
        or []
    )
    messages = []
    for m in msgs_raw:
        if not isinstance(m, dict):
            continue
        content = (
            m.get("content")
            or m.get("text")
            or m.get("message")
            or m.get("body")
            or ""
        )
        if isinstance(content, list):
            # content kan være [{"type": "text", "text": "..."}]
            parts = []
            for part in content:
                if isinstance(part, dict):
                    parts.append(part.get("text") or part.get("content") or "")
                elif isinstance(part, str):
                    parts.append(part)
            content = "\n".join(parts)

        role_raw = (
            m.get("role")
            or m.get("sender")
            or m.get("author")
            or m.get("from")
            or "assistant"
        )
        role_str = str(role_raw).lower()
        role     = "user" if role_str in ("human", "user", "you", "human_turn") else "assistant"

        if content and str(content).strip():
            messages.append({"role": role, "content": str(content).strip()})
    return messages


def _conv_title(conv: dict, fallback: str = "Samtale") -> str:
    t = (
        conv.get("title")
        or conv.get("name")
        or conv.get("subject")
        or ""
    )
    if t:
        return str(t).strip()[:120]
    # Generér fra første user-besked
    msgs = _extract_messages(conv)
    for m in msgs:
        if m["role"] == "user":
            return m["content"][:80].replace("\n", " ").strip()
    return fallback


def _conv_date(conv: dict) -> str:
    for key in ("create_time", "created_at", "timestamp", "date", "updated_at"):
        val = conv.get(key)
        if val:
            return _parse_ts(val)
    return datetime.now().strftime("%Y-%m-%d")


def _conv_id(conv: dict, path: Path) -> str:
    raw = conv.get("id") or conv.get("conversation_id") or str(path) + _conv_title(conv)
    return hashlib.md5(str(raw).encode()).hexdigest()[:12]


def _build_markdown(title: str, date: str, messages: list[dict], url: str = "") -> str:
    full_text = "\n".join(m["content"] for m in messages)
    tags      = _auto_tags(full_text)
    polly_rel = "ja" if _is_polly(full_text) else "nej"

    fm = dedent(f"""\
        ---
        title: "{title}"
        date: {date}
        platform: grokcom
        polly_relevant: {polly_rel}
        tags: [{", ".join(tags)}]
        messages: {len(messages)}
        url: "{url}"
        ---
    """)

    body = [f"# {title}\n\n_grokcom | {date} | {len(messages)} beskeder_\n"]

    for msg in messages:
        role    = msg["role"]
        content = msg["content"].strip()
        if not content:
            continue
        speaker = "**Du**" if role == "user" else "**Grok**"
        body.append(f"\n---\n\n{speaker}\n\n{content}\n")

    polly_msgs = [m for m in messages if _is_polly(m["content"])]
    if polly_msgs:
        body.append("\n---\n\n## Polly-relevante passager\n")
        for m in polly_msgs[:5]:
            snippet = m["content"][:400].replace("\n", " ")
            body.append(f"\n> {snippet}{'…' if len(m['content']) > 400 else ''}\n")

    return fm + "\n" + "".join(body)


def _save_conv(title: str, date: str, messages: list[dict], url: str = "") -> Path:
    VAULT_GROK.mkdir(parents=True, exist_ok=True)
    filename = f"{date}-{_slug(title)}.md"
    out      = VAULT_GROK / filename
    counter  = 1
    while out.exists():
        out = VAULT_GROK / f"{date}-{_slug(title)}-{counter}.md"
        counter += 1
    out.write_text(_build_markdown(title, date, messages, url), encoding="utf-8")
    return out


def step3_import(structure: dict) -> list[dict]:
    log("\n📥 IMPORTERER SAMTALER:")
    log("-" * 50)

    imported  = []
    skipped   = []
    errors    = []
    seen_ids  = set()

    files = structure["files"]
    json_files = structure.get("json_files", [])

    # ── JSON-filer ────────────────────────────────────────────────────────────
    for jf in json_files:
        try:
            convs = _parse_grok_json(jf)
            if not convs:
                log(f"  ⚠ {jf.name}: ingen samtaler fundet (tomt eller ukendt format)")
                continue
            log(f"  📂 {jf.name}: {len(convs)} samtaler")

            for i, conv in enumerate(convs, 1):
                cid   = _conv_id(conv, jf)
                if cid in seen_ids:
                    continue
                seen_ids.add(cid)

                title    = _conv_title(conv, fallback=f"Samtale-{i}")
                date     = _conv_date(conv)
                messages = _extract_messages(conv)
                url      = conv.get("url") or conv.get("link") or ""

                if not messages:
                    log(f"    [{i}/{len(convs)}] ⚠ Ingen beskeder: {title[:50]}")
                    skipped.append(title)
                    continue

                out = _save_conv(title, date, messages, url)
                polly_tag = " 🟢 POLLY" if _is_polly("".join(m["content"] for m in messages)) else ""
                log(f"    [{i}/{len(convs)}] ✅ {out.name} ({len(messages)} beskeder){polly_tag}")

                imported.append({
                    "title":  title,
                    "date":   date,
                    "file":   out.name,
                    "polly":  _is_polly("".join(m["content"] for m in messages)),
                    "msgs":   len(messages),
                })

        except Exception as exc:
            log(f"  ❌ Fejl i {jf.name}: {exc}")
            errors.append(str(exc))

    # ── Ikke-JSON tekstfiler ──────────────────────────────────────────────────
    text_files = [f for f in files if f.suffix.lower() in (".txt", ".md", ".markdown", ".html")
                  and f not in json_files]

    if text_files:
        log(f"\n  Tekst/HTML-filer: {len(text_files)}")

    for tf in text_files:
        try:
            content = tf.read_text(encoding="utf-8", errors="replace")

            # Enkel HTML → tekst stripping
            if tf.suffix.lower() == ".html":
                content = re.sub(r"<[^>]+>", " ", content)
                content = re.sub(r"\s+", " ", content).strip()

            if len(content) < 50:
                continue

            # Forsøg at splittte på You:/Grok: mønstre
            pattern  = re.compile(r"(?m)^(You|Grok|Human|Assistant)\s*[:\n]", re.IGNORECASE)
            parts    = pattern.split(content)
            messages = []
            if len(parts) >= 3:
                i = 1
                while i < len(parts) - 1:
                    role_raw = parts[i].lower()
                    role     = "user" if role_raw in ("you", "human") else "assistant"
                    text     = parts[i + 1].strip()
                    if text:
                        messages.append({"role": role, "content": text})
                    i += 2
            else:
                messages = [{"role": "raw", "content": content.strip()}]

            if not messages:
                continue

            title = tf.stem.replace("_", " ").replace("-", " ").title()
            date  = datetime.now().strftime("%Y-%m-%d")
            cid   = hashlib.md5((title + content[:100]).encode()).hexdigest()[:12]
            if cid in seen_ids:
                continue
            seen_ids.add(cid)

            out = _save_conv(title, date, messages)
            log(f"  ✅ {out.name} ({len(messages)} beskeder) [fra {tf.suffix}]")
            imported.append({
                "title": title, "date": date, "file": out.name,
                "polly": _is_polly(content),
                "msgs":  len(messages),
            })

        except Exception as exc:
            log(f"  ❌ Fejl i {tf.name}: {exc}")
            errors.append(str(exc))

    log(f"\n  Total importeret: {len(imported)}")
    log(f"  Sprunget over (ingen beskeder): {len(skipped)}")
    if errors:
        log(f"  Fejl: {len(errors)}")
    log("-" * 50)
    return imported


# ── STEP 4: Byg index.md ──────────────────────────────────────────────────────

def step4_build_index() -> None:
    log("\n📋 BYGGER index.md:")

    files = sorted(
        [f for f in VAULT_GROK.glob("*.md") if f.name != "index.md"],
        key=lambda f: f.name,
    )

    entries = []
    for f in files:
        try:
            lines  = f.read_text(encoding="utf-8").splitlines()
            entry  = {"file": f.name, "title": f.stem, "date": "", "platform": "grokcom", "polly": False, "msgs": 0}
            in_fm  = False
            fm_end = 0
            for li, line in enumerate(lines):
                if line.strip() == "---":
                    if not in_fm:
                        in_fm = True
                    else:
                        fm_end = li
                        break
                    continue
                if not in_fm:
                    continue
                if line.startswith("title:"):
                    entry["title"] = line.split(":", 1)[1].strip().strip('"')
                elif line.startswith("date:"):
                    entry["date"] = line.split(":", 1)[1].strip()
                elif line.startswith("platform:"):
                    entry["platform"] = line.split(":", 1)[1].strip()
                elif line.startswith("polly_relevant:"):
                    entry["polly"] = line.split(":", 1)[1].strip().lower() == "ja"
                elif line.startswith("messages:"):
                    try:
                        entry["msgs"] = int(line.split(":", 1)[1].strip())
                    except Exception:
                        pass
            entries.append(entry)
        except Exception:
            pass

    entries.sort(key=lambda e: e["date"] or "")
    polly_entries = [e for e in entries if e["polly"]]

    def table(ents):
        if not ents:
            return "_Ingen samtaler_\n"
        rows = "| Samtale | Dato | Platform | Beskeder | Polly |\n|---|---|---|---|---|\n"
        for e in ents:
            stem = Path(e["file"]).stem
            pol  = "✅" if e["polly"] else "—"
            rows += (
                f"| [[grok/{stem}\\|{e['title'][:60]}]] "
                f"| {e['date']} | {e['platform']} | {e['msgs']} | {pol} |\n"
            )
        return rows

    polly_section = ""
    if polly_entries:
        polly_section = f"## ⭐ Polly-relevante samtaler ({len(polly_entries)})\n\n{table(polly_entries)}\n"

    content = dedent(f"""\
        # Grok Samtaler — Indeks

        _Sidst opdateret: {datetime.now().strftime("%Y-%m-%d %H:%M")}_

        ## Statistik

        | Metrik | Antal |
        |---|---|
        | Total samtaler | {len(entries)} |
        | Polly-relevante | {len(polly_entries)} |
        | Importeret fra | grok_export.zip |

        ---

        {polly_section}## Alle samtaler (kronologisk)

        {table(entries)}
        ---
        _Importeret af grok_zip_import.py_
    """)

    INDEX_FILE.write_text(content, encoding="utf-8")
    log(f"✅ index.md gemt: {INDEX_FILE}")
    log(f"   {len(entries)} samtaler | {len(polly_entries)} Polly-relevante")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("GROK ZIP IMPORT — starter")
    print(f"ZIP:    {ZIP_PATH}")
    print(f"Output: {VAULT_GROK}")
    print("=" * 60)

    # Step 1
    if not step1_extract():
        sys.exit(1)

    # Step 2
    structure = step2_analyze()

    # Step 3
    imported = step3_import(structure)

    # Step 4
    step4_build_index()

    # Rapport
    polly_n = sum(1 for e in imported if e["polly"])
    print()
    print("=" * 60)
    print("✅  IMPORT FÆRDIG")
    print("=" * 60)
    print(f"  Importeret:      {len(imported)} samtaler")
    print(f"  Polly-relevante: {polly_n}")
    print(f"  Gemt i:          {VAULT_GROK}")
    print(f"  Indeks:          {INDEX_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()
