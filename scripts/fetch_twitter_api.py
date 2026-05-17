#!/usr/bin/env python3
"""
Fetch AI tweets via an API supplement to bird CLI.

Uses a provider search endpoint for keyword-based Twitter searches. This
catches breaking news that might be missed by the bird CLI's account-based
approach.

Output: pipe-delimited TITLE|URL|SOURCE format.
Sources tagged with "(tweet)" when no external article URL exists.

Usage:
    python3 fetch_twitter_api.py [--max-queries 3] [--provider auto]

Environment:
    TWITTERAPI_IO_KEY - used by the default twitterapi.io path
    XQUIK_API_KEY - used when --provider xquik is selected
"""

import json
import os
import re
import sys
import ssl
import argparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError

_SSL_CTX = ssl.create_default_context()

TIMEOUT = 15
TWITTERAPI_BASE = "https://api.twitterapi.io/twitter"
XQUIK_API_BASE = "https://xquik.com/api/v1"
XQUIK_CONTRACT_DATE = "2026-04-29"
PROVIDER_AUTO = "auto"
PROVIDER_TWITTERAPI = "twitterapi"
PROVIDER_XQUIK = "xquik"
PROVIDER_CHOICES = (PROVIDER_AUTO, PROVIDER_TWITTERAPI, PROVIDER_XQUIK)

# Search queries - focused on breaking AI news
SEARCH_QUERIES = [
    '"breaking" (AI OR "artificial intelligence" OR LLM) -is:retweet lang:en',
    '(Anthropic OR Claude OR OpenAI) (announce OR launch OR release) -is:retweet lang:en',
    '(AI OR "artificial intelligence") (acquisition OR merger OR billion OR deal) -is:retweet lang:en',
]

# Minimum engagement to filter noise
MIN_LIKES = 50
MIN_FOLLOWERS = 5000


def resolve_provider(provider_choice, env=os.environ):
    """Resolve an explicit or automatic provider choice to a provider and key."""
    if provider_choice == PROVIDER_XQUIK:
        return PROVIDER_XQUIK, env.get("XQUIK_API_KEY", "")
    if provider_choice == PROVIDER_TWITTERAPI:
        return PROVIDER_TWITTERAPI, env.get("TWITTERAPI_IO_KEY", "")

    twitterapi_key = env.get("TWITTERAPI_IO_KEY", "")
    if twitterapi_key:
        return PROVIDER_TWITTERAPI, twitterapi_key
    return PROVIDER_XQUIK, env.get("XQUIK_API_KEY", "")


def search_twitter(query, api_key, provider=PROVIDER_TWITTERAPI, max_results=10):
    """Search tweets via the selected provider."""
    if provider == PROVIDER_XQUIK:
        return search_xquik(query, api_key, max_results=max_results)
    return search_twitterapi(query, api_key, max_results=max_results)


def search_twitterapi(query, api_key, max_results=10):
    """Search tweets via twitterapi.io."""
    from urllib.parse import urlencode

    params = urlencode({
        "query": query,
        "queryType": "Latest",
    })
    url = f"{TWITTERAPI_BASE}/tweet/advanced_search?{params}"

    req = Request(url, headers={
        "X-API-Key": api_key,
        "User-Agent": "NewsScanner/1.0",
    })

    try:
        with urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data.get("tweets", [])
    except HTTPError as e:
        if e.code == 401:
            print("  Error: Invalid TWITTERAPI_IO_KEY", file=sys.stderr)
        elif e.code == 429:
            print("  Warning: twitterapi.io rate limit", file=sys.stderr)
        else:
            print(f"  Warning: twitterapi.io HTTP {e.code}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"  Warning: twitterapi.io error: {e}", file=sys.stderr)
        return []


def search_xquik(query, api_key, max_results=10):
    """Search tweets via Xquik."""
    from urllib.parse import urlencode

    params = urlencode({
        "q": query,
        "queryType": "Latest",
        "limit": max_results,
    })
    url = f"{XQUIK_API_BASE}/x/tweets/search?{params}"

    req = Request(url, headers={
        "x-api-key": api_key,
        "xquik-api-contract": XQUIK_CONTRACT_DATE,
        "User-Agent": "NewsScanner/1.0",
    })

    try:
        with urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data.get("tweets", [])
    except HTTPError as e:
        if e.code == 401:
            print("  Error: Invalid XQUIK_API_KEY", file=sys.stderr)
        elif e.code == 402:
            print("  Warning: Xquik API access required", file=sys.stderr)
        elif e.code == 429:
            print("  Warning: Xquik rate limit", file=sys.stderr)
        else:
            print(f"  Warning: Xquik HTTP {e.code}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"  Warning: Xquik error: {e}", file=sys.stderr)
        return []


def _external_url_from_entity(url_entity):
    expanded = (
        url_entity.get("expanded_url")
        or url_entity.get("expandedUrl")
        or url_entity.get("url", "")
    )
    if (
        expanded
        and "twitter.com" not in expanded
        and "t.co" not in expanded
        and "x.com" not in expanded
    ):
        return expanded
    return ""


def _author_screen_name(author):
    return (
        author.get("userName")
        or author.get("username")
        or author.get("screenName")
        or ""
    )


def extract_url_from_tweet(tweet):
    """
    Extract the first external URL from a tweet.
    Returns (url, is_tweet_only):
      - (external_url, False) if an article URL was found in entities
      - (tweet_url, True) if only the tweet's own URL is available
      - ("", True) if no URL could be constructed
    """
    entities = tweet.get("entities", {})
    urls = entities.get("urls", [])
    for u in urls:
        expanded = _external_url_from_entity(u)
        if expanded:
            return expanded, False

    author = tweet.get("author", {})
    screen_name = _author_screen_name(author)
    tweet_id = tweet.get("id", "")
    if screen_name and tweet_id:
        return f"https://x.com/{screen_name}/status/{tweet_id}", True
    return "", True


def main():
    parser = argparse.ArgumentParser(description="Fetch AI tweets via an API provider")
    parser.add_argument('--max-queries', type=int, default=3,
                       help='Max queries to run (default: 3)')
    parser.add_argument('--provider', choices=PROVIDER_CHOICES,
                       default=PROVIDER_AUTO,
                       help='Tweet search provider (default: auto)')
    args = parser.parse_args()

    provider, api_key = resolve_provider(args.provider)
    if not api_key:
        print("  Warning: TWITTERAPI_IO_KEY or XQUIK_API_KEY not set, skipping",
              file=sys.stderr)
        return 0

    seen_urls = set()
    all_results = []

    queries = SEARCH_QUERIES[:args.max_queries]

    for query in queries:
        tweets = search_twitter(query, api_key, provider=provider)
        for tweet in tweets:
            likes = tweet.get("likeCount", 0)
            author = tweet.get("author", {})
            followers = author.get("followers", 0)

            if likes < MIN_LIKES and followers < MIN_FOLLOWERS:
                continue

            text = tweet.get("text", "").strip()
            if not text:
                continue

            title = re.sub(r'https?://\S+', '', text).strip()
            title = title.replace('\n', ' ').replace('|', ' -')
            title = re.sub(r'\s+', ' ', title)

            if len(title) > 200:
                title = title[:197] + "..."
            if len(title) < 15:
                continue

            url, is_tweet_only = extract_url_from_tweet(tweet)
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            screen_name = _author_screen_name(author) or "unknown"
            source_tag = f"X/@{screen_name} (tweet)" if is_tweet_only else f"X/@{screen_name}"
            all_results.append(f"{title}|{url}|{source_tag}")

    for line in all_results:
        print(line)

    print(
        f"  Done: {len(all_results)} tweets from {len(queries)} queries via {provider}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
