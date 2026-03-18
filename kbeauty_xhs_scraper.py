#!/usr/bin/env python3
"""
XHS (小红书 / RedNote) K-Beauty Scraper — Persistent Session 버전
브라우저 프로필을 로컬에 저장 → 한 번 로그인 후 매일 자동 수집.

사용법:
  1. python3 kbeauty_xhs_scraper.py
     → 처음 실행 시 브라우저가 열림 → XHS 로그인 → Enter 입력
     → 이후 실행부터 저장된 세션 자동 재사용
  2. xhs_data_YYYY-MM-DD.json 파일 생성됨
  3. git add xhs_data_*.json && git push
"""

import os, json, re, time, urllib.parse, threading
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(SCRIPT_DIR, ".xhs_browser_profile")

KEYWORD_CATEGORIES = {
    # 스킨케어
    "韩国护肤":         "스킨케어",
    "韩国精华":         "스킨케어",
    "韩国水乳":         "스킨케어",
    "韩国防晒":         "스킨케어",
    "韩国面膜":         "스킨케어",
    "cosrx":            "스킨케어",
    "beauty of joseon": "스킨케어",
    "anua":             "스킨케어",
    "skin1004":         "스킨케어",
    "laneige":          "스킨케어",
    # 메이크업
    "韩国彩妆":         "메이크업",
    "韩系妆容":         "메이크업",
    "韩国口红":         "메이크업",
    "romand":           "메이크업",
    "3ce":              "메이크업",
    "peripera":         "메이크업",
    # 헤어케어
    "韩国护发":         "헤어케어",
    "韩国洗发水":       "헤어케어",
    # 종합
    "oliveyoung":       "종합",
    "k-beauty":         "종합",
    "韩国美妆":         "종합",
    "韩国素颜霜":       "종합",
}

PAGE_SIZE = 20


def parse_likes(val):
    if not val:
        return 0
    s = str(val).replace(",", "").strip()
    m = re.match(r"([\d.]+)([万千kKwW]?)", s)
    if not m:
        return 0
    n = float(m.group(1))
    u = m.group(2).lower()
    if u in ("万", "w"):
        n *= 10000
    elif u in ("千", "k"):
        n *= 1000
    return int(n)


def check_login(ctx):
    """로그인 상태 확인 — edith API user/me로 확인."""
    result = {}
    page = ctx.new_page()
    page.on("dialog", lambda d: d.dismiss())

    def capture(resp):
        if "user/me" in resp.url:
            try:
                result["data"] = resp.json()
            except Exception:
                pass

    page.on("response", capture)
    page.goto("https://www.xiaohongshu.com", wait_until="domcontentloaded", timeout=30000)
    time.sleep(3)
    page.close()

    d = result.get("data") or {}
    inner = d.get("data") or {} if d.get("success") else {}
    nickname = inner.get("nickname", "") if isinstance(inner, dict) else ""
    return bool(nickname), nickname


def search_notes(ctx, keyword, category, seen_ids):
    """검색 결과 페이지 열기 → XHS가 자체 호출하는 API 응답 인터셉트."""
    page = ctx.new_page()
    page.on("dialog", lambda d: d.dismiss())

    captured = []
    done = threading.Event()

    def capture(resp):
        url = resp.url
        # edith 또는 www 도메인의 search/notes, homefeed 응답 캡처
        if ("search/notes" in url or "homefeed" in url) and resp.status == 200:
            try:
                data = resp.json()
                if data.get("success"):
                    items = (data.get("data") or {}).get("items") or []
                    if items:
                        captured.extend(items)
                        done.set()
            except Exception:
                pass

    page.on("response", capture)

    search_url = (
        "https://www.xiaohongshu.com/search_result"
        f"?keyword={urllib.parse.quote(keyword)}&type=note"
    )
    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
    except Exception:
        pass

    # 최대 8초 대기
    done.wait(timeout=8)
    page.close()

    items = []
    for note in captured:
        ni  = note.get("note_card") or {}
        nid = note.get("id", "")
        if not nid or nid in seen_ids:
            continue
        seen_ids.add(nid)

        xsec  = note.get("xsec_token", "")
        url   = f"https://www.xiaohongshu.com/explore/{nid}?xsec_token={xsec}&xsec_source=pc_search"
        cover_o = ni.get("cover") or {}
        cover   = cover_o.get("url_default") or cover_o.get("url_pre") or ""
        user    = ni.get("user") or {}
        inter   = ni.get("interact_info") or {}
        likes   = parse_likes(inter.get("liked_count") or 0)
        cmts    = int(inter.get("comment_count") or 0)
        rtype   = ni.get("type", "normal")
        items.append({
            "id":         nid,
            "url":        url,
            "title":      ni.get("display_title") or ni.get("title") or "",
            "type":       "video" if rtype == "video" else "image",
            "cover":      cover,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "source_tag": keyword,
            "category":   category,
            "creator": {
                "userId":   user.get("user_id", ""),
                "username": user.get("nickname") or user.get("nick_name") or "",
                "avatar":   user.get("avatar", ""),
            },
            "stats": {"likes": likes, "comments": cmts},
        })
    return items


def main():
    from playwright.sync_api import sync_playwright

    date_str = datetime.now().strftime("%Y-%m-%d")
    out_path = os.path.join(SCRIPT_DIR, f"xhs_data_{date_str}.json")

    all_items = []
    seen_ids  = set()

    os.makedirs(PROFILE_DIR, exist_ok=True)

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=False,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
        )

        # 로그인 확인
        print("로그인 상태 확인 중...")
        logged_in, nickname = check_login(ctx)

        if not logged_in:
            print("\n⚠️  XHS 로그인이 필요합니다.")
            print("   열린 브라우저에서 xiaohongshu.com에 로그인하세요.")
            print("   로그인 완료 후 여기서 Enter를 누르세요...")
            # 브라우저에 XHS 열기
            p = ctx.new_page()
            p.goto("https://www.xiaohongshu.com", wait_until="domcontentloaded", timeout=30000)
            input()
            p.close()
            # 다시 확인
            logged_in, nickname = check_login(ctx)
            if not logged_in:
                print("❌ 로그인 확인 실패. 다시 실행해주세요.")
                ctx.close()
                return

        print(f"✅ 로그인 확인: {nickname}")

        for keyword, category in KEYWORD_CATEGORIES.items():
            print(f"  [{keyword}] 검색 중...")
            try:
                items = search_notes(ctx, keyword, category, seen_ids)
                all_items.extend(items)
                print(f"    → {len(items)}개 추가 (누적: {len(all_items)})")
            except Exception as e:
                print(f"    오류: {e}")
            time.sleep(1.5)

        ctx.close()

    # 좋아요순 정렬
    all_items.sort(key=lambda x: x["stats"]["likes"], reverse=True)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_items, f, ensure_ascii=False, indent=2)

    print(f"\n✅ {len(all_items)}개 XHS 포스트 저장 → {out_path}")
    print("다음: git add xhs_data_*.json && git push")


if __name__ == "__main__":
    main()
