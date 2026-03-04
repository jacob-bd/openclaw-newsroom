#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# news_scan_deduped.sh — Automated News Scan Pipeline v2
# ═══════════════════════════════════════════════════════════════════
#
# Orchestrates six data sources and pipes them through quality scoring,
# enrichment, and Gemini Flash (llm_editor.py) for AI-powered curation.
#
# Flow:
#   1. RSS via blogwatcher (25 feeds)
#   2. Reddit via JSON API (13 subreddits, score-filtered)
#   3. Twitter via bird CLI + twitterapi.io
#   4. GitHub trending + releases
#   5. Tavily web search (breaking news supplement)
#   6. All → quality_score.py → enrich_top_articles.py → llm_editor.py
#   7. blogwatcher read-all
#
# Usage:
#   ./news_scan_deduped.sh              # default: top 7 picks
#   ./news_scan_deduped.sh --top 5      # top 5 picks
# ═══════════════════════════════════════════════════════════════════

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# ── Parse arguments ──────────────────────────────────────────────────
TOP_N=7

while [[ $# -gt 0 ]]; do
  case $1 in
    --top) TOP_N="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--top N]"
      echo "  --top N   Number of stories to curate (default: 7)"
      exit 0
      ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

export TOP_N

# ── Temp files (cleaned up on exit) ─────────────────────────────────
ARTICLES_FILE=$(mktemp /tmp/newscan_articles.XXXXXX)
REDDIT_FILE=$(mktemp /tmp/newscan_reddit.XXXXXX)
TAVILY_FILE=$(mktemp /tmp/newscan_tavily.XXXXXX)
TWITTER_API_FILE=$(mktemp /tmp/newscan_twitterapi.XXXXXX)
SCORED_FILE=$(mktemp /tmp/newscan_scored.XXXXXX)
ENRICHED_FILE=$(mktemp /tmp/newscan_enriched.XXXXXX)
PERSISTENT_CANDIDATES="$SCRIPT_DIR/../memory/last_scan_candidates.txt"
PERSISTENT_GITHUB="$SCRIPT_DIR/../memory/last_scan_github.txt"
GITHUB_FILE=$(mktemp /tmp/newscan_github.XXXXXX)
TWITTER_RAW=$(mktemp /tmp/newscan_twitter.XXXXXX)
PICKS_FILE=$(mktemp /tmp/newscan_picks.XXXXXX)

cleanup() {
  rm -f "$ARTICLES_FILE" "$REDDIT_FILE" "$TAVILY_FILE" "$TWITTER_API_FILE" \
        "$SCORED_FILE" "$ENRICHED_FILE" "$GITHUB_FILE" "$TWITTER_RAW" "$PICKS_FILE"
}
trap cleanup EXIT

# ── Counters for stats ───────────────────────────────────────────────
RSS_COUNT=0
REDDIT_COUNT=0
TWITTER_COUNT=0
TWITTER_API_COUNT=0
GITHUB_COUNT=0
TAVILY_COUNT=0
PICKS_COUNT=0

echo "═══════════════════════════════════════════════════════════"
echo "  News Scanner v2 (top $TOP_N)"
echo "═══════════════════════════════════════════════════════════"
echo ""

# ═════════════════════════════════════════════════════════════════════
# SOURCE 1: RSS via blogwatcher (25 feeds)
# ═════════════════════════════════════════════════════════════════════
echo "[1/5] Scanning RSS feeds..."

/usr/local/bin/timeout 90s /usr/local/bin/blogwatcher scan > /dev/null 2>&1 || echo "  Warning: RSS scan timed out (continuing)"

python3 -c '
import sys, subprocess, re

outpath = sys.argv[1]

try:
    result = subprocess.run(
        ["/usr/local/bin/blogwatcher", "articles"],
        capture_output=True, text=True, timeout=30
    )
    raw = result.stdout
except Exception as e:
    print(f"  Warning: Could not run blogwatcher articles: {e}", file=sys.stderr)
    raw = ""

# ── AI keyword filter (same logic as filter_ai_news.sh) ──────────
SHORT_KW = re.compile(r"\b(AI|AGI|LLM|GPU|TPU|RAG|API)\b")
LONG_KW = re.compile(
    r"artificial intelligence|machine learning|deep learning|"
    r"language model|GPT|Claude|Gemini|ChatGPT|OpenAI|Anthropic|"
    r"Google AI|DeepMind|agentic|neural network|transformer|"
    r"diffusion|generative AI|gen AI|Llama|Mistral|Hugging Face|"
    r"inference|training|fine-tuning|open.source|NVIDIA|DeepSeek|"
    r"Grok|xAI|Qwen|Codex|Copilot|Meta AI|Cohere|Perplexity|"
    r"multimodal|reasoning model|robotics|autonomous|chip|"
    r"acquisition|funding|valuation|launch|release|"
    r"OpenClaw|Amazon Q|Bedrock|benchmark",
    re.IGNORECASE
)

lines = raw.split("\n")
articles = []
filtered_out = 0
i = 0

while i < len(lines):
    line = lines[i].strip()
    m = re.match(r"^\[\d+\]\s+\[new\]\s+(.+)$", line)
    if m:
        title = m.group(1).strip()
        title = title.replace("|", " -")
        source = ""
        url = ""
        for j in range(i + 1, min(i + 5, len(lines))):
            next_line = lines[j].strip()
            if next_line.startswith("Blog:"):
                source = next_line[5:].strip().replace("|", " -")
            elif next_line.startswith("URL:"):
                url = next_line[4:].strip()
        if title and url:
            if SHORT_KW.search(title) or LONG_KW.search(title):
                articles.append(f"{title}|{url}|{source}")
            else:
                filtered_out += 1
    i += 1

with open(outpath, "w") as f:
    for a in articles:
        f.write(a + "\n")

print(f"  Extracted {len(articles)} AI-relevant RSS articles ({filtered_out} non-AI filtered out)", file=sys.stderr)
' "$ARTICLES_FILE"

RSS_COUNT=$(wc -l < "$ARTICLES_FILE" | tr -d ' ')
echo "     Found $RSS_COUNT articles from RSS feeds"

# ═════════════════════════════════════════════════════════════════════
# SOURCE 2: Reddit via JSON API (score-filtered)
# ═════════════════════════════════════════════════════════════════════
echo ""
echo "[2/5] Scanning Reddit (JSON API)..."

if /usr/local/bin/timeout 60s python3 "$SCRIPT_DIR/fetch_reddit_news.py" --hours 24 > "$REDDIT_FILE" 2>/dev/null; then
  REDDIT_COUNT=$(wc -l < "$REDDIT_FILE" | tr -d ' ')
  echo "  Found $REDDIT_COUNT Reddit posts (score-filtered)"
  cat "$REDDIT_FILE" >> "$ARTICLES_FILE"
else
  echo "  Warning: Reddit scan failed (continuing without)"
  REDDIT_COUNT=0
fi

# ═════════════════════════════════════════════════════════════════════
# SOURCE 3: Twitter/X (bird CLI + twitterapi.io)
# ═════════════════════════════════════════════════════════════════════
echo ""
echo "[3/5] Scanning X/Twitter..."

# 3a: bird CLI (primary — account-based)
if /usr/local/bin/timeout 90s "$SCRIPT_DIR/scan_twitter_ai.sh" > "$TWITTER_RAW" 2>&1; then
  echo "  bird CLI scan completed"
else
  echo "  Warning: bird CLI scan timed out or failed (continuing)"
fi

if [ -s "$TWITTER_RAW" ]; then
  TWITTER_COUNT=$(python3 -c '
import sys, re

twitter_file = sys.argv[1]
articles_file = sys.argv[2]
count = 0

with open(twitter_file, "r") as f:
    lines = f.readlines()

with open(articles_file, "a") as out:
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith(("===", "---", "Scanning", "Tier", "Breaking", "Product", "CEO")):
            continue
        text = line.replace("|", " -")
        urls = re.findall(r"(https?://\S+)", line)
        external_url = ""
        tweet_url = ""
        for u in urls:
            if "x.com/" in u or "twitter.com/" in u or "t.co/" in u:
                if not tweet_url:
                    tweet_url = u
            else:
                if not external_url:
                    external_url = u
        if external_url:
            out.write(f"{text}|{external_url}|X/Twitter\n")
        else:
            url = tweet_url
            out.write(f"{text}|{url}|X/Twitter (tweet)\n")
        count += 1

print(count)
' "$TWITTER_RAW" "$ARTICLES_FILE")
  echo "     bird CLI: $TWITTER_COUNT tweets"
else
  TWITTER_COUNT=0
fi

# 3b: twitterapi.io (supplement — keyword search)
if /usr/local/bin/timeout 30s python3 "$SCRIPT_DIR/fetch_twitter_api.py" --max-queries 2 > "$TWITTER_API_FILE" 2>/dev/null; then
  TWITTER_API_COUNT=$(wc -l < "$TWITTER_API_FILE" | tr -d ' ')
  echo "     twitterapi.io: $TWITTER_API_COUNT tweets"
  cat "$TWITTER_API_FILE" >> "$ARTICLES_FILE"
else
  echo "  Warning: twitterapi.io scan failed (continuing)"
  TWITTER_API_COUNT=0
fi

# ═════════════════════════════════════════════════════════════════════
# SOURCE 4: GitHub Trending + Releases
# ═════════════════════════════════════════════════════════════════════
echo ""
echo "[4/5] Scanning GitHub trending + releases..."

if /usr/local/bin/timeout 45s python3 "$SCRIPT_DIR/github_trending.py" > "$GITHUB_FILE" 2>/dev/null; then
  GITHUB_COUNT=$(wc -l < "$GITHUB_FILE" | tr -d ' ')
  echo "  Found $GITHUB_COUNT trending/release repos"
else
  echo "  Warning: GitHub scan timed out or failed (continuing)"
  GITHUB_COUNT=0
fi

# ═════════════════════════════════════════════════════════════════════
# SOURCE 5: Tavily Web Search (breaking news supplement)
# ═════════════════════════════════════════════════════════════════════
echo ""
echo "[5/5] Tavily web search..."

if /usr/local/bin/timeout 30s python3 "$SCRIPT_DIR/fetch_web_news.py" --max-queries 3 --max-results 5 > "$TAVILY_FILE" 2>/dev/null; then
  TAVILY_COUNT=$(wc -l < "$TAVILY_FILE" | tr -d ' ')
  echo "  Found $TAVILY_COUNT web articles"
  cat "$TAVILY_FILE" >> "$ARTICLES_FILE"
else
  echo "  Warning: Tavily scan failed (continuing)"
  TAVILY_COUNT=0
fi

# ═════════════════════════════════════════════════════════════════════
# QUALITY SCORING PRE-FILTER
# ═════════════════════════════════════════════════════════════════════
echo ""
TOTAL_RAW=$((RSS_COUNT + REDDIT_COUNT + TWITTER_COUNT + TWITTER_API_COUNT + TAVILY_COUNT))
echo "Quality scoring ($TOTAL_RAW candidates)..."

if [ "$TOTAL_RAW" -gt 0 ]; then
  python3 "$SCRIPT_DIR/quality_score.py" --input "$ARTICLES_FILE" --max 50 > "$SCORED_FILE" 2>/dev/null
  SCORED_COUNT=$(wc -l < "$SCORED_FILE" | tr -d ' ')
  echo "  Top $SCORED_COUNT articles after scoring + dedup"
else
  cp "$ARTICLES_FILE" "$SCORED_FILE"
  SCORED_COUNT=0
fi

# ═════════════════════════════════════════════════════════════════════
# ARTICLE ENRICHMENT (full text for top articles)
# ═════════════════════════════════════════════════════════════════════
echo ""
echo "Enriching top articles with full text..."

if [ "$SCORED_COUNT" -gt 0 ]; then
  if /usr/local/bin/timeout 60s python3 "$SCRIPT_DIR/enrich_top_articles.py" --input "$SCORED_FILE" --max 8 --max-chars 1200 > "$ENRICHED_FILE" 2>/dev/null; then
    echo "  Enrichment complete"
  else
    echo "  Warning: Enrichment failed (using scored articles without full text)"
    cp "$SCORED_FILE" "$ENRICHED_FILE"
  fi
else
  cp "$SCORED_FILE" "$ENRICHED_FILE"
fi

# ═════════════════════════════════════════════════════════════════════
# LLM EDITORIAL FILTER (Gemini Flash via llm_editor.py)
# ═════════════════════════════════════════════════════════════════════
echo ""
echo "Running LLM editorial filter (Gemini Flash)..."

TOTAL_CANDIDATES=$((TOTAL_RAW + GITHUB_COUNT))
echo "   Pipeline: ${TOTAL_RAW} raw -> ${SCORED_COUNT:-$TOTAL_RAW} scored -> LLM"

if [ "$TOTAL_CANDIDATES" -eq 0 ]; then
  echo ""
  echo "No new stories found from any source. Nothing to curate."
  exit 0
fi

LLM_CMD="python3 $SCRIPT_DIR/llm_editor.py --file $ENRICHED_FILE"
if [ -s "$GITHUB_FILE" ]; then
  LLM_CMD="$LLM_CMD --github $GITHUB_FILE"
fi

LLM_SUCCESS=true
if eval "$LLM_CMD" > "$PICKS_FILE" 2>/tmp/llm_editor.log; then
  PICKS_COUNT=$(wc -l < "$PICKS_FILE" | tr -d ' ')
  echo "  LLM selected $PICKS_COUNT stories"
else
  echo "  Warning: LLM editor failed (see /tmp/llm_editor.log)"
  LLM_SUCCESS=false
fi

# ═════════════════════════════════════════════════════════════════════
# FORMAT & DISPLAY OUTPUT
# ═════════════════════════════════════════════════════════════════════
echo ""
cp "$ENRICHED_FILE" "$PERSISTENT_CANDIDATES" 2>/dev/null
cp "$GITHUB_FILE" "$PERSISTENT_GITHUB" 2>/dev/null

echo "═══════════════════════════════════════════════════════════"
echo "  TOP PICKS"
echo "═══════════════════════════════════════════════════════════"
echo ""

if [ "$LLM_SUCCESS" = false ] || [ ! -s "$PICKS_FILE" ]; then
  echo "All LLM providers failed. No curated stories to display."
  echo "Check /tmp/llm_editor.log for details."
  echo ""
  echo "Candidates were saved to: $PERSISTENT_CANDIDATES"
  echo "Re-run manually: python3 $SCRIPT_DIR/llm_editor.py --file $PERSISTENT_CANDIDATES"
else
  python3 -c '
import sys, json

picks_file = sys.argv[1]

EMOJI_MAP = {
    "rss": "[article]",
    "twitter": "[tweet]",
    "github": "[github]",
}

with open(picks_file, "r") as f:
    lines = f.readlines()

total = sum(1 for l in lines if l.strip())

for i, line in enumerate(lines):
    line = line.strip()
    if not line:
        continue
    try:
        pick = json.loads(line)
    except json.JSONDecodeError:
        continue

    rank = pick.get("rank", "?")
    title = pick.get("title", "(no title)")
    summary = pick.get("summary", "")
    url = pick.get("url", "")
    source = pick.get("source", "unknown")
    category = pick.get("category", "other")
    story_type = pick.get("type", "rss")

    is_tweet = "(tweet)" in source

    if is_tweet:
        tag = "[tweet]"
    else:
        tag = EMOJI_MAP.get(story_type, "[article]")

    print(f"{rank}. {tag} {title}")
    if summary:
        print(f"   Why: {summary}")
    if url:
        if is_tweet:
            print(f"   View tweet: {url}")
        else:
            print(f"   Link: {url}")
    source_display = source.replace(" (tweet)", "")
    print(f"   Source: {source_display} [{category}]")
    print()
    if i < len(lines) - 1:
        print("---")
        print()
' "$PICKS_FILE"
fi

# ═════════════════════════════════════════════════════════════════════
# RECORD ALL SCORED CANDIDATES TO DEDUP DB
# ═════════════════════════════════════════════════════════════════════
if [ -s "$SCORED_FILE" ]; then
  python3 -c '
import sys
sys.path.insert(0, sys.argv[2])
try:
    from dedup_db import DedupDB
    db = DedupDB()
    articles = []
    with open(sys.argv[1], "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("|")
            if len(parts) >= 3:
                articles.append({"url": parts[1], "title": parts[0], "source": parts[2]})
    db.record_batch(articles, status="scored")
    print(f"  Recorded {len(articles)} scored candidates to dedup DB", file=sys.stderr)
except ImportError:
    print("  Warning: dedup_db not available, skipping DB recording", file=sys.stderr)
except Exception as e:
    print(f"  Warning: DB recording failed: {e}", file=sys.stderr)
' "$SCORED_FILE" "$SCRIPT_DIR" 2>&1
fi

# ═════════════════════════════════════════════════════════════════════
# CLEANUP: Mark articles as read in blogwatcher
# ═════════════════════════════════════════════════════════════════════
echo "Marking RSS articles as read..."
echo "y" | /usr/local/bin/blogwatcher read-all > /dev/null 2>&1 || echo "  Warning: Could not mark articles as read"

# ═════════════════════════════════════════════════════════════════════
# STATS
# ═════════════════════════════════════════════════════════════════════
echo "═══════════════════════════════════════════════════════════"
echo "Sources: $RSS_COUNT RSS + $REDDIT_COUNT Reddit + $((TWITTER_COUNT + TWITTER_API_COUNT)) Twitter + $GITHUB_COUNT GitHub + $TAVILY_COUNT Tavily"
echo "Pipeline: $TOTAL_CANDIDATES raw -> ${SCORED_COUNT:-N/A} scored -> $PICKS_COUNT curated picks"
echo "═══════════════════════════════════════════════════════════"
