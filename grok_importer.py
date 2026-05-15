"""
grok_importer.py — Polly 9: Hent og gem Grok-samtaler i Obsidian vault.

Tre importmetoder (ingen API-nøgler kræves):
  1. JSON-eksport fra grok.com/i/grok  →  læs automatisk fra INBOX_DIR
  2. Tekst-/Markdown-fil dump          →  læs fra INBOX_DIR
  3. Browser-automation (Playwright)   →  kør med --scrape flag

Kørsel:
  python grok_importer.py              # importer fra inbox-mappe
  python grok_importer.py --scrape     # åbn browser og hent samtaler
  python grok_importer.py --watch      # kør løbende og overvåg inbox
"""

import argparse
import hashlib
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from textwrap import dedent
from typing import Optional

# ── Paths ─────────────────────────────────────────────────────────────────────
VAULT_PATH = Path(r"C:\Users\bebob\ObsidianVault\Polly")
BOT_PATH   = Path(r"C:\Users\bebob\polymarket-bots")

GROK_DIR   = VAULT_PATH / "grok"          # Gem samtaler her
INDEX_FILE = GROK_DIR / "index.md"         # Søgbart indeks
INBOX_DIR  = BOT_PATH / "grok_inbox"       # Drop eksport-filer her
DONE_DIR   = INBOX_DIR / "imported"        # Flyt hertil efter import
STATE_FILE = BOT_PATH / "grok_importer_state.json"

# Tags som sættes automatisk på Polly-relevante samtaler
POLLY_KEYWORDS = [
    "polymarket", "prediction market", "market", "polly", "trade", "forecast",
    "edge", "ml", "machine learning", "probability", "odds", "bet", "yes", "no",
    "city", "volume", "resolution", "pnl",
]

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("grok_importer")

# ── State ─────────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"imported_ids": []}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, default=str, indent=2), encoding="utf-8")


def _conv_id(conv: dict) -> str:
    raw = conv.get("id") or conv.get("conversation_id") or json.dumps(conv, sort_keys=True)
    return hashlib.md5(str(raw).encode()).hexdigest()[:12]


# ── Parsere ───────────────────────────────────────────────────────────────────

def _parse_timestamp(val) -> Optional[datetime]:
    if not val:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(val), fmt)
        except ValueError:
            continue
    return None


def _parse_json_export(path: Path) -> list[dict]:
    """
    Grok JSON-eksport format (fra grok.com → Settings → Export):
    { "conversations": [ { "id": "...", "title": "...", "create_time": "...",
                           "messages": [ {"role": "user"|"assistant", "content": "..."} ] } ] }
    Understøtter også ChatGPT-lignende format og simple lister.
    """
    data = json.loads(path.read_text(encoding="utf-8"))

    # Format 1: { "conversations": [...] }
    if isinstance(data, dict) and "conversations" in data:
        return data["conversations"]
    # Format 2: direkte liste
    if isinstance(data, list):
        return data
    # Format 3: enkelt samtale
    if isinstance(data, dict) and "messages" in data:
        return [data]
    log.warning("Ukendt JSON-format i %s", path.name)
    return []


def _parse_text_export(path: Path) -> list[dict]:
    """
    Læs en tekst-dump hvor samtalen er adskilt med 'You:' / 'Grok:' linjer.
    """
    content = path.read_text(encoding="utf-8", errors="replace")
    # Split på You:/Grok: skifter
    pattern = re.compile(r"^(You|Grok)\s*:\s*", re.MULTILINE | re.IGNORECASE)
    parts   = pattern.split(content)

    if len(parts) < 3:
        # Ingen struktur — gem som rå note
        return [{
            "id":          path.stem,
            "title":       path.stem,
            "create_time": datetime.now().isoformat(),
            "messages":    [{"role": "raw", "content": content}],
        }]

    messages = []
    i = 1
    while i < len(parts) - 1:
        role    = "user" if parts[i].lower() == "you" else "assistant"
        content_part = parts[i + 1].strip()
        if content_part:
            messages.append({"role": role, "content": content_part})
        i += 2

    title = messages[0]["content"][:60].replace("\n", " ") if messages else path.stem

    return [{
        "id":          path.stem,
        "title":       title,
        "create_time": datetime.now().isoformat(),
        "messages":    messages,
    }]


def _parse_markdown_export(path: Path) -> list[dict]:
    """
    Markdown-dump typisk kopieret fra Grok-UI.
    Detekterer headers som samtalestart.
    """
    content = path.read_text(encoding="utf-8", errors="replace")
    # Split på # Conversation / ## Conversation headers
    conv_blocks = re.split(r"^#{1,2}\s+Conversation", content, flags=re.MULTILINE)

    convs = []
    for block in conv_blocks:
        if len(block.strip()) < 20:
            continue
        # Prøv at udtrække titel fra første linje
        lines = block.strip().splitlines()
        title = lines[0].strip("# ").strip() if lines else path.stem

        # Parse You/Grok skift inde i blokken
        sub = _parse_text_export_from_str(block, title)
        convs.append(sub)

    return convs if convs else _parse_text_export(path)


def _parse_text_export_from_str(text: str, title: str) -> dict:
    pattern  = re.compile(r"^(You|Grok)\s*:\s*", re.MULTILINE | re.IGNORECASE)
    parts    = pattern.split(text)
    messages = []
    i = 1
    while i < len(parts) - 1:
        role    = "user" if parts[i].lower() == "you" else "assistant"
        content = parts[i + 1].strip()
        if content:
            messages.append({"role": role, "content": content})
        i += 2
    if not messages:
        messages = [{"role": "raw", "content": text.strip()}]
    return {
        "id":          hashlib.md5(text.encode()).hexdigest()[:8],
        "title":       title,
        "create_time": datetime.now().isoformat(),
        "messages":    messages,
    }


def _load_inbox() -> list[tuple[Path, list[dict]]]:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for path in sorted(INBOX_DIR.iterdir()):
        if path.is_dir():
            continue
        suffix = path.suffix.lower()
        try:
            if suffix == ".json":
                convs = _parse_json_export(path)
            elif suffix in (".md", ".markdown"):
                convs = _parse_markdown_export(path)
            elif suffix in (".txt", ".text", ""):
                convs = _parse_text_export(path)
            else:
                log.info("Springer over ukendt filtype: %s", path.name)
                continue
            if convs:
                results.append((path, convs))
                log.info("Parsed %d samtale(r) fra %s", len(convs), path.name)
        except Exception as exc:
            log.error("Fejl ved parsing af %s: %s", path.name, exc)
    return results


# ── Obsidian Markdown bygger ──────────────────────────────────────────────────

def _auto_tags(conv: dict) -> list[str]:
    text = " ".join(
        m.get("content", "") for m in conv.get("messages", [])
    ).lower()
    tags = ["grok"]
    for kw in POLLY_KEYWORDS:
        if kw in text:
            tags.append("polly")
            break
    # Tilføj emne-tags baseret på nøgleord
    if any(w in text for w in ["forecast", "probability", "odds"]):
        tags.append("forecasting")
    if any(w in text for w in ["python", "code", "script", "def ", "class "]):
        tags.append("code")
    if any(w in text for w in ["strategy", "edge", "system"]):
        tags.append("strategy")
    return list(dict.fromkeys(tags))  # deduplicate, order preserved


def _conv_to_markdown(conv: dict) -> str:
    title    = str(conv.get("title") or "Grok samtale").strip()[:120]
    ts       = _parse_timestamp(conv.get("create_time") or conv.get("created_at"))
    date_str = ts.strftime("%Y-%m-%d %H:%M") if ts else datetime.now().strftime("%Y-%m-%d %H:%M")
    tags     = _auto_tags(conv)
    messages = conv.get("messages", [])

    # Frontmatter
    frontmatter = dedent(f"""\
        ---
        title: "{title}"
        date: {date_str}
        tags: [{", ".join(tags)}]
        source: grok
        messages: {len(messages)}
        ---
    """)

    # Samtaletekst
    body_parts = [f"# {title}\n\n_Importeret: {date_str} | {len(messages)} beskeder_\n"]

    for i, msg in enumerate(messages, 1):
        role    = msg.get("role", "?")
        content = msg.get("content", "").strip()
        if not content:
            continue

        if role == "user":
            speaker = "**Du**"
        elif role == "assistant":
            speaker = "**Grok**"
        else:
            speaker = f"_{role}_"

        # Detektér kode-blokke og bevar dem
        body_parts.append(f"\n---\n\n{speaker}\n\n{content}\n")

    # Polly-relevans sektion
    polly_msgs = [m for m in messages if any(
        kw in m.get("content", "").lower() for kw in POLLY_KEYWORDS
    )]
    if polly_msgs:
        body_parts.append("\n---\n\n## Polly-relevante passager\n")
        for m in polly_msgs[:5]:
            snippet = m["content"][:300].replace("\n", " ")
            body_parts.append(f"\n> {snippet}{'…' if len(m['content']) > 300 else ''}\n")

    return frontmatter + "\n" + "".join(body_parts)


def _filename_for(conv: dict, ts: Optional[datetime]) -> str:
    date_prefix = (ts or datetime.now()).strftime("%Y-%m-%d")
    title       = str(conv.get("title") or "grok-samtale")
    # Rens filnavn
    safe_title  = re.sub(r'[^\w\s-]', '', title).strip()
    safe_title  = re.sub(r'\s+', '-', safe_title).lower()[:60]
    return f"{date_prefix}-{safe_title}.md"


# ── Importer ──────────────────────────────────────────────────────────────────

def import_conversations(convs: list[dict], state: dict) -> int:
    GROK_DIR.mkdir(parents=True, exist_ok=True)
    imported = 0

    for conv in convs:
        cid = _conv_id(conv)
        if cid in state["imported_ids"]:
            log.debug("Springer over allerede importeret: %s", cid)
            continue

        ts       = _parse_timestamp(conv.get("create_time") or conv.get("created_at"))
        filename = _filename_for(conv, ts)
        out_path = GROK_DIR / filename

        # Undgå overskrivning — tilføj suffix
        counter = 1
        while out_path.exists():
            stem    = out_path.stem
            out_path = GROK_DIR / f"{stem}-{counter}.md"
            counter += 1

        md = _conv_to_markdown(conv)
        out_path.write_text(md, encoding="utf-8")
        state["imported_ids"].append(cid)
        log.info("Importeret: %s", out_path.name)
        imported += 1

    return imported


def _move_to_done(path: Path) -> None:
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    dest = DONE_DIR / path.name
    counter = 1
    while dest.exists():
        dest = DONE_DIR / f"{path.stem}-{counter}{path.suffix}"
        counter += 1
    path.rename(dest)


# ── Indeks-opdatering ─────────────────────────────────────────────────────────

def update_index() -> None:
    GROK_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(GROK_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    files = [f for f in files if f.name != "index.md"]

    rows = ""
    for f in files:
        # Læs frontmatter for titel og dato
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
            title = f.stem
            date  = ""
            tags  = ""
            for line in lines[1:15]:
                if line.startswith("title:"):
                    title = line.split(":", 1)[1].strip().strip('"')
                elif line.startswith("date:"):
                    date = line.split(":", 1)[1].strip()
                elif line.startswith("tags:"):
                    tags = line.split(":", 1)[1].strip()
                elif line == "---" and date:
                    break
        except Exception:
            title, date, tags = f.stem, "", ""
        rows += f"| [[grok/{f.stem}\\|{title}]] | {date} | {tags} |\n"

    content = (
        f"# Grok Samtaler — Indeks\n\n"
        f"_Sidst opdateret: {datetime.now().strftime('%Y-%m-%d %H:%M')}_  \n"
        f"_Samtaler: {len(files)}_\n\n"
        "| Samtale | Dato | Tags |\n|---|---|---|\n"
        + rows
        + "\n\n---\n_Importér nye samtaler ved at lægge filer i `grok_inbox/`_\n"
    )
    INDEX_FILE.write_text(content, encoding="utf-8")
    log.info("Indeks opdateret: %d samtaler", len(files))


# ── Browser scraper (Playwright) ──────────────────────────────────────────────

def scrape_with_playwright(max_convs: int = 20) -> list[dict]:
    """
    Åbner Grok i en browser, logger ind via X.com og henter samtale-historik.
    Kræver: pip install playwright && playwright install chromium
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        log.error("Playwright ikke installeret. Kør: pip install playwright && playwright install chromium")
        return []

    convs = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)  # headless=False → du logger ind manuelt
        ctx     = browser.new_context()
        page    = ctx.new_page()

        log.info("Åbner Grok — log ind manuelt og tryk Enter her når klar...")
        page.goto("https://x.com/i/grok")
        input(">>> Tryk Enter efter login: ")

        # Hent samtale-liste fra Grok's interne API
        # Grok bruger GraphQL — vi fanger requests
        captured: list[dict] = []

        def on_response(response):
            try:
                if "GrokConversationItems" in response.url or "grok/conversation" in response.url.lower():
                    body = response.json()
                    captured.append(body)
            except Exception:
                pass

        page.on("response", on_response)
        page.reload()
        page.wait_for_timeout(3000)

        # Scroll igennem samtale-listen
        for _ in range(5):
            page.keyboard.press("End")
            page.wait_for_timeout(1000)

        browser.close()

        # Parse captured responses
        for body in captured:
            extracted = _extract_from_grok_api(body)
            convs.extend(extracted)
            if len(convs) >= max_convs:
                break

    log.info("Scraped %d samtaler via browser", len(convs))
    return convs[:max_convs]


def _extract_from_grok_api(body: dict) -> list[dict]:
    """Udtræk samtaler fra Grok's GraphQL response."""
    convs = []
    try:
        # Prøv forskellig nesting
        items = (
            body.get("data", {}).get("grok_conversation_items", {}).get("conversation_items", [])
            or body.get("conversations", [])
            or (body if isinstance(body, list) else [])
        )
        for item in items:
            msgs = []
            for m in item.get("messages", item.get("entries", [])):
                role    = m.get("sender", m.get("role", "")).lower()
                content = m.get("message", m.get("content", ""))
                if content:
                    msgs.append({"role": "user" if role in ("human", "user") else "assistant",
                                 "content": content})
            if msgs:
                convs.append({
                    "id":          item.get("id", item.get("conversation_id")),
                    "title":       item.get("title") or msgs[0]["content"][:60],
                    "create_time": item.get("create_time", item.get("created_at")),
                    "messages":    msgs,
                })
    except Exception as exc:
        log.debug("Kunne ikke parse Grok API response: %s", exc)
    return convs


# ── Clipboard import (Windows) ────────────────────────────────────────────────

def import_from_clipboard() -> list[dict]:
    """Importer en Grok-samtale fra clipboard (Windows)."""
    try:
        import subprocess
        result = subprocess.run(
            ["powershell", "-command", "Get-Clipboard"],
            capture_output=True, text=True, timeout=5
        )
        text = result.stdout.strip()
        if len(text) < 20:
            log.warning("Clipboard er tom eller for kort")
            return []
        conv = _parse_text_export_from_str(text, title=text[:60].replace("\n", " "))
        log.info("Importeret %d beskeder fra clipboard", len(conv["messages"]))
        return [conv]
    except Exception as exc:
        log.error("Clipboard import fejlede: %s", exc)
        return []


# ── Hoved-flow ────────────────────────────────────────────────────────────────

def run_import() -> None:
    state = _load_state()
    total = 0

    # 1. Inbox-filer
    inbox_items = _load_inbox()
    for path, convs in inbox_items:
        n = import_conversations(convs, state)
        total += n
        if n > 0:
            _move_to_done(path)

    # 2. Opdater indeks
    update_index()
    _save_state(state)

    log.info("Import færdig: %d nye samtaler importeret", total)


def watch_inbox(interval_sec: int = 60) -> None:
    log.info("Overvåger inbox hvert %ds — tryk Ctrl+C for at stoppe", interval_sec)
    while True:
        run_import()
        time.sleep(interval_sec)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Polly 9 — Importer Grok-samtaler til Obsidian vault"
    )
    parser.add_argument("--scrape",    action="store_true", help="Hent via browser (Playwright)")
    parser.add_argument("--clipboard", action="store_true", help="Importer fra Windows clipboard")
    parser.add_argument("--watch",     action="store_true", help="Overvåg inbox løbende")
    parser.add_argument("--max",       type=int, default=20, help="Max antal samtaler ved scraping")
    parser.add_argument("--interval",  type=int, default=60, help="Interval i sek ved --watch")
    args = parser.parse_args()

    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    GROK_DIR.mkdir(parents=True, exist_ok=True)

    state = _load_state()

    if args.scrape:
        log.info("Starter browser-scraping...")
        convs = scrape_with_playwright(max_convs=args.max)
        n = import_conversations(convs, state)
        update_index()
        _save_state(state)
        log.info("Scraped og importeret %d samtaler", n)

    elif args.clipboard:
        convs = import_from_clipboard()
        n = import_conversations(convs, state)
        update_index()
        _save_state(state)
        log.info("Importeret %d samtale(r) fra clipboard", n)

    elif args.watch:
        watch_inbox(interval_sec=args.interval)

    else:
        run_import()

    log.info("Samtaler gemt i: %s", GROK_DIR)
    log.info("Indeks: %s", INDEX_FILE)


if __name__ == "__main__":
    main()
