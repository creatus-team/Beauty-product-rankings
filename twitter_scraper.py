#!/usr/bin/env python3
"""
K-Beauty Japan Twitter/X Daily Scraper
일본 트위터에서 K-beauty 트렌드 트윗 수집.
Uses Apify Tweet Scraper (actor: CJdippxWmn9uRfooo) — $0.25/1K tweets.
"""

import requests
import time
import json
import os
from datetime import datetime

APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
ACTOR_ID    = "CJdippxWmn9uRfooo"   # Tweet Scraper $0.25/1K by kaitoeasyapi
BASE_URL    = "https://api.apify.com/v2"
OUTPUT_DIR  = os.path.dirname(os.path.abspath(__file__))

# 일본어 K-beauty 키워드 — 모두 lang:ja 필터 적용
KBEAUTY_QUERIES = [
    # ── 일본어 핵심 키워드 ──
    "韓国コスメ lang:ja",
    "韓国スキンケア lang:ja",
    "韓国化粧品 lang:ja",
    "Kビューティー lang:ja",
    "韓国コスメ おすすめ lang:ja",
    "韓国コスメ 購入 lang:ja",
    # ── 트렌딩 성분/루틴 ──
    "韓国スキンケア ルーティン lang:ja",
    "ガラス肌 韓国 lang:ja",
    "韓国美容 lang:ja",
    "毛穴ケア 韓国 lang:ja",
    # ── 브랜드명 ──
    "COSRX lang:ja",
    "Beauty of Joseon lang:ja",
    "anua lang:ja",
    "laneige lang:ja",
    "romand lang:ja",
    # ── 인기 해시태그 (일본 트위터에서 사용) ──
    "#韓国コスメ lang:ja",
    "#韓国スキンケア lang:ja",
    "#Kビューティー lang:ja",
    "#韓国化粧品 lang:ja",
    "#コリアンビューティー lang:ja",
]

RESULTS_PER_QUERY = 80


def start_run(query: str) -> str:
    url = f"{BASE_URL}/acts/{ACTOR_ID}/runs?token={APIFY_TOKEN}"
    payload = {
        "searchTerms": [query],
        "maxItems": RESULTS_PER_QUERY,
        "sort": "Top",          # 트렌딩 트윗 우선
    }
    r = requests.post(url, json=payload, timeout=30)
    r.raise_for_status()
    run_id = r.json()["data"]["id"]
    print(f"  Started run {run_id} for: {query}")
    return run_id


def wait_for_run(run_id: str, timeout: int = 300) -> dict:
    url = f"{BASE_URL}/actor-runs/{run_id}?token={APIFY_TOKEN}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()["data"]
        status = data["status"]
        if status == "SUCCEEDED":
            return data
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Run {run_id} ended with status: {status}")
        time.sleep(8)
    raise TimeoutError(f"Run {run_id} timed out")


def fetch_dataset(dataset_id: str) -> list[dict]:
    url = f"{BASE_URL}/datasets/{dataset_id}/items?token={APIFY_TOKEN}&format=json&limit=1000"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


def normalize_tweet(item: dict, source_query: str) -> dict | None:
    try:
        if item.get("noResults") or not item.get("id"):
            return None
        author = item.get("author", {}) or {}

        media_url = ""
        for m in (item.get("extendedEntities", {}) or {}).get("media", []):
            url = m.get("media_url_https") or m.get("media_url", "")
            if url:
                media_url = url
                break

        hashtags = [
            h.get("text", "").lower()
            for h in (item.get("entities", {}) or {}).get("hashtags", [])
            if h.get("text")
        ]

        username = author.get("userName", "") or author.get("username", "")
        # source_tag: lang:ja 제거하고 클린하게
        clean_tag = source_query.replace(" lang:ja", "").replace("#", "").strip()
        return {
            "id":         str(item.get("id", "")),
            "url":        item.get("url", "") or item.get("twitterUrl", ""),
            "text":       (item.get("text", "") or "")[:500],
            "created_at": item.get("createdAt", ""),
            "source_tag": clean_tag,
            "lang":       item.get("lang", "ja"),
            "views":      item.get("viewCount", 0) or 0,
            "likes":      item.get("likeCount", 0) or 0,
            "retweets":   item.get("retweetCount", 0) or 0,
            "replies":    item.get("replyCount", 0) or 0,
            "bookmarks":  item.get("bookmarkCount", 0) or 0,
            "hashtags":   hashtags,
            "media_url":  media_url,
            "is_retweet": item.get("retweeted_tweet") is not None,
            "author": {
                "username":  username,
                "name":      author.get("name", ""),
                "followers": author.get("followers", 0) or 0,
                "verified":  author.get("isBlueVerified", False) or author.get("isVerified", False),
                "avatar":    author.get("profilePicture", "").replace("_normal.", "_400x400."),
                "url":       author.get("url", "") or f"https://x.com/{username}",
            },
        }
    except Exception as e:
        print(f"  Warning: could not normalize tweet: {e}")
        return None


def scrape_all_queries() -> list[dict]:
    all_tweets: dict[str, dict] = {}
    run_queue: list[tuple[str, str]] = []

    print("\n[1/3] Launching Apify Twitter runs (Japan K-beauty)...")
    for query in KBEAUTY_QUERIES:
        try:
            run_id = start_run(query)
            run_queue.append((run_id, query))
        except Exception as e:
            print(f"  WARNING: Could not start run for '{query}': {e}")

    print(f"\n[2/3] Waiting for {len(run_queue)} runs to complete...")
    for run_id, query in run_queue:
        try:
            print(f"  Waiting on '{query}' ({run_id})...")
            run_data   = wait_for_run(run_id)
            dataset_id = run_data["defaultDatasetId"]
            items      = fetch_dataset(dataset_id)
            added = 0
            for item in items:
                t = normalize_tweet(item, query)
                if t and t["id"] and t["id"] not in all_tweets:
                    all_tweets[t["id"]] = t
                    added += 1
            print(f"  '{query}': {len(items)} fetched, {added} new")
        except Exception as e:
            print(f"  WARNING: Failed to collect '{query}': {e}")

    return list(all_tweets.values())


def main():
    print("K-Beauty Japan Twitter/X Daily Scraper")
    print("=" * 45)

    tweets = scrape_all_queries()

    if not tweets:
        print("\nNo tweets collected. Check Apify credits and token.")
        return

    # 좋아요 + 리트윗 + 뷰 기준 정렬
    tweets.sort(key=lambda t: t["likes"] + t["retweets"] * 3 + t["views"] // 100, reverse=True)

    print(f"\n[3/3] Saving {len(tweets)} unique tweets...")

    date_str  = datetime.now().strftime("%Y-%m-%d")
    json_path = os.path.join(OUTPUT_DIR, f"twitter_{date_str}.json")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(tweets, f, ensure_ascii=False, indent=2)

    total_likes = sum(t["likes"] for t in tweets)
    top = tweets[:3]

    print(f"\nDone! Saved to {json_path}")
    print(f"Total likes across all tweets: {total_likes:,}")
    print(f"\nTop 3 tweets by engagement:")
    for i, t in enumerate(top, 1):
        print(f"  {i}. @{t['author']['username']} — 좋아요 {t['likes']:,} / RT {t['retweets']:,}")
        print(f"     {t['text'][:80]}")


if __name__ == "__main__":
    main()
