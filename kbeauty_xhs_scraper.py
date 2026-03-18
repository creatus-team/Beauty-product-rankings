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
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
XHS_COOKIE  = os.getenv("XHS_COOKIE", "")
XHS_ACTOR_ID = "9qkezGwljt2uc4DY9"  # easyapi/rednote-xiaohongshu-search-scraper
BASE = "https://api.apify.com/v2"

# 키워드 → 카테고리 매핑 (뷰티 특화)
KEYWORD_CATEGORIES = {
    # 스킨케어
    "韩国护肤":     "스킨케어",
    "韩国精华":     "스킨케어",
    "韩国水乳":     "스킨케어",   # Korean toner+lotion
    "韩国防晒":     "스킨케어",   # Korean sunscreen
    "韩国面膜":     "스킨케어",   # Korean sheet mask
    "cosrx":        "스킨케어",
    "beauty of joseon": "스킨케어",
    "anua":         "스킨케어",
    "skin1004":     "스킨케어",
    "laneige":      "스킨케어",
    # 메이크업
    "韩国彩妆":     "메이크업",
    "韩系妆容":     "메이크업",   # Korean-style makeup
    "韩国口红":     "메이크업",   # Korean lipstick
    "韩国粉底":     "메이크업",   # Korean foundation
    "romand":       "메이크업",
    "3ce":          "메이크업",
    "peripera":     "메이크업",
    # 헤어케어
    "韩国护发":     "헤어케어",   # Korean hair care
    "韩国洗发水":   "헤어케어",   # Korean shampoo
    # 종합/바이럴
    "oliveyoung":   "종합",
    "小韩护肤":     "종합",       # Korean-style skincare (slang)
    "k-beauty":     "종합",
    "韩国美妆":     "종합",       # Korean beauty
    "韩国素颜霜":   "종합",       # Korean no-makeup cream
}

MAX_ITEMS_PER_KEYWORD = 30


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
    print(f"  [{keyword}] 실행 중...")
    r = requests.post(
        f"{BASE}/acts/{XHS_ACTOR_ID}/runs",
        params={"token": APIFY_TOKEN},
        json={"keyword": keyword, "maxItems": MAX_ITEMS_PER_KEYWORD, **({"cookie": XHS_COOKIE} if XHS_COOKIE else {})},
        timeout=30
    )
    r.raise_for_status()
    data = r.json().get("data", {})
    run_id = data.get("id")
    if not run_id:
        print(f"    오류: run ID 없음 → {r.text[:200]}")
        return []
    print(f"    Run ID: {run_id} — 완료 대기 중...")
    status = "RUNNING"
    status_r = None
    for _ in range(60):
        time.sleep(5)
        status_r = requests.get(f"{BASE}/actor-runs/{run_id}", params={"token": APIFY_TOKEN}, timeout=15)
        status = status_r.json()["data"]["status"]
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            break
    print(f"    상태: {status}")
    if status != "SUCCEEDED":
        return []
    dataset_id = status_r.json()["data"]["defaultDatasetId"]
    items_r = requests.get(
        f"{BASE}/datasets/{dataset_id}/items",
        params={"token": APIFY_TOKEN, "limit": MAX_ITEMS_PER_KEYWORD},
        timeout=30
    )
    return items_r.json()


def normalize(raw_item, source_tag, category):
    """Apify XHS 출력을 표준 포맷으로 변환.
    두 가지 포맷 지원:
      - 신형: {"item": {"id":..., "note_card":{...}}, "link":..., "scrapedAt":...}
      - 구형: {"postData": {...}} 또는 flat
    """
    # 신형 포맷 (쿠키 인증 후 반환되는 구조)
    item_obj  = raw_item.get("item") or {}
    note_card = item_obj.get("note_card") or {}
    if item_obj and note_card:
        note_id  = item_obj.get("id", "")
        post_url = raw_item.get("link", "")
        if not post_url and note_id:
            xsec = item_obj.get("xsec_token", "")
            post_url = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={xsec}"

        user_obj   = note_card.get("user") or {}
        interact   = note_card.get("interact_info") or {}
        cover_obj  = note_card.get("cover") or {}
        cover_url  = cover_obj.get("url_default") or cover_obj.get("url_pre", "")
        likes_raw  = interact.get("liked_count", 0)
        title      = note_card.get("display_title", "")
        # note_card.type: "normal"(image) | "video"
        raw_type   = note_card.get("type", "normal")
        post_type  = "video" if raw_type == "video" else "image"
        scraped_at = raw_item.get("scrapedAt") or datetime.utcnow().isoformat() + "Z"
        comments   = int(interact.get("comment_count", 0) or 0)
        return {
            "id":         note_id,
            "url":        post_url,
            "title":      title,
            "type":       post_type,
            "cover":      cover_url,
            "created_at": scraped_at,
            "source_tag": source_tag,
            "category":   category,
            "creator": {
                "userId":   user_obj.get("user_id", ""),
                "username": user_obj.get("nickname") or user_obj.get("nick_name", ""),
                "avatar":   user_obj.get("avatar", ""),
            },
            "stats": {
                "likes":    parse_likes(likes_raw),
                "comments": comments,
            },
        }

    # 구형/기타 포맷 fallback
    pd = raw_item.get("postData") or raw_item
    note_id  = pd.get("noteId") or pd.get("id") or raw_item.get("noteId", "")
    post_url = pd.get("postUrl") or pd.get("url") or raw_item.get("postUrl", "")
    if not post_url and note_id:
        xsec = pd.get("xsecToken", "")
        post_url = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={xsec}"
    user       = pd.get("user") or raw_item.get("user") or {}
    interact   = pd.get("interactInfo") or raw_item.get("interactInfo") or {}
    cover_obj  = pd.get("cover") or raw_item.get("cover") or {}
    cover_url  = ""
    if isinstance(cover_obj, dict):
        cover_url = (
            cover_obj.get("urlDefault") or cover_obj.get("urlPre") or
            ((cover_obj.get("infoList") or [{}])[0].get("url", ""))
        )
    likes_raw  = interact.get("likedCount") or raw_item.get("likes") or raw_item.get("likedCount") or 0
    title      = pd.get("displayTitle") or raw_item.get("displayTitle") or raw_item.get("title") or ""
    post_type  = pd.get("type") or raw_item.get("type") or "image"
    scraped_at = raw_item.get("scrapedAt") or datetime.utcnow().isoformat() + "Z"
    return {
        "id":         note_id,
        "url":        post_url,
        "title":      title,
        "type":       post_type,
        "cover":      cover_url,
        "created_at": scraped_at,
        "source_tag": source_tag,
        "category":   category,
        "creator": {
            "userId":   user.get("userId", ""),
            "username": user.get("nickName") or user.get("nickname", ""),
            "avatar":   user.get("avatar", ""),
        },
        "stats": {
            "likes":    parse_likes(likes_raw),
            "comments": int(raw_item.get("comments", 0) or 0),
        },
    }


def main():
    if not APIFY_TOKEN:
        print("❌ APIFY_TOKEN 없음. .env 파일에 APIFY_TOKEN=... 추가 후 재실행하세요.")
        return

    date_str = datetime.now().strftime("%Y-%m-%d")
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"xhs_data_{date_str}.json")

    all_items = []
    seen_ids  = set()

    for kw, cat in KEYWORD_CATEGORIES.items():
        try:
            raw   = run_actor(kw)
            count = 0
            for item in raw:
                norm = normalize(item, kw, cat)
                if norm["id"] and norm["id"] not in seen_ids:
                    seen_ids.add(norm["id"])
                    all_items.append(norm)
                    count += 1
            print(f"    → {count}개 추가 (누적: {len(all_items)})\n")
        except Exception as e:
            print(f"    오류 '{kw}': {e}\n")

    # 좋아요순 정렬
    all_items.sort(key=lambda x: x["stats"]["likes"], reverse=True)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_items, f, ensure_ascii=False, indent=2)

    print(f"✅ {len(all_items)}개 XHS 포스트 저장 → {out_path}")
    print("다음: git add xhs_data_*.json && git commit -m 'Add XHS data' && git push")


if __name__ == "__main__":
    main()
