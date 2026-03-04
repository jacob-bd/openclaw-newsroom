#!/usr/bin/env python3
"""
llm_editor.py - AI Editor for Automated News Scanning
======================================================
Replaces deterministic keyword filtering with Gemini Flash AI-powered
story selection. Reads candidate articles, an editorial profile, and
recent post history, then calls Gemini to pick the top stories.

Usage:
    python3 llm_editor.py --file candidates.txt [--github github.txt]

Input format (pipe-delimited, one per line):
    TITLE|URL|SOURCE
    TITLE|URL|SOURCE|TIER   (tier is optional, ignored by LLM)

Output (stdout, one JSON object per line):
    {"rank": 1, "title": "...", "url": "...", "source": "...",
     "type": "rss", "summary": "...", "category": "..."}

Logs picked stories to scanner_presented.md (append).
All status/debug messages go to stderr.
"""

import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

try:
    from dedup_db import DedupDB, normalize_url
    HAS_DEDUP_DB = True
except ImportError:
    HAS_DEDUP_DB = False

# ── Paths (customize to your workspace) ──────────────────────────────
WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE",
                                os.path.expanduser("~/.openclaw/workspace")))
MEMORY = WORKSPACE / "memory"
EDITORIAL_PROFILE = MEMORY / "editorial_profile.md"
SCANNER_PRESENTED = MEMORY / "scanner_presented.md"
NEWS_LOG = MEMORY / "news_log.md"

# ── Configuration ────────────────────────────────────────────────────
GEMINI_MODEL = "gemini-3-flash-preview"
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)
TEMPERATURE = 0.3
TIMEOUT_SEC = 120
MAX_ARTICLES = 500

# ── Failover LLM chain ──────────────────────────────────────────────
FAILOVER_CHAIN = [
    {
        "name": "Gemini 3.1 Flash Lite",
        "model": "gemini-3.1-flash-lite-preview",
        "api": "gemini",
        "env_key": "GEMINI_API_KEY",
        "timeout": 120,
    },
    {
        "name": "OpenRouter (Grok 4.1 Fast)",
        "model": "x-ai/grok-4.1-fast",
        "api": "openrouter",
        "env_key": "OPENROUTER_API_KEY",
        "timeout": 90,
    },
    {
        "name": "Gemini 3 Flash Preview",
        "model": "gemini-3-flash-preview",
        "api": "gemini",
        "env_key": "GEMINI_API_KEY",
        "timeout": 120,
    },
]
VALID_CATEGORIES = {
    "ai_product", "m_and_a", "model_release", "security", "geopolitics",
    "github_trending", "gaming", "fintech", "hardware", "open_source", "other"
}


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[llm_editor {ts}] {msg}", file=sys.stderr)


def estimate_tokens(text):
    return len(text) // 4


def parse_articles(filepath):
    articles = []
    try:
        with open(filepath, "r") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("|")
                if len(parts) < 3:
                    continue
                articles.append({
                    "title": parts[0].strip(),
                    "url": parts[1].strip(),
                    "source": parts[2].strip(),
                })
    except FileNotFoundError:
        log(f"ERROR: File not found: {filepath}")
        sys.exit(1)
    return articles


def load_file_safe(path, tail_lines=None):
    try:
        with open(path, "r") as f:
            lines = f.readlines()
        if tail_lines and len(lines) > tail_lines:
            lines = lines[-tail_lines:]
        return "".join(lines)
    except FileNotFoundError:
        return ""
    except Exception as e:
        log(f"  Error reading {path}: {e}")
        return ""


def filter_already_posted(articles):
    """
    Deterministic pre-filter using SQLite dedup database.
    Falls back to text-file URL matching if dedup_db unavailable.
    """
    if HAS_DEDUP_DB:
        db = DedupDB()
        new, dupes, url_dupes, title_dupes = db.bulk_check(articles)
        if dupes:
            log("Pre-filtered %d candidates via SQLite (%d URL, %d title matches)" % (
                len(dupes), url_dupes, title_dupes))
        return new

    # Fallback: original text-file matching
    full_log = load_file_safe(NEWS_LOG)
    if not full_log:
        return articles

    presented_log = load_file_safe(SCANNER_PRESENTED)

    url_pattern = re.compile(r'https?://[^\s|>\]\)"\']+')
    posted_urls = set()
    for text in [full_log, presented_log]:
        for url in url_pattern.findall(text):
            url = url.rstrip(".,;:)")
            posted_urls.add(url)

    if not posted_urls:
        return articles

    filtered = []
    removed = 0
    for a in articles:
        candidate_url = a["url"].rstrip(".,;:)")
        if candidate_url in posted_urls:
            log("  PRE-FILTERED (already posted): %s" % a['title'][:60])
            removed += 1
        else:
            filtered.append(a)

    log("Pre-filtered %d candidates (already posted)" % removed)
    return filtered


def build_prompt(articles, github_articles, editorial_profile, recent_posts, top_n):
    article_list = []
    for i, a in enumerate(articles, 1):
        article_list.append(f"  {i}. [{a['source']}] {a['title']}\n     URL: {a['url']}")
    articles_text = "\n".join(article_list)

    github_text = ""
    if github_articles:
        gh_list = []
        for i, g in enumerate(github_articles, 1):
            gh_list.append(f"  {i}. [{g['source']}] {g['title']}\n     URL: {g['url']}")
        github_text = (
            "\n\n## GitHub Trending Repos\n"
            "These are trending GitHub repositories. Include any that are genuinely\n"
            "newsworthy for your audience.\n\n"
            + "\n".join(gh_list)
        )

    prompt = f"""You are the AI editor for an automated news channel. Your job is to select
the top {top_n} stories from the candidate list below.

## Editorial Profile
{editorial_profile}

## Recently Posted Stories (do NOT pick duplicates of these)
{recent_posts if recent_posts else '(No recent posts available)'}

## Candidate Articles
{articles_text}
{github_text}

## Your Task
Select UP TO {top_n} stories from the candidates above. Rank them by
newsworthiness for the target audience.

## Rules
1. Return UP TO {top_n} stories. Quality matters more than quantity — 3 great picks are better than 7 mediocre ones.
2. Do NOT pick stories that duplicate recently posted stories (same event).
   If a candidate covers the SAME EVENT as a recently posted story — even
   from a different source or with a different headline — do NOT pick it.
3. Maximum 2 stories from the same source.
4. Include a 1-sentence summary explaining WHY each story matters.
5. Rank by newsworthiness: breaking news > major deals > product launches > analysis.
6. Prefer concrete news (X acquired Y, X launched Z) over speculation or opinion.
7. If a GitHub repo is trending AND relevant to the audience, include it.
8. Assign each story a category from this list:
   ai_product, m_and_a, model_release, security, geopolitics,
   github_trending, gaming, fintech, hardware, open_source, other

## Required JSON Output Format
Return a JSON array of your selected stories (up to {top_n}), each with these fields:
[
  {{
    "rank": 1,
    "title": "Story headline",
    "url": "https://...",
    "source": "Source name",
    "type": "rss, twitter, or github (use twitter for X/Twitter sources)",
    "summary": "One sentence why this matters.",
    "category": "category_from_list_above"
  }}
]

Return ONLY the JSON array. No markdown, no commentary, no code fences."""
    return prompt


def call_gemini(prompt, api_key):
    url = f"{GEMINI_URL}?key={api_key}"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": TEMPERATURE,
            "responseMimeType": "application/json",
        }
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    token_est = estimate_tokens(prompt)
    log(f"Sending prompt to Gemini Flash (~{token_est} tokens)")

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8")
            result = json.loads(body)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else "(no body)"
        log(f"API HTTP error {e.code}: {error_body[:500]}")
        return None
    except urllib.error.URLError as e:
        log(f"API connection error: {e.reason}")
        return None
    except Exception as e:
        log(f"API call failed: {e}")
        return None

    try:
        text = result["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        log(f"Unexpected API response structure: {e}")
        return None

    try:
        picks = json.loads(text)
        if isinstance(picks, list):
            return picks
        if isinstance(picks, dict) and "stories" in picks:
            return picks["stories"]
        return None
    except json.JSONDecodeError:
        match = re.search(r'\[\s*\{.*?\}\s*\]', text, re.DOTALL)
        if match:
            try:
                picks = json.loads(match.group())
                if isinstance(picks, list):
                    return picks
            except json.JSONDecodeError:
                pass
        log(f"Could not parse LLM response. First 500 chars: {text[:500]}")
        return None


def call_llm_with_failover(prompt, articles, github_articles, editorial_profile, recent_posts, top_n):
    """
    Try LLM providers in sequence: Gemini Flash -> Gemini Flash Lite -> OpenRouter.
    Each step may reduce candidate count for speed.
    """
    for i, provider in enumerate(FAILOVER_CHAIN):
        api_key = os.environ.get(provider["env_key"])
        if not api_key:
            log("  Skipping %s: %s not set" % (provider["name"], provider["env_key"]))
            continue

        log("Trying %s (model: %s, timeout: %ds)" % (
            provider["name"], provider["model"], provider["timeout"]))

        # For later failovers, reduce candidate list for speed
        current_articles = articles
        current_github = github_articles
        if i >= 1:
            current_articles = articles[:30]
            current_github = github_articles[:5] if github_articles else []

        # Rebuild prompt with current candidates
        current_prompt = build_prompt(
            current_articles, current_github, editorial_profile, recent_posts, top_n
        )

        if provider["api"] == "gemini":
            model_url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "%s:generateContent" % provider["model"]
            )
            picks = _call_gemini_api(current_prompt, api_key, model_url, provider["timeout"])
        elif provider["api"] == "openrouter":
            picks = _call_openrouter_api(current_prompt, api_key, provider["model"], provider["timeout"])
        else:
            continue

        if picks is not None:
            log("  %s returned %d picks" % (provider["name"], len(picks)))
            return picks

        log("  %s failed, trying next..." % provider["name"])

    log("ERROR: All LLM providers failed")
    return None


def _call_gemini_api(prompt, api_key, model_url, timeout):
    """Call a Gemini API model. Returns parsed picks list or None."""
    url = "%s?key=%s" % (model_url, api_key)

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": TEMPERATURE,
            "responseMimeType": "application/json",
        }
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    token_est = estimate_tokens(prompt)
    log("  Sending ~%d tokens to Gemini API" % token_est)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            result = json.loads(body)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else "(no body)"
        log("  Gemini API HTTP error %d: %s" % (e.code, error_body[:500]))
        return None
    except urllib.error.URLError as e:
        log("  Gemini API connection error: %s" % e.reason)
        return None
    except Exception as e:
        log("  Gemini API call failed: %s" % e)
        return None

    try:
        text = result["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        log("  Unexpected Gemini response structure: %s" % e)
        return None

    return _parse_llm_json(text)


def _call_openrouter_api(prompt, api_key, model, timeout):
    """Call OpenRouter API. Returns parsed picks list or None."""
    url = "https://openrouter.ai/api/v1/chat/completions"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": TEMPERATURE,
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % api_key,
        },
        method="POST",
    )

    token_est = estimate_tokens(prompt)
    log("  Sending ~%d tokens to OpenRouter (%s)" % (token_est, model))

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            result = json.loads(body)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else "(no body)"
        log("  OpenRouter HTTP error %d: %s" % (e.code, error_body[:500]))
        return None
    except urllib.error.URLError as e:
        log("  OpenRouter connection error: %s" % e.reason)
        return None
    except Exception as e:
        log("  OpenRouter call failed: %s" % e)
        return None

    try:
        text = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        log("  Unexpected OpenRouter response structure: %s" % e)
        return None

    return _parse_llm_json(text)


def _parse_llm_json(text):
    """Parse LLM response text into a list of picks."""
    try:
        picks = json.loads(text)
        if isinstance(picks, list):
            return picks
        if isinstance(picks, dict) and "stories" in picks:
            return picks["stories"]
        if isinstance(picks, dict):
            # Try to find a list value in the dict
            for v in picks.values():
                if isinstance(v, list):
                    return v
        return None
    except json.JSONDecodeError:
        match = re.search(r'\[\s*\{.*?\}\s*\]', text, re.DOTALL)
        if match:
            try:
                picks = json.loads(match.group())
                if isinstance(picks, list):
                    return picks
            except json.JSONDecodeError:
                pass
        log("  Could not parse LLM response. First 500 chars: %s" % text[:500])
        return None


def validate_picks(picks, top_n):
    validated = []
    for i, pick in enumerate(picks):
        if not isinstance(pick, dict):
            continue
        entry = {
            "rank": pick.get("rank", i + 1),
            "title": pick.get("title", "(no title)"),
            "url": pick.get("url", ""),
            "source": pick.get("source", "unknown"),
            "type": pick.get("type", "rss"),
            "summary": pick.get("summary", ""),
            "category": pick.get("category", "other"),
        }
        if entry["category"] not in VALID_CATEGORIES:
            entry["category"] = "other"
        if entry["type"] not in ("rss", "twitter", "github"):
            entry["type"] = "rss"
        validated.append(entry)

    for i, v in enumerate(validated):
        v["rank"] = i + 1

    if len(validated) != top_n:
        log(f"  Warning: expected {top_n} picks, got {len(validated)}")
    return validated


def log_to_scanner_presented(picks):
    today = datetime.now().strftime("%Y-%m-%d")
    today_header = f"## {today}"
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    try:
        existing = ""
        if SCANNER_PRESENTED.exists():
            existing = SCANNER_PRESENTED.read_text()

        with open(SCANNER_PRESENTED, "a") as f:
            if today_header not in existing:
                f.write(f"\n{today_header}\n\n")
            for pick in picks:
                f.write(f"[{ts}] {pick['title']} | {pick['url']}\n")

        log(f"Logged {len(picks)} picks to scanner_presented.md")
    except Exception as e:
        log(f"Warning: could not log to scanner_presented.md: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="AI Editor — selects top stories using Gemini Flash"
    )
    parser.add_argument("--file", "-f", required=True,
                       help="Path to article candidates file")
    parser.add_argument("--github", "-g",
                       help="Path to GitHub trending repos file")
    parser.add_argument("--dry-run", action="store_true",
                       help="Build prompt and print to stderr, but don't call API")
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        log("WARNING: GEMINI_API_KEY not set (failover providers may still work)")

    top_n = int(os.environ.get("TOP_N", "7"))
    log(f"Configuration: top_n={top_n}, model={GEMINI_MODEL}")

    log(f"Loading articles from {args.file}")
    articles = parse_articles(args.file)
    log(f"  Loaded {len(articles)} candidates")
    if len(articles) > MAX_ARTICLES:
        articles = articles[:MAX_ARTICLES]

    if not articles:
        log("ERROR: No articles found in input file")
        sys.exit(1)

    github_articles = []
    if args.github:
        github_articles = parse_articles(args.github)
        log(f"  Loaded {len(github_articles)} GitHub repos")

    log("Running deterministic URL pre-filter")
    articles = filter_already_posted(articles)
    if github_articles:
        github_articles = filter_already_posted(github_articles)

    total_candidates = len(articles) + len(github_articles)
    if top_n > total_candidates:
        top_n = total_candidates

    log("Loading editorial profile")
    editorial_profile = load_file_safe(EDITORIAL_PROFILE)
    if not editorial_profile:
        editorial_profile = (
            "Select stories about AI, LLMs, tech deals, and security.\n"
            "Prefer breaking news and concrete announcements over opinion."
        )

    log("Loading recent post history for dedup")
    recent_presented = load_file_safe(SCANNER_PRESENTED, tail_lines=60)
    recent_news_log = load_file_safe(NEWS_LOG, tail_lines=150)
    recent_posts = ""
    if recent_presented:
        recent_posts += "### scanner_presented.md (recent)\n" + recent_presented + "\n"
    if recent_news_log:
        recent_posts += "### news_log.md (recent)\n" + recent_news_log + "\n"

    prompt = build_prompt(articles, github_articles, editorial_profile, recent_posts, top_n)
    prompt_tokens = estimate_tokens(prompt)
    log(f"Prompt built: ~{prompt_tokens} estimated tokens")

    if args.dry_run:
        log("DRY RUN — printing prompt to stderr")
        print(prompt, file=sys.stderr)
        return

    picks = call_llm_with_failover(
        prompt, articles, github_articles, editorial_profile, recent_posts, top_n
    )

    if picks is None:
        log("ERROR: All LLM providers failed. No stories to output.")
        return 1

    picks = validate_picks(picks, top_n)

    for pick in picks:
        print(json.dumps(pick, ensure_ascii=False))

    log_to_scanner_presented(picks)

    # Record picks to SQLite dedup database
    if HAS_DEDUP_DB:
        db = DedupDB()
        pick_articles = [{"url": p["url"], "title": p["title"], "source": p.get("source", "")} for p in picks]
        db.record_batch(pick_articles, status="presented")
        log("Recorded %d picks to dedup database" % len(picks))

    log(f"Done. {len(picks)} stories selected.")


if __name__ == "__main__":
    main()
