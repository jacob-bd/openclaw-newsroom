# Changelog

All notable changes to the OpenClaw News Scanner pipeline are documented here.

---

## [v2] — 2026-03-04

### Major: Deduplication Overhaul

**Problem:** Stories were appearing multiple times across scans — the same event reported by different outlets, URLs with different query parameters pointing to the same article, and previously posted stories resurfacing.

**What changed:**

- **New: SQLite dedup database (`dedup_db.py`)** — Persistent cross-scan memory. Every article the pipeline processes is recorded with a normalized URL and title. Before the LLM sees any candidates, the database filters out articles that match a previously seen URL (after normalization) or have a title that is 75%+ similar to a recent article. This replaces the old text-file-only approach that could only match exact URLs.

- **URL normalization** strips query parameters, fragments, `www.` prefixes, and trailing punctuation, then lowercases the domain and normalizes to `https://`. Two URLs that point to the same article but differ only in tracking parameters are now correctly identified as duplicates.

- **Cross-scan title matching** uses `SequenceMatcher` at a 75% threshold over a 2-day window. "Sam Altman tells OpenAI staff decisions are up to government" and "Sam Altman says operational decisions up to US government" are correctly caught as the same story.

- **`quality_score.py` integration** — After within-batch dedup (80% threshold), a new `cross_scan_dedup()` step filters candidates against the SQLite database before they reach the LLM editor.

- **Seeding from history** — Run `python3 dedup_db.py --seed` to import your existing `news_log.md` and `scanner_presented.md` into the database. This gives the dedup system historical context from day one.

### Major: LLM Failover Chain

**Problem:** If the Gemini API was down or timed out, the pipeline fell back to raw scored articles with no editorial curation — often resulting in low-quality or off-topic picks.

**What changed:**

- **3-tier failover chain** in `llm_editor.py`:
  1. **Gemini 3.1 Flash Lite** (primary — cheapest)
  2. **Grok 4.1 Fast via OpenRouter** (different provider — avoids double failure if Google is down)
  3. **Gemini 3 Flash Preview** (last resort)

- The chain intentionally alternates providers. If Google's API fails, the pipeline hits Grok (OpenRouter) on the second try instead of wasting another timeout on a second Google model.

- **Removed raw fallback.** If all 3 LLM providers fail, the pipeline now prints a clean error message and points you to the saved candidates file for a manual re-run — instead of dumping unfiltered articles.

- **New env var: `OPENROUTER_API_KEY`** — Required if you want the Grok failover. Without it, slot 2 is skipped and the chain degrades to Flash Lite → Flash Preview (both Google).

### Major: AI Keyword Pre-Filter

**Problem:** Non-AI articles (sports, energy drinks, phone reviews) were leaking into the candidate pool from RSS feeds, wasting LLM tokens and sometimes slipping through the AI editor's curation.

**What changed:**

- **Inline keyword filter in `news_scan_deduped.sh`** — Applied during RSS extraction, before scoring. Uses word-bounded short keywords (`\bAI\b`, `\bLLM\b`, `\bGPU\b`, etc.) and substring long keywords (`OpenAI`, `Anthropic`, `machine learning`, etc.) to filter articles.

- Word-boundary matching prevents false positives: "foreign affairs" does not match `\bAI\b`, but "new AI system" does.

- Non-AI articles are counted and reported (e.g., "Filtered 57 non-AI articles") but silently dropped from the pipeline.

- The old `filter_ai_news.sh` script still exists for standalone use, but the main pipeline now handles keyword filtering inline.

### Changed: Quality Over Quantity

**Problem:** The editorial rule "Every scan MUST produce at least 5 stories" pressured the LLM to pad results with mediocre or off-topic picks when the candidate pool was thin.

**What changed:**

- **Rule #1 updated** in the editorial profile template and the LLM prompt:
  > Select UP TO 7 stories per scan. Quality matters more than quantity — 3 great picks are better than 7 mediocre ones. Only select stories that genuinely match the editorial focus. It is perfectly fine to return fewer stories when the candidate pool is thin.

- The LLM prompt now says "UP TO N" instead of "EXACTLY N".

### Added: Unit Test Suite

- **New: `test_components.py`** — 68 tests covering all pipeline components:
  - `dedup_db.py`: URL normalization (10 tests), DB operations + bulk check (12 tests)
  - `quality_score.py`: scoring logic (4), within-batch dedup (2), cross-scan dedup (1)
  - `llm_editor.py`: failover chain config (5), validate_picks (5), prompt wording (2), JSON parsing (5), SQLite pre-filter (1)
  - AI keyword filter: AI-relevant titles (10), non-AI titles (8), edge cases (3)

- Run with: `cd scripts && python3 test_components.py`

### Migration Guide (v1 → v2)

1. **Copy the new and updated scripts** to your workspace:
   ```bash
   cp scripts/dedup_db.py ~/.openclaw/workspace/scripts/
   cp scripts/test_components.py ~/.openclaw/workspace/scripts/
   cp scripts/quality_score.py ~/.openclaw/workspace/scripts/
   cp scripts/llm_editor.py ~/.openclaw/workspace/scripts/
   cp scripts/news_scan_deduped.sh ~/.openclaw/workspace/scripts/
   chmod +x ~/.openclaw/workspace/scripts/news_scan_deduped.sh
   ```

2. **Seed the dedup database** from your existing logs:
   ```bash
   cd ~/.openclaw/workspace/scripts
   python3 dedup_db.py --seed
   python3 dedup_db.py --stats
   ```

3. **Set `OPENROUTER_API_KEY`** (optional, for Grok failover):
   Add to your LaunchAgent plist or export in shell.

4. **Update your editorial profile** — replace rule #1 with the new quality-over-quantity wording from `config/editorial_profile_template.md`.

5. **Verify everything works:**
   ```bash
   cd ~/.openclaw/workspace/scripts
   python3 test_components.py          # 68 tests should pass
   ./news_scan_deduped.sh --top 5      # manual pipeline test
   ```

---

## [v1] — 2026-02-28

Initial release. 5-source news scanning pipeline with quality scoring, article enrichment, and Gemini Flash editorial curation.
