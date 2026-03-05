# OpenClaw Automated News Scanner

![Version](https://img.shields.io/badge/version-v2.1-blue) [![Changelog](https://img.shields.io/badge/changelog-view-lightgrey)](CHANGELOG.md)

<a href="https://t.me/genaispot"><img src="assets/gen-ai-spotlight-logo.jpg" width="120" align="left" style="margin-right: 16px;" /></a>

**See it in action:** This pipeline powers [Gen AI Spotlight](https://t.me/genaispot) on Telegram — a fully automated AI news channel. Join to see what the output looks like in production.

<br clear="left"/>

### Video Walkthroughs

<table>
<tr>
<td width="50%">
<a href="https://youtu.be/2nk5CqrXX9E"><img src="assets/video-thumbnail-1.jpg" width="100%" /></a>
<br/><a href="https://youtu.be/2nk5CqrXX9E">Building the News Scan Pipeline</a>
</td>
<td width="50%">
<a href="https://youtu.be/cvdAqCM1wGs"><img src="assets/video-thumbnail-2.jpg" width="100%" /></a>
<br/><a href="https://youtu.be/cvdAqCM1wGs">Pipeline Deep Dive & Demo</a>
</td>
</tr>
</table>

---

A complete, automated AI news scanning pipeline for [OpenClaw](https://github.com/openclaw/openclaw). Scans 5 data sources every 2 hours, scores and deduplicates results with a persistent SQLite database, enriches top articles with full text, and uses a 3-tier LLM failover chain (Gemini Flash Lite → Grok via OpenRouter → Gemini Flash) to curate the best stories for your channel.

**Pipeline cost:** ~$5/month (Gemini Flash Lite API + Tavily free tier)

---

## How This Fits Into OpenClaw

This pipeline is designed to run as an **OpenClaw cron job**. Here's how it integrates:

```
OpenClaw Gateway
├── Cron scheduler fires every 2 hours
│   └── Runs news_scan_deduped.sh (the orchestrator)
│       ├── Calls 5 data source scripts (RSS, Reddit, Twitter, GitHub, Tavily)
│       ├── Scores + deduplicates via quality_score.py + dedup_db.py
│       ├── Enriches top articles via enrich_top_articles.py
│       └── Curates via llm_editor.py (3-tier LLM failover)
│
├── Agent receives the pipeline output
│   └── Formats and delivers to your channel (Telegram, Slack, etc.)
│
├── Nightly cron (optional)
│   └── Runs update_editorial_profile.py to learn from your approvals/rejections
│
└── memory/ directory
    ├── news_dedup.db             ← SQLite dedup database (cross-scan)
    ├── editorial_profile.md      ← LLM editor reads this for guidance
    ├── editorial_decisions.md    ← Your approval/rejection log
    ├── scanner_presented.md      ← Auto-logged: what was presented
    ├── news_log.md               ← Your posted stories (for dedup)
    ├── last_scan_candidates.txt  ← Persistent for "next 10" requests
    └── github_trending_state.json ← Star velocity tracking
```

**Key integration points:**

1. **Scripts live in** `~/.openclaw/workspace/scripts/` — OpenClaw's standard location for agent-callable scripts
2. **Memory files live in** `~/.openclaw/workspace/memory/` — persistent across sessions
3. **The cron job** uses `sessionTarget: "isolated"` so each scan gets a clean session (no context contamination)
4. **The agent model** (e.g., Kimi K2.5) orchestrates the pipeline. The actual AI curation uses a 3-tier LLM failover chain (Gemini Flash Lite → Grok → Gemini Flash) via direct API calls — so your cron model doesn't need to be expensive
5. **Delivery** is handled by OpenClaw's channel system (Telegram, Slack, etc.)

**Not using OpenClaw?** The scripts work standalone too — just run `./news_scan_deduped.sh` from a regular cron job or shell. The only OpenClaw-specific parts are the cron job setup and channel delivery.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    news_scan_deduped.sh                          │
│                    (Main Orchestrator)                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  [1] RSS Feeds          ──→  inline AI keyword filter (25 feeds) │
│  [2] Reddit JSON API    ──→  fetch_reddit_news.py (13 subs)     │
│  [3] Twitter/X          ──→  scan_twitter_ai.sh (bird CLI)      │
│                          ──→  fetch_twitter_api.py (API search)  │
│  [4] GitHub             ──→  github_trending.py (trending+rel)  │
│  [5] Tavily Web Search  ──→  fetch_web_news.py (5 queries)      │
│                                                                 │
│  All sources are best-effort — failures don't kill the pipeline │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  dedup_db.py        → SQLite cross-scan dedup (URL + title)     │
│                       Persistent memory across all runs         │
│                                                                 │
│  quality_score.py   → Score + within-batch dedup (80%)          │
│                       + cross-scan dedup via SQLite             │
│                       Output: top 50 scored candidates          │
│                                                                 │
│  enrich_top_articles.py → Fetch full text for top 8 articles    │
│                           CF Markdown preferred, HTML fallback  │
│                                                                 │
│  llm_editor.py      → 3-tier LLM failover chain                │
│                       Flash Lite → Grok (OpenRouter) → Flash   │
│                       Reads editorial_profile.md for guidance   │
│                       SQLite pre-filter before LLM call         │
│                       Output: up to 7 ranked picks (JSON)       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Prerequisites

### Required
- **OpenClaw** (v2026.2.23+) — the AI agent platform that runs the cron job
- **Python 3.9+** — all scripts use stdlib only (no pip packages)
- **blogwatcher** — RSS feed scanner (`brew install blogwatcher` or equivalent)

### API Keys (set as environment variables)
| Key | Required? | Purpose | Free Tier |
|-----|-----------|---------|-----------|
| `GEMINI_API_KEY` | Yes | Gemini Flash Lite / Flash for LLM curation | Google AI Studio — generous free tier |
| `OPENROUTER_API_KEY` | Recommended | Grok 4.1 Fast failover (via OpenRouter) | Pay-per-token (cheap) |
| `GH_TOKEN` | Recommended | GitHub API (5000 req/h vs 60/h unauthenticated) | GitHub personal access token (free) |
| `TAVILY_API_KEY` | Optional | Tavily web search for breaking news | 1000 queries/month free |
| `TWITTERAPI_IO_KEY` | Optional | twitterapi.io keyword search supplement | Paid (small monthly fee) |

### Optional Tools
- **bird** — Twitter/X CLI tool (for `scan_twitter_ai.sh`). Install: `npm install -g @steipete/bird` or `brew install steipete/tap/bird` — see [bird.fast](https://bird.fast). If not installed, the Twitter bird CLI source is skipped gracefully.

---

## Installation

### Step 1: Copy Scripts

Copy all scripts from the `scripts/` directory to your OpenClaw workspace:

```bash
cp scripts/*.sh scripts/*.py ~/.openclaw/workspace/scripts/
chmod +x ~/.openclaw/workspace/scripts/news_scan_deduped.sh
chmod +x ~/.openclaw/workspace/scripts/filter_ai_news.sh
chmod +x ~/.openclaw/workspace/scripts/scan_twitter_ai.sh
```

### Step 2: Set Up RSS Feeds (blogwatcher)

Install blogwatcher and add your RSS feeds. Here's a recommended starter set:

```bash
# Wire services (Tier 1 — highest trust)
blogwatcher add "Reuters Tech" "https://www.reuters.com/technology/rss"
blogwatcher add "Axios AI" "https://api.axios.com/feed/top/technology"

# Tech press (Tier 2)
blogwatcher add "TechCrunch AI" "https://techcrunch.com/category/artificial-intelligence/feed/"
blogwatcher add "The Verge" "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"
blogwatcher add "THE DECODER" "https://the-decoder.com/feed/"
blogwatcher add "Ars Technica" "https://feeds.arstechnica.com/arstechnica/technology-lab"
blogwatcher add "VentureBeat AI" "https://venturebeat.com/category/ai/feed/"
blogwatcher add "Wired AI" "https://www.wired.com/feed/tag/ai/latest/rss"
blogwatcher add "MIT Tech Review" "https://www.technologyreview.com/feed/"

# AI company blogs (Tier 1-2)
blogwatcher add "OpenAI Blog" "https://openai.com/blog/rss.xml"
blogwatcher add "Google AI Blog" "https://blog.google/technology/ai/rss/"
blogwatcher add "Hugging Face Blog" "https://huggingface.co/blog/feed.xml"

# Bloggers & newsletters (Tier 2-3)
blogwatcher add "Simon Willison" "https://simonwillison.net/atom/everything/"
blogwatcher add "Bens Bites" "https://www.bensbites.com/feed"
```

Adjust the `SOURCE_TIERS` dictionary in `filter_ai_news.sh` to match your feed names exactly.

### Step 3: Set Up Editorial Profile

Copy and customize the editorial profile template:

```bash
mkdir -p ~/.openclaw/workspace/memory
cp config/editorial_profile_template.md ~/.openclaw/workspace/memory/editorial_profile.md
```

Edit `~/.openclaw/workspace/memory/editorial_profile.md` to reflect your channel's editorial voice:
- What topics you always pick
- What you usually skip
- Your source trust ranking
- Story selection rules

This profile is read by the LLM editor on every scan and directly influences story selection.

### Step 4: Seed the Dedup Database

Import your existing post history so the dedup system has context from day one:

```bash
cd ~/.openclaw/workspace/scripts
python3 dedup_db.py --seed
python3 dedup_db.py --stats
```

If this is a fresh install with no history, skip this step — the database will populate automatically as the pipeline runs.

### Step 5: Set Environment Variables

Add API keys to your OpenClaw LaunchAgent plist (macOS):

```bash
# Add to ~/Library/LaunchAgents/ai.openclaw.gateway.plist under EnvironmentVariables:
# <key>GEMINI_API_KEY</key>
# <string>your-gemini-api-key</string>
# <key>OPENROUTER_API_KEY</key>
# <string>your-openrouter-key</string>
# <key>GH_TOKEN</key>
# <string>your-github-token</string>
# <key>TAVILY_API_KEY</key>
# <string>your-tavily-key</string>
# <key>TWITTERAPI_IO_KEY</key>
# <string>your-twitterapi-key</string>

# Then restart the gateway:
launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway
```

Or export them in your shell for testing:

```bash
export GEMINI_API_KEY="your-key"
export OPENROUTER_API_KEY="your-key"
export GH_TOKEN="your-token"
export TAVILY_API_KEY="your-key"
```

### Step 6: Create the Cron Job

Add the news scan as an OpenClaw cron job:

```bash
openclaw cron add \
  --name "Bi-Hourly News Scan" \
  --cron "40 9,11,13,15,17,19,21 * * *" \
  --message "Run the Gen AI news scanner: bash ~/.openclaw/workspace/scripts/news_scan_deduped.sh" \
  --agent main \
  --model "kimi-coding/k2p5" \
  --announce \
  --channel telegram \
  --tz "America/New_York"
```

**Schedule breakdown:** Runs at :40 past the hour at 9am, 11am, 1pm, 3pm, 5pm, 7pm, 9pm. Adjust the hours and timezone to match your audience.

**Model choice:** The cron job uses a cheap/mid-tier model (like Kimi K2.5) to orchestrate the pipeline. The actual AI curation happens via Gemini Flash API directly (called by `llm_editor.py`), so the cron model doesn't need to be expensive.

### Step 7: Test the Pipeline

Run a manual test:

```bash
cd ~/.openclaw/workspace/scripts
./news_scan_deduped.sh --top 5
```

You should see output like:
```
═══════════════════════════════════════════════════════════
  📡 [YOUR_CHANNEL_NAME] — News Scanner v2 (top 5)
═══════════════════════════════════════════════════════════

📰 [1/5] Scanning RSS feeds...
  ✅ Extracted 12 new RSS articles
🔴 [2/5] Scanning Reddit (JSON API)...
  ✅ Found 45 Reddit posts (score-filtered)
...
```

---

## How Each Script Works

### 1. `news_scan_deduped.sh` — Main Orchestrator
The master script that calls everything else in sequence. Collects articles from all 5 sources, pipes through scoring/enrichment/LLM, and formats output. All sources are best-effort — if one fails, the pipeline continues with what it has.

### 2. `filter_ai_news.sh` — RSS Keyword Filter (standalone)
Reads articles from blogwatcher, filters by AI-related keywords (with word-boundary matching for short keywords like "AI" to avoid false positives), assigns source tiers, and filters out Reddit noise (questions, rants, memes).

> **Note:** As of v2, the main orchestrator (`news_scan_deduped.sh`) handles AI keyword filtering inline during RSS extraction. This script still exists for standalone use or debugging, but is no longer called by the pipeline.

### 3. `fetch_reddit_news.py` — Reddit JSON API Scanner
Fetches posts from 13 AI-related subreddits using Reddit's public JSON API (no auth needed). Features:
- Per-subreddit score thresholds (30-50 upvotes minimum)
- Flair filtering for noisy subs (e.g., only "News" flair from r/technology)
- Noise filter (skips questions, rants, short titles)
- Concurrent fetching (3 workers)

### 4. `scan_twitter_ai.sh` — Twitter/X bird CLI Scanner
Scans official AI company accounts, tech reporters/leakers, and CEO accounts using the `bird` CLI tool. Three-tier account system:
- Tier 1: Official accounts (OpenAI, Anthropic, Google, etc.)
- Tier 2: Reporters and leakers (break news first)
- Tier 3: CEOs (context, not breaking news)

### 5. `fetch_twitter_api.py` — twitterapi.io Keyword Search
Supplements bird CLI with keyword-based search. Uses engagement filtering (50+ likes or 5000+ followers) to cut noise. Properly tags tweet-only stories (no external article URL).

### 6. `github_trending.py` — GitHub Trending + Releases
Three strategies:
- **Emerging:** Repos created in the last 7 days with 50+ stars
- **Velocity:** Established repos (1000+ stars) gaining traction fast
- **Releases:** New releases from 16 key AI repos (Anthropic SDK, OpenAI SDK, Ollama, etc.)

Maintains state between runs to calculate star velocity.

### 7. `fetch_web_news.py` — Tavily Web Search
Catches breaking news that RSS feeds miss. 5 focused queries, 2-day freshness filter. Skips domains already covered by RSS (Reddit, Twitter, GitHub, YouTube, arxiv). Filters out homepage URLs.

### 8. `dedup_db.py` — SQLite Cross-Scan Dedup Database
Persistent dedup memory shared across all pipeline runs. Stores normalized URLs and titles from every scan in `~/.openclaw/workspace/memory/news_dedup.db`. Features:
- URL normalization (strips query params, fragments, www prefix, trailing punctuation)
- Title similarity matching (75% threshold via SequenceMatcher, 2-day window)
- Bulk check API for efficient pre-filtering
- CLI for seeding from historical logs, checking URLs/titles, and viewing stats

### 9. `quality_score.py` — Scoring + Deduplication
Scores every article based on:
- Source tier (wire services get +5, tech press +3, etc.)
- High-value keywords (acquisitions, billion, launch, security, etc.)
- Breaking news signals (exclusive, confirmed, first look, etc.)
- Title quality (length heuristic)

Two-stage dedup: within-batch similarity (80% threshold) followed by cross-scan dedup against the SQLite database. Outputs top 50.

### 10. `enrich_top_articles.py` — Full Text Fetcher
Fetches full article text for the top 8 scored articles. Tries Cloudflare Markdown for Agents first (clean markdown), falls back to HTML extraction. Skips paywalled sites. 1200 character cap per article.

### 11. `llm_editor.py` — LLM Editorial Curation
The AI brain of the pipeline. Sends all scored candidates + editorial profile + recent post history to a 3-tier LLM failover chain. The LLM selects up to 7 stories, ranks them, assigns categories, and writes 1-sentence summaries.

Features:
- **3-tier failover chain:** Gemini 3.1 Flash Lite → Grok 4.1 Fast (OpenRouter) → Gemini 3 Flash Preview. Alternates providers to avoid double failure.
- SQLite pre-filter (skips already-seen URLs and similar titles before calling the LLM)
- Editorial profile integration (learns your preferences over time)
- Structured JSON output with validation and robust parsing (handles markdown fences, dict wrappers, etc.)
- Records all picks to the SQLite dedup database after selection
- Logs all presented stories to `scanner_presented.md`

### 12. `update_editorial_profile.py` — Profile Updater
Runs nightly. Analyzes your approval/rejection patterns and updates the editorial profile's stats section. Also identifies "blind spots" — topics you manually seek out but the scanner doesn't catch.

---

## Customization Guide

### Adding RSS Feeds
1. Add the feed to blogwatcher: `blogwatcher add "Feed Name" "https://feed-url/rss"`
2. Add the feed name to `SOURCE_TIERS` in `filter_ai_news.sh` with the appropriate tier (1-3)
3. Add any new keywords to the `LONG_KEYWORDS` list if needed

### Adding Reddit Subreddits
Edit the `SUBREDDITS` list in `fetch_reddit_news.py`:
```python
{"sub": "YourSubreddit", "sort": "hot", "limit": 25, "min_score": 30,
 "flairs": ["News", "Discussion"]},  # flairs are optional
```

### Adding Twitter Accounts to Monitor
Edit the account arrays in `scan_twitter_ai.sh`:
- `OFFICIAL_ACCOUNTS` — for company accounts
- `REPORTER_ACCOUNTS` — for journalists and leakers
- `CEO_ACCOUNTS` — for thought leaders

### Adding GitHub Release Repos
Add to the `RELEASE_REPOS` list in `github_trending.py`:
```python
"owner/repo-name",
```

### Changing the LLM Models
Edit the `FAILOVER_CHAIN` list in `llm_editor.py`. Each entry specifies a model name, API type (`gemini` or `openrouter`), environment variable for the API key, and timeout. The chain is tried in order — the first provider that responds wins.

### Adjusting Scan Frequency
Edit the cron expression:
```bash
openclaw cron edit <job-id> --cron "0 */3 * * *"  # every 3 hours
```

---

## File Structure

```
openclaw-news-scan/
├── README.md                              # This file
├── CHANGELOG.md                           # Version history and migration guide
├── scripts/
│   ├── news_scan_deduped.sh              # Main orchestrator (inline AI filter)
│   ├── dedup_db.py                       # SQLite cross-scan dedup database
│   ├── quality_score.py                  # Scoring + two-stage dedup
│   ├── enrich_top_articles.py            # Full text fetcher
│   ├── llm_editor.py                     # LLM curation (3-tier failover)
│   ├── filter_ai_news.sh                 # RSS keyword filter (standalone)
│   ├── fetch_reddit_news.py              # Reddit JSON API
│   ├── scan_twitter_ai.sh               # Twitter bird CLI
│   ├── fetch_twitter_api.py              # twitterapi.io search
│   ├── github_trending.py               # GitHub trending + releases
│   ├── fetch_web_news.py                # Tavily web search
│   ├── update_editorial_profile.py      # Editorial profile updater
│   └── test_components.py               # Unit tests (68 tests)
└── config/
    └── editorial_profile_template.md     # Template — customize for your channel
```

---

## Pipeline Flow Summary

```
RSS (25 feeds) ─────────┐                                                   ┌─ Gemini Flash Lite
Reddit (13 subs) ───────┤    AI keyword     quality_score.py   enrich_top   │
Twitter (bird + API) ───┤──→ pre-filter ──→ + dedup_db.py  ──→ articles ──→ ├─ Grok (OpenRouter) ──→ Output
GitHub (trending+rel) ──┤   (inline)        (score + dedup)    (max 8)      │  (failover chain)
Tavily (5 queries) ─────┘                    (max 50)                       └─ Gemini Flash Preview
```

**Typical run:** ~100 raw → ~50 after AI filter → 50 scored → 8 enriched → 3-7 curated picks

---

## Cost Breakdown

| Component | Monthly Cost | Notes |
|-----------|-------------|-------|
| Gemini Flash Lite API | ~$1-2/month | Primary LLM — ~7 calls/day, ~30K tokens each |
| OpenRouter (Grok failover) | ~$0-1/month | Only used when Gemini fails |
| Tavily API | Free | 1000 queries/month free tier covers it |
| GitHub API | Free | Personal access token, 5000 req/h |
| twitterapi.io | ~$10/month | Optional — bird CLI is free |
| OpenClaw cron model | Varies | Depends on your model choice |
| **Total** | **~$5/month** | Without twitterapi.io |

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| "GEMINI_API_KEY not set" | Add to LaunchAgent plist or export in shell. Pipeline warns but continues (failover may use OpenRouter). |
| Reddit 429 (rate limit) | Normal with 2h spacing. Reduce subreddits or increase --hours |
| Reddit 404 on a sub | Sub may be private/quarantined. Remove from config. |
| bird CLI not found | Install bird or remove scan_twitter_ai.sh call |
| "No new stories found" | RSS feeds may all be read. Wait for new articles. |
| All LLM providers failed | Check that `GEMINI_API_KEY` and/or `OPENROUTER_API_KEY` are set. The pipeline saves candidates to a file for manual re-run. |
| LLM editor timeout | Increase timeout values in the `FAILOVER_CHAIN` in `llm_editor.py` |
| Pipeline takes too long | Increase cron timeout: `openclaw cron edit <id> --timeout 120` |
| GitHub rate limit | Set GH_TOKEN env var for 5000 req/h (vs 60/h) |
| Duplicate stories | SQLite dedup handles this automatically. Run `python3 dedup_db.py --seed` to import historical posts. Check DB status: `python3 dedup_db.py --stats` |
| Non-AI articles leaking | The inline AI keyword filter should catch these. Check the keyword patterns in `news_scan_deduped.sh` and add missing terms. |

---

## Learning & Feedback Loop

The system learns from your editorial decisions:

1. **During the day:** The scanner presents picks. You approve or skip them.
2. **At night:** `update_editorial_profile.py` analyzes your patterns.
3. **Next scan:** The LLM editor reads the updated profile and adjusts.

To log decisions, create `~/.openclaw/workspace/memory/editorial_decisions.md`:
```
[2026-03-01T10:00:00-05:00] APPROVED | Story Title Here | https://url | category
[2026-03-01T10:00:00-05:00] SKIPPED | Another Story | https://url | category
[2026-03-01T14:00:00-05:00] MANUAL_DRAFT | Story I Found Myself | https://url | category
```

---

## Credits

Built by [Jacob Ben David](https://github.com/jacob-bd) with [OpenClaw](https://github.com/openclaw/openclaw), Gemini Flash, and a collection of free/low-cost APIs.
Inspired by the `tech-news-digest` ClawHub skill (v3.14.0 by dinstein).

## License

MIT — use it however you want. If you build something cool with it, let me know!
