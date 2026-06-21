# Daily Market News Telegram Digest

This repository contains a GitHub Actions scheduled job that fetches market-news RSS items from:

- Google News RSS: `https://news.google.com/rss/search?q=finance+market`
- Yahoo Finance RSS: `https://finance.yahoo.com/rss/finance`
- Investing.com RSS: `https://www.investing.com/rss/news.rss`

The job deduplicates and ranks the feed items, formats a 10-headline Telegram digest, and sends it through the Telegram Bot API.

## GitHub Setup

1. Push this repository to GitHub.
2. In the GitHub repository, open **Settings > Secrets and variables > Actions**.
3. Add these repository secrets:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. Open **Actions > Daily Market News Telegram Digest**.
5. Use **Run workflow** once to test it manually.

The workflow also runs automatically every day at `22:30 UTC`, which is `06:30 Asia/Singapore`.

## Behavior

The job intentionally fails if any required RSS feed cannot be fetched. It does this to avoid publishing a digest from unapproved fallback sources.
