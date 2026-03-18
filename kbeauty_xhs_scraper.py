#!/usr/bin/env python3
"""
XHS (小红书 / RedNote) K-Beauty Scraper
Apify actor: easyapi/rednote-xiaohongshu-search-scraper (ID: 9qkezGwljt2uc4DY9)

사용법:
  1. https://console.apify.com/actors/9qkezGwljt2uc4DY9 에서 액터 렌트 (무료)
  2. python3 kbeauty_xhs_scraper.py
  3. xhs_data_YYYY-MM-DD.json 파일 생성됨
  4. git add xhs_data_*.json && git push
"""

import os, json, time, requests, re
from datetime import datetime

APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
XHS_ACTOR_ID = "9qkezGwljt2uc4DY9"  # easyapi/rednote-xiaohongshu-search-scraper
BASE = "https://api.apify.com/v2"

KEYWORDS = [
    "韩国护肤",      # Korean skincare
    "韩国彩妆",      # Korean makeup
    "oliveyoung",
    "小韩护肤",      # Korean-style skincare
    "韩国防晒",      # Korean sunscreen
    "cosrx",
    "laneige",
    "beauty of joseon",
    "k-beauty",
    "韩国素颜霜",    # Korean no-makeup cream
    "韩国精华",      # Korean serum/essence
    "韩系妆容",      # Korean-style makeup
]

MAX_ITEMS_PER_KEYWORD = 20


def parse_likes(val):
    if not val:
        return 0
    s = str(val).replace(',', '').strip()
    m = re.match(r'([\d.]+)([万千kKwW]?)', s)
    if not m:
        return 0
    n = float(m.group(1))
    u = m.group(2).lower()
    if u in ('万', 'w'):
        n *= 10000
    elif u in ('千', 'k'):
        n *= 1000
    return int(n)


def run_actor(keyword):
    print(f"  Running actor for: {keyword}")
    r = requests.post(
        f"{BASE}/acts/{XHS_ACTOR_ID}/runs",
        params={"token": APIFY_TOKEN},
        json={"keyword": keyword, "maxItems": MAX_ITEMS_PER_KEYWORD},
        timeout=30
    )
    r.raise_for_status()
    run_id = r.json()["data"]["id"]
    print(f"    Run ID: {run_id} — waiting...")
    for _ in range(60):
        time.sleep(5)
        status_r = requests.get(f"{BASE}/actor-runs/{run_id}", params={"token": APIFY_TOKEN}, timeout=15)
        status = status_r.json()["data"]["status"]
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            print(f"    Status: {status}")
            break
    if status != "SUCCEEDED":
        print(f"    Skipping (status={status})")
        return []
    dataset_id = status_r.json()["data"]["defaultDatasetId"]
    items_r = requests.get(
        f"{BASE}/datasets/{dataset_id}/items",
        params={"token": APIFY_TOKEN, "limit": MAX_ITEMS_PER_KEYWORD},
        timeout=30
    )
    return items_r.json()


def normalize(raw_item, source_tag):
    """Normalize raw Apify XHS output to our standard format."""
    pd = raw_item.get("postData") or raw_item  # some actors wrap in postData
    note_id = pd.get("noteId") or pd.get("id") or raw_item.get("noteId", "")
    post_url = pd.get("postUrl") or pd.get("url") or raw_item.get("postUrl", "")
    if not post_url and note_id:
        xsec = pd.get("xsecToken", "")
        post_url = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={xsec}"

    user = pd.get("user") or raw_item.get("user") or {}
    interact = pd.get("interactInfo") or raw_item.get("interactInfo") or {}
    cover_obj = pd.get("cover") or raw_item.get("cover") or {}
    cover_url = (
        cover_obj.get("urlDefault") or
        cover_obj.get("urlPre") or
        (cover_obj.get("infoList") or [{}])[0].get("url", "") if isinstance(cover_obj, dict) else ""
    )

    likes_raw = interact.get("likedCount") or raw_item.get("likes") or raw_item.get("likedCount") or 0
    title = pd.get("displayTitle") or raw_item.get("displayTitle") or raw_item.get("title") or ""
    post_type = pd.get("type") or raw_item.get("type") or "image"
    scraped_at = raw_item.get("scrapedAt") or datetime.utcnow().isoformat() + "Z"

    return {
        "id": note_id,
        "url": post_url,
        "title": title,
        "type": post_type,
        "cover": cover_url,
        "created_at": scraped_at,
        "source_tag": source_tag,
        "creator": {
            "userId": user.get("userId", ""),
            "username": user.get("nickName") or user.get("nickname", ""),
            "avatar": user.get("avatar", ""),
        },
        "stats": {
            "likes": parse_likes(likes_raw),
            "comments": int(raw_item.get("comments", 0) or 0),
        },
    }


def main():
    date_str = datetime.now().strftime("%Y-%m-%d")
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"xhs_data_{date_str}.json")

    all_items = []
    seen_ids = set()

    for kw in KEYWORDS:
        try:
            raw = run_actor(kw)
            count = 0
            for item in raw:
                norm = normalize(item, kw)
                if norm["id"] and norm["id"] not in seen_ids:
                    seen_ids.add(norm["id"])
                    all_items.append(norm)
                    count += 1
            print(f"    → {count} new items (total: {len(all_items)})")
        except Exception as e:
            print(f"    ERROR for '{kw}': {e}")

    all_items.sort(key=lambda x: x["stats"]["likes"], reverse=True)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_items, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Saved {len(all_items)} XHS posts → {out_path}")
    print("Next: git add xhs_data_*.json && git commit -m 'Add XHS data' && git push")


if __name__ == "__main__":
    main()
