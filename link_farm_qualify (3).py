import pandas as pd
import requests
import time
import re
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# ── CONFIG ───────────────────────────────────────────────────────────────────
SEMRUSH_API_KEY   = "YOUR_SEMRUSH_API_KEY_HERE"
ANTHROPIC_API_KEY = "YOUR_ANTHROPIC_API_KEY_HERE"
MIN_AS            = 10
MAX_AS            = 60
REQUEST_TIMEOUT   = 10
DELAY_BETWEEN     = 0.3
THREADS           = 10
CHECKPOINT_EVERY  = 100

# ── HELPERS ──────────────────────────────────────────────────────────────────
def normalize_domain(domain):
    domain = domain.strip().lower()
    if not domain.startswith("http"):
        domain = "https://" + domain
    return domain.rstrip("/")

def check_site_alive(url):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

    from urllib.parse import urlparse
    parsed = urlparse(url)
    hostname = parsed.netloc

    if hostname.startswith("www."):
        non_www = hostname[4:]
        variants = [
            f"https://{hostname}",
            f"https://{non_www}",
            f"http://{hostname}",
            f"http://{non_www}",
        ]
    else:
        variants = [
            f"https://{hostname}",
            f"https://www.{hostname}",
            f"http://{hostname}",
            f"http://www.{hostname}",
        ]

    got_403 = False

    for variant in variants:
        try:
            r = requests.head(variant, timeout=REQUEST_TIMEOUT, allow_redirects=True, headers=headers)
            if r.status_code < 400:
                r = requests.get(variant, timeout=REQUEST_TIMEOUT, allow_redirects=True, headers=headers)
                return "alive", r
            elif r.status_code == 403:
                got_403 = True
        except Exception:
            pass

        for attempt in range(2):
            try:
                r = requests.get(variant, timeout=REQUEST_TIMEOUT, allow_redirects=True, headers=headers)
                if r.status_code < 400:
                    return "alive", r
                elif r.status_code == 403:
                    got_403 = True
                break
            except Exception:
                if attempt == 0:
                    time.sleep(2)
                continue

    if got_403:
        return "bot_blocked", None
    return "dead", None

def detect_language(response):
    from bs4 import BeautifulSoup
    try:
        soup = BeautifulSoup(response.text, "html.parser")
        html_tag = soup.find("html")
        if html_tag and html_tag.get("lang"):
            return html_tag.get("lang", "").lower().startswith("en")
        meta = soup.find("meta", attrs={"http-equiv": re.compile("content-language", re.I)})
        if meta:
            return "en" in meta.get("content", "").lower()
        return True
    except Exception:
        return True

def get_authority_score(domain):
    try:
        url = (
            f"https://api.semrush.com/analytics/v1/"
            f"?key={SEMRUSH_API_KEY}"
            f"&type=backlinks_overview"
            f"&target={domain}"
            f"&target_type=root_domain"
            f"&export_columns=ascore"
        )
        r = requests.get(url, timeout=REQUEST_TIMEOUT)
        lines = r.text.strip().split("\n")
        if len(lines) < 2:
            return None
        values = lines[1].split(";")
        return int(values[0]) if len(values) > 0 and values[0].isdigit() else None
    except Exception:
        return None

def is_good_prospect(domain):
    """Claude check: is this a real mid-market B2B company or SaaS product that would care about SEO?"""
    prompt = f"""Is "{domain}" a real mid-market B2B company or SaaS product that would actively care about its SEO rankings?

Answer NO if any of these are true:
- It's a major well-known brand (e.g. HubSpot, Semrush, Forbes, Canva, Salesforce, Shopify)
- It's a news, media, or publisher site
- It's a personal blog or portfolio
- It's a directory, aggregator, or review site
- It's an affiliate or coupon site
- It's a non-profit, government, or educational institution
- It's a social platform or app store

Answer YES if:
- It appears to be a mid-market B2B software, SaaS, or digital services company
- It's the kind of company that would have an SEO manager or CMO who cares about rankings

Answer with only YES or NO."""
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
                "max_tokens": 5,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=REQUEST_TIMEOUT
        )
        answer = response.json()["content"][0]["text"].strip().upper()
        return answer.startswith("YES")
    except Exception:
        return True  # default to keep if API fails

# ── PROCESS SINGLE DOMAIN ────────────────────────────────────────────────────
def process_row(args):
    i, total, row = args
    domain    = str(row.get("domain", "")).strip()
    tier      = str(row.get("tier", "")).strip()
    farms     = str(row.get("farms", "")).strip()
    farm_detail = str(row.get("farm_detail", "")).strip()
    farm_count  = row.get("farm_count", 0)
    total_pages = row.get("total_pages", 0)

    result = {
        "domain"       : domain,
        "tier"         : tier,
        "farm_count"   : farm_count,
        "total_pages"  : total_pages,
        "farms"        : farms,
        "farm_detail"  : farm_detail,
        "site_alive"   : None,
        "is_english"   : None,
        "authority_score": None,
        "is_good_prospect": None,
        "qualified"    : False,
        "fail_reason"  : ""
    }

    if not domain:
        result["fail_reason"] = "no_domain"
        return i, result, f"[{i}/{total}] (empty) ... ❌ no domain"

    base_url = normalize_domain(domain)

    # FILTER 1: Site alive
    site_status, response = check_site_alive(base_url)
    result["site_alive"] = site_status in ("alive", "bot_blocked")
    if site_status == "dead":
        result["fail_reason"] = "dead_site"
        return i, result, f"[{i}/{total}] {domain} ... ❌ dead site"

    # FILTER 2: Language (skip if bot-blocked — no response body available)
    if site_status == "alive":
        is_english = detect_language(response)
        result["is_english"] = is_english
        if not is_english:
            result["fail_reason"] = "non_english"
            return i, result, f"[{i}/{total}] {domain} ... ❌ non-English"
    else:
        # bot_blocked — assume English, confirmed active by virtue of blocking bots
        result["is_english"] = True

    # FILTER 3: Authority Score (10–60 band)
    time.sleep(DELAY_BETWEEN)
    ascore = get_authority_score(domain)
    result["authority_score"] = ascore

    if ascore is None:
        result["fail_reason"] = "no_ascore"
        return i, result, f"[{i}/{total}] {domain} ... ❌ no authority score"

    if ascore < MIN_AS:
        result["fail_reason"] = "low_authority_score"
        return i, result, f"[{i}/{total}] {domain} ... ❌ low AS ({ascore})"

    if ascore > MAX_AS:
        result["fail_reason"] = "high_authority_score"
        return i, result, f"[{i}/{total}] {domain} ... ❌ high AS ({ascore}) — likely big brand"

    # FILTER 4: Claude ICP check
    good = is_good_prospect(domain)
    result["is_good_prospect"] = good
    if not good:
        result["fail_reason"] = "not_icp"
        return i, result, f"[{i}/{total}] {domain} ... ❌ not ICP (AS:{ascore})"

    result["qualified"] = True
    status_note = "bot-blocked" if site_status == "bot_blocked" else f"AS:{ascore}"
    return i, result, f"[{i}/{total}] {domain} ... ✅ qualified ({status_note}, tier:{tier})"

# ── MAIN ─────────────────────────────────────────────────────────────────────
def qualify(input_csv, output_csv):
    df = pd.read_csv(input_csv)
    df = df.drop_duplicates(subset="domain").reset_index(drop=True)
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
    print(f"🔄 Processing {remaining} remaining domains with {THREADS} threads\n")

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

    qualified = int(out_df["qualified"].sum())
    failed    = len(out_df) - qualified

    print(f"\n{'─'*50}")
    print(f"✅ Qualified      : {qualified}")
    print(f"❌ Filtered out   : {failed}")
    print(f"📊 Total          : {len(out_df)}")
    print(f"\nFail breakdown:")
    fails = out_df[out_df["fail_reason"] != ""]["fail_reason"].value_counts()
    for reason, count in fails.items():
        print(f"  {reason}: {count}")
    print(f"\n💾 Output saved to: {output_csv}")

if __name__ == "__main__":
    qualify(
        input_csv  = "link_farm_domains.csv",
        output_csv = "link_farm_qualified.csv"
    )
