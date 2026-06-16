import pandas as pd
import requests
import time
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# ── CONFIG ───────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = "YOUR_ANTHROPIC_API_KEY_HERE"
REQUEST_TIMEOUT   = 15
THREADS           = 1
CHECKPOINT_EVERY  = 100
DELAY_BETWEEN     = 0.5

# ── HELPERS ──────────────────────────────────────────────────────────────────
def pick_top_farms(farms_str, farm_detail_str, n=2):
    """Pick n most credible-sounding farm names to mention in the hook."""
    farms = [f.strip() for f in farms_str.split(",") if f.strip()]
    if len(farms) <= n:
        return farms

    # parse farm_detail to get page counts per farm
    # format: "farm1.com(X pages) | farm2.com(Y pages)"
    farm_pages = {}
    for part in farm_detail_str.split("|"):
        part = part.strip()
        if "(" in part and "pages)" in part:
            name = part[:part.index("(")].strip()
            try:
                count = int(part[part.index("(")+1:part.index(" pages)")])
                farm_pages[name] = count
            except:
                farm_pages[name] = 0

    # sort farms by page count descending, pick top n
    sorted_farms = sorted(farms, key=lambda f: farm_pages.get(f, 0), reverse=True)
    return sorted_farms[:n]

def clean_farm_name(farm):
    """Return just the domain name without TLD for readability e.g. ranktracker.com -> Ranktracker"""
    name = farm.split(".")[0].replace("-", " ").replace("_", " ").title()
    return name

def generate_hook(domain, tier, farm_count, total_pages, farms_str, farm_detail_str):
    top_farms = pick_top_farms(farms_str, farm_detail_str, n=2)
    farm_names = [clean_farm_name(f) for f in top_farms]

    if len(farm_names) == 1:
        farms_mention = farm_names[0]
    else:
        farms_mention = f"{farm_names[0]} and {farm_names[1]}"

    if tier == "hot":
        risk_context = f"found across {farm_count} different link farms including {farms_mention}"
    else:
        risk_context = f"found on {farms_mention} across {total_pages} different pages"

    prompt = f"""Write a two-sentence cold email opening hook for an SEO agency reaching out to {domain}.

Context: We found their backlinks {risk_context}. These are known link farms that Google penalizes sites for buying links from.

Requirements:
- Sentence 1: State what we found. Mention the number of link farms ({farm_count}) and name {farms_mention} specifically. Make it sound like we manually reviewed their backlink profile.
- Sentence 2: One line on the risk — what this means for their rankings. Keep it factual, not alarmist.
- Sound like a human who did research, not a mass email blast
- No buzzwords like "toxic", "link profile", "outreach", "leverage", "synergy"
- No em dashes
- No mention of our company or any CTA — just the hook
- Max 2 sentences, no more
- Write in plain text only, no formatting

Example style (do not copy verbatim):
"Noticed {domain} has backlinks coming from {farm_count} link farms, including {farms_mention} — sites that exist purely to sell links and have been flagged in multiple Google updates. Companies with this kind of backlink pattern typically see ranking drops before they connect it back to the source."

Now write a fresh version:"""

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 150,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=REQUEST_TIMEOUT
        )
        hook = response.json()["content"][0]["text"].strip()
        # strip any leading/trailing quotes
        hook = hook.strip('"').strip("'")
        return hook
    except Exception as e:
        return f"ERROR: {e}"

# ── PROCESS SINGLE ROW ───────────────────────────────────────────────────────
def process_row(args):
    i, total, row = args
    domain      = str(row.get("domain", "")).strip()
    tier        = str(row.get("tier", "")).strip()
    farm_count  = int(row.get("farm_count", 1))
    total_pages = int(row.get("total_pages", 1))
    farms       = str(row.get("farms", "")).strip()
    farm_detail = str(row.get("farm_detail", "")).strip()

    result = {
        "domain"      : domain,
        "tier"        : tier,
        "farm_count"  : farm_count,
        "total_pages" : total_pages,
        "farms"       : farms,
        "farm_detail" : farm_detail,
        "authority_score": row.get("authority_score", ""),
        "hook"        : "",
        "hook_status" : ""
    }

    if not domain:
        result["hook_status"] = "no_domain"
        return i, result, f"[{i}/{total}] (empty) ... ❌ no domain"

    time.sleep(DELAY_BETWEEN)
    hook = generate_hook(domain, tier, farm_count, total_pages, farms, farm_detail)

    if hook.startswith("ERROR"):
        result["hook"] = ""
        result["hook_status"] = "api_error"
        return i, result, f"[{i}/{total}] {domain} ... ❌ API error"

    result["hook"] = hook
    result["hook_status"] = "ok"
    return i, result, f"[{i}/{total}] {domain} ... ✅"

# ── MAIN ─────────────────────────────────────────────────────────────────────
def generate_hooks(input_csv, output_csv):
    df = pd.read_csv(input_csv)
    total = len(df)
    print(f"\n📥 Loaded {total} prospects\n")

    checkpoint_file = output_csv + ".checkpoint"
    done_domains = set()
    results = []
    if os.path.exists(checkpoint_file):
        existing = pd.read_csv(checkpoint_file)
        done_domains = set(existing["domain"].tolist())
        results = existing.to_dict("records")
        print(f"♻️  Resuming from checkpoint — {len(done_domains)} already processed\n")

    df = df[~df["domain"].isin(done_domains)].reset_index(drop=True)
    remaining = len(df)
    print(f"🔄 Generating hooks for {remaining} domains with {THREADS} threads\n")

    lock = Lock()
    processed = 0

    args_list = [(i + len(done_domains) + 1, total, row) for i, row in df.iterrows()]

    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = {executor.submit(process_row, args): args for args in args_list}
        for future in as_completed(futures):
            try:
                idx, result, log = future.result()
                with lock:
                    results.append(result)
                    processed += 1
                    print(log)
                    if processed % CHECKPOINT_EVERY == 0:
                        pd.DataFrame(results).to_csv(checkpoint_file, index=False)
                        print(f"\n💾 Checkpoint saved ({processed}/{remaining})\n")
            except Exception as e:
                print(f"Error: {e}")

    out_df = pd.DataFrame(results)
    out_df.to_csv(output_csv, index=False)

    if os.path.exists(checkpoint_file):
        os.remove(checkpoint_file)

    ok    = int((out_df["hook_status"] == "ok").sum())
    error = int((out_df["hook_status"] != "ok").sum())

    print(f"\n{'─'*50}")
    print(f"✅ Hooks generated : {ok}")
    print(f"❌ Errors          : {error}")
    print(f"📊 Total           : {len(out_df)}")
    print(f"\n💾 Output saved to: {output_csv}")

if __name__ == "__main__":
    generate_hooks(
        input_csv  = "link_farm_qualified.csv",
        output_csv = "link_farm_hooks.csv"
    )
