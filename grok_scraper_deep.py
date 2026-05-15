"""
grok_scraper_deep.py — Polly 9 Deep Scraper

Bruger din EKSISTERENDE Chrome-profil (allerede logget ind).
Kræver INGEN manuel login — henter ALT fra begge platforme kronologisk.

Kørsel:
    python grok_scraper_deep.py

Kræver:
    pip install playwright
    playwright install chromium
"""

import json
import re
import sys
import time
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from textwrap import dedent
from typing import Optional

# ── Paths ─────────────────────────────────────────────────────────────────────
VAULT_GROK  = Path(r"C:\Users\bebob\ObsidianVault\Polly\grok")
BOT_PATH    = Path(r"C:\Users\bebob\polymarket-bots")
INDEX_FILE  = VAULT_GROK / "index.md"
STATE_FILE  = BOT_PATH / "grok_scraper_state.json"

# Chrome-profil — bruger din eksisterende session (allerede logget ind)
CHROME_USER_DATA = Path(r"C:\Users\bebob\AppData\Local\Google\Chrome\User Data")
CHROME_PROFILE   = "Default"   # eller "Profile 1", "Profile 2" osv.

POLLY_KEYWORDS = [
    "polymarket", "edge", "forecast", "ml", "trading", "weather", "polly",
    "bot", "temperature", "staging", "live", "bankroll", "win rate",
    "position", "trade", "market", "probability", "yes", "no", "pnl",
    "city", "volume", "resolution", "entry", "exit", "score",
]

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(BOT_PATH / "grok_scraper.log", encoding="utf-8", errors="replace"),
    ],
)
log = logging.getLogger("grok_scraper")

# ── State ─────────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"scraped_ids": []}


def _save_state(state: dict) -> None:
    BOT_PATH.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, default=str, indent=2), encoding="utf-8")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slug(text: str, maxlen: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", text).strip()
    s = re.sub(r"\s+", "-", s).lower()
    return s[:maxlen]


def _conv_hash(platform: str, title: str, first_msg: str) -> str:
    raw = f"{platform}::{title}::{first_msg[:100]}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _is_polly(text: str) -> bool:
    tl = text.lower()
    return any(kw in tl for kw in POLLY_KEYWORDS)


def _auto_tags(text: str) -> list[str]:
    tags = []
    tl   = text.lower()
    if any(k in tl for k in ["polymarket", "polly", "market", "trade", "bet"]):
        tags.append("polly")
    if any(k in tl for k in ["forecast", "probability", "prediction"]):
        tags.append("forecasting")
    if any(k in tl for k in ["python", "code", "script", "def ", "class ", "import"]):
        tags.append("code")
    if any(k in tl for k in ["weather", "temperature", "rain", "celsius"]):
        tags.append("weather")
    if any(k in tl for k in ["ml", "machine learning", "model", "score", "feature"]):
        tags.append("ml")
    if any(k in tl for k in ["edge", "strategy", "system", "bankroll"]):
        tags.append("strategy")
    return tags if tags else ["grok"]


def _build_markdown(
    title: str,
    date_str: str,
    platform: str,
    messages: list[dict],
    url: str = "",
) -> str:
    full_text = " ".join(m.get("content", "") for m in messages)
    tags      = _auto_tags(full_text)
    polly_rel = "ja" if _is_polly(full_text) else "nej"

    fm = dedent(f"""\
        ---
        title: "{title}"
        date: {date_str}
        platform: {platform}
        tags: [{", ".join(tags)}]
        polly_relevant: {polly_rel}
        messages: {len(messages)}
        url: "{url}"
        ---
    """)

    body = [f"# {title}\n\n_Platform: {platform} | Dato: {date_str} | Beskeder: {len(messages)}_\n"]

    for msg in messages:
        role    = msg.get("role", "")
        content = msg.get("content", "").strip()
        if not content:
            continue
        speaker = "**Du**" if role == "user" else "**Grok**"
        body.append(f"\n---\n\n{speaker}\n\n{content}\n")

    # Polly-uddrag
    polly_msgs = [m for m in messages if _is_polly(m.get("content", ""))]
    if polly_msgs:
        body.append("\n---\n\n## Polly-relevante passager\n")
        for m in polly_msgs[:5]:
            snippet = m["content"][:400].replace("\n", " ")
            body.append(f"\n> {snippet}{'…' if len(m['content']) > 400 else ''}\n")

    return fm + "\n" + "".join(body)


def _save_conversation(
    title: str,
    date_str: str,
    platform: str,
    messages: list[dict],
    url: str,
) -> Path:
    VAULT_GROK.mkdir(parents=True, exist_ok=True)
    safe_date  = re.sub(r"[T ].*", "", date_str) or datetime.now().strftime("%Y-%m-%d")
    prefix     = "xcom_" if platform == "xcom" else "grokcom_"
    filename   = f"{safe_date}-{prefix}{_slug(title)}.md"
    out        = VAULT_GROK / filename

    counter = 1
    while out.exists():
        out = VAULT_GROK / f"{safe_date}-{prefix}{_slug(title)}-{counter}.md"
        counter += 1

    md = _build_markdown(title, date_str, platform, messages, url)
    out.write_text(md, encoding="utf-8")
    return out


# ══════════════════════════════════════════════════════════════════════════════
# PLATFORM 1 — x.com/i/grok
# ══════════════════════════════════════════════════════════════════════════════

XCOM_CONV_SELECTORS = [
    # Sidebar conversation links — prøver alle kendte selektorer
    'nav[aria-label] a[href*="/i/grok/"]',
    'a[href*="/i/grok/c/"]',
    'div[data-testid="conversation-item"] a',
    'aside a[href*="grok"]',
    '[data-testid="grok-conversation"] a',
    'a[href^="/i/grok/"]',
]

XCOM_MSG_SELECTORS = [
    '[data-testid="grok-message"]',
    'div[class*="message"]',
    'article[data-testid="tweet"]',  # fallback for embedded
    '[class*="MessageBubble"]',
    '[class*="conversationMessage"]',
]

XCOM_ROLE_ATTR = ["data-role", "data-sender", "aria-label"]


def _scroll_sidebar_fully(page, sidebar_sel: str, max_scrolls: int = 40) -> None:
    try:
        sidebar = page.locator(sidebar_sel).first
        prev_count = 0
        for i in range(max_scrolls):
            sidebar.evaluate("el => el.scrollTop += 1200")
            page.wait_for_timeout(600)
            current = page.locator(sidebar_sel + " a").count()
            if current == prev_count and i > 3:
                break
            prev_count = current
    except Exception:
        for _ in range(max_scrolls // 2):
            page.keyboard.press("End")
            page.wait_for_timeout(500)


def _extract_xcom_messages(page) -> list[dict]:
    messages = []
    for sel in XCOM_MSG_SELECTORS:
        els = page.locator(sel).all()
        if len(els) >= 1:
            for el in els:
                try:
                    text = el.inner_text().strip()
                    if len(text) < 2:
                        continue
                    # Forsøg at bestemme rolle
                    role = "assistant"
                    for attr in XCOM_ROLE_ATTR:
                        val = el.get_attribute(attr) or ""
                        if "user" in val.lower() or "human" in val.lower():
                            role = "user"
                            break
                    messages.append({"role": role, "content": text})
                except Exception:
                    continue
            if messages:
                break

    # Fallback: hent al tekst og split på mønster
    if not messages:
        try:
            full = page.inner_text("main") or page.inner_text("body")
            messages = _split_raw_text(full)
        except Exception:
            pass

    return messages


def _split_raw_text(text: str) -> list[dict]:
    pattern  = re.compile(r"^(You|Grok|Human|Assistant)\s*[:\n]", re.MULTILINE | re.IGNORECASE)
    parts    = pattern.split(text)
    messages = []
    i = 1
    while i < len(parts) - 1:
        role    = "user" if parts[i].lower() in ("you", "human") else "assistant"
        content = parts[i + 1].strip()
        if content and len(content) > 3:
            messages.append({"role": role, "content": content})
        i += 2
    return messages


def scrape_xcom(page, state: dict) -> list[dict]:
    log.info("=== PLATFORM 1: x.com/i/grok ===")
    page.goto("https://x.com/i/grok", timeout=30000)
    page.wait_for_timeout(3000)

    # Find sidebar og scroll til bunden (ældste samtaler øverst)
    SIDEBAR_SELS = [
        'nav[aria-label*="conversation"]',
        'aside[role="complementary"]',
        'div[data-testid="grok-sidebar"]',
        'div[class*="sidebar"]',
        'nav',
    ]
    sidebar_sel = "body"
    for sel in SIDEBAR_SELS:
        try:
            if page.locator(sel).count() > 0:
                sidebar_sel = sel
                break
        except Exception:
            continue

    log.info("Scroller sidebar for alle samtaler...")
    _scroll_sidebar_fully(page, sidebar_sel)

    # Saml alle samtale-links
    conv_links = []
    for sel in XCOM_CONV_SELECTORS:
        try:
            els = page.locator(sel).all()
            for el in els:
                href  = el.get_attribute("href") or ""
                title = (el.inner_text() or href).strip()[:120]
                if href and href not in [l["href"] for l in conv_links]:
                    conv_links.append({"href": href, "title": title or "Samtale"})
        except Exception:
            continue

    log.info("Fandt %d samtaler på x.com/i/grok", len(conv_links))

    # Kronologisk — ældste først (reverse)
    conv_links = list(reversed(conv_links))

    scraped = []
    for i, link in enumerate(conv_links, 1):
        href  = link["href"]
        title = link["title"]
        full_url = f"https://x.com{href}" if href.startswith("/") else href
        cid   = _conv_hash("xcom", title, href)

        if cid in state["scraped_ids"]:
            log.info("[%d/%d] Springer over (allerede hentet): %s", i, len(conv_links), title[:50])
            continue

        log.info("[%d/%d] Henter: %s", i, len(conv_links), title[:60])
        try:
            page.goto(full_url, timeout=20000, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)

            # Scroll ned i samtalen for at loade alt
            for _ in range(10):
                page.keyboard.press("End")
                page.wait_for_timeout(400)

            messages = _extract_xcom_messages(page)
            if not messages:
                log.warning("  ⚠ Ingen beskeder fundet — springer over")
                continue

            date_str = datetime.now().strftime("%Y-%m-%d")
            # Prøv at finde dato i siden
            try:
                ts_el = page.locator("time[datetime]").first
                dt_raw = ts_el.get_attribute("datetime") or ""
                if dt_raw:
                    date_str = dt_raw[:10]
            except Exception:
                pass

            out = _save_conversation(title, date_str, "xcom", messages, full_url)
            state["scraped_ids"].append(cid)
            _save_state(state)

            scraped.append({
                "title": title, "date": date_str, "platform": "xcom",
                "messages": len(messages), "file": out.name,
                "polly": _is_polly(" ".join(m["content"] for m in messages)),
            })
            log.info("  ✅ Gemt: %s (%d beskeder)", out.name, len(messages))

        except Exception as exc:
            log.error("  ❌ Fejl ved %s: %s", title[:50], exc)

        page.wait_for_timeout(500)

    log.info("x.com/i/grok: %d nye samtaler hentet", len(scraped))
    return scraped


# ══════════════════════════════════════════════════════════════════════════════
# PLATFORM 2 — grok.com
# ══════════════════════════════════════════════════════════════════════════════

GROKCOM_CONV_SELECTORS = [
    'a[href*="/conversation/"]',
    'a[href*="/chat/"]',
    'a[href*="/c/"]',
    '[data-testid="conversation-link"]',
    '[class*="ConversationItem"] a',
    '[class*="chatItem"] a',
    'nav a',
    'aside a',
]

GROKCOM_MSG_SELECTORS = [
    '[data-testid="message"]',
    '[class*="Message"]',
    '[class*="message"]',
    '[class*="ChatMessage"]',
    '[class*="bubble"]',
    'article',
]


def _extract_grokcom_messages(page) -> list[dict]:
    messages = []
    for sel in GROKCOM_MSG_SELECTORS:
        try:
            els = page.locator(sel).all()
            if len(els) < 1:
                continue
            for el in els:
                text = el.inner_text().strip()
                if len(text) < 3:
                    continue
                # Bestem rolle
                role = "assistant"
                classes = el.get_attribute("class") or ""
                aria    = el.get_attribute("aria-label") or ""
                combined = (classes + aria).lower()
                if any(w in combined for w in ["user", "human", "you", "query"]):
                    role = "user"
                messages.append({"role": role, "content": text})
            if messages:
                break
        except Exception:
            continue

    if not messages:
        try:
            full = page.inner_text("main") or page.inner_text("body")
            messages = _split_raw_text(full)
        except Exception:
            pass

    return messages


def scrape_grokcom(page, state: dict) -> list[dict]:
    log.info("=== PLATFORM 2: grok.com ===")

    # Grok.com kan have flere mulige URL-mønstre
    for url in ["https://grok.com", "https://grok.x.ai", "https://www.grok.com"]:
        try:
            page.goto(url, timeout=15000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            if "grok" in page.url.lower():
                log.info("Åbnede %s", page.url)
                break
        except Exception:
            continue

    page.wait_for_timeout(2000)

    # Scroll sidebar fuldt ud
    log.info("Scroller sidebar for alle samtaler...")
    SIDEBAR_SELS = [
        'nav[aria-label]',
        'aside',
        '[class*="sidebar"]',
        '[class*="Sidebar"]',
        'nav',
    ]
    sidebar_sel = "body"
    for sel in SIDEBAR_SELS:
        try:
            if page.locator(sel).count() > 0:
                sidebar_sel = sel
                break
        except Exception:
            continue

    _scroll_sidebar_fully(page, sidebar_sel, max_scrolls=60)

    # Saml alle samtale-links
    conv_links = []
    for sel in GROKCOM_CONV_SELECTORS:
        try:
            els = page.locator(sel).all()
            for el in els:
                href  = el.get_attribute("href") or ""
                title = (el.inner_text() or href).strip()[:120]
                if href and href not in [l["href"] for l in conv_links]:
                    conv_links.append({"href": href, "title": title or "Samtale"})
        except Exception:
            continue

    # Fjern duplikater og filtrér ikke-samtale-links
    seen   = set()
    unique = []
    for l in conv_links:
        k = l["href"]
        if k not in seen and any(p in k for p in ["/conversation/", "/chat/", "/c/", "grok"]):
            seen.add(k)
            unique.append(l)
    conv_links = unique

    log.info("Fandt %d samtaler på grok.com", len(conv_links))
    conv_links = list(reversed(conv_links))  # kronologisk — ældste først

    scraped = []
    for i, link in enumerate(conv_links, 1):
        href  = link["href"]
        title = link["title"]

        if href.startswith("/"):
            base     = re.match(r"https?://[^/]+", page.url)
            full_url = (base.group() if base else "https://grok.com") + href
        else:
            full_url = href

        cid = _conv_hash("grokcom", title, href)
        if cid in state["scraped_ids"]:
            log.info("[%d/%d] Springer over (allerede hentet): %s", i, len(conv_links), title[:50])
            continue

        log.info("[%d/%d] Henter: %s", i, len(conv_links), title[:60])
        try:
            page.goto(full_url, timeout=20000, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)

            # Scroll ned for at loade alt indhold
            for _ in range(12):
                page.keyboard.press("End")
                page.wait_for_timeout(350)

            # "Show more" / "Load more" knapper
            for _ in range(5):
                try:
                    more = page.locator(
                        'button:has-text("Load more"), button:has-text("Show more"), '
                        'button:has-text("See more"), [class*="loadMore"]'
                    ).first
                    if more.is_visible():
                        more.click()
                        page.wait_for_timeout(1000)
                except Exception:
                    break

            messages = _extract_grokcom_messages(page)
            if not messages:
                log.warning("  ⚠ Ingen beskeder fundet — springer over")
                continue

            date_str = datetime.now().strftime("%Y-%m-%d")
            try:
                ts_el = page.locator("time[datetime]").first
                dt_raw = ts_el.get_attribute("datetime") or ""
                if dt_raw:
                    date_str = dt_raw[:10]
            except Exception:
                pass

            out = _save_conversation(title, date_str, "grokcom", messages, full_url)
            state["scraped_ids"].append(cid)
            _save_state(state)

            scraped.append({
                "title": title, "date": date_str, "platform": "grokcom",
                "messages": len(messages), "file": out.name,
                "polly": _is_polly(" ".join(m["content"] for m in messages)),
            })
            log.info("  ✅ Gemt: %s (%d beskeder)", out.name, len(messages))

        except Exception as exc:
            log.error("  ❌ Fejl ved %s: %s", title[:50], exc)

        page.wait_for_timeout(500)

    log.info("grok.com: %d nye samtaler hentet", len(scraped))
    return scraped


# ══════════════════════════════════════════════════════════════════════════════
# INDEX BYGGER
# ══════════════════════════════════════════════════════════════════════════════

def build_index(all_scraped: list[dict]) -> None:
    VAULT_GROK.mkdir(parents=True, exist_ok=True)

    # Læs ALLE eksisterende md-filer (inkl. tidligere kørsler)
    existing = []
    for f in VAULT_GROK.glob("*.md"):
        if f.name == "index.md":
            continue
        try:
            lines   = f.read_text(encoding="utf-8").splitlines()
            title   = f.stem
            date    = ""
            platform = ""
            polly   = "nej"
            for line in lines[1:15]:
                if line.startswith("title:"):
                    title = line.split(":", 1)[1].strip().strip('"')
                elif line.startswith("date:"):
                    date = line.split(":", 1)[1].strip()
                elif line.startswith("platform:"):
                    platform = line.split(":", 1)[1].strip()
                elif line.startswith("polly_relevant:"):
                    polly = line.split(":", 1)[1].strip()
                elif line == "---" and date:
                    break
            existing.append({
                "title": title, "date": date, "platform": platform,
                "polly": polly == "ja", "file": f.name,
            })
        except Exception:
            pass

    existing.sort(key=lambda x: x["date"] or "")

    xcom_all    = [e for e in existing if e["platform"] == "xcom"]
    grokcom_all = [e for e in existing if e["platform"] == "grokcom"]
    polly_all   = [e for e in existing if e["polly"]]

    def row(e):
        pol = "✅" if e["polly"] else "—"
        return f"| [[grok/{Path(e['file']).stem}\\|{e['title'][:60]}]] | {e['date']} | {e['platform']} | {pol} |\n"

    polly_section = ""
    if polly_all:
        polly_section = "## ⭐ Polly-relevante samtaler\n\n"
        polly_section += "| Samtale | Dato | Platform | Polly |\n|---|---|---|---|\n"
        polly_section += "".join(row(e) for e in polly_all)
        polly_section += "\n"

    all_rows = "".join(row(e) for e in existing)

    content = f"""# Grok Samtaler — Indeks

_Sidst opdateret: {datetime.now().strftime("%Y-%m-%d %H:%M")}_

## Statistik

| Platform | Antal samtaler | Polly-relevante |
|---|---|---|
| x.com/i/grok | {len(xcom_all)} | {sum(1 for e in xcom_all if e["polly"])} |
| grok.com | {len(grokcom_all)} | {sum(1 for e in grokcom_all if e["polly"])} |
| **Total** | **{len(existing)}** | **{len(polly_all)}** |

---

{polly_section}
## Alle samtaler (kronologisk)

| Samtale | Dato | Platform | Polly |
|---|---|---|---|
{all_rows}
---
_Generet af grok_scraper_deep.py_
"""
    INDEX_FILE.write_text(content, encoding="utf-8")
    log.info("✅ index.md bygget: %d samtaler total, %d Polly-relevante", len(existing), len(polly_all))


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("\n❌ Playwright ikke installeret.")
        print("Kør:\n  pip install playwright\n  playwright install chromium\n")
        sys.exit(1)

    VAULT_GROK.mkdir(parents=True, exist_ok=True)
    BOT_PATH.mkdir(parents=True, exist_ok=True)
    state = _load_state()

    log.info("Starter Grok Deep Scraper — bruger eksisterende Chrome-profil")
    log.info("Chrome profil: %s / %s", CHROME_USER_DATA, CHROME_PROFILE)

    all_scraped = []

    with sync_playwright() as pw:
        # Brug eksisterende Chrome-profil — allerede logget ind
        try:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(CHROME_USER_DATA),
                channel="chrome",
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"],
            )
            page = ctx.new_page()
            log.info("Chrome åbnet med eksisterende profil (allerede logget ind)")

        except Exception as e:
            log.warning("Kunne ikke bruge Chrome-profil (%s) — prøver Chromium", e)
            try:
                browser = pw.chromium.launch(headless=False)
                ctx     = browser.new_context()
                page    = ctx.new_page()
                log.info("Chromium åbnet — du skal muligvis logge ind manuelt")
                input(">>> Gå til x.com/i/grok, log ind, og tryk Enter: ")
            except Exception as e2:
                log.error("Kunne ikke starte browser: %s", e2)
                sys.exit(1)

        # ── Scrape x.com/i/grok ──────────────────────────────────────────────
        try:
            xcom_scraped = scrape_xcom(page, state)
            all_scraped.extend(xcom_scraped)
        except Exception as exc:
            log.error("Fejl under x.com scraping: %s", exc)

        # ── Scrape grok.com ───────────────────────────────────────────────────
        try:
            grokcom_scraped = scrape_grokcom(page, state)
            all_scraped.extend(grokcom_scraped)
        except Exception as exc:
            log.error("Fejl under grok.com scraping: %s", exc)

        ctx.close()

    # ── Byg index ─────────────────────────────────────────────────────────────
    build_index(all_scraped)

    # ── Final rapport ─────────────────────────────────────────────────────────
    xcom_n    = sum(1 for s in all_scraped if s["platform"] == "xcom")
    grokcom_n = sum(1 for s in all_scraped if s["platform"] == "grokcom")
    polly_n   = sum(1 for s in all_scraped if s["polly"])

    print("\n" + "=" * 60)
    print("✅ GROK DEEP SCRAPER FÆRDIG")
    print("=" * 60)
    print(f"  x.com/i/grok :  {xcom_n} nye samtaler")
    print(f"  grok.com     :  {grokcom_n} nye samtaler")
    print(f"  Polly-relevante: {polly_n}")
    print(f"  Gemt i: {VAULT_GROK}")
    print(f"  Indeks: {INDEX_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()
