# Daily Market News Telegram Digest

This repository contains a GitHub Actions scheduled job that fetches market-news RSS items from:

- Google News RSS: `https://news.google.com/rss/search?q=finance+market`
- Yahoo Finance RSS: `https://finance.yahoo.com/rss/finance`
- Investing.com RSS: `https://www.investing.com/rss/news.rss`

The job deduplicates and ranks the feed items, formats a 10-headline Telegram digest, and sends it through the Telegram Bot API. It also sends a follow-up "5-Minute Market Read" article synthesizing the same top 10 items into a narrative, controlled by `ARTICLE_MODE`:

- `off` — skip the article, send only the bullet digest.
- `template` (default) — a free, deterministic article stitched from the same category/sentiment data used in the digest. No extra setup required. Runs a couple of minutes' worth of reading, denser than a full 5-minute article since it has no model doing the writing.
- `llm` — an LLM writes the full ~5-minute article from the same inputs. Model-agnostic: point it at any OpenAI-chat-completions-compatible endpoint (OpenAI, Anthropic, OpenRouter, a self-hosted model, etc.) via `LLM_API_BASE`/`LLM_MODEL`/`LLM_API_KEY`. If the call fails for any reason, the job automatically falls back to the `template` article rather than failing the run.

## GitHub Setup

1. Push this repository to GitHub.
2. In the GitHub repository, open **Settings > Secrets and variables > Actions**.
3. Add these repository secrets:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `LLM_API_KEY` (only needed if you set `ARTICLE_MODE: llm` in the workflow)
4. Open **Actions > Daily Market News Telegram Digest**.
5. Use **Run workflow** once to test it manually.

The workflow also runs automatically every day at `00:00 UTC`, which is `08:00 Asia/Singapore`.

## Behavior

The job intentionally fails if any required RSS feed cannot be fetched. It does this to avoid publishing a digest from unapproved fallback sources.
