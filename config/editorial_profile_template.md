# Editorial Profile — [YOUR_CHANNEL_NAME]

> This profile is read by the AI editor (llm_editor.py) on every news scan.
> It captures what you pick, what you skip, and what makes a story worth posting.
> The "Approval History Stats" section is updated automatically by
> update_editorial_profile.py based on your approval/rejection decisions.

## Identity
- Channel: [YOUR_CHANNEL_NAME] on [Platform]
- Editor: [Your Name]
- Voice: [Describe your editorial voice — e.g., "Sharp, concise, no fluff. Breaking news > opinion. Facts > speculation."]

## What You Always Pick (high confidence)
<!-- Stories in these categories almost always get posted. -->
<!-- Examples — customize to YOUR interests: -->
- Major AI company announcements (product launches, acquisitions, partnerships)
- New model/architecture releases (especially novel approaches and benchmarks)
- AI security incidents (hacks, prompt injection, model attacks)
- AI + geopolitics (military applications, government regulation, trade policy)
- Open-source model releases that challenge frontier models
- Major funding rounds and M&A deals over $100M

## What You Usually Pick (medium confidence)
<!-- Sometimes posted, depends on the angle and timing. -->
- NVIDIA and chip industry news (when market-moving)
- Google/Apple AI product launches
- Creative AI tools (image/video/music generation)
- Major partnerships between tech companies
- Original research reports with novel data points

## What You Usually Skip (anti-patterns)
<!-- The LLM editor will learn to avoid these over time. -->
- Enterprise SaaS funding rounds under $50M
- Generic "AI will change everything" opinion pieces
- Routine product updates without a unique angle
- Earnings reports (unless market-moving)
- Crypto/blockchain/NFT/web3 crossover stories
- Routine job market news (hiring, small layoffs)
- Conference/event announcements
- Podcast/interview promotions

## Emerging Interests (watch for these)
<!-- Topics you're starting to pay attention to. -->
- GitHub repos gaining rapid traction (star velocity signals)
- Tools/frameworks before they become mainstream
- AI regulation and policy shifts
- [Add your emerging interests here]

## Source Trust Ranking
<!-- Higher-tier sources get priority in scoring. -->
<!-- Customize to match YOUR blogwatcher feed names. -->
- Tier 1 (Wire): Bloomberg, Reuters, CNBC, Axios, Politico
- Tier 2 (Tech Press): TechCrunch, The Verge, Ars Technica, Wired, The Decoder, 404 Media
- Tier 3 (Aggregator): VentureBeat, SiliconANGLE, 9to5Google, Crunchbase News
- Tier 4 (Community): Reddit (r/singularity, r/ClaudeAI, r/LocalLLaMA), Hacker News
- Tier 5 (Primary): Company blogs, GitHub repos, research papers

## Story Selection Rules
1. Select UP TO 7 stories per scan. Quality matters more than quantity — 3 great picks are better than 7 mediocre ones. Only select stories that genuinely match the editorial focus. It is perfectly fine to return fewer stories when the candidate pool is thin.
2. No exact duplicates of previously posted stories
3. Same event from different angles = OK if the angle is genuinely new
4. Prefer concrete news (X acquired Y, X launched Z) over speculation
5. Prefer exclusives and scoops over recycled takes
6. Max 2 stories from the same source per scan (diversity)
7. Include 1 sentence summary explaining WHY this story matters
8. If a GitHub repo is trending AND relevant, include it

## Approval History Stats
<!-- This section is auto-populated by update_editorial_profile.py -->
<!-- It analyzes your approval/rejection patterns and updates nightly -->
- No decisions logged yet.
- Tracking begins when you approve/skip stories in editorial_decisions.md.

## Scanner Blind Spots
<!-- Auto-detected: topics you manually seek out but the scanner misses -->
<!-- Populated by update_editorial_profile.py after enough decisions -->
