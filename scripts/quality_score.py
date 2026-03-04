#!/usr/bin/env python3
"""
Quality scoring pre-filter for the news scan pipeline.

Reads pipe-delimited articles (TITLE|URL|SOURCE or TITLE|URL|SOURCE|TIER),
scores them based on source tier, title quality, freshness signals, and
deduplicates by title similarity.

Outputs the top N articles in the same pipe-delimited format, sorted by score.

Usage:
    python3 quality_score.py --input articles.txt [--max 50] [--dedup-threshold 0.80]
"""

import sys
import re
import argparse
from difflib import SequenceMatcher

try:
    from dedup_db import DedupDB, normalize_url
    HAS_DEDUP_DB = True
except ImportError:
    HAS_DEDUP_DB = False

# ── Source priority scoring ──────────────────────────────────────────
# Higher = better. Customize to match your blogwatcher feed names.
PRIORITY_SOURCES = {
    # T1: Wire services + official AI lab blogs (+5 bonus)
    'Reuters Tech': 5, 'Bloomberg Tech': 5, 'Axios AI': 5, 'CNBC Tech': 5,
    'OpenAI Blog': 5,
    # T2: Tech press + priority bloggers (+3 bonus)
    'TechCrunch AI': 3, 'The Verge': 3, 'THE DECODER': 3, 'VentureBeat AI': 3,
    'Ars Technica': 3, '404 Media': 3, 'Wired AI': 3, 'MIT Tech Review': 3,
    'Google AI Blog': 3, 'Hugging Face Blog': 3, 'Simon Willison': 3,
    'Latent Space': 3, 'Crunchbase News': 3,
    # T3: Aggregators (+1 bonus)
    'Hacker News AI': 1, 'SiliconANGLE AI': 1, 'AI News': 1,
    'Gary Marcus': 1, 'Bens Bites': 1,
    # X/Twitter (+2 — original source, not aggregated)
    'X/Twitter': 2,
}

# High-value keywords that boost score
HIGH_VALUE_KEYWORDS = re.compile(
    r'\b(acqui|merger|billion|partnership|launch|release|'
    r'announce|breakthrough|regulation|ban|security|vulnerability|'
    r'open.source|Pentagon|military|government|antitrust)\b',
    re.IGNORECASE
)

# Signal words for breaking/exclusive news
BREAKING_KEYWORDS = re.compile(
    r'\b(breaking|exclusive|just in|confirmed|leaked|first look|'
    r'officially|unveil|reveal)\b',
    re.IGNORECASE
)


def title_similarity(t1, t2):
    """Fast title similarity using SequenceMatcher."""
    return SequenceMatcher(None, t1.lower(), t2.lower()).ratio()


def compute_score(title, source, tier_str):
    """Compute a quality score for an article."""
    score = 0

    score += PRIORITY_SOURCES.get(source, 0)

    if source.startswith('r/'):
        score += 1

    if source.startswith('GitHub'):
        score += 2

    try:
        tier = int(tier_str) if tier_str else 3
    except ValueError:
        tier = 3
    if tier == 1:
        score += 4
    elif tier == 2:
        score += 2
    elif tier == 3:
        score += 1

    hv_matches = HIGH_VALUE_KEYWORDS.findall(title)
    score += min(len(hv_matches) * 2, 6)

    if BREAKING_KEYWORDS.search(title):
        score += 3

    title_len = len(title)
    if title_len < 30:
        score -= 1
    elif 50 <= title_len <= 150:
        score += 1

    return score


def deduplicate(articles, threshold=0.80):
    """Remove near-duplicate articles by title similarity. Keep highest-scored."""
    unique = []
    for article in articles:
        is_dup = False
        for existing in unique:
            sim = title_similarity(article['title'], existing['title'])
            if sim >= threshold:
                is_dup = True
                if article['score'] > existing['score']:
                    unique.remove(existing)
                    unique.append(article)
                break
        if not is_dup:
            unique.append(article)
    return unique


def cross_scan_dedup(articles):
    """Remove articles already seen in previous scans (via SQLite DB)."""
    if not HAS_DEDUP_DB:
        print("  Warning: dedup_db not available, skipping cross-scan dedup", file=sys.stderr)
        return articles

    db = DedupDB()
    article_dicts = [{"url": a["url"], "title": a["title"]} for a in articles]
    new_dicts, dupe_dicts, url_dupes, title_dupes = db.bulk_check(article_dicts)

    # Build set of new URLs for filtering
    new_urls = set()
    for d in new_dicts:
        new_urls.add(normalize_url(d["url"]))
    filtered = [a for a in articles if normalize_url(a["url"]) in new_urls]

    removed = len(articles) - len(filtered)
    if removed > 0:
        print("  Cross-scan dedup: removed %d (%d URL, %d title matches)" % (removed, url_dupes, title_dupes), file=sys.stderr)

    return filtered


def main():
    parser = argparse.ArgumentParser(description="Quality scoring pre-filter")
    parser.add_argument('--input', '-i', required=True, help='Input pipe-delimited file')
    parser.add_argument('--max', type=int, default=50, help='Max articles to output (default: 50)')
    parser.add_argument('--dedup-threshold', type=float, default=0.80,
                       help='Title similarity threshold for dedup (default: 0.80)')
    args = parser.parse_args()

    articles = []
    try:
        with open(args.input, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split('|')
                if len(parts) < 3:
                    continue
                title = parts[0]
                url = parts[1]
                source = parts[2]
                tier = parts[3] if len(parts) > 3 else ''

                score = compute_score(title, source, tier)
                articles.append({
                    'title': title,
                    'url': url,
                    'source': source,
                    'tier': tier,
                    'score': score,
                    'line': line,
                })
    except FileNotFoundError:
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        return 1

    if not articles:
        print("No articles to score", file=sys.stderr)
        return 0

    articles.sort(key=lambda x: -x['score'])
    unique = deduplicate(articles, args.dedup_threshold)
    unique = cross_scan_dedup(unique)
    unique.sort(key=lambda x: -x['score'])
    output = unique[:args.max]

    for article in output:
        if article['tier']:
            print(f"{article['title']}|{article['url']}|{article['source']}|{article['tier']}")
        else:
            print(f"{article['title']}|{article['url']}|{article['source']}")

    total = len(articles)
    deduped = total - len(unique)
    final = len(output)
    print(f"  Done: {total} in -> {deduped} dupes removed -> {final} out", file=sys.stderr)


if __name__ == "__main__":
    main()
