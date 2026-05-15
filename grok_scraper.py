"""
grok_scraper.py — Scraper Grok-samtaler fra x.com/i/grok og grok.com
ved hjælp af eksisterende Chrome-profil (allerede logget ind).

Auto-installerer dependencies. Kør:
    python grok_scraper.py
"""

# ── Auto-install dependencies ─────────────────────────────────────────────────
import subprocess, sys, importlib

def _pip(package, import_as=None):
    name = import_as or package.split("[")[0].replace("-", "_")
    try:
        importlib.import_module(name)
    except ImportError:
        print(f"[SETUP] Installerer {package}...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", package, "-q"],
            stdout=subprocess.DEVNULL,
        )
        print(f"[SETUP] {package} installeret ✓")

_pip("selenium")
_pip("undetected-chromedriver", "undetected_chromedriver")

# ── Imports ───────────────────────────────────────────────────────────────────
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from textwrap import dedent
from typing import Optional

import undetected_chromedriver as uc
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# ── Paths ─────────────────────────────────────────────────────────────────────
VAULT_GROK = Path(r"C:\Users\bebob\ObsidianVault\Polly\grok")
BOT_PATH   = Path(r"C:\Users\bebob\polymarket-bots")
INDEX_FILE = VAULT_GROK / "index.md"
STATE_FILE = BOT_PATH / "grok_scraper_state.json"

CHROME_USER_DATA = r"C:\Users\bebob\AppData\Local\Google\Chrome\User Data"
CHROME_PROFILE   = "Default"

POLLY_KEYWORDS = [
    "polymarket", "edge", "forecast", "ml", "trading", "weather", "polly",
    "bot", "temperature", "staging", "live", "bankroll", "position",
    "win rate", "trade", "market", "probability", "pnl", "city",
    "volume", "resolution", "entry", "exit", "score",
]

# ── Logging ───────────────────────────────────────────────────────────────────
VAULT_GROK.mkdir(parents=True, exist_ok=True)
BOT_PATH.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BOT_PATH / "grok_scraper.log", encoding="utf-8"),
    ],
)
log = logging.getLogger()

# ── State ─────────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"scraped_ids": [], "total_xcom": 0, "total_grokcom": 0}


def _save_state(s: dict) -> None:
    STATE_FILE.write_text(json.dumps(s, indent=2, default=str), encoding="utf-8")


def _cid(platform: str, url: str, title: str = "") -> str:
    raw = f"{platform}::{url}::{title[:80]}"
    return hashlib.md5(raw.encode()).hexdigest()[:14]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slug(text: str, maxlen: int = 55) -> str:
    s = re.sub(r"[^\w\s-]", "", str(text)).strip()
    return re.sub(r"\s+", "-", s).lower()[:maxlen]


def _is_polly(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in POLLY_KEYWORDS)


def _auto_tags(text: str) -> list[str]:
    t    = text.lower()
    tags = ["grok"]
    if any(k in t for k in ["polymarket", "polly", "market", "trade"]):
        tags.append("polly")
    if any(k in t for k in ["forecast", "probability", "prediction"]):
        tags.append("forecasting")
    if any(k in t for k in ["python", "code", "script", "def ", "class "]):
        tags.append("code")
    if any(k in t for k in ["weather", "temperature", "rain"]):
        tags.append("weather")
    if any(k in t for k in ["ml", "machine learning", "model", "score"]):
        tags.append("ml")
    if any(k in t for k in ["edge", "strategy", "bankroll"]):
        tags.append("strategy")
    return list(dict.fromkeys(tags))


def _write_md(
    title: str, date: str, platform: str, messages: list[dict], url: str
) -> Path:
    full_text = "\n".join(m.get("content", "") for m in messages)
    tags      = _auto_tags(full_text)
    polly_rel = "ja" if _is_polly(full_text) else "nej"
    safe_date = (re.match(r"\d{4}-\d{2}-\d{2}", date) or re.match(r"\d{4}", date) or type("x", (), {"group": lambda s, n: datetime.now().strftime("%Y-%m-%d")})()).group(0) if date else datetime.now().strftime("%Y-%m-%d")

    prefix   = "xcom_" if platform == "xcom" else "grokcom_"
    filename = f"{safe_date}-{prefix}{_slug(title)}.md"
    out      = VAULT_GROK / filename
    counter  = 1
    while out.exists():
        out = VAULT_GROK / f"{safe_date}-{prefix}{_slug(title)}-{counter}.md"
        counter += 1

    fm = dedent(f"""\
        ---
        title: "{title}"
        date: {safe_date}
        platform: {platform}
        polly_relevant: {polly_rel}
        tags: [{", ".join(tags)}]
        messages: {len(messages)}
        url: "{url}"
        ---
    """)

    body = [f"# {title}\n\n_Platform: {platform} | {safe_date} | {len(messages)} beskeder_\n"]
    for msg in messages:
        role    = msg.get("role", "assistant")
        content = msg.get("content", "").strip()
        if not content:
            continue
        speaker = "**Du**" if role == "user" else "**Grok**"
        body.append(f"\n---\n\n{speaker}\n\n{content}\n")

    polly_msgs = [m for m in messages if _is_polly(m.get("content", ""))]
    if polly_msgs:
        body.append("\n---\n\n## Polly-relevante passager\n")
        for m in polly_msgs[:5]:
            snippet = m["content"][:400].replace("\n", " ")
            body.append(f"\n> {snippet}{'…' if len(m['content']) > 400 else ''}\n")

    out.write_text(fm + "\n" + "".join(body), encoding="utf-8")
    return out


# ── Chrome driver ─────────────────────────────────────────────────────────────

def _build_driver() -> uc.Chrome:
    opts = uc.ChromeOptions()
    opts.add_argument(f"--user-data-dir={CHROME_USER_DATA}")
    opts.add_argument(f"--profile-directory={CHROME_PROFILE}")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--disable-popup-blocking")
    opts.add_argument("--start-maximized")
    # Ikke headless — vi ser hvad der sker
    driver = uc.Chrome(options=opts, use_subprocess=True)
    driver.implicitly_wait(5)
    return driver


# ── Selenium helpers ──────────────────────────────────────────────────────────

def _wait(driver, seconds: float = 2.0) -> None:
    time.sleep(seconds)


def _scroll_element(driver, element, times: int = 20) -> None:
    for _ in range(times):
        try:
            driver.execute_script(
                "arguments[0].scrollTop += arguments[0].scrollHeight;", element
            )
        except Exception:
            driver.execute_script("window.scrollBy(0, 800);")
        time.sleep(0.4)


def _scroll_page(driver, times: int = 15) -> None:
    for _ in range(times):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.5)


def _find_any(driver, selectors: list[str]) -> list:
    for sel in selectors:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                return els
        except Exception:
            continue
    return []


def _safe_text(el) -> str:
    try:
        return el.text.strip()
    except StaleElementReferenceException:
        return ""


def _safe_attr(el, attr: str) -> str:
    try:
        return el.get_attribute(attr) or ""
    except StaleElementReferenceException:
        return ""


def _js_extract_all_text(driver) -> str:
    """Udtræk al synlig tekst fra siden via JavaScript."""
    try:
        return driver.execute_script(
            "return document.body ? document.body.innerText : '';"
        ) or ""
    except Exception:
        return ""


def _split_conversation_text(raw: str) -> list[dict]:
    """Split rå tekst i beskeder baseret på You/Grok mønstre."""
    pattern  = re.compile(
        r"(?m)^(You|Grok|Human|Assistant|User)\s*[:\n]", re.IGNORECASE
    )
    parts    = pattern.split(raw)
    messages = []
    i        = 1
    while i < len(parts) - 1:
        speaker = parts[i].lower()
        content = parts[i + 1].strip()
        role    = "user" if speaker in ("you", "human", "user") else "assistant"
        if content and len(content) > 5:
            messages.append({"role": role, "content": content})
        i += 2
    return messages


def _extract_messages_from_dom(driver) -> list[dict]:
    """
    Prøver en række CSS-selektorer for at finde besked-bobler.
    Fallback til rå tekst-splitting.
    """
    MSG_SELECTORS = [
        # x.com/i/grok
        '[data-testid="grok-message"]',
        '[data-testid="messageEntry"]',
        'div[class*="Message"]:not([class*="MessageList"])',
        'div[class*="message-bubble"]',
        'div[class*="ChatMessage"]',
        'div[class*="conversationTurn"]',
        # grok.com
        '[class*="message"][class*="container"]',
        '[class*="response"]',
        '[class*="humanMessage"]',
        '[class*="assistantMessage"]',
        # Generiske
        'article[data-message-author-role]',
        '[data-message-author-role]',
    ]

    ROLE_MAP = {
        "human": "user", "you": "user", "user": "user",
        "grok": "assistant", "assistant": "assistant", "ai": "assistant",
    }

    for sel in MSG_SELECTORS:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if len(els) < 1:
                continue
            msgs = []
            for el in els:
                text = _safe_text(el)
                if len(text) < 3:
                    continue
                # Bestem rolle
                role = "assistant"
                for attr in ["data-role", "data-message-author-role", "data-sender", "aria-label"]:
                    val = _safe_attr(el, attr).lower()
                    if val in ROLE_MAP:
                        role = ROLE_MAP[val]
                        break
                # Kig på CSS-klasser
                classes = _safe_attr(el, "class").lower()
                if any(w in classes for w in ["human", "user", "you", "query", "input"]):
                    role = "user"
                msgs.append({"role": role, "content": text})
            if msgs:
                return msgs
        except Exception:
            continue

    # Fallback: split rå side-tekst
    raw  = _js_extract_all_text(driver)
    msgs = _split_conversation_text(raw)
    if msgs:
        return msgs

    # Allersidste fallback: gem alt tekst som ét råt dokument
    if raw.strip():
        return [{"role": "raw", "content": raw.strip()}]

    return []


# ══════════════════════════════════════════════════════════════════════════════
# PLATFORM 1 — x.com/i/grok
# ══════════════════════════════════════════════════════════════════════════════

XCOM_SIDEBAR_SELS = [
    'nav[aria-label*="conversation" i]',
    'nav[aria-label*="grok" i]',
    'div[data-testid="grok-sidebar"]',
    'div[class*="sidebar" i]',
    'aside',
    'nav',
]

XCOM_CONV_LINK_SELS = [
    'a[href*="/i/grok/"]',
    'a[href*="/i/grok/c/"]',
    '[data-testid="conversation-item"] a',
    '[data-testid="grokConversation"] a',
    'nav a[href*="grok"]',
    'aside a[href*="grok"]',
]


def _collect_xcom_conversation_links(driver) -> list[dict]:
    """Scroller sidebar og samler ALLE samtale-links."""
    log.info("[x.com] Loader samtale-liste...")
    driver.get("https://x.com/i/grok")
    _wait(driver, 4)

    # Find sidebar-element til scrolling
    sidebar = None
    for sel in XCOM_SIDEBAR_SELS:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                sidebar = els[0]
                break
        except Exception:
            continue

    # Aggressiv scrolling af sidebar
    prev_count = 0
    stale_rounds = 0
    for round_num in range(50):
        if sidebar:
            try:
                driver.execute_script(
                    "arguments[0].scrollTop += 1500;", sidebar
                )
            except Exception:
                driver.execute_script("window.scrollBy(0, 1000);")
        else:
            driver.execute_script("window.scrollBy(0, 1000);")
        time.sleep(0.7)

        links = _find_any(driver, XCOM_CONV_LINK_SELS)
        curr  = len(links)
        log.info(
            "[x.com] Scroll %d/50 — %d samtaler fundet", round_num + 1, curr
        )
        if curr == prev_count:
            stale_rounds += 1
            if stale_rounds >= 5:
                break
        else:
            stale_rounds = 0
        prev_count = curr

    # Saml links
    links_data = []
    seen_hrefs = set()
    for sel in XCOM_CONV_LINK_SELS:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                href  = _safe_attr(el, "href")
                title = _safe_text(el) or href.split("/")[-1]
                if href and href not in seen_hrefs:
                    seen_hrefs.add(href)
                    links_data.append({"href": href, "title": title[:120]})
        except Exception:
            continue

    log.info("[x.com] Total samtaler fundet i sidebar: %d", len(links_data))
    return list(reversed(links_data))  # ældste først


def scrape_xcom(driver, state: dict) -> list[dict]:
    log.info("=" * 60)
    log.info("PLATFORM 1: x.com/i/grok")
    log.info("=" * 60)

    conv_links = _collect_xcom_conversation_links(driver)
    if not conv_links:
        log.warning("[x.com] Ingen samtaler fundet i sidepanelet.")
        return []

    scraped = []
    errors  = []

    for idx, link in enumerate(conv_links, 1):
        href  = link["href"]
        title = link["title"] or f"Samtale-{idx}"
        url   = href if href.startswith("http") else f"https://x.com{href}"
        cid   = _cid("xcom", url, title)

        if cid in state["scraped_ids"]:
            log.info("[x.com] [%d/%d] Spring over (allerede hentet): %s",
                     idx, len(conv_links), title[:50])
            continue

        log.info("[x.com] [%d/%d] Henter: %s", idx, len(conv_links), title[:60])

        try:
            driver.get(url)
            _wait(driver, 3)

            # Scroll samtalen ned for at loade alt
            for _ in range(12):
                driver.execute_script(
                    "window.scrollTo(0, document.body.scrollHeight);"
                )
                time.sleep(0.5)

            # Forsøg "Show more" / "Load older" knapper
            for _ in range(5):
                clicked = False
                for btn_text in ["Show more", "Load older", "Load more", "See more"]:
                    try:
                        btns = driver.find_elements(
                            By.XPATH,
                            f"//button[contains(normalize-space(), '{btn_text}')]",
                        )
                        for btn in btns:
                            if btn.is_displayed():
                                btn.click()
                                _wait(driver, 1)
                                clicked = True
                    except Exception:
                        pass
                if not clicked:
                    break

            messages = _extract_messages_from_dom(driver)
            if not messages:
                log.warning("[x.com]   ⚠ Ingen beskeder — springer over")
                errors.append(f"Ingen beskeder: {title[:50]}")
                continue

            # Find dato
            date_str = datetime.now().strftime("%Y-%m-%d")
            try:
                time_els = driver.find_elements(By.CSS_SELECTOR, "time[datetime]")
                if time_els:
                    raw_dt = _safe_attr(time_els[0], "datetime")
                    if raw_dt:
                        date_str = raw_dt[:10]
            except Exception:
                pass

            out = _write_md(title, date_str, "xcom", messages, url)
            state["scraped_ids"].append(cid)
            state["total_xcom"] = state.get("total_xcom", 0) + 1
            _save_state(state)

            result = {
                "title":    title,
                "date":     date_str,
                "platform": "xcom",
                "messages": len(messages),
                "file":     out.name,
                "polly":    _is_polly("".join(m["content"] for m in messages)),
                "url":      url,
            }
            scraped.append(result)
            polly_tag = " 🟢 POLLY" if result["polly"] else ""
            log.info(
                "[x.com]   ✅ Gemt: %s (%d beskeder)%s",
                out.name, len(messages), polly_tag,
            )

        except WebDriverException as exc:
            msg = str(exc)[:80]
            log.error("[x.com]   ❌ WebDriver fejl: %s", msg)
            errors.append(f"{title[:50]}: {msg}")
        except Exception as exc:
            log.error("[x.com]   ❌ Fejl: %s", exc)
            errors.append(f"{title[:50]}: {exc}")

        time.sleep(0.8)

    log.info("[x.com] Færdig: %d nye samtaler hentet, %d fejl",
             len(scraped), len(errors))
    for e in errors:
        log.warning("[x.com] Fejl: %s", e)

    return scraped


# ══════════════════════════════════════════════════════════════════════════════
# PLATFORM 2 — grok.com
# ══════════════════════════════════════════════════════════════════════════════

GROKCOM_URLS = [
    "https://grok.com",
    "https://x.ai/grok",
]

GROKCOM_SIDEBAR_SELS = [
    'nav[aria-label]',
    'aside[class*="sidebar" i]',
    'div[class*="Sidebar"]',
    'div[class*="sidebar"]',
    'div[class*="panel"]',
    'nav',
    'aside',
]

GROKCOM_CONV_LINK_SELS = [
    'a[href*="/conversation/"]',
    'a[href*="/chat/"]',
    'a[href*="/c/"]',
    '[data-testid="conversation-link"]',
    '[class*="ConversationItem"] a',
    '[class*="conversationItem"] a',
    '[class*="chatItem"] a',
    '[class*="historyItem"] a',
    'nav a[href*="/"]',
    'aside a',
]


def _collect_grokcom_links(driver) -> list[dict]:
    log.info("[grok.com] Loader samtale-liste...")

    loaded = False
    for grok_url in GROKCOM_URLS:
        try:
            driver.get(grok_url)
            _wait(driver, 4)
            if "grok" in driver.current_url.lower():
                log.info("[grok.com] Åbnede: %s", driver.current_url)
                loaded = True
                break
        except Exception as exc:
            log.warning("[grok.com] Kunne ikke åbne %s: %s", grok_url, exc)

    if not loaded:
        log.error("[grok.com] Kunne ikke navigere til grok.com")
        return []

    # Find sidebar
    sidebar = None
    for sel in GROKCOM_SIDEBAR_SELS:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                sidebar = els[0]
                break
        except Exception:
            continue

    # Aggressiv scrolling
    prev_count  = 0
    stale_rounds = 0
    for round_num in range(60):
        if sidebar:
            try:
                driver.execute_script(
                    "arguments[0].scrollTop += 1500;", sidebar
                )
            except Exception:
                driver.execute_script("window.scrollBy(0, 1000);")
        else:
            driver.execute_script("window.scrollBy(0, 1000);")
        time.sleep(0.7)

        links = _find_any(driver, GROKCOM_CONV_LINK_SELS)
        curr  = len(links)
        log.info("[grok.com] Scroll %d/60 — %d samtaler fundet", round_num + 1, curr)
        if curr == prev_count:
            stale_rounds += 1
            if stale_rounds >= 6:
                break
        else:
            stale_rounds = 0
        prev_count = curr

    # Saml links
    base_url    = re.match(r"https?://[^/]+", driver.current_url)
    base        = base_url.group() if base_url else "https://grok.com"
    links_data  = []
    seen_hrefs  = set()

    for sel in GROKCOM_CONV_LINK_SELS:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                href  = _safe_attr(el, "href")
                if not href:
                    continue
                if href.startswith("/"):
                    href = base + href
                title = _safe_text(el) or href.split("/")[-1]
                if href not in seen_hrefs:
                    seen_hrefs.add(href)
                    links_data.append({"href": href, "title": title[:120]})
        except Exception:
            continue

    # Filtrer ikke-samtale-links
    def is_conv_link(href: str) -> bool:
        return any(p in href for p in [
            "/conversation/", "/chat/", "/c/", "grok.com/", "x.ai/"
        ]) and not any(p in href for p in [
            "login", "signup", "settings", "about", "support"
        ])

    links_data = [l for l in links_data if is_conv_link(l["href"])]
    log.info("[grok.com] Total samtaler fundet: %d", len(links_data))
    return list(reversed(links_data))  # ældste først


def scrape_grokcom(driver, state: dict) -> list[dict]:
    log.info("=" * 60)
    log.info("PLATFORM 2: grok.com")
    log.info("=" * 60)

    conv_links = _collect_grokcom_links(driver)
    if not conv_links:
        log.warning("[grok.com] Ingen samtaler fundet.")
        return []

    scraped = []
    errors  = []

    for idx, link in enumerate(conv_links, 1):
        href  = link["href"]
        title = link["title"] or f"Samtale-{idx}"
        url   = href
        cid   = _cid("grokcom", url, title)

        if cid in state["scraped_ids"]:
            log.info("[grok.com] [%d/%d] Spring over (allerede hentet): %s",
                     idx, len(conv_links), title[:50])
            continue

        log.info("[grok.com] [%d/%d] Henter: %s", idx, len(conv_links), title[:60])

        try:
            driver.get(url)
            _wait(driver, 3)

            # Scroll for at loade alt
            for _ in range(15):
                driver.execute_script(
                    "window.scrollTo(0, document.body.scrollHeight);"
                )
                time.sleep(0.4)

            # Load more knapper
            for _ in range(8):
                clicked = False
                for phrase in ["Load more", "Show more", "See more", "Load older", "Vis mere"]:
                    try:
                        btns = driver.find_elements(
                            By.XPATH,
                            f"//button[contains(normalize-space(), '{phrase}')]",
                        )
                        for btn in btns:
                            if btn.is_displayed():
                                btn.click()
                                _wait(driver, 1.2)
                                clicked = True
                    except Exception:
                        pass
                if not clicked:
                    break

            messages = _extract_messages_from_dom(driver)
            if not messages:
                log.warning("[grok.com]   ⚠ Ingen beskeder — springer over")
                errors.append(f"Ingen beskeder: {title[:50]}")
                continue

            date_str = datetime.now().strftime("%Y-%m-%d")
            try:
                time_els = driver.find_elements(By.CSS_SELECTOR, "time[datetime]")
                if time_els:
                    raw_dt = _safe_attr(time_els[0], "datetime")
                    if raw_dt:
                        date_str = raw_dt[:10]
            except Exception:
                pass

            out = _write_md(title, date_str, "grokcom", messages, url)
            state["scraped_ids"].append(cid)
            state["total_grokcom"] = state.get("total_grokcom", 0) + 1
            _save_state(state)

            result = {
                "title":    title,
                "date":     date_str,
                "platform": "grokcom",
                "messages": len(messages),
                "file":     out.name,
                "polly":    _is_polly("".join(m["content"] for m in messages)),
                "url":      url,
            }
            scraped.append(result)
            polly_tag = " 🟢 POLLY" if result["polly"] else ""
            log.info(
                "[grok.com]   ✅ Gemt: %s (%d beskeder)%s",
                out.name, len(messages), polly_tag,
            )

        except WebDriverException as exc:
            msg = str(exc)[:80]
            log.error("[grok.com]   ❌ WebDriver fejl: %s", msg)
            errors.append(f"{title[:50]}: {msg}")
        except Exception as exc:
            log.error("[grok.com]   ❌ Fejl: %s", exc)
            errors.append(f"{title[:50]}: {exc}")

        time.sleep(0.8)

    log.info("[grok.com] Færdig: %d nye samtaler hentet, %d fejl",
             len(scraped), len(errors))
    for e in errors:
        log.warning("[grok.com] Fejl: %s", e)

    return scraped


# ══════════════════════════════════════════════════════════════════════════════
# INDEX
# ══════════════════════════════════════════════════════════════════════════════

def build_index() -> None:
    files = sorted(
        [f for f in VAULT_GROK.glob("*.md") if f.name != "index.md"],
        key=lambda f: f.name,
    )

    all_entries = []
    for f in files:
        try:
            lines    = f.read_text(encoding="utf-8").splitlines()
            entry    = {"file": f.name, "title": f.stem, "date": "", "platform": "", "polly": False}
            in_fm    = False
            for line in lines:
                if line == "---":
                    in_fm = not in_fm
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
            all_entries.append(entry)
        except Exception:
            pass

    all_entries.sort(key=lambda e: e["date"] or "")
    xcom_entries    = [e for e in all_entries if e["platform"] == "xcom"]
    grokcom_entries = [e for e in all_entries if e["platform"] == "grokcom"]
    polly_entries   = [e for e in all_entries if e["polly"]]

    def table_rows(entries):
        if not entries:
            return "_Ingen samtaler endnu_\n"
        out = "| Samtale | Dato | Platform | Polly |\n|---|---|---|---|\n"
        for e in entries:
            stem = Path(e["file"]).stem
            pol  = "✅" if e["polly"] else "—"
            out += f"| [[grok/{stem}\\|{e['title'][:65]}]] | {e['date']} | {e['platform']} | {pol} |\n"
        return out

    polly_section = ""
    if polly_entries:
        polly_section = "## ⭐ Polly-relevante samtaler\n\n" + table_rows(polly_entries) + "\n"

    content = dedent(f"""\
        # Grok Samtaler — Indeks

        _Sidst opdateret: {datetime.now().strftime("%Y-%m-%d %H:%M")}_

        ## Statistik

        | Platform | Samtaler | Polly-relevante |
        |---|---|---|
        | x.com/i/grok | {len(xcom_entries)} | {sum(1 for e in xcom_entries if e["polly"])} |
        | grok.com | {len(grokcom_entries)} | {sum(1 for e in grokcom_entries if e["polly"])} |
        | **Total** | **{len(all_entries)}** | **{len(polly_entries)}** |

        ---

        {polly_section}## Alle samtaler (kronologisk)

        {table_rows(all_entries)}
        ---
        _Genereret af grok_scraper.py_
    """)

    INDEX_FILE.write_text(content, encoding="utf-8")
    log.info("index.md bygget: %d samtaler, %d Polly-relevante",
             len(all_entries), len(polly_entries))


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    log.info("=" * 60)
    log.info("GROK SCRAPER — starter")
    log.info("Chrome profil: %s / %s", CHROME_USER_DATA, CHROME_PROFILE)
    log.info("Output:        %s", VAULT_GROK)
    log.info("=" * 60)

    state = _load_state()

    driver = _build_driver()
    try:
        # ── Platform 1 ────────────────────────────────────────────────────────
        xcom_scraped = scrape_xcom(driver, state)

        # ── Platform 2 ────────────────────────────────────────────────────────
        grokcom_scraped = scrape_grokcom(driver, state)

    finally:
        driver.quit()

    # ── Index ─────────────────────────────────────────────────────────────────
    build_index()

    # ── Rapport ───────────────────────────────────────────────────────────────
    all_scraped = xcom_scraped + grokcom_scraped
    polly_n     = sum(1 for s in all_scraped if s["polly"])

    log.info("")
    log.info("=" * 60)
    log.info("✅  SCRAPER FÆRDIG")
    log.info("=" * 60)
    log.info("  x.com/i/grok :  %d nye samtaler", len(xcom_scraped))
    log.info("  grok.com     :  %d nye samtaler", len(grokcom_scraped))
    log.info("  Polly-relevante: %d", polly_n)
    log.info("  Gemt i: %s", VAULT_GROK)
    log.info("  Log:    %s", BOT_PATH / "grok_scraper.log")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
