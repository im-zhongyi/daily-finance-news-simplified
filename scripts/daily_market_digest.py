#!/usr/bin/env python3
"""Build a market-news digest from fixed RSS feeds and send it to Telegram."""

from __future__ import annotations

import email.utils
import html
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

MARKET_KEYWORDS = {
    "fed": 14,
    "federal reserve": 14,
    "rates": 12,
    "inflation": 12,
    "pce": 12,
    "cpi": 12,
    "central bank": 11,
    "bank of england": 11,
    "ecb": 11,
    "boj": 11,
    "stock": 10,
    "stocks": 10,
    "equity": 10,
    "equities": 10,
    "nasdaq": 10,
    "s&p": 10,
    "dow": 9,
    "oil": 10,
    "crude": 10,
    "gold": 8,
    "copper": 8,
    "commodity": 8,
    "commodities": 8,
    "yield": 9,
    "yields": 9,
    "bond": 8,
    "bonds": 8,
    "dollar": 8,
    "yen": 8,
    "earnings": 8,
    "guidance": 7,
    "ai": 7,
    "chip": 7,
    "nvidia": 7,
    "apple": 6,
    "tesla": 6,
    "bitcoin": 6,
    "crypto": 6,
    "tariff": 8,
    "trade": 6,
    "war": 7,
    "iran": 7,
    "china": 7,
    "merger": 6,
    "acquisition": 6,
    "ipo": 6,
}


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

    parsed = email.utils.parsedate_to_datetime(raw)
    if parsed is not None:
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
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


def score_item(item: FeedItem) -> tuple[int, float]:
    text = f"{item.title} {item.summary}".lower()
    score = 0
    for keyword, weight in MARKET_KEYWORDS.items():
        if keyword in text:
            score += weight

    age_hours = max(0.0, (datetime.now(UTC) - item.published).total_seconds() / 3600)
    recency_bonus = max(0, int(24 - min(age_hours, 24)))

    if item.feed == "Investing.com":
        score += 3
    elif item.feed == "Yahoo Finance":
        score += 2
    elif item.feed == "Google News":
        score += 1

    return score + recency_bonus, item.published.timestamp()


def select_items(items: list[FeedItem]) -> list[FeedItem]:
    seen: set[str] = set()
    ranked = sorted(items, key=score_item, reverse=True)
    selected: list[FeedItem] = []

    for item in ranked:
        key = normalize_title(item.title)
        if not key or key in seen:
            continue
        seen.add(key)
        selected.append(item)
        if len(selected) == HEADLINE_COUNT:
            break

    if len(selected) < HEADLINE_COUNT:
        raise RuntimeError(f"Only found {len(selected)} usable RSS items; expected {HEADLINE_COUNT}")

    return selected


def make_context(item: FeedItem) -> str:
    text = f"{item.title}. {item.summary}".lower()

    if any(word in text for word in ("fed", "rate", "inflation", "central bank", "pce", "cpi")):
        return "The item matters for rate expectations, yields, and broad risk appetite."
    if any(word in text for word in ("oil", "crude", "gold", "copper", "commodity", "commodities")):
        return "The item matters for commodity prices, inflation inputs, and resource-linked equities."
    if any(word in text for word in ("stock", "stocks", "equity", "nasdaq", "s&p", "dow", "earnings")):
        return "The item matters for equity sentiment, positioning, and sector leadership."
    if any(word in text for word in ("bitcoin", "crypto")):
        return "The item matters for digital-asset sentiment and risk-on trading conditions."
    if any(word in text for word in ("china", "iran", "tariff", "trade", "war", "geopolitical")):
        return "The item matters for geopolitical risk, trade flows, and cross-asset volatility."
    if any(word in text for word in ("merger", "acquisition", "ipo", "deal")):
        return "The item matters for deal activity, valuation signals, and related sector moves."
    return "The item is relevant to daily market sentiment and investor positioning."


def format_digest(items: list[FeedItem], now: datetime) -> str:
    lines = [f"Market Digest - {now.strftime('%B %-d, %Y') if os.name != 'nt' else now.strftime('%B %#d, %Y')}", ""]
    for index, item in enumerate(items, start=1):
        title = re.sub(r"\s+", " ", item.title).strip()
        lines.append(f"{index}. {title} - {make_context(item)}")
        lines.append(f"    Source: {item.link}")
        lines.append("")
    return "\n".join(lines).strip()


def send_telegram(message: str) -> None:
    if os.environ.get("DRY_RUN", "").lower() in {"1", "true", "yes"}:
        print("DRY_RUN is set; skipping Telegram send.", file=sys.stderr)
        return

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
        }
    ).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        body = response.read().decode("utf-8", errors="replace")
        if response.status >= 400:
            raise RuntimeError(f"Telegram returned HTTP {response.status}: {body}")
        print(body)


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

    digest = format_digest(select_items(all_items), datetime.now(UTC))
    print(digest)
    send_telegram(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
