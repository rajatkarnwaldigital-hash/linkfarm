# link-farm-outreach

Identifies companies buying backlinks from known link farms and generates personalised cold email hooks referencing the specific farms found. Part of the EthicalSEO outbound engine.

---

## What it does

Takes a pre-cleaned list of domains extracted from link farm outgoing link exports (sourced from Ahrefs) and runs them through a 2-stage pipeline:

1. **Qualify** — filters down to mid-market B2B/SaaS companies worth contacting (active site, English, Authority Score 10–60, Claude ICP check). Bot-blocked sites are kept — a site blocking automated requests is provably active and is still your ICP.
2. **Generate hooks** — for each qualified domain, writes a 2-sentence personalised cold email opening that names the specific link farms found, the number of farms, and the ranking risk — using Claude Haiku

The signal: if a company's domain appears in the outgoing links of a known link farm, they bought those links. That's a concrete, verifiable proof point that writes the outreach for you.

---

## Input preparation

`link_farm_qualify.py` expects a CSV called `link_farm_domains.csv` with one row per domain. This file is produced by exporting outgoing domain reports from Ahrefs for each known link farm, merging them, and removing non-website rows.

**Required columns:**

| Column | Required | Notes |
|--------|----------|-------|
| `domain` | Yes | Root domain only, no https/www (e.g. `acmesoftware.com`) |
| `tier` | Yes | `hot` = domain appears across 2+ farms, `warm` = single farm |
| `farm_count` | Yes | Number of distinct link farms linking to this domain |
| `total_pages` | Yes | Total number of link farm pages linking to this domain |
| `farms` | Yes | Comma-separated list of farm domains (e.g. `ranktracker.com,guestpost.io`) |
| `farm_detail` | Yes | Pipe-separated list with page counts per farm (e.g. `ranktracker.com(12 pages) \| guestpost.io(4 pages)`) |

**How to prepare the input from Ahrefs:**

1. For each known link farm domain, open it in Ahrefs → Outgoing Links → Linked Domains → export as CSV
2. Merge all exports into a single file
3. Remove rows where the domain is a social media platform, podcast host, app store, video platform, or any non-website URL (Facebook, Spotify, YouTube, LinkedIn, Apple Podcasts, etc.)
4. Add `tier`, `farm_count`, `total_pages`, `farms`, and `farm_detail` columns based on how many farms link to each domain
5. Save as `link_farm_domains.csv`

**Example input row:**
```
domain,tier,farm_count,total_pages,farms,farm_detail
acmesoftware.com,hot,3,27,ranktracker.com,guestpost.io,linkbuilder.net,ranktracker.com(14 pages) | guestpost.io(9 pages) | linkbuilder.net(4 pages)
```

---

## Scripts

### 1. `link_farm_qualify.py`

Filters the domain list down to qualified prospects worth contacting.

**Input:** `link_farm_domains.csv`

**Output:** `link_farm_qualified.csv`

Output columns:
| Column | Description |
|--------|-------------|
| `domain` | Domain |
| `tier` | `hot` or `warm` — passed through from input |
| `farm_count` | Number of farms — passed through |
| `total_pages` | Total link farm pages — passed through |
| `farms` | Farm list — passed through |
| `farm_detail` | Farm detail — passed through |
| `site_alive` | `True` if site responded or is bot-blocked |
| `is_english` | `True` if English detected; `True` assumed for bot-blocked sites |
| `authority_score` | SEMrush Authority Score |
| `is_good_prospect` | `True` if Claude Haiku confirmed mid-market B2B/SaaS ICP |
| `qualified` | `True` if all filters passed |
| `fail_reason` | Why the domain was filtered out (if applicable) |

**Filters applied (in order):**
1. Site alive check — tries 4 URL variants (https/http × www/non-www), HEAD then GET fallback. **Bot-blocked sites (403) are kept and passed through** — they are provably active
2. Language detection — reads `<html lang>` attribute. Skipped for bot-blocked sites (no response body); English assumed
3. Authority Score 10–60 band (SEMrush `backlinks_overview`). Below 10 = too small/spammy; above 60 = major brand, not a fit
4. ICP check — Claude Haiku YES/NO: is this a real mid-market B2B company or SaaS product that would care about SEO rankings? Rejects major brands, news/media sites, directories, affiliates, non-profits, and social platforms

**SEMrush cost:** ~10 units per domain (single `backlinks_overview` call)

**Performance:** 10 parallel workers via `ThreadPoolExecutor`. Checkpoints every 100 rows to `link_farm_qualified.csv.checkpoint` — safe to interrupt and resume.

---

### 2. `link_farm_hook_generator.py`

Generates a 2-sentence personalised cold email hook for each qualified domain.

**Input:** `link_farm_qualified.csv` — all rows, no filter on `qualified` column applied internally; feed it only qualified rows (the output of `link_farm_qualify.py` already contains only qualified domains if you filter before passing, or pass the full file and it will process all rows)

> **Tip:** To process only qualified domains, filter `link_farm_qualified.csv` to rows where `qualified == True` before running, or pass the full file — the script processes every row it receives.

**Output:** `link_farm_hooks.csv`

Output columns:
| Column | Description |
|--------|-------------|
| `domain` | Domain |
| `tier` | `hot` or `warm` |
| `farm_count` | Number of distinct link farms |
| `total_pages` | Total link farm pages linking to this domain |
| `farms` | Farm list |
| `farm_detail` | Farm detail |
| `authority_score` | Passed through from qualify output |
| `hook` | 2-sentence personalised cold email opening |
| `hook_status` | `ok` or `api_error` |

**Hook generation logic (single Claude Haiku call per domain):**

1. Picks the top 2 most prominent farm names by page count from `farm_detail`
2. Selects hook framing based on tier:
   - **Hot** (`farm_count` ≥ 2): leads with number of farms — "found across X different link farms including Farm1 and Farm2"
   - **Warm** (single farm): leads with page volume — "found on Farm1 and Farm2 across X different pages"
3. Sentence 1 — states what was found, names the farms, sounds like a manual backlink review
4. Sentence 2 — states the ranking risk factually, no alarmism

Hook rules enforced in the prompt: no buzzwords (toxic, link profile, leverage, synergy), no em dashes, no mention of the agency, no CTA, plain text only, max 2 sentences.

**Performance:** Single-threaded (`THREADS = 1`) with 0.5s delay between calls — conservative to avoid Anthropic rate limits. On 500 domains expect ~5 minutes.

---

## API Keys Required

| Key | Variable name | Used in |
|-----|--------------|---------|
| SEMrush API key | `SEMRUSH_API_KEY` | `link_farm_qualify.py` |
| Anthropic API key | `ANTHROPIC_API_KEY` | `link_farm_qualify.py`, `link_farm_hook_generator.py` |

Set both at the top of each script in the `CONFIG` block.

> **Note:** The SEMrush key is IP-whitelisted. Run locally or from a consistent IP. Do not run from cloud VMs with dynamic IPs.

---

## Installation

```bash
pip install pandas requests beautifulsoup4
```

Python 3.9+ required.

---

## Usage

### Step 1 — Prepare your input

Export outgoing domain reports from Ahrefs for each known link farm, merge, clean out non-website rows, and save as `link_farm_domains.csv`. See Input preparation section above for exact column requirements.

### Step 2 — Run link_farm_qualify.py

```bash
python link_farm_qualify.py
```

Produces `link_farm_qualified.csv`. On 2,000 domains expect ~25 minutes with 10 threads and ~20,000 SEMrush units.

### Step 3 — Filter and run link_farm_hook_generator.py

Filter `link_farm_qualified.csv` to rows where `qualified == True`, save as a separate file if preferred, then run:

```bash
python link_farm_hook_generator.py
```

Produces `link_farm_hooks.csv`. On 500 qualified domains expect ~5 minutes.

---

## Checkpointing and Resume

Both scripts are safe to interrupt mid-run.

| Script | Checkpoint file | How to resume |
|--------|----------------|---------------|
| `link_farm_qualify.py` | `link_farm_qualified.csv.checkpoint` | Re-run — already-processed domains skipped automatically |
| `link_farm_hook_generator.py` | `link_farm_hooks.csv.checkpoint` | Re-run — already-processed domains skipped automatically |

Checkpoint files are deleted automatically on successful completion.

---

## SEMrush Unit Consumption (per full run)

| Stage | Units per domain | On 2,000 domains |
|-------|-----------------|-----------------|
| `link_farm_qualify.py` | ~10 units (AS only) | ~20,000 units |
| `link_farm_hook_generator.py` | 0 (Anthropic only) | 0 |
| **Total** | | **~20,000 units** |

This pipeline is significantly cheaper than the LHF or traffic drop pipelines — only one SEMrush call per domain.

---

## Known Limitations

- Bot-blocked sites pass through without language verification. In practice, English can be inferred from the farm context (link farms typically target English-language SEO), but manually spot-check a sample if running on non-English geographies.
- The Claude ICP check uses domain name only — it cannot visit the site for bot-blocked domains. Accuracy is high for well-known SaaS brands but may misclassify niche or obscure domains.
- Hook quality depends on `farm_detail` being populated. If `farm_detail` is empty, the hook falls back to generic farm count framing without specific page numbers.
- The pipeline does not verify that the link farm pages still exist at run time — Ahrefs data may be weeks old. A small percentage of hooks may reference farms that have since been deindexed.

---

## File Structure

```
link-farm-outreach/
├── link_farm_qualify.py          # Stage 1: qualify domains
├── link_farm_hook_generator.py   # Stage 2: generate outreach hooks
├── link_farm_domains.csv         # Your input file (gitignored)
├── link_farm_qualified.csv       # Output of Stage 1 (gitignored)
└── link_farm_hooks.csv           # Output of Stage 2 — load into Plusvibe
```

---

## Plusvibe Sequence (Reference)

Load `link_farm_hooks.csv` into Plusvibe. Map the `hook` column to `{{hook}}` in the email copy.

| Step | Day | Purpose |
|------|-----|---------|
| Email 1 | Day 1 | Hook-led opener — `{{hook}}` as the first paragraph |
| Email 2 | Day 4 | Follow-up — reinforce the risk angle, add social proof |
| Email 3 | Day 9 | Short nudge — one line + CTA |
| Email 4 | Day 14 | Break-up email |

Tone note: copy for this sequence uses a less accusatory framing — leads with "we noticed" rather than "you have a problem". Give the prospect plausible deniability (old agency, previous contractor). Proof points: Wallester (800% organic growth in 12 months), Vespia (10x traffic, acquired by Veriff), Remofirst (136 backlinks, 478% overall growth, $170K/year traffic value).
