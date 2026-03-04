#!/usr/bin/env python3
"""
dedup_db.py — SQLite-backed dedup database for the news scan pipeline.

Shared by quality_score.py and llm_editor.py. Stores normalized URLs
and titles from every scan to prevent cross-scan duplicates.

Database: ~/.openclaw/workspace/memory/news_dedup.db

Usage as module:
    from dedup_db import DedupDB
    db = DedupDB()
    if db.is_seen(url):
        print("duplicate!")
    db.record(url, title, source, status="presented")

Usage as CLI (seed from logs):
    python3 dedup_db.py --seed
    python3 dedup_db.py --stats
    python3 dedup_db.py --check-url "https://example.com/article"
"""

import os
import re
import sqlite3
import sys
import argparse
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Set
from urllib.parse import urlparse, urlunparse

# ── Paths ────────────────────────────────────────────────────────────
WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE",
                                os.path.expanduser("~/.openclaw/workspace")))
DB_PATH = WORKSPACE / "memory" / "news_dedup.db"
NEWS_LOG = WORKSPACE / "memory" / "news_log.md"
SCANNER_PRESENTED = WORKSPACE / "memory" / "scanner_presented.md"


# ── URL normalization ────────────────────────────────────────────────

def normalize_url(url):
    """
    Normalize a URL for dedup comparison:
    - Strip query parameters and fragments
    - Remove www. prefix
    - Normalize to https://
    - Remove trailing slashes
    - Lowercase domain
    """
    if not url:
        return ""

    url = url.strip().rstrip(".,;:)")

    try:
        parsed = urlparse(url)
    except Exception:
        return url.lower()

    # Normalize scheme to https
    scheme = "https"

    # Lowercase and strip www from domain
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    # Keep path, strip trailing slash (but keep "/" for root)
    path = parsed.path.rstrip("/") if parsed.path != "/" else "/"

    # Drop query params and fragment entirely
    normalized = urlunparse((scheme, netloc, path, "", "", ""))

    return normalized


# ── Database class ───────────────────────────────────────────────────

class DedupDB:
    """SQLite-backed dedup database."""

    def __init__(self, db_path=None):
        # type: (Optional[str]) -> None
        self.db_path = db_path or str(DB_PATH)
        self._ensure_db()

    def _ensure_db(self):
        """Create database and tables if they don't exist."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url_normalized TEXT NOT NULL,
                url_original TEXT NOT NULL,
                title TEXT NOT NULL,
                source TEXT DEFAULT '',
                status TEXT DEFAULT 'presented',
                first_seen TEXT NOT NULL,
                scan_id TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_url_norm
            ON seen_articles(url_normalized)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_first_seen
            ON seen_articles(first_seen)
        """)
        conn.commit()
        conn.close()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def is_seen(self, url):
        """Check if a normalized URL already exists in the database."""
        norm = normalize_url(url)
        if not norm:
            return False
        conn = self._connect()
        cursor = conn.execute(
            "SELECT 1 FROM seen_articles WHERE url_normalized = ? LIMIT 1",
            (norm,)
        )
        found = cursor.fetchone() is not None
        conn.close()
        return found

    def find_similar_titles(self, title, threshold=0.75, days=7):
        """
        Find titles in the DB similar to the given title.
        Only checks articles from the last N days for performance.
        Returns list of (db_title, similarity_score, url_normalized).
        """
        if not title:
            return []

        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        conn = self._connect()
        cursor = conn.execute(
            "SELECT title, url_normalized FROM seen_articles WHERE first_seen > ?",
            (cutoff,)
        )
        rows = cursor.fetchall()
        conn.close()

        matches = []
        title_lower = title.lower()
        for db_title, db_url in rows:
            sim = SequenceMatcher(None, title_lower, db_title.lower()).ratio()
            if sim >= threshold:
                matches.append((db_title, sim, db_url))

        matches.sort(key=lambda x: -x[1])
        return matches

    def record(self, url, title, source="", status="presented", scan_id=""):
        """Record an article in the database."""
        norm = normalize_url(url)
        if not norm:
            return
        now = datetime.now().isoformat()
        conn = self._connect()
        conn.execute(
            """INSERT INTO seen_articles
               (url_normalized, url_original, title, source, status, first_seen, scan_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (norm, url, title, source, status, now, scan_id)
        )
        conn.commit()
        conn.close()

    def bulk_check(self, articles):
        """
        Check a list of article dicts against the database.
        Returns (new_articles, duplicate_articles, url_dupe_count, title_dupe_count).

        Each article dict must have 'url' and 'title' keys.
        Checks both URL match and title similarity (>75% over last 2 days).
        """
        new = []
        dupes = []
        url_dupes = 0
        title_dupes = 0

        for article in articles:
            url = article.get("url", "")
            title = article.get("title", "")

            # Check URL first (fast)
            if self.is_seen(url):
                url_dupes += 1
                dupes.append(article)
                continue

            # Check title similarity (slower, only last 2 days for speed)
            similar = self.find_similar_titles(title, threshold=0.75, days=2)
            if similar:
                title_dupes += 1
                dupes.append(article)
                continue

            new.append(article)

        return new, dupes, url_dupes, title_dupes

    def record_batch(self, articles, status="presented", scan_id=""):
        """Record multiple articles in a single transaction."""
        if not articles:
            return
        now = datetime.now().isoformat()
        conn = self._connect()
        for a in articles:
            url = a.get("url", "")
            title = a.get("title", "")
            source = a.get("source", "")
            norm = normalize_url(url)
            if norm:
                conn.execute(
                    """INSERT INTO seen_articles
                       (url_normalized, url_original, title, source, status, first_seen, scan_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (norm, url, title, source, status, now, scan_id)
                )
        conn.commit()
        conn.close()

    def stats(self):
        """Return database statistics."""
        conn = self._connect()
        total = conn.execute("SELECT COUNT(*) FROM seen_articles").fetchone()[0]
        presented = conn.execute(
            "SELECT COUNT(*) FROM seen_articles WHERE status='presented'"
        ).fetchone()[0]
        published = conn.execute(
            "SELECT COUNT(*) FROM seen_articles WHERE status='published'"
        ).fetchone()[0]
        today = datetime.now().strftime("%Y-%m-%d")
        today_count = conn.execute(
            "SELECT COUNT(*) FROM seen_articles WHERE first_seen LIKE ?",
            (today + "%",)
        ).fetchone()[0]
        conn.close()
        return {
            "total": total,
            "presented": presented,
            "published": published,
            "today": today_count,
        }

    def seed_from_logs(self, news_log_path=None, scanner_presented_path=None):
        """
        One-time import: parse existing news_log.md and scanner_presented.md
        to populate the database with historical URLs and titles.
        """
        news_log = news_log_path or str(NEWS_LOG)
        scanner = scanner_presented_path or str(SCANNER_PRESENTED)
        imported = 0

        # Parse news_log.md
        # Format: DATE | POSTED | TITLE | msg_id:NNN | t.me_url | article_url
        try:
            with open(news_log, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split("|")
                    if len(parts) >= 6:
                        title = parts[2].strip()
                        article_url = parts[5].strip()
                        if article_url and not article_url.startswith("http"):
                            continue
                        if title and article_url:
                            if not self.is_seen(article_url):
                                self.record(article_url, title, status="published")
                                imported += 1
        except FileNotFoundError:
            pass

        # Parse scanner_presented.md
        # Format: [TIMESTAMP] TITLE | URL
        url_pattern = re.compile(r'https?://[^\s|>\]\)"\']+')
        try:
            with open(scanner, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    # Extract timestamp, title, URL
                    m = re.match(r'\[([^\]]+)\]\s*(.+)', line)
                    if not m:
                        continue
                    rest = m.group(2)
                    parts = rest.split("|")
                    title = parts[0].strip() if parts else ""
                    url = parts[1].strip() if len(parts) > 1 else ""
                    if not url:
                        urls = url_pattern.findall(rest)
                        url = urls[0] if urls else ""
                    url = url.rstrip(".,;:)")
                    if title and url and url.startswith("http"):
                        if "t.me/" in url:
                            continue
                        if not self.is_seen(url):
                            self.record(url, title, status="presented")
                            imported += 1
        except FileNotFoundError:
            pass

        return imported


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="News dedup database utility")
    parser.add_argument("--seed", action="store_true",
                       help="Seed DB from news_log.md and scanner_presented.md")
    parser.add_argument("--stats", action="store_true",
                       help="Show database statistics")
    parser.add_argument("--check-url",
                       help="Check if a URL has been seen")
    parser.add_argument("--check-title",
                       help="Find similar titles in the DB")
    args = parser.parse_args()

    db = DedupDB()

    if args.seed:
        count = db.seed_from_logs()
        print("Seeded %d articles from existing logs" % count)
        s = db.stats()
        print("DB stats: %d total, %d published, %d presented" % (
            s["total"], s["published"], s["presented"]))

    elif args.stats:
        s = db.stats()
        print("Total: %d" % s["total"])
        print("  Published: %d" % s["published"])
        print("  Presented: %d" % s["presented"])
        print("  Today: %d" % s["today"])

    elif args.check_url:
        norm = normalize_url(args.check_url)
        seen = db.is_seen(args.check_url)
        print("URL: %s" % args.check_url)
        print("Normalized: %s" % norm)
        print("Seen: %s" % seen)

    elif args.check_title:
        matches = db.find_similar_titles(args.check_title)
        if matches:
            print("Found %d similar titles:" % len(matches))
            for title, sim, url in matches[:5]:
                print("  %.0f%% | %s | %s" % (sim * 100, title[:80], url))
        else:
            print("No similar titles found")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
