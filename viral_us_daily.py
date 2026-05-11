#!/usr/bin/env python3
"""
US Viral Beauty TikTok Daily Scraper
미국 틱톡 뷰티 카테고리 7일 내 잘된 영상 수집 → 따라찍기 카탈로그 생성
"""

import requests
import time
import json
import os
from datetime import datetime, timezone, timedelta

# ── Config ──────────────────────────────────────────────────────────────────
APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
ACTOR_ID    = "GdWCkxBtKWOsKjdch"
BASE_URL    = "https://api.apify.com/v2"

LOOKBACK_DAYS = 7              # 최근 7일 영상만
MIN_VIEWS     = 50_000         # 5만 뷰 이상만 (Underdog 잡기 위해 적절)
RESULTS_PER_HASHTAG = 200   # latest 정렬은 신선도 보장하지만 viral 보장 X → 더 많이 받아 sift
RESULTS_PER_KEYWORD = 200
TOP_N_VIDEOS  = 100            # 디지스트에 보일 영상 수 (조회수 순)

# ── 해시태그 12개 (K-direct + K-brand only) ──────────────────────────────
US_BEAUTY_HASHTAGS = [
    # K-direct
    "koreanskincare",
    "kbeauty",
    "glassskin",
    "porelessskin",
    "snailmucin",
    "centellaskincare",
    "skinbarrierrepair",
    # K-brand 직타 (US TikTok 폭발 중)
    "beautyofjoseon",
    "anua",
    "medicube",
    "cosrx",
    "skin1004",
]

# ── 키워드 8개 (K-focused only) ──────────────────────────────────────────
US_BEAUTY_KEYWORDS = [
    "korean skincare routine",
    "rice toner routine",
    "korean glow up",
    "beauty of joseon",
    "anua heartleaf",
    "snail mucin essence",
    "korean glass skin",
    "k-beauty review",
]

# ── K-시그널 안전망 (caption/hashtag 에 1개라도 있어야 통과) ─────────────
K_SIGNALS = [
    "korean", "kbeauty", "k-beauty", "k beauty",
    "glass skin", "glassskin", "snail mucin", "snailmucin",
    "centella", "cica", "rice toner", "ricetoner", "heartleaf",
    "beauty of joseon", "beautyofjoseon", "anua", "medicube",
    "cosrx", "skin1004", "laneige", "sulwhasoo", "innisfree",
    "etude", "missha", "torriden", "klairs", "round lab", "roundlab",
    "mediheal", "some by mi", "somebymi", "dr.melaxin", "drmelaxin",
    "korea", "seoul",
]

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


# ── Apify helpers ────────────────────────────────────────────────────────────

def _oldest_post_date() -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    return cutoff.strftime("%Y-%m-%d")


def start_run(hashtag: str) -> str:
    url = f"{BASE_URL}/acts/{ACTOR_ID}/runs?token={APIFY_TOKEN}"
    payload = {
        "hashtags": [hashtag],
        "resultsPerPage": RESULTS_PER_HASHTAG,
        "oldestPostDate": _oldest_post_date(),
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
    url = f"{BASE_URL}/acts/{ACTOR_ID}/runs?token={APIFY_TOKEN}"
    payload = {
        "searchKeywords": [keyword],
        "resultsPerPage": RESULTS_PER_KEYWORD,
        "oldestPostDate": _oldest_post_date(),
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
        "shouldDownloadAvatars": False,
        "shouldDownloadMusicCovers": False,
        "shouldDownloadSubtitles": "NEVER_DOWNLOAD_SUBTITLES",
        "sortingType": "latest",
    }
    r = requests.post(url, json=payload, timeout=30)
    r.raise_for_status()
    run_id = r.json()["data"]["id"]
    print(f"  Started run {run_id} for keyword '{keyword}'")
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
    raise TimeoutError(f"Run {run_id} did not finish within {timeout}s")


def fetch_dataset(dataset_id: str) -> list[dict]:
    url = f"{BASE_URL}/datasets/{dataset_id}/items?token={APIFY_TOKEN}&format=json&limit=1000"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


# ── Data processing (영어/미국만 필터) ────────────────────────────────────

def normalize_video(item: dict, source_tag: str, region_filter: bool = True) -> dict | None:
    """영어 영상만, USA/UK 지역만 (region_filter=True일 때) 통과, 7일 이내 영상만."""
    try:
        # 0a. 광고/스폰서 영상 제외 — organic만
        if item.get("isAd") or item.get("isSponsored"):
            return None

        # 0b. 날짜 필터: Apify가 oldestPostDate 무시하는 경우가 있어 코드에서 강제
        created = item.get("createTimeISO", "") or ""
        if not created:
            return None
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - dt).days > LOOKBACK_DAYS:
                return None
        except Exception:
            return None

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

        # A. 언어 필터: 영어 아니면 제외
        if lang and lang != "en":
            return None

        # A2. K-시그널 안전망: caption + hashtag 에 K-키워드 1개라도 없으면 제외
        text_blob = ((item.get("text", "") or "") + " " + " ".join(hashtags)).lower()
        if not any(sig in text_blob for sig in K_SIGNALS):
            return None

        # B. 지역 필터 (옵션): hashtag로 비-영미권 지역 시그널 있으면 제외
        if region_filter:
            non_us_hints = {"indonesia","thailand","vietnam","philippines","singapore",
                            "malaysia","taiwan","korea","korean","china","chinese",
                            "brazil","brasil","mexico","spain","uae","dubai","japan","japanese"}
            if any(t.lower() in non_us_hints for t in hashtags):
                return None

        return {
            "id":         item.get("id", ""),
            "url":        item.get("webVideoUrl", ""),
            "caption":    (item.get("text", "") or "")[:200],
            "created_at": item.get("createTimeISO", ""),
            "duration":   video_meta.get("duration", 0),
            "cover":      video_meta.get("coverUrl", "") or video_meta.get("originalCoverUrl", ""),
            "hashtags":   hashtags,
            "language":   lang,
            "source_tag": source_tag,
            "stats":      stats,
            "creator": {
                "username":  author.get("name", "unknown"),
                "nickname":  author.get("nickName", ""),
                "followers": author.get("fans", 0) or 0,
                "verified":  author.get("verified", False),
                "url":       f"https://www.tiktok.com/@{author.get('name', '')}",
                "avatar":    author.get("avatar", "") or author.get("originalAvatarUrl", ""),
            },
            "music": {
                "title":    music.get("musicName", ""),
                "artist":   music.get("musicAuthor", ""),
                "original": music.get("musicOriginal", False),
            },
        }
    except Exception:
        return None


BATCH_SIZE = 2


def _collect_batch(run_queue, all_videos: dict, region_filter: bool) -> None:
    """MIN_VIEWS 필터는 main() 에서 fallback 과 함께 적용 — 여기선 lang/region/K-필터만."""
    for run_id, label, source_tag in run_queue:
        try:
            print(f"  Waiting on {label} ({run_id})...")
            run_data   = wait_for_run(run_id)
            dataset_id = run_data["defaultDatasetId"]
            items      = fetch_dataset(dataset_id)
            added, skipped_filter = 0, 0
            for item in items:
                v = normalize_video(item, source_tag, region_filter=region_filter)
                if not v:
                    skipped_filter += 1
                    continue
                if not v["id"]:
                    continue
                vid_id = v["id"]
                if vid_id not in all_videos or v["stats"]["views"] > all_videos[vid_id]["stats"]["views"]:
                    all_videos[vid_id] = v
                    added += 1
            print(f"  {label}: {len(items)} fetched, {added} kept, "
                  f"{skipped_filter} skip-lang/region/K")
        except Exception as e:
            print(f"  WARNING: Failed to collect {label}: {e}")


def scrape_all(region_filter: bool = True) -> list[dict]:
    all_videos: dict[str, dict] = {}

    # Phase 1: Hashtags
    tag_batches = [US_BEAUTY_HASHTAGS[i:i+BATCH_SIZE] for i in range(0, len(US_BEAUTY_HASHTAGS), BATCH_SIZE)]
    print(f"\n[Phase 1] Hashtags: {len(US_BEAUTY_HASHTAGS)} tags in {len(tag_batches)} batches")
    for bi, batch in enumerate(tag_batches, 1):
        print(f"\n  [Hashtag Batch {bi}/{len(tag_batches)}]")
        run_queue = []
        for tag in batch:
            try:
                run_id = start_run(tag)
                run_queue.append((run_id, f"#{tag}", tag))
            except Exception as e:
                print(f"  WARNING: Could not start #{tag}: {e}")
        _collect_batch(run_queue, all_videos, region_filter)
        if bi < len(tag_batches):
            time.sleep(10)
    print(f"\n  Hashtag phase done: {len(all_videos)} unique videos")

    # Phase 2: Keywords
    kw_batches = [US_BEAUTY_KEYWORDS[i:i+BATCH_SIZE] for i in range(0, len(US_BEAUTY_KEYWORDS), BATCH_SIZE)]
    print(f"\n[Phase 2] Keywords: {len(US_BEAUTY_KEYWORDS)} queries in {len(kw_batches)} batches")
    for bi, batch in enumerate(kw_batches, 1):
        print(f"\n  [Keyword Batch {bi}/{len(kw_batches)}]")
        run_queue = []
        for kw in batch:
            try:
                run_id = start_keyword_run(kw)
                run_queue.append((run_id, f'"{kw}"', f"kw:{kw}"))
            except Exception as e:
                print(f"  WARNING: Could not start '{kw}': {e}")
        _collect_batch(run_queue, all_videos, region_filter)
        if bi < len(kw_batches):
            time.sleep(10)
    print(f"\n  Keyword phase done: {len(all_videos)} unique videos total")

    # B. 자동 폴백: 결과가 너무 적으면 region 필터 OFF로 재실행
    if region_filter and len(all_videos) < 50:
        print(f"\n  [FALLBACK] Only {len(all_videos)} videos with region filter — retrying without it")
        return scrape_all(region_filter=False)

    return list(all_videos.values())


# ── Digest (단순 스크롤 카탈로그) ─────────────────────────────────────────

def fmt_num(n: int) -> str:
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000:     return f"{n/1_000:.1f}K"
    return str(n)


def days_ago(iso_str: str) -> str:
    if not iso_str:
        return "?"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z","+00:00"))
        delta = datetime.now(timezone.utc) - dt
        d = delta.days
        if d == 0:
            h = delta.seconds // 3600
            return f"{h}시간 전" if h > 0 else "방금"
        return f"{d}일 전"
    except Exception:
        return "?"


def build_digest(videos: list[dict]) -> str:
    today = datetime.now().strftime("%B %d, %Y")
    sorted_videos = sorted(videos, key=lambda v: v["stats"]["views"], reverse=True)[:TOP_N_VIDEOS]

    lines = [
        f"# 🇺🇸 미국 틱톡 뷰티 바이럴 카탈로그",
        f"### {today}",
        f"7일 내 영어/미국 영상 · 10만뷰 이상 · TOP {len(sorted_videos)}",
        "",
        "---",
        "",
    ]

    for i, v in enumerate(sorted_videos, 1):
        s = v["stats"]
        c = v["creator"]
        m = v["music"]
        first_line = (v["caption"].split("\n")[0] if v["caption"] else "").strip()[:120]
        tags = " ".join(f"`#{t}`" for t in v["hashtags"][:5])
        sound = f"{m['title']} — {m['artist']}" if m["title"] else "(original sound)"

        lines += [
            f"### {i}. @{c['username']} · {days_ago(v['created_at'])}",
            f"![]({v['cover']})" if v['cover'] else "",
            f"**[▶ TikTok에서 보기]({v['url']})**",
            f"👁 **{fmt_num(s['views'])}** · ❤️ {fmt_num(s['likes'])} · 💬 {fmt_num(s['comments'])} · 🔖 {fmt_num(s['saves'])}",
            f"💬 {first_line}" if first_line else "",
            f"🎵 {sound}",
            tags if tags else "",
            "",
            "---",
            "",
        ]

    lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M KST')}*")
    return "\n".join(line for line in lines if line is not None)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("US Viral Beauty TikTok Daily Scraper")
    print("=" * 45)

    if not APIFY_TOKEN:
        print("ERROR: APIFY_TOKEN not set")
        return

    # region_filter=False 로 시작 — fallback 시 비용 2배 나는 문제 회피
    # 영어 필터만으로도 USA/UK 영상 거의 다 잡힘 (지역 필터 너무 엄격해서 50개 못 채우고 재실행함)
    all_candidates = scrape_all(region_filter=False)

    if not all_candidates:
        print("\nNo videos collected at all (Apify 모두 실패).")
        return

    # ── MIN_VIEWS 자동 fallback: 통과량 < 30 이면 임계 낮춤 ───────────
    # GH Actions runner IP 차단으로 일부 runs 실패하면 후보 적음 → 무조건 결과 저장 보장
    TARGET_MIN = 30
    THRESHOLDS = [MIN_VIEWS, 30_000, 20_000, 10_000, 5_000, 0]
    videos = []
    chosen_threshold = MIN_VIEWS
    for thresh in THRESHOLDS:
        videos = [v for v in all_candidates if v["stats"]["views"] >= thresh]
        if len(videos) >= TARGET_MIN or thresh == 0:
            chosen_threshold = thresh
            break
        print(f"  [Fallback] MIN_VIEWS={thresh:,} → {len(videos)} 개 (목표 {TARGET_MIN}+), 임계 더 낮춤")

    print(f"\n[Final] Using MIN_VIEWS={chosen_threshold:,}: {len(videos)} videos")
    digest = build_digest(videos)

    date_str  = datetime.now().strftime("%Y-%m-%d")
    md_path   = os.path.join(OUTPUT_DIR, f"viral_us_beauty_{date_str}.md")
    json_path = os.path.join(OUTPUT_DIR, f"viral_us_data_{date_str}.json")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(digest)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(videos, f, ensure_ascii=False, indent=2)

    print(f"\nDone!")
    print(f"  Digest: {md_path}")
    print(f"  Data:   {json_path}")


if __name__ == "__main__":
    main()
