#!/usr/bin/env python3
"""Unit tests for all pipeline components."""
import sys
import os
import tempfile
import json
import re

PASS = 0
FAIL = 0


def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  PASS: %s" % name)
    else:
        FAIL += 1
        print("  FAIL: %s -- %s" % (name, detail))


# ==============================================================
print("=" * 60)
print("COMPONENT 1: dedup_db.py")
print("=" * 60)

from dedup_db import DedupDB, normalize_url

# --- URL normalization ---
print("\n[URL Normalization]")

test("Strip query params",
     normalize_url("https://bloomberg.com/article?accessToken=abc&ref=123") == "https://bloomberg.com/article")
test("Strip fragment",
     normalize_url("https://example.com/page#section") == "https://example.com/page")
test("Remove www prefix",
     normalize_url("https://www.cnbc.com/story") == "https://cnbc.com/story")
test("Normalize http to https",
     normalize_url("http://example.com/path") == "https://example.com/path")
test("Remove trailing slash",
     normalize_url("https://example.com/path/") == "https://example.com/path")
test("Keep root slash",
     normalize_url("https://example.com/") == "https://example.com/")
test("Lowercase domain only",
     normalize_url("https://WWW.Example.COM/CasePath") == "https://example.com/CasePath")
test("Strip trailing punctuation",
     normalize_url("https://example.com/article.,;:)") == "https://example.com/article")
test("Empty URL returns empty",
     normalize_url("") == "")
test("Same article different params normalize equal",
     normalize_url("https://bloomberg.com/news?tok=aaa") == normalize_url("https://bloomberg.com/news?tok=bbb"))

# --- DB + Bulk Check ---
print("\n[DB Operations + Bulk Check]")

db_path = os.path.join(tempfile.gettempdir(), "test_unit.db")
if os.path.exists(db_path):
    os.remove(db_path)
db = DedupDB(db_path=db_path)

db.record("https://example.com/story1?utm=abc",
          "Sam Altman tells OpenAI staff operational decisions up to government", "CNBC")
db.record("https://bloomberg.com/anthropic-20b",
          "Anthropic nears 20 billion revenue run rate", "Bloomberg")

test("is_seen exact URL", db.is_seen("https://example.com/story1?utm=abc"))
test("is_seen normalized (diff params)", db.is_seen("https://example.com/story1?utm=xyz"))
test("is_seen with www prefix", db.is_seen("https://www.example.com/story1"))
test("is_seen negative", not db.is_seen("https://example.com/totally-new"))

articles = [
    {"url": "https://example.com/story1?ref=reddit", "title": "OpenAI news"},
    {"url": "https://yahoo.com/sam-altman", "title": "Sam Altman tells OpenAI staff decisions are up to US government"},
    {"url": "https://new.com/fresh", "title": "Google releases brand new Gemini model"},
    {"url": "https://another.com/new", "title": "NVIDIA announces next-gen chip architecture"},
]
new, dupes, url_d, title_d = db.bulk_check(articles)
test("Bulk: 2 new articles", len(new) == 2, "got %d new" % len(new))
test("Bulk: 2 dupes caught", len(dupes) == 2, "got %d dupes" % len(dupes))
test("Bulk: 1 URL dupe", url_d == 1, "got %d" % url_d)
test("Bulk: 1 title dupe", title_d == 1, "got %d" % title_d)

# Record batch
batch = [
    {"url": "https://batch.com/a", "title": "Batch A", "source": "Src"},
    {"url": "https://batch.com/b", "title": "Batch B", "source": "Src"},
]
db.record_batch(batch, status="scored")
test("Batch A recorded", db.is_seen("https://batch.com/a"))
test("Batch B recorded", db.is_seen("https://batch.com/b"))

# Stats
s = db.stats()
test("Stats total > 0", s["total"] > 0, "total=%d" % s["total"])
test("Stats keys present", all(k in s for k in ["total", "presented", "published", "today"]))

os.remove(db_path)

# ==============================================================
print("\n" + "=" * 60)
print("COMPONENT 2: quality_score.py")
print("=" * 60)

from quality_score import compute_score, deduplicate, title_similarity

print("\n[Scoring Logic]")

score_t1 = compute_score("OpenAI launches GPT-6", "Reuters Tech", "1")
score_t3 = compute_score("OpenAI launches GPT-6", "AI News", "3")
test("T1 source scores higher than T3", score_t1 > score_t3,
     "T1=%d, T3=%d" % (score_t1, score_t3))

score_breaking = compute_score("BREAKING: OpenAI acquires startup", "TechCrunch AI", "2")
score_normal = compute_score("OpenAI discusses future plans", "TechCrunch AI", "2")
test("Breaking news scores higher", score_breaking > score_normal,
     "breaking=%d, normal=%d" % (score_breaking, score_normal))

score_hv = compute_score("Microsoft acquisition of AI company for billion dollars", "Reuters Tech", "1")
score_no_hv = compute_score("Company releases quarterly earnings report", "Reuters Tech", "1")
test("High-value keywords boost score", score_hv > score_no_hv,
     "hv=%d, no_hv=%d" % (score_hv, score_no_hv))

score_short = compute_score("AI news", "AI News", "3")
score_good = compute_score("OpenAI launches revolutionary new language model for developers", "AI News", "3")
test("Short title penalized", score_short < score_good,
     "short=%d, good=%d" % (score_short, score_good))

print("\n[Within-Batch Dedup]")

articles = [
    {"title": "OpenAI launches GPT-6", "score": 10},
    {"title": "OpenAI launches GPT-6 model", "score": 8},
    {"title": "NVIDIA announces new GPU", "score": 7},
]
unique = deduplicate(articles, threshold=0.80)
test("Within-batch dedup removes near-dup", len(unique) == 2,
     "got %d (expected 2)" % len(unique))
test("Higher-scored dupe kept", any(a["score"] == 10 for a in unique))

print("\n[Cross-Scan Dedup]")
try:
    from quality_score import cross_scan_dedup
    test("cross_scan_dedup function exists", True)
except ImportError:
    test("cross_scan_dedup function exists", False, "not found")

# ==============================================================
print("\n" + "=" * 60)
print("COMPONENT 3: llm_editor.py")
print("=" * 60)

import llm_editor

print("\n[Failover Chain Config]")
chain = llm_editor.FAILOVER_CHAIN
test("3 providers in chain", len(chain) == 3, "got %d" % len(chain))
test("Primary is Flash Lite", "Flash Lite" in chain[0]["name"],
     "got %s" % chain[0]["name"])
test("Second is Grok/OpenRouter",
     "Grok" in chain[1]["name"] or "OpenRouter" in chain[1]["name"],
     "got %s" % chain[1]["name"])
test("Third is Flash Preview", "Flash Preview" in chain[2]["name"],
     "got %s" % chain[2]["name"])
test("Slots 1-2 use different providers",
     chain[0]["env_key"] != chain[1]["env_key"],
     "both use %s" % chain[0]["env_key"])

print("\n[Validate Picks]")
raw_picks = [
    {"rank": 1, "title": "Story A", "url": "https://a.com", "source": "Src",
     "type": "rss", "summary": "Good", "category": "ai_product"},
    {"rank": 2, "title": "Story B", "url": "https://b.com", "source": "Src",
     "type": "invalid_type", "summary": "OK", "category": "bad_category"},
    {"rank": 3, "title": "Story C", "url": "https://c.com"},
]
validated = llm_editor.validate_picks(raw_picks, 3)
test("Validate: 3 picks returned", len(validated) == 3, "got %d" % len(validated))
test("Validate: invalid type fixed to rss", validated[1]["type"] == "rss",
     "got %s" % validated[1]["type"])
test("Validate: invalid category fixed to other", validated[1]["category"] == "other",
     "got %s" % validated[1]["category"])
test("Validate: missing fields filled", validated[2]["source"] == "unknown")
test("Validate: ranks renumbered 1-3",
     [v["rank"] for v in validated] == [1, 2, 3])

print("\n[Prompt Wording]")
prompt = llm_editor.build_prompt(
    [{"title": "Test", "url": "https://x.com", "source": "S"}],
    [], "Editorial profile", "Recent posts", 5
)
test("Prompt says UP TO (not EXACTLY)",
     "UP TO 5" in prompt and "EXACTLY" not in prompt,
     "still says EXACTLY" if "EXACTLY" in prompt else "UP TO not found")
test("Prompt mentions quality", "quality" in prompt.lower())

print("\n[Parse LLM JSON]")
test("Parse valid array",
     llm_editor._parse_llm_json('[{"rank":1}]') == [{"rank": 1}])
test("Parse dict with stories key",
     llm_editor._parse_llm_json('{"stories":[{"rank":1}]}') == [{"rank": 1}])
test("Parse dict with arbitrary list value",
     llm_editor._parse_llm_json('{"results":[{"rank":1}]}') == [{"rank": 1}])
test("Parse with markdown fences",
     llm_editor._parse_llm_json('```json\n[{"rank":1}]\n```') == [{"rank": 1}])
test("Parse garbage returns None",
     llm_editor._parse_llm_json("this is not json at all") is None)

print("\n[SQLite Pre-Filter]")
test("HAS_DEDUP_DB is True", llm_editor.HAS_DEDUP_DB is True)

# ==============================================================
print("\n" + "=" * 60)
print("COMPONENT 4: AI Keyword Filter (from news_scan_deduped.sh)")
print("=" * 60)

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


def is_ai(title):
    return bool(SHORT_KW.search(title) or LONG_KW.search(title))


print("\n[Should PASS filter (AI-relevant)]")
ai_titles = [
    "OpenAI launches GPT-6 with breakthrough reasoning",
    "NVIDIA announces next-gen AI chip",
    "New LLM benchmark shows surprising results",
    "Anthropic raises 10 billion in funding round",
    "Google releases Gemini 4 multimodal model",
    "Meta AI open-sources Llama 5",
    "DeepSeek releases new reasoning model",
    "autonomous driving AI reaches level 4",
    "GPU shortage impacts cloud AI providers",
    "New open source transformer beats proprietary models",
]
for t in ai_titles:
    test("AI: %s" % t[:50], is_ai(t))

print("\n[Should FAIL filter (non-AI)]")
non_ai_titles = [
    "Best energy drinks for gamers in 2026",
    "Messi stadium deal falls through",
    "Samsung Galaxy S27 review - best phone yet",
    "Netflix earnings beat expectations",
    "Housing market trends in March 2026",
    "Top 10 hiking trails in Colorado",
    "Bitcoin reaches new all-time high",
    "Best wireless speakers under 200 dollars",
]
for t in non_ai_titles:
    test("Non-AI: %s" % t[:50], not is_ai(t))

print("\n[Edge Cases]")
test("AI in middle of word (affairs) - should NOT match",
     not is_ai("Foreign affairs committee meets"))
test("AI as standalone word - should match",
     is_ai("New AI system detected underwater mines"))
test("API standalone - should match",
     is_ai("REST API design best practices for developers"))

# ==============================================================
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
print("Total: %d passed, %d failed" % (PASS, FAIL))
if FAIL > 0:
    print("*** FAILURES DETECTED ***")
    sys.exit(1)
else:
    print("All tests passed!")
