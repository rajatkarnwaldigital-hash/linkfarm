import csv
import os
from urllib.parse import urlparse
from collections import defaultdict

INPUT_FILES = [
    'link_farms_-_robinwaite_com.csv',
    'link_farms_2_-_portotheme_com.csv',
    'link_farms_2_-_paylinedata_com.csv',
    'link_farms_2_-_tivazo_com.csv',
    'link_farms_2_-_appsgeyser_com.csv',
    'link_farms_-_leadgenapp_io.csv',
    'link_farms_-_ecommercefastlane_com.csv',
    'link_farms_-_iemlabs_com.csv',
    'link_farms_-_Ranktracker_com.csv',
]

OUTPUT_FILE = 'link_farm_prospects.csv'

NOISE_DOMAINS = {
    'facebook.com','twitter.com','linkedin.com','instagram.com','youtube.com',
    'pinterest.com','reddit.com','tiktok.com','snapchat.com','x.com',
    'apple.com','podcasts.apple.com','spotify.com','soundcloud.com',
    'google.com','podcasts.google.com','play.google.com','googleapis.com',
    'amazon.com','wikipedia.org','github.com','medium.com',
    'wordpress.com','blogspot.com','tumblr.com','wix.com','squarespace.com',
    'app.kit.com','kit.com','ipse.co.uk','web.archive.org',
    'yelp.com','trustpilot.com','clutch.co','g2.com','capterra.com',
    'forbes.com','techcrunch.com','entrepreneur.com','inc.com','huffpost.com',
    'shopify.com','etsy.com','ebay.com','walmart.com',
}


def get_clean_domain(url):
    try:
        netloc = urlparse(url.strip()).netloc.lower()
        return netloc.lstrip('www.') if netloc else None
    except Exception:
        return None


def is_noise(domain):
    return any(domain == n or domain.endswith('.' + n) for n in NOISE_DOMAINS)


def get_farm_name(filepath):
    name = os.path.basename(filepath).replace('.csv', '')
    for prefix in ['link_farms_2_-_', 'link_farms_-_']:
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name.replace('_', '.')


def main():
    domain_farms = defaultdict(set)
    domain_pages = defaultdict(lambda: defaultdict(set))

    print("Reading CSVs...")
    for f in INPUT_FILES:
        if not os.path.exists(f):
            print(f"  WARNING: {f} not found, skipping")
            continue
        farm_name = get_farm_name(f)
        row_count = 0
        with open(f) as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                source_page = row['Link Farm Domain'].strip()
                target = row['Target domain'].strip()
                target_domain = get_clean_domain(target)
                if not target_domain or is_noise(target_domain):
                    continue
                domain_farms[target_domain].add(farm_name)
                domain_pages[target_domain][farm_name].add(source_page)
                row_count += 1
        print(f"  {farm_name}: {row_count} rows processed")

    print(f"\nTotal unique domains (noise removed): {len(domain_farms)}")

    prospects = []
    for domain, farms in domain_farms.items():
        total_pages = sum(len(pages) for pages in domain_pages[domain].values())
        if total_pages < 2:
            continue

        farm_count = len(farms)
        farms_list = ', '.join(sorted(farms))
        farm_detail = ' | '.join(
            f"{farm}({len(domain_pages[domain][farm])} pages)"
            for farm in sorted(farms)
        )
        tier = 'hot' if farm_count >= 2 else 'warm'

        prospects.append({
            'domain'     : domain,
            'tier'       : tier,
            'farm_count' : farm_count,
            'total_pages': total_pages,
            'farms'      : farms_list,
            'farm_detail': farm_detail,
        })

    prospects.sort(key=lambda x: (-int(x['tier'] == 'hot'), -x['farm_count'], -x['total_pages']))

    hot  = sum(1 for p in prospects if p['tier'] == 'hot')
    warm = sum(1 for p in prospects if p['tier'] == 'warm')

    with open(OUTPUT_FILE, 'w', newline='') as f:
        fieldnames = ['domain', 'tier', 'farm_count', 'total_pages', 'farms', 'farm_detail']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(prospects)

    print(f"\nProspects after 2+ page filter : {len(prospects)}")
    print(f"  Hot  (2+ farms)              : {hot}")
    print(f"  Warm (1 farm, 2+ pages)      : {warm}")
    print(f"\nOutput saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
