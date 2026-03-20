#!/usr/bin/env python3
"""
K-Beauty Daily Research Tool
Scrapes TikTok for viral K-beauty content via Apify and generates a daily digest.
"""

import requests
import time
import json
import os
from datetime import datetime
from collections import defaultdict

# ── Config ──────────────────────────────────────────────────────────────────
APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
ACTOR_ID    = "GdWCkxBtKWOsKjdch"
BASE_URL    = "https://api.apify.com/v2"

# 주 2회(월/목) 실행 기준 — 1회 11개 실행 × $0.44 × 8.6회/월 ≈ $38/월
KBEAUTY_HASHTAGS = [
    # ── 핵심 버즈 (반드시 포함) ──
    "kbeauty",
    "koreanskincare",
    "koreanbeauty",
    "skintok",
    # ── 2026 트렌드 ──
    "skinbarrier",
    "kbeauty2026",
    # ── 바이럴 성분 ──
    "snailmucin",
    # ── 룩 트렌드 ──
    "glassskin",
]

# ── 키워드 검색 — 트렌드 발굴 핵심 3개 ──────────────────────────────────
KBEAUTY_KEYWORDS = [
    "viral korean skincare 2026",
    "trending kbeauty",
    "korean skin trend",
]

RESULTS_PER_HASHTAG = 150  # videos to pull per hashtag (Starter plan)
RESULTS_PER_KEYWORD = 100  # videos to pull per keyword search
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
TOP_N_VIDEOS = 30          # videos shown in digest
TOP_N_CREATORS = 25        # creators shown in digest


# ── Apify helpers ────────────────────────────────────────────────────────────

def start_run(hashtag: str) -> str:
    """Trigger one actor run for a single hashtag. Returns run ID."""
    url = f"{BASE_URL}/acts/{ACTOR_ID}/runs?token={APIFY_TOKEN}"
    payload = {
        "hashtags": [hashtag],
        "resultsPerPage": RESULTS_PER_HASHTAG,
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
        "shouldDownloadAvatars": False,
        "shouldDownloadMusicCovers": False,
        "shouldDownloadSubtitles": "NEVER_DOWNLOAD_SUBTITLES",
        "profileScrapingSection": "videos",
        "sortingType": "latest",
    }
    r = requests.post(url, json=payload, timeout=30)
    r.raise_for_status()
    run_id = r.json()["data"]["id"]
    print(f"  Started run {run_id} for #{hashtag}")
    return run_id


def start_keyword_run(keyword: str) -> str:
    """Trigger one actor run for a keyword search. Returns run ID."""
    url = f"{BASE_URL}/acts/{ACTOR_ID}/runs?token={APIFY_TOKEN}"
    payload = {
        "searchKeywords": [keyword],
        "resultsPerPage": RESULTS_PER_KEYWORD,
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
        "shouldDownloadAvatars": False,
        "shouldDownloadMusicCovers": False,
        "shouldDownloadSubtitles": "NEVER_DOWNLOAD_SUBTITLES",
        "sortingType": "relevant",
    }
    r = requests.post(url, json=payload, timeout=30)
    r.raise_for_status()
    run_id = r.json()["data"]["id"]
    print(f"  Started run {run_id} for keyword '{keyword}'")
    return run_id


def wait_for_run(run_id: str, timeout: int = 300) -> dict:
    """Poll until run finishes. Returns run data dict."""
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
    raise TimeoutError(f"Run {run_id} did not finish within {timeout}s")


def fetch_dataset(dataset_id: str) -> list[dict]:
    """Download all items from a dataset."""
    url = f"{BASE_URL}/datasets/{dataset_id}/items?token={APIFY_TOKEN}&format=json&limit=1000"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


# ── Data processing ──────────────────────────────────────────────────────────

LANG_TO_REGION = {
    "ko": "🇰🇷 Korea",
    "zh": "🇨🇳 China",
    "id": "🇮🇩 Indonesia",
    "th": "🇹🇭 Thailand",
    "vi": "🇻🇳 Vietnam",
    "tl": "🇵🇭 Philippines",
    "ms": "🇸🇬 Singapore",
    "pt": "🇧🇷 Brazil",
    "es": "🇪🇸 Spain / Mexico",
    "ar": "🇦🇪 UAE",
    "ja": "🇯🇵 Japan",
    "en": "🇺🇸 USA / UK",
    "fr": "🇫🇷 France",
    "de": "🇩🇪 Germany",
    "tr": "🇹🇷 Turkey",
}

HASHTAG_REGION_HINTS = {
    "indonesia": "🇮🇩 Indonesia", "id": "🇮🇩 Indonesia",
    "thailand":  "🇹🇭 Thailand",  "th": "🇹🇭 Thailand",
    "vietnam":   "🇻🇳 Vietnam",   "vn": "🇻🇳 Vietnam",
    "philippines":"🇵🇭 Philippines","ph":"🇵🇭 Philippines",
    "singapore": "🇸🇬 Singapore", "sg": "🇸🇬 Singapore",
    "malaysia":  "🇲🇾 Malaysia",  "my": "🇲🇾 Malaysia",
    "taiwan":    "🇹🇼 Taiwan",    "tw": "🇹🇼 Taiwan",
    "korea":     "🇰🇷 Korea",     "korean": "🇰🇷 Korea",
    "china":     "🇨🇳 China",     "chinese": "🇨🇳 China",
    "usa":       "🇺🇸 USA / UK",  "uk": "🇺🇸 USA / UK",
    "brazil":    "🇧🇷 Brazil",    "brasil": "🇧🇷 Brazil",
    "mexico":    "🇪🇸 Spain / Mexico",
    "spain":     "🇪🇸 Spain / Mexico",
    "uae":       "🇦🇪 UAE",       "dubai": "🇦🇪 UAE",
}

def detect_region(lang: str, hashtags: list[str]) -> str:
    # Check hashtags first for stronger signal
    for tag in hashtags:
        t = tag.lower()
        if t in HASHTAG_REGION_HINTS:
            return HASHTAG_REGION_HINTS[t]
    # Fall back to language
    return LANG_TO_REGION.get(lang, "🌍 Other")


def normalize_video(item: dict, source_hashtag: str) -> dict | None:
    """Extract only the fields we care about from a raw Apify item."""
    try:
        author = item.get("authorMeta", {}) or {}
        music  = item.get("musicMeta", {}) or {}
        stats  = {
            "views":    item.get("playCount", 0) or 0,
            "likes":    item.get("diggCount", 0) or 0,
            "comments": item.get("commentCount", 0) or 0,
            "shares":   item.get("shareCount", 0) or 0,
            "saves":    item.get("collectCount", 0) or 0,
        }
        video_meta = item.get("videoMeta", {}) or {}
        lang       = (item.get("textLanguage", "") or "").lower()
        hashtags   = [h.get("name", "") for h in (item.get("hashtags") or [])]
        region     = detect_region(lang, hashtags)
        return {
            "id":            item.get("id", ""),
            "url":           item.get("webVideoUrl", ""),
            "caption":       (item.get("text", "") or "")[:200],
            "created_at":    item.get("createTimeISO", ""),
            "duration":      video_meta.get("duration", 0),
            "cover":         video_meta.get("coverUrl", "") or video_meta.get("originalCoverUrl", ""),
            "hashtags":      hashtags,
            "language":      lang,
            "region":        region,
            "source_tag":    source_hashtag,
            "stats":         stats,
            "engagement":    stats["likes"] + stats["comments"] * 3 + stats["shares"] * 5 + stats["saves"] * 2,
            "creator": {
                "username":   author.get("name", "unknown"),
                "nickname":   author.get("nickName", ""),
                "followers":  author.get("fans", 0) or 0,
                "total_likes":author.get("heart", 0) or 0,
                "videos":     author.get("video", 0) or 0,
                "verified":   author.get("verified", False),
                "url":        f"https://www.tiktok.com/@{author.get('name', '')}",
                "avatar":     author.get("avatar", "") or author.get("originalAvatarUrl", ""),
            },
            "music": {
                "title":    music.get("musicName", ""),
                "artist":   music.get("musicAuthor", ""),
                "original": music.get("musicOriginal", False),
            },
        }
    except Exception:
        return None


BATCH_SIZE = 2  # Starter plan allows ~2 concurrent runs


def _collect_batch(run_queue: list[tuple[str, str, str]], all_videos: dict) -> None:
    """Wait for a batch of runs and merge results into all_videos. Keeps highest-view dupe."""
    for run_id, label, source_tag in run_queue:
        try:
            print(f"  Waiting on {label} ({run_id})...")
            run_data   = wait_for_run(run_id)
            dataset_id = run_data["defaultDatasetId"]
            items      = fetch_dataset(dataset_id)
            added = 0
            for item in items:
                v = normalize_video(item, source_tag)
                if not v or not v["id"]:
                    continue
                vid_id = v["id"]
                # keep whichever copy has more views
                if vid_id not in all_videos or v["stats"]["views"] > all_videos[vid_id]["stats"]["views"]:
                    all_videos[vid_id] = v
                    added += 1
            print(f"  {label}: {len(items)} fetched, {added} new/updated")
        except Exception as e:
            print(f"  WARNING: Failed to collect {label}: {e}")


def scrape_all() -> list[dict]:
    """Run actor for all hashtags + keywords, collect and deduplicate all videos."""
    all_videos: dict[str, dict] = {}

    # ── Phase 1: Hashtags ────────────────────────────────────────────────────
    tag_batches = [
        KBEAUTY_HASHTAGS[i:i + BATCH_SIZE]
        for i in range(0, len(KBEAUTY_HASHTAGS), BATCH_SIZE)
    ]
    total_tag_batches = len(tag_batches)
    print(f"\n[Phase 1] Hashtags: {len(KBEAUTY_HASHTAGS)} tags in {total_tag_batches} batches")

    for batch_idx, batch in enumerate(tag_batches, 1):
        print(f"\n  [Hashtag Batch {batch_idx}/{total_tag_batches}] Launching {len(batch)} runs...")
        run_queue = []
        for tag in batch:
            try:
                run_id = start_run(tag)
                run_queue.append((run_id, f"#{tag}", tag))
            except Exception as e:
                print(f"  WARNING: Could not start run for #{tag}: {e}")
        _collect_batch(run_queue, all_videos)
        if batch_idx < total_tag_batches:
            print(f"  Waiting 10s before next batch...")
            time.sleep(10)

    print(f"\n  Hashtag phase done: {len(all_videos)} unique videos so far")

    # ── Phase 2: Keywords ────────────────────────────────────────────────────
    kw_batches = [
        KBEAUTY_KEYWORDS[i:i + BATCH_SIZE]
        for i in range(0, len(KBEAUTY_KEYWORDS), BATCH_SIZE)
    ]
    total_kw_batches = len(kw_batches)
    print(f"\n[Phase 2] Keywords: {len(KBEAUTY_KEYWORDS)} queries in {total_kw_batches} batches")

    for batch_idx, batch in enumerate(kw_batches, 1):
        print(f"\n  [Keyword Batch {batch_idx}/{total_kw_batches}] Launching {len(batch)} runs...")
        run_queue = []
        for kw in batch:
            try:
                run_id = start_keyword_run(kw)
                run_queue.append((run_id, f'"{kw}"', f"kw:{kw}"))
            except Exception as e:
                print(f"  WARNING: Could not start keyword run for '{kw}': {e}")
        _collect_batch(run_queue, all_videos)
        if batch_idx < total_kw_batches:
            print(f"  Waiting 10s before next batch...")
            time.sleep(10)

    print(f"\n  Keyword phase done: {len(all_videos)} unique videos total")
    return list(all_videos.values())


# ── Digest formatting ────────────────────────────────────────────────────────

def fmt_num(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def build_digest(videos: list[dict]) -> str:
    today = datetime.now().strftime("%B %d, %Y")
    lines = []

    lines += [
        f"# K-Beauty TikTok Daily Digest",
        f"### {today}",
        f"Total unique videos analyzed: {len(videos)}",
        "",
    ]

    # ── Top viral videos (by raw views) ─────────────────────────────────────
    top_by_views = sorted(videos, key=lambda v: v["stats"]["views"], reverse=True)[:TOP_N_VIDEOS]
    lines += ["---", "## TOP VIRAL VIDEOS (by views)", ""]
    for i, v in enumerate(top_by_views, 1):
        s = v["stats"]
        c = v["creator"]
        tags = " ".join(f"#{t}" for t in v["hashtags"][:5])
        lines += [
            f"### {i}. @{c['username']} ({fmt_num(c['followers'])} followers)",
            f"**Views:** {fmt_num(s['views'])}  |  **Likes:** {fmt_num(s['likes'])}  |  "
            f"**Comments:** {fmt_num(s['comments'])}  |  **Shares:** {fmt_num(s['shares'])}",
            f"**Caption:** {v['caption']}",
            f"**Tags:** {tags}",
            f"**URL:** {v['url']}",
            f"**Found via:** #{v['source_tag']}",
            "",
        ]

    # ── Top creators (aggregate engagement across their videos) ──────────────
    creator_map: dict[str, dict] = {}
    for v in videos:
        c = v["creator"]
        u = c["username"]
        if u not in creator_map:
            creator_map[u] = {**c, "total_views": 0, "total_engagement": 0, "video_count": 0, "tags": set()}
        creator_map[u]["total_views"]      += v["stats"]["views"]
        creator_map[u]["total_engagement"] += v["engagement"]
        creator_map[u]["video_count"]      += 1
        creator_map[u]["tags"].update(v["hashtags"])

    top_creators = sorted(creator_map.values(), key=lambda c: c["total_views"], reverse=True)[:TOP_N_CREATORS]

    lines += ["---", "## TOP CREATORS TO FOLLOW", ""]
    for i, c in enumerate(top_creators, 1):
        verified = " [V]" if c["verified"] else ""
        tags = " ".join(f"#{t}" for t in list(c["tags"])[:4])
        lines += [
            f"**{i}. @{c['username']}{verified}** — {fmt_num(c['followers'])} followers",
            f"   Views from scraped videos: {fmt_num(c['total_views'])}  |  "
            f"Videos in dataset: {c['video_count']}",
            f"   Common tags: {tags}",
            f"   Profile: {c['url']}",
            "",
        ]

    # ── Trending hashtags ────────────────────────────────────────────────────
    tag_counts: dict[str, int] = defaultdict(int)
    tag_views:  dict[str, int] = defaultdict(int)
    for v in videos:
        for t in v["hashtags"]:
            if t:
                tag_counts[t] += 1
                tag_views[t]  += v["stats"]["views"]

    # Remove the source hashtags themselves to surface co-occurring tags
    for base in KBEAUTY_HASHTAGS:
        tag_counts.pop(base, None)

    top_tags = sorted(tag_counts.items(), key=lambda x: tag_views[x[0]], reverse=True)[:30]

    lines += ["---", "## TRENDING CO-OCCURRING HASHTAGS", ""]
    lines.append("| Hashtag | Videos | Total Views |")
    lines.append("|---------|--------|-------------|")
    for tag, count in top_tags:
        lines.append(f"| #{tag} | {count} | {fmt_num(tag_views[tag])} |")

    # ── Trending audio ────────────────────────────────────────────────────────
    music_map: dict[str, dict] = defaultdict(lambda: {"count": 0, "views": 0})
    for v in videos:
        m = v["music"]
        if m["title"] and not m["original"]:
            key = f"{m['title']} — {m['artist']}"
            music_map[key]["count"]  += 1
            music_map[key]["views"]  += v["stats"]["views"]

    top_music = sorted(music_map.items(), key=lambda x: x[1]["views"], reverse=True)[:10]

    lines += ["", "---", "## TRENDING AUDIO / SOUNDS", ""]
    lines.append("| Track | Used in | Total Views |")
    lines.append("|-------|---------|-------------|")
    for track, data in top_music:
        lines.append(f"| {track} | {data['count']} videos | {fmt_num(data['views'])} |")

    lines += ["", "---", f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}*"]
    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("K-Beauty TikTok Daily Research Tool")
    print("=" * 45)

    videos = scrape_all()

    if not videos:
        print("\nNo videos collected. Check your Apify token and actor ID.")
        return

    print(f"\n[3/3] Building digest from {len(videos)} unique videos...")
    digest = build_digest(videos)

    date_str  = datetime.now().strftime("%Y-%m-%d")
    out_path  = os.path.join(OUTPUT_DIR, f"digest_{date_str}.md")
    json_path = os.path.join(OUTPUT_DIR, f"data_{date_str}.json")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(digest)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(videos, f, ensure_ascii=False, indent=2)

    print(f"\nDone!")
    print(f"  Digest : {out_path}")
    print(f"  Raw data: {json_path}")
    print(f"\n--- PREVIEW ---\n")
    print("\n".join(digest.split("\n")[:40]))


if __name__ == "__main__":
    main()
