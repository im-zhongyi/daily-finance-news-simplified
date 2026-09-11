#!/usr/bin/env python3
"""Build a market-news digest from fixed RSS feeds and send it to Telegram."""

from __future__ import annotations

import email.utils
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime


FEEDS = [
    ("Google News", "https://news.google.com/rss/search?q=finance+market&hl=en-US&gl=US&ceid=US:en"),
    ("Yahoo Finance", "https://finance.yahoo.com/rss/finance"),
    ("Investing.com", "https://www.investing.com/rss/news.rss"),
]

MAX_ITEMS_PER_FEED = 30
HEADLINE_COUNT = 10
REQUEST_TIMEOUT_SECONDS = 30

TITLE_WEIGHT_MULTIPLIER = 2
RECENCY_WINDOW_HOURS = 24
RECENCY_BONUS_CAP = 8
MAX_PER_CATEGORY = 4
NOISE_PENALTY = 25

TELEGRAM_MESSAGE_LIMIT = 4096
ARTICLE_TARGET_WORDS = 1100
ARTICLE_HEADER = "5-Minute Market Read"

# (pattern, weight, category). Word-boundary patterns avoid substring false
# positives (e.g. "dow" inside "slowdown", "gold" inside "Goldman", "ai" inside
# "chair"/"said"). Singular/plural variants share one entry so a headline
# doesn't get double-counted for "stock" and "stocks".
_RAW_KEYWORDS: list[tuple[str, int, str]] = [
    (r"\bfed\b", 14, "policy"),
    (r"\bfederal reserve\b", 14, "policy"),
    (r"\brates?\b", 12, "policy"),
    (r"\brate cuts?\b", 13, "policy"),
    (r"\brate hikes?\b", 13, "policy"),
    (r"\bhawkish\b", 11, "policy"),
    (r"\bdovish\b", 11, "policy"),
    (r"\bfomc\b", 12, "policy"),
    (r"\bpowell\b", 10, "policy"),
    (r"\blagarde\b", 9, "policy"),
    (r"\bdot plot\b", 9, "policy"),
    (r"\bjackson hole\b", 8, "policy"),
    (r"\bquantitative tightening\b", 8, "policy"),
    (r"\bqt\b", 6, "policy"),
    (r"\bbalance sheet\b", 6, "policy"),
    (r"\bcentral banks?\b", 11, "policy"),
    (r"\bbank of england\b", 11, "policy"),
    (r"\becb\b", 11, "policy"),
    (r"\bboj\b", 11, "policy"),
    (r"\binflation\b", 12, "inflation"),
    (r"\bpce\b", 12, "inflation"),
    (r"\bcpi\b", 12, "inflation"),
    (r"\bppi\b", 10, "inflation"),
    (r"\bcore inflation\b", 12, "inflation"),
    (r"\bdisinflation\b", 10, "inflation"),
    (r"\bdeflation\b", 10, "inflation"),
    (r"\bstagflation\b", 11, "inflation"),
    (r"\bgdp\b", 12, "growth"),
    (r"\brecessions?\b", 13, "growth"),
    (r"\bsoft landing\b", 10, "growth"),
    (r"\bhard landing\b", 11, "growth"),
    (r"\bslowdowns?\b", 9, "growth"),
    (r"\bcontractions?\b", 9, "growth"),
    (r"\bexpansions?\b", 7, "growth"),
    (r"\bpmi\b", 9, "growth"),
    (r"\bism\b", 8, "growth"),
    (r"\bmanufacturing\b", 6, "growth"),
    (r"\bretail sales\b", 8, "growth"),
    (r"\bconsumer confidence\b", 8, "growth"),
    (r"\bconsumer sentiment\b", 8, "growth"),
    (r"\bhousing starts\b", 7, "growth"),
    (r"\bdurable goods\b", 6, "growth"),
    (r"\bpayrolls?\b", 11, "labor"),
    (r"\bnonfarm\b", 11, "labor"),
    (r"\bjobless claims\b", 10, "labor"),
    (r"\bunemployment\b", 10, "labor"),
    (r"\bwage growth\b", 8, "labor"),
    (r"\blabor market\b", 8, "labor"),
    (r"\blayoffs?\b", 9, "labor"),
    (r"\bhiring\b", 6, "labor"),
    (r"\bdebt ceiling\b", 9, "fiscal"),
    (r"\bshutdowns?\b", 9, "fiscal"),
    (r"\bdeficits?\b", 7, "fiscal"),
    (r"\btreasury\b", 7, "fiscal"),
    (r"\bdowngrades?\b", 8, "fiscal"),
    (r"\bfiscal\b", 6, "fiscal"),
    (r"\bstimulus\b", 7, "fiscal"),
    (r"\bvix\b", 8, "financial_conditions"),
    (r"\bvolatility\b", 6, "financial_conditions"),
    (r"\brisk[- ]off\b", 9, "financial_conditions"),
    (r"\brisk[- ]on\b", 9, "financial_conditions"),
    (r"\bsafe haven\b", 7, "financial_conditions"),
    (r"\bflight to safety\b", 8, "financial_conditions"),
    (r"\bcredit spreads?\b", 8, "financial_conditions"),
    (r"\bliquidity\b", 6, "financial_conditions"),
    (r"\bstocks?\b", 10, "equities"),
    (r"\bequit(?:y|ies)\b", 10, "equities"),
    (r"\bnasdaq\b", 10, "equities"),
    (r"\bs&p\b", 10, "equities"),
    (r"\bdow\b", 9, "equities"),
    (r"\bearnings\b", 8, "equities"),
    (r"\bguidance\b", 7, "equities"),
    (r"\boil\b", 10, "commodities"),
    (r"\bcrude\b", 10, "commodities"),
    (r"\bgold\b", 8, "commodities"),
    (r"\bcopper\b", 8, "commodities"),
    (r"\bcommodit(?:y|ies)\b", 8, "commodities"),
    (r"\byields?\b", 9, "rates"),
    (r"\bbonds?\b", 8, "rates"),
    (r"\bdollar\b", 8, "fx"),
    (r"\byen\b", 8, "fx"),
    (r"\byuan\b", 7, "fx"),
    (r"\brenminbi\b", 7, "fx"),
    (r"\beuro\b", 6, "fx"),
    (r"\bsterling\b", 6, "fx"),
    (r"\bemerging markets?\b", 7, "fx"),
    (r"\bcapital flows?\b", 6, "fx"),
    (r"\btariffs?\b", 8, "geopolitics"),
    (r"\btrade\b", 6, "geopolitics"),
    (r"\bwars?\b", 7, "geopolitics"),
    (r"\biran\b", 7, "geopolitics"),
    (r"\bchina\b", 7, "geopolitics"),
    (r"\bgeopolitical\b", 7, "geopolitics"),
    (r"\bmergers?\b", 6, "deals"),
    (r"\bacquisitions?\b", 6, "deals"),
    (r"\bipos?\b", 6, "deals"),
    (r"\bai\b", 7, "tech"),
    (r"\bchips?\b", 7, "tech"),
    (r"\bnvidia\b", 4, "micro"),
    (r"\bapple\b", 3, "micro"),
    (r"\btesla\b", 3, "micro"),
    (r"\bbitcoin\b", 6, "crypto"),
    (r"\bcrypto\b", 6, "crypto"),
]

MARKET_KEYWORDS: list[tuple[re.Pattern[str], int, str]] = [
    (re.compile(pattern), weight, category) for pattern, weight, category in _RAW_KEYWORDS
]

# Promotional / listicle titles that pollute a "finance market" RSS query but
# carry no macro signal (e.g. "Best brokerage accounts", "How to retire early").
NOISE_TITLE_PATTERNS = [
    re.compile(r"^\s*(the\s+)?(best|top \d+)\b", re.IGNORECASE),
    re.compile(r"^\s*how to\b", re.IGNORECASE),
    re.compile(r"\bshould you buy\b", re.IGNORECASE),
    re.compile(r"\bmotley fool\b", re.IGNORECASE),
]

CATEGORY_CONTEXT = {
    "policy": "The item matters for central-bank policy expectations, rate paths, and broad risk appetite.",
    "inflation": "The item matters for the inflation trajectory, real yields, and the policy response to price pressures.",
    "growth": "The item matters for the growth/recession debate and the broader economic-cycle outlook.",
    "labor": "The item matters for labor-market health, wage pressures, and the Fed's employment mandate.",
    "fiscal": "The item matters for fiscal policy, government funding risk, and sovereign credit conditions.",
    "financial_conditions": "The item matters for market volatility, liquidity, and risk appetite across assets.",
    "equities": "The item matters for equity sentiment, positioning, and sector leadership.",
    "commodities": "The item matters for commodity prices, inflation inputs, and resource-linked equities.",
    "rates": "The item matters for bond yields, duration risk, and rate-sensitive assets.",
    "fx": "The item matters for currency markets, capital flows, and cross-border trade dynamics.",
    "geopolitics": "The item matters for geopolitical risk, trade flows, and cross-asset volatility.",
    "deals": "The item matters for deal activity, valuation signals, and related sector moves.",
    "crypto": "The item matters for digital-asset sentiment and risk-on trading conditions.",
    "tech": "The item matters for tech-sector positioning and AI-driven capex and earnings themes.",
    "micro": "The item matters for single-stock positioning rather than the broader macro tape.",
}

_WORD_RE = re.compile(r"[a-z']+")

NEGATORS = {
    "no", "not", "isn't", "wasn't", "aren't", "weren't", "without",
    "unlikely", "denies", "denied", "avoids", "avoided",
}
DEESCALATORS = {
    "fade", "fades", "faded", "fading", "ease", "eases", "eased", "easing",
    "recede", "recedes", "receded", "subside", "subsides", "subsided",
    "dissipate", "dissipates", "dissipated",
}
ALARM_NOUNS = {"fears", "fear", "concerns", "concern", "worries", "worry"}
COOLING_WORDS = {
    "cools", "cooling", "cooled", "eases", "easing", "eased", "falls",
    "falling", "fell", "slows", "slowing", "slowed", "moderates",
    "moderating", "moderated", "declines", "declining", "declined",
}
HEATING_WORDS = {
    "hot", "surges", "surging", "surged", "accelerates", "accelerating",
    "accelerated", "rises", "rising", "rose", "jumps", "jumping", "jumped",
    "spikes", "spiking", "spiked", "soars", "soaring", "soared",
}
INFLATION_ANCHORS = {"inflation", "prices", "cpi", "pce", "ppi"}

BULLISH_PATTERNS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"\brall(?:y|ies|ying)\b"), 8),
    (re.compile(r"\bsurg(?:e|es|ing)\b"), 6),
    (re.compile(r"\breb ?ound(?:s|ing)?\b"), 7),
    (re.compile(r"\bgains?\b"), 6),
    (re.compile(r"\bbeats?\b"), 6),
    (re.compile(r"\bupgrades?\b"), 6),
    (re.compile(r"\brecover(?:y|s|ing)\b"), 7),
    (re.compile(r"\boptimis(?:m|tic)\b"), 8),
    (re.compile(r"\brecord high\b"), 7),
    (re.compile(r"\bupbeat\b"), 6),
    (re.compile(r"\bresilient\b"), 6),
    (re.compile(r"\bsoft landing\b"), 9),
    (re.compile(r"\bgoldilocks\b"), 9),
]

BEARISH_PATTERNS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"\bplunge(?:s|d)?\b"), 8),
    (re.compile(r"\bslump(?:s|ed|ing)?\b"), 7),
    (re.compile(r"\bselloff\b"), 8),
    (re.compile(r"\btumble(?:s|d)?\b"), 7),
    (re.compile(r"\brout\b"), 7),
    (re.compile(r"\bfears?\b"), 6),
    (re.compile(r"\bwarnings?\b"), 6),
    (re.compile(r"\bwarns?\b"), 6),
    (re.compile(r"\bdowngrades?\b"), 6),
    (re.compile(r"\bmisses?\b"), 6),
    (re.compile(r"\blayoffs?\b"), 7),
    (re.compile(r"\bcris(?:is|es)\b"), 8),
    (re.compile(r"\bcorrections?\b"), 6),
    (re.compile(r"\bbear market\b"), 8),
    (re.compile(r"\bweak(?:er|ness)?\b"), 5),
    (re.compile(r"\bhard landing\b"), 9),
    (re.compile(r"\bstagflation\b"), 9),
]


@dataclass(frozen=True)
class FeedItem:
    feed: str
    title: str
    link: str
    source: str
    published: datetime
    summary: str


def fetch_url(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "market-digest-bot/1.0 (+https://github.com/actions)",
            "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return response.read()


def text_of(element: ET.Element, tag: str) -> str:
    child = element.find(tag)
    if child is None or child.text is None:
        return ""
    return html.unescape(child.text).strip()


def parse_date(raw: str) -> datetime:
    if not raw:
        return datetime.now(UTC)

    s = raw.strip()

    # Try the robust RFC/RSS parser first
    try:
        parsed = email.utils.parsedate_to_datetime(s)
        if parsed is not None:
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
    except Exception:
        pass

    # Normalize common ISO variants so datetime.fromisoformat can handle them
    # e.g. 2026-06-19T22:33:00Z -> 2026-06-19T22:33:00+00:00
    s_iso = s
    if s_iso.endswith("Z"):
        s_iso = s_iso[:-1] + "+00:00"

    # Convert timezone offsets like +0000 to +00:00
    s_iso = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s_iso)

    # Drop fractional seconds if present (fromisoformat can handle, but keep safe)
    s_iso = re.sub(r"\.\d+", "", s_iso)

    try:
        # datetime.fromisoformat handles YYYY-MM-DDTHH:MM:SS(+|-)HH:MM
        parsed = datetime.fromisoformat(s_iso)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except Exception:
        pass

    # Fallback common strptime formats
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s.split(" ")[0] if "T" in fmt and " " in s else s, fmt).replace(tzinfo=UTC)
        except Exception:
            pass

    # Give up gracefully and return current time in UTC
    return datetime.now(UTC)


def parse_feed(feed_name: str, url: str) -> list[FeedItem]:
    root = ET.fromstring(fetch_url(url))
    channel = root.find("channel")
    if channel is None:
        raise ValueError(f"{feed_name} RSS feed has no channel element")

    items: list[FeedItem] = []
    for item in channel.findall("item")[:MAX_ITEMS_PER_FEED]:
        title = text_of(item, "title")
        link = text_of(item, "link")
        description = text_of(item, "description")
        pub_date = text_of(item, "pubDate")
        source_el = item.find("source")
        source = html.unescape(source_el.text).strip() if source_el is not None and source_el.text else feed_name

        if not title or not link:
            continue
        items.append(
            FeedItem(
                feed=feed_name,
                title=title,
                link=link,
                source=source,
                published=parse_date(pub_date),
                summary=clean_summary(description),
            )
        )
    return items


def clean_summary(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", html.unescape(value)).strip()
    return value


def normalize_title(title: str) -> str:
    title = title.lower()
    title = re.sub(r"\s+-\s+[^-]+$", "", title)
    title = re.sub(r"[^a-z0-9]+", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def dominant_category(item: FeedItem) -> str:
    combined = f"{item.title} {item.summary}".lower()
    totals: dict[str, int] = {}
    for pattern, weight, category in MARKET_KEYWORDS:
        if pattern.search(combined):
            totals[category] = totals.get(category, 0) + weight
    if not totals:
        return "general"
    return max(totals, key=totals.get)


def sentiment_score(text: str) -> tuple[int, int]:
    """Return (bullish, bearish) polarity points for a text blob.

    Handles negation ("no signs of a recession") and directional bigrams
    around inflation ("inflation cools" is bullish, "inflation surges" is
    bearish) that a flat keyword-topic score can't distinguish.
    """
    lower = text.lower()
    tokens = _WORD_RE.findall(lower)
    bullish = 0
    bearish = 0

    for idx, token in enumerate(tokens):
        window_before = tokens[max(0, idx - 3):idx]
        window_after = tokens[idx + 1:idx + 4]

        if token in ALARM_NOUNS and any(w in DEESCALATORS for w in window_after):
            bullish += 8
            continue

        if token in INFLATION_ANCHORS:
            nearby = window_before + window_after
            if any(w in COOLING_WORDS for w in nearby):
                bullish += 10
            elif any(w in HEATING_WORDS for w in nearby):
                bearish += 10

    for pattern, weight in BULLISH_PATTERNS:
        for match in pattern.finditer(lower):
            preceding = _WORD_RE.findall(lower[max(0, match.start() - 30):match.start()])
            if any(w in NEGATORS for w in preceding[-3:]):
                continue
            bullish += weight

    for pattern, weight in BEARISH_PATTERNS:
        for match in pattern.finditer(lower):
            preceding = _WORD_RE.findall(lower[max(0, match.start() - 30):match.start()])
            if any(w in NEGATORS for w in preceding[-3:]):
                continue
            bearish += weight

    return bullish, bearish


def sentiment_label(bullish: int, bearish: int) -> str:
    if bullish == bearish:
        return "neutral"
    return "bullish" if bullish > bearish else "bearish"


def score_item(item: FeedItem) -> tuple[int, float]:
    title_text = item.title.lower()
    body_text = item.summary.lower()

    score = 0
    for pattern, weight, _category in MARKET_KEYWORDS:
        if pattern.search(title_text):
            score += weight * TITLE_WEIGHT_MULTIPLIER
        elif pattern.search(body_text):
            score += weight

    if any(pattern.search(item.title) for pattern in NOISE_TITLE_PATTERNS):
        score -= NOISE_PENALTY

    age_hours = max(0.0, (datetime.now(UTC) - item.published).total_seconds() / 3600)
    recency_fraction = max(0.0, (RECENCY_WINDOW_HOURS - min(age_hours, RECENCY_WINDOW_HOURS)) / RECENCY_WINDOW_HOURS)
    recency_bonus = round(recency_fraction * RECENCY_BONUS_CAP)

    if item.feed == "Investing.com":
        score += 3
    elif item.feed == "Yahoo Finance":
        score += 2
    elif item.feed == "Google News":
        score += 1

    return score + recency_bonus, item.published.timestamp()


def select_items(items: list[FeedItem]) -> list[FeedItem]:
    seen_titles: set[str] = set()
    category_counts: dict[str, int] = {}
    ranked = sorted(items, key=score_item, reverse=True)

    selected: list[FeedItem] = []
    deferred: list[FeedItem] = []

    for item in ranked:
        key = normalize_title(item.title)
        if not key or key in seen_titles:
            continue

        category = dominant_category(item)
        if category_counts.get(category, 0) >= MAX_PER_CATEGORY:
            deferred.append(item)
            continue

        seen_titles.add(key)
        category_counts[category] = category_counts.get(category, 0) + 1
        selected.append(item)
        if len(selected) == HEADLINE_COUNT:
            break

    # Backfill from over-represented categories rather than fail outright if
    # the diversity cap left us short of a full digest.
    if len(selected) < HEADLINE_COUNT:
        for item in deferred:
            key = normalize_title(item.title)
            if not key or key in seen_titles:
                continue
            seen_titles.add(key)
            selected.append(item)
            if len(selected) == HEADLINE_COUNT:
                break

    if len(selected) < HEADLINE_COUNT:
        raise RuntimeError(f"Only found {len(selected)} usable RSS items; expected {HEADLINE_COUNT}")

    return selected


def make_context(item: FeedItem) -> str:
    category = dominant_category(item)
    return CATEGORY_CONTEXT.get(category, "The item is relevant to daily market sentiment and investor positioning.")


def compute_macro_tone(items: list[FeedItem]) -> str:
    bullish_total = 0
    bearish_total = 0
    for item in items:
        bullish, bearish = sentiment_score(f"{item.title}. {item.summary}")
        bullish_total += bullish
        bearish_total += bearish

    total = bullish_total + bearish_total
    if total == 0:
        return "Net macro tone: neutral (no strong directional signals detected)."

    net = bullish_total - bearish_total
    if net > total * 0.2:
        label = "risk-on / constructive"
    elif net < -total * 0.2:
        label = "risk-off / cautious"
    else:
        label = "mixed / balanced"

    return (
        f"Net macro tone: {label} "
        f"({bullish_total} bullish vs {bearish_total} bearish signal points across {len(items)} headlines)."
    )


def call_llm(system_prompt: str, user_prompt: str) -> str:
    """Call an OpenAI-chat-completions-compatible endpoint.

    Model-agnostic by design: which provider/model actually runs is entirely
    controlled by LLM_API_BASE/LLM_MODEL/LLM_API_KEY, since that request shape
    is supported natively or via a compatibility endpoint by most providers
    (OpenAI, Anthropic, OpenRouter, Azure OpenAI, self-hosted models). Swapping
    models is a config change, not a code change.
    """
    api_base = os.environ.get("LLM_API_BASE")
    model = os.environ.get("LLM_MODEL")
    api_key = os.environ.get("LLM_API_KEY")
    missing = [name for name, value in (("LLM_API_BASE", api_base), ("LLM_MODEL", model), ("LLM_API_KEY", api_key)) if not value]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    url = f"{api_base.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM request failed: {exc}") from exc

    try:
        parsed = json.loads(body)
        return parsed["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, ValueError) as exc:
        raise RuntimeError(f"Unexpected LLM response shape: {body[:500]}") from exc


def render_llm_article(items: list[FeedItem], tone_line: str) -> str:
    lines = []
    for item in items:
        bullish, bearish = sentiment_score(f"{item.title}. {item.summary}")
        lines.append(
            f"- [{dominant_category(item)} / {sentiment_label(bullish, bearish)}] "
            f"{item.title}. {item.summary}"
        )

    user_prompt = (
        f"{tone_line}\n\n"
        "Today's top market headlines (category / sentiment tag, then headline and summary):\n"
        + "\n".join(lines)
    )
    system_prompt = (
        f"You are a markets editor. Write a cohesive ~{ARTICLE_TARGET_WORDS}-word "
        "(5-minute read) article synthesizing today's macro/market news into a narrative "
        "organized by theme (policy, growth, inflation, equities, etc. as relevant). "
        "Plain paragraphs only: no markdown, no headers, no bullet points, no links, "
        "since this goes directly into a plain-text chat message. Open with the overall "
        "macro tone and close with a short forward-looking wrap-up."
    )
    return call_llm(system_prompt, user_prompt)


SENTIMENT_IMPLICATION = {
    "bullish": "That reading leans constructive and supports a more risk-on tone.",
    "bearish": "That reading adds to the case for caution and keeps risk-off hedges in play.",
    "neutral": "The read-through here is limited on its own, but it adds to today's information set.",
}


def render_template_article(items: list[FeedItem], tone_line: str) -> str:
    groups: dict[str, list[FeedItem]] = {}
    order: list[str] = []
    for item in items:
        category = dominant_category(item)
        if category not in groups:
            groups[category] = []
            order.append(category)
        groups[category].append(item)

    paragraphs = [
        f"{tone_line} Here's what's driving that read across today's top headlines."
    ]

    for category in order:
        category_items = groups[category]
        intro = CATEGORY_CONTEXT.get(category, "Elsewhere, several stories stood out.")
        sentences = [intro]
        for item in category_items:
            bullish, bearish = sentiment_score(f"{item.title}. {item.summary}")
            label = sentiment_label(bullish, bearish)
            title = re.sub(r"\s+", " ", item.title).strip()
            sentences.append(f"{title}. {SENTIMENT_IMPLICATION[label]}")
        paragraphs.append(" ".join(sentences))

    paragraphs.append(
        "Taken together, today's flow points to a market still parsing mixed signals "
        "across growth, policy, and risk appetite -- worth watching how these themes "
        "develop into tomorrow's session."
    )
    return "\n\n".join(paragraphs)


def chunk_for_telegram(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split text into chunks that fit Telegram's sendMessage length cap.

    Splits on paragraph boundaries first, falling back to sentence/word
    boundaries only if a single paragraph itself exceeds the limit.
    """
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current:
            chunks.append(current)
            current = ""

    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue

        flush()
        if len(paragraph) <= limit:
            current = paragraph
            continue

        # A single paragraph is longer than the limit; split on whitespace.
        words = paragraph.split(" ")
        piece = ""
        for word in words:
            candidate_piece = f"{piece} {word}" if piece else word
            if len(candidate_piece) > limit:
                chunks.append(piece)
                piece = word
            else:
                piece = candidate_piece
        current = piece

    flush()
    return chunks


def format_digest(items: list[FeedItem], now: datetime) -> str:
    # Build HTML-formatted message. Use the feed/source as hyperlink text
    header = now.strftime('%B %-d, %Y') if os.name != 'nt' else now.strftime('%B %#d, %Y')
    tone_line = compute_macro_tone(items)
    parts: list[str] = [f"<b>Market Digest - {html.escape(header)}</b>", html.escape(tone_line), ""]
    for index, item in enumerate(items, start=1):
        title = re.sub(r"\s+", " ", item.title).strip()
        context = make_context(item)
        bullish, bearish = sentiment_score(f"{item.title}. {item.summary}")
        tag = sentiment_label(bullish, bearish).capitalize()
        esc_title = html.escape(title)
        esc_context = html.escape(context)
        esc_link = html.escape(item.link, quote=True)
        esc_source = html.escape(item.source)
        parts.append(f"{index}. {esc_title} - {esc_context} [{tag}]")
        parts.append(f"Source: <a href=\"{esc_link}\">{esc_source}</a>")
        parts.append("")
    return "\n".join(parts).strip()


def _post_telegram_message(message: str) -> None:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    missing = [name for name, value in (("TELEGRAM_BOT_TOKEN", bot_token), ("TELEGRAM_CHAT_ID", chat_id)) if not value]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": "true",
            "parse_mode": "HTML",
        }
    ).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        body = response.read().decode("utf-8", errors="replace")
        if response.status >= 400:
            raise RuntimeError(f"Telegram returned HTTP {response.status}: {body}")
        print(body)


def send_telegram(message: str) -> None:
    if os.environ.get("DRY_RUN", "").lower() in {"1", "true", "yes"}:
        print("DRY_RUN is set; skipping Telegram send.", file=sys.stderr)
        return
    _post_telegram_message(message)


def send_telegram_chunks(chunks: list[str]) -> None:
    if os.environ.get("DRY_RUN", "").lower() in {"1", "true", "yes"}:
        print(f"DRY_RUN is set; skipping Telegram send of {len(chunks)} chunk(s).", file=sys.stderr)
        return
    for chunk in chunks:
        _post_telegram_message(chunk)


def main() -> int:
    all_items: list[FeedItem] = []
    failures: list[str] = []

    for feed_name, url in FEEDS:
        try:
            items = parse_feed(feed_name, url)
            if not items:
                raise RuntimeError("feed returned no usable items")
            all_items.extend(items)
            print(f"Fetched {len(items)} items from {feed_name}", file=sys.stderr)
        except (ET.ParseError, OSError, urllib.error.URLError, ValueError, RuntimeError) as exc:
            failures.append(f"{feed_name}: {exc}")

    if failures:
        print("RSS fetch failed; refusing to publish off-source digest.", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    now = datetime.now(UTC)
    selected = select_items(all_items)
    digest = format_digest(selected, now)
    print(digest)
    send_telegram(digest)

    article_mode = os.environ.get("ARTICLE_MODE", "off").strip().lower()
    if article_mode not in {"off", "template", "llm"}:
        print(f"Unknown ARTICLE_MODE {article_mode!r}; skipping article.", file=sys.stderr)
        article_mode = "off"

    if article_mode != "off":
        tone_line = compute_macro_tone(selected)
        article_text = None
        if article_mode == "llm":
            try:
                article_text = render_llm_article(selected, tone_line)
            except RuntimeError as exc:
                print(f"LLM article generation failed ({exc}); falling back to template.", file=sys.stderr)

        if article_text is None:
            article_text = render_template_article(selected, tone_line)

        article_html = f"<b>{html.escape(ARTICLE_HEADER)}</b>\n\n{html.escape(article_text)}"
        chunks = chunk_for_telegram(article_html)
        print(article_html)
        send_telegram_chunks(chunks)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
