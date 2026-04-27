#!/usr/bin/env python3
"""
Amazon Beauty Rankings Dashboard
- 나라별 아마존 뷰티/이너뷰티 베스트셀러 랭킹 대시보드
- Run: python3 amazon_rankings.py
"""

from flask import Flask, jsonify, render_template_string, request, make_response
import json, os, requests, glob, subprocess, sys
from datetime import datetime
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")

ACTOR_RUNS = {
    "us": "FUrLIRxfa5zNube8L",   # US Beauty (amazon.com)
    "uk": "z08tLogf8r7pc4B9m",   # UK Beauty (amazon.co.uk)
    "jp": "2uOgD8xqcPAbGFweJ",   # JP Beauty (amazon.co.jp)
}

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Vercel 서버리스: 쓰기 가능한 /tmp 사용, 로컬: 스크립트 디렉토리
CACHE_FILE = "/tmp/amazon_cache.json" if os.getenv("VERCEL") else os.path.join(_SCRIPT_DIR, "amazon_cache.json")
BUNDLED_CACHE = os.path.join(_SCRIPT_DIR, "amazon_cache.json")  # 배포에 포함된 읽기전용 캐시

DOMAIN_MAP = {
    "amazon.com":    {"code": "US", "flag": "🇺🇸", "name": "United States"},
    "amazon.co.uk":  {"code": "UK", "flag": "🇬🇧", "name": "United Kingdom"},
    "amazon.co.jp":  {"code": "JP", "flag": "🇯🇵", "name": "Japan"},
    "amazon.de":     {"code": "DE", "flag": "🇩🇪", "name": "Germany"},
    "amazon.fr":     {"code": "FR", "flag": "🇫🇷", "name": "France"},
    "amazon.ca":     {"code": "CA", "flag": "🇨🇦", "name": "Canada"},
    "amazon.com.au": {"code": "AU", "flag": "🇦🇺", "name": "Australia"},
    "amazon.it":     {"code": "IT", "flag": "🇮🇹", "name": "Italy"},
    "amazon.es":     {"code": "ES", "flag": "🇪🇸", "name": "Spain"},
}

YESSTYLE_CATEGORIES = {
    "All Beauty":  "https://www.yesstyle.com/en/beauty-beauty/list.html/bcc.15478_bpt.46?sb=136",
    "Skin Care":   "https://www.yesstyle.com/en/beauty-skin-care/list.html/bcc.15544_bpt.46?sb=136",
    "Makeup":      "https://www.yesstyle.com/en/beauty-makeup/list.html/bcc.15479_bpt.46?sb=136",
}

# OliveYoung Global bestseller endpoints
OY_ORDER_BEST_URL = "https://global.oliveyoung.com/display/product/best-seller/order-best"
OY_KOREA_BEST_URL = "https://product-ranking-service.oliveyoung.com/v1/pages/ranking/sales/products"
OY_KOREA_PARAMS = {"category-id": "1000000001", "region": "KR", "language-code": "en",
                   "margin-country-code": "9999", "delivery-country-code": "1230"}
OY_IMG_BASE = "https://cdn-image.oliveyoung.com/"
OY_PRODUCT_BASE = "https://global.oliveyoung.com/product/detail?prdtNo="
OY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://global.oliveyoung.com/display/page/best-seller?target=pillsTab1Nav1",
    "Accept": "application/json, text/plain, */*",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _scrape_yesstyle_page(url, subcategory):
    import re
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"}
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', r.text, re.DOTALL)
    big = max(scripts, key=len)
    m = re.search(r'self\.__next_f\.push\(\[1,\"(.*)\"\]\)', big, re.DOTALL)
    if not m:
        return []
    raw = m.group(1).encode().decode('unicode_escape')
    idx = raw.find('"products":[{')
    if idx < 0:
        return []
    start = raw.find('[{', idx + len('"products":'))
    items, pos, rank = [], start + 1, 0
    while raw[pos] == '{':
        depth, p = 0, pos
        while pos < len(raw):
            if raw[pos] == '{': depth += 1
            elif raw[pos] == '}':
                depth -= 1
                if depth == 0: break
            pos += 1
        obj = json.loads(raw[p:pos+1])
        rank += 1
        prod = obj.get('product', {})
        img = prod.get('images', {})
        price_usd = obj.get('sellPriceInUSD')
        items.append({
            "name": f"{prod.get('brandName', '')} - {prod.get('name', '')}".strip(' -'),
            "url": "https://www.yesstyle.com" + prod.get("url", ""),
            "asin": None,
            "position": rank,
            "thumbnailUrl": img.get("m") or img.get("s") or None,
            "stars": None,
            "reviewsCount": None,
            "categoryName": subcategory,
            "categoryFullName": f"YesStyle {subcategory} Bestsellers",
            "_country_code": "YS",
            "_country_flag": "🍀",
            "_country_name": "YesStyle",
            "_ys_subcategory": subcategory,
            "_price_value": float(price_usd) if price_usd else None,
            "_price_currency": "$",
        })
        pos += 1
        if pos < len(raw) and raw[pos] == ',': pos += 1
    return items

def fetch_yesstyle():
    """YesStyle 뷰티 베스트셀러 스크래핑 (All Beauty / Skin Care / Makeup)"""
    all_items = []
    for subcat, url in YESSTYLE_CATEGORIES.items():
        try:
            items = _scrape_yesstyle_page(url, subcat)
            print(f"[YesStyle] {subcat}: {len(items)} items")
            all_items.extend(items)
        except Exception as e:
            print(f"[YesStyle] {subcat} failed: {e}")
    return all_items

def fetch_oliveyoung():
    """올리브영 글로벌 베스트셀러 (Top orders + Top in Korea)"""
    items = []
    # ── Top orders ──
    try:
        resp = requests.get(OY_ORDER_BEST_URL, headers=OY_HEADERS, timeout=20)
        resp.raise_for_status()
        for rank, p in enumerate(resp.json(), 1):
            img = p.get("imagePath")
            items.append({
                "name": p.get("prdtName", ""),
                "url": OY_PRODUCT_BASE + p.get("prdtNo", ""),
                "asin": p.get("prdtNo"),
                "position": rank,
                "thumbnailUrl": (OY_IMG_BASE + img + "?RS=400x400&QT=80") if img else None,
                "stars": float(p["avgScore"]) if p.get("avgScore") else None,
                "reviewsCount": int(p["reviewCnt"]) if p.get("reviewCnt") else None,
                "categoryName": "Top orders",
                "categoryFullName": "OliveYoung Top orders Bestsellers",
                "_country_code": "OY",
                "_country_flag": "🌿",
                "_country_name": "OliveYoung",
                "_oy_subcategory": "Top orders",
                "_price_value": float(p["saleAmt"]) if p.get("saleAmt") else None,
                "_price_currency": "$",
            })
        print(f"[OliveYoung] Top orders: {len(items)} items")
    except Exception as e:
        print(f"[OliveYoung] Top orders failed: {e}")
    # ── Top in Korea ──
    korea_items = []
    try:
        resp = requests.get(OY_KOREA_BEST_URL, params=OY_KOREA_PARAMS, headers=OY_HEADERS, timeout=20)
        resp.raise_for_status()
        products = resp.json().get("data", {}).get("pages.ranking.products", [])
        for rank, p in enumerate(products, 1):
            img = p.get("thumbnail_img_url")
            korea_items.append({
                "name": p.get("name", ""),
                "url": OY_PRODUCT_BASE + p.get("id", ""),
                "asin": p.get("id"),
                "position": rank,
                "thumbnailUrl": (OY_IMG_BASE + img + "?RS=400x400&QT=80") if img else None,
                "stars": float(p["rate"]) if p.get("rate") else None,
                "reviewsCount": None,
                "categoryName": "Top in Korea",
                "categoryFullName": "OliveYoung Top in Korea Bestsellers",
                "_country_code": "OY",
                "_country_flag": "🌿",
                "_country_name": "OliveYoung",
                "_oy_subcategory": "Top in Korea",
                "_price_value": float(p["sale_price"]) if p.get("sale_price") else None,
                "_price_currency": "$",
            })
        print(f"[OliveYoung] Top in Korea: {len(korea_items)} items")
    except Exception as e:
        print(f"[OliveYoung] Top in Korea failed: {e}")
    return items + korea_items

def _classify_qoo10(name):
    """Qoo10 제품명(일본어+영어)으로 서브카테고리 분류"""
    n = name.lower()
    # 헤어케어
    if any(k in n for k in ['シャンプー','shampoo','コンディショナー','conditioner','トリートメント','treatment','ヘアオイル','hair oil','ヘアマスク','hair mask','ヘアケア','hair care','ヘアパック','育毛','発毛','アナゲン']):
        return 'ヘアケア'
    # 메이크업
    if any(k in n for k in ['リップ','lip','アイシャドウ','eyeshadow','マスカラ','mascara','ファンデ','foundation','チーク','blush','コンシーラー','concealer','アイライナー','eyeliner','ハイライト','highlighter','カラコン','ビューラー','眉','アイブロウ','プライマー','カラーコレクター','フェイスパウダー','パウダー']):
        return 'メイクアップ'
    # 스킨케어 (뷰티 기본값)
    return 'スキンケア'

def fetch_qoo10():
    """Qoo10 Japan 뷰티 베스트셀러 (Playwright 필요, 없으면 빈 리스트)"""
    import re as _re
    try:
        from playwright.sync_api import sync_playwright
        from bs4 import BeautifulSoup
    except ImportError:
        print("[Qoo10] playwright/bs4 not installed, skipping")
        return []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page.set_extra_http_headers({"Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8"})
            page.goto("https://www.qoo10.jp/gmkt.inc/Bestsellers/?g=2", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)
            html = page.content()
            browser.close()

        soup = BeautifulSoup(html, 'html.parser')
        raw_items = soup.find_all('li', id=lambda x: x and x.startswith('g_'))
        items = []
        for li in raw_items[:200]:
            rank_el = li.find('span', class_='rank')
            rank = int(rank_el.get_text(strip=True)) if rank_el else None
            img_el = li.find('a', class_='thmb')
            img_src = img_el.find('img')['src'] if img_el and img_el.find('img') else None
            name_el = li.find('a', class_='tt')
            name = (name_el.get('title') or name_el.get_text(strip=True)) if name_el else ''
            brand_el = li.find('a', class_='txt_brand')
            brand = brand_el.get('title', '').strip() if brand_el else ''
            url_el = name_el or img_el
            product_url = (url_el.get('href', '') if url_el else '') or ''
            prc_div = li.find('div', class_='prc')
            price = None
            if prc_div:
                strong = prc_div.find('strong')
                if strong:
                    try:
                        price = float(strong.get_text(strip=True).replace('円','').replace(',',''))
                    except Exception:
                        pass
            review_el = li.find('span', class_='review_total_count')
            review_count = None
            if review_el:
                m = _re.search(r'\d[\d,]*', review_el.get_text())
                if m:
                    review_count = int(m.group().replace(',', ''))
            sold_el = li.find('div', class_='sold')
            sold = None
            if sold_el:
                em = sold_el.find('em')
                if em:
                    try:
                        sold = int(em.get_text(strip=True).replace(',', ''))
                    except Exception:
                        pass
            full_name = f"{brand} {name}".strip() if brand else name
            subcat = _classify_qoo10(full_name)
            product_id = li.get('id', '').replace('g_', '')
            items.append({
                "name": full_name,
                "url": product_url if product_url.startswith('http') else f"https://www.qoo10.jp{product_url}",
                "asin": product_id,
                "position": rank,
                "thumbnailUrl": img_src,
                "stars": None,
                "reviewsCount": review_count,
                "categoryName": subcat,
                "categoryFullName": f"Qoo10 {subcat} Bestsellers",
                "_country_code": "QJ",
                "_country_flag": "🛒",
                "_country_name": "Qoo10 Japan",
                "_qj_subcategory": subcat,
                "_price_value": price,
                "_price_currency": "¥",
                "_sold_count": sold,
            })
        print(f"[Qoo10] fetched {len(items)} beauty items")
        return items
    except Exception as e:
        print(f"[Qoo10] failed: {e}")
        return []

TT_ACTOR_ID = "ukNOBkY1TUxHNE8os"  # TikTok Shop Search Scraper
TT_DATASET_IDS = [
    "cfFWOfqsb8LqWnspO",  # korean skincare
    "06hfuMW8ncCuVM76a",  # korean skincare 2
    "ThpQiEAhPPF2Q4Xwn",  # k-beauty makeup
    "latRsoanhCFXKXN14",  # korean hair care
    "f6GFTWpOEAfEzxYYH",  # skincare serum
    "MfffGkB29amqMRhKq",  # tiktok viral beauty
    "RmCxekOiD3AkHlM6Z",  # face cream moisturizer
    "bd9cKAYhFUUbw4Z2h",  # sunscreen spf
    "rKK7EI7r6TuhrJJoH",  # lip gloss tint
    "wbWSoKdj3forJEFVp",  # vitamin c serum
    "V3Ro6n29Xk5fc70p0",  # retinol cream
    "om2T2UcdKbbLVe5av",  # hyaluronic acid
    "iL5YBhJy9EfxNYvhN",  # niacinamide toner
    "nE66cVmQxyATFekEc",  # eye cream anti-aging
    "J7sXPCc8DKm6QUKcy",  # foundation makeup
    "g2c7mn0ACMNNqcGkp",  # mascara lashes
    "nKWuFqiWUx48cglhb",  # blush bronzer
    "Q0kA3rNDWU6U3ELH6",  # setting spray
    "pgfeTfmevYSXyaZeA",  # concealer
    "Xuq7izyAYmx6iHD3b",  # hair serum
    "BYSVSKbmRXNUcUB9X",  # shampoo scalp care
    "tK5jcNSfd0XpPj1rU",  # hair mask treatment
    "5yVV94hKqIkGh7MXa",  # body lotion
    "eEY85fGhX20lgM6Db",  # exfoliating scrub
    "lRnWkQAYZYas8c9hB",  # face wash cleanser
    "VanQT0wEipYSl6sui",  # sheet mask
    "YTwj6HQ4IjLj0diDq",  # essence ampoule
    "dK7T2775xKyCWzB81",  # snail mucin
    "rbRbT9fZKIJmU9ckC",  # centella asiatica
]

def _classify_tiktok(categories):
    """TikTok Shop 카테고리 경로로 서브카테고리 분류"""
    cats = (categories or '').lower()
    if any(k in cats for k in ['hair', 'scalp', 'shampoo', 'conditioner', 'curler', 'straightener']):
        return 'Hair Care'
    if any(k in cats for k in ['makeup', 'lipstick', 'lip gloss', 'lip tint', 'lip treatment', 'foundation',
                                'blush', 'concealer', 'mascara', 'eyeliner', 'eyeshadow', 'contour',
                                'makeup brush', 'setting spray', 'bb cream', 'cc cream']):
        return 'Makeup'
    if any(k in cats for k in ['body care', 'bath', 'shower', 'body lotion', 'body scrub',
                                'deodorant', 'body wash', 'body glaze', 'body cream']):
        return 'Body Care'
    return 'Skincare'

def _parse_sale_cnt(s):
    import re as _re
    s = str(s or '').strip().replace(',', '')
    m = _re.match(r'([\d.]+)([KMkm]?)', s)
    if not m: return 0
    n = float(m.group(1))
    u = m.group(2).upper()
    if u == 'K': n *= 1000
    elif u == 'M': n *= 1000000
    return int(n)

def fetch_tiktok():
    """TikTok Shop US 뷰티 베스트셀러 – Apify 데이터셋에서 로드, 판매량 순 정렬"""
    seen_ids = set()
    items = []
    for dataset_id in TT_DATASET_IDS:
        try:
            url = (f"https://api.apify.com/v2/datasets/{dataset_id}/items"
                   f"?token={APIFY_TOKEN}&limit=100")
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            raw = resp.json()
            for p in raw:
                cats = p.get('categories', '')
                # Beauty & Personal Care 로 시작하는 것만, 가전제품 제외
                if not cats.startswith('Beauty & Personal Care'):
                    continue
                if 'Appliance' in cats:
                    continue
                pid = str(p.get('product_id') or p.get('group_id') or '')
                if not pid or pid in seen_ids:
                    continue
                seen_ids.add(pid)
                name = p.get('product_name') or p.get('highlight') or ''
                subcat = _classify_tiktok(cats)
                price_str = p.get('avg_price_fz') or p.get('avg_price') or ''
                try:
                    price_val = float(str(price_str).replace('$', '').replace(',', ''))
                except Exception:
                    price_val = None
                review_raw = p.get('review_count') or 0
                try:
                    review_val = int(str(review_raw).replace(',', '').replace('K', '000').replace('k', '000'))
                except Exception:
                    review_val = 0
                sale_raw   = str(p.get('total_sale_cnt') or '')
                sale_30d   = str(p.get('total_sale_30d_cnt') or '')
                sale_7d    = str(p.get('total_sale_7d_cnt') or '')
                cover = p.get('cover_url', '')
                # hdnet CDN은 외부 핫링크 403 차단 → 빈값 처리하여 플레이스홀더 표시
                if 'hdnet.workers.dev' in cover:
                    cover = ''
                product_url = f"https://shop.tiktok.com/view/product/{pid}" if pid else ''
                items.append({
                    "name": name,
                    "url": product_url,
                    "asin": pid,
                    "position": 0,
                    "thumbnailUrl": cover,
                    "stars": float(p.get('product_rating') or 0) or None,
                    "reviewsCount": review_val,
                    "categoryName": subcat,
                    "categoryFullName": cats,
                    "_country_code": "TT",
                    "_country_flag": "🇺🇸",
                    "_country_name": "TikTok Shop US",
                    "_tt_subcategory": subcat,
                    "_price_value": price_val,
                    "_price_currency": "$",
                    "_sale_cnt":     sale_raw,
                    "_sale_cnt_num": _parse_sale_cnt(sale_raw),
                    "_sale_30d_cnt": sale_30d,
                    "_sale_30d_num": _parse_sale_cnt(sale_30d),
                    "_sale_7d_cnt":  sale_7d,
                    "_sale_7d_num":  _parse_sale_cnt(sale_7d),
                    "_commission": p.get('commission', ''),
                })
        except Exception as e:
            print(f"[TikTok] dataset {dataset_id} failed: {e}")
    # 판매량 내림차순 정렬 후 position 할당
    items.sort(key=lambda x: x['_sale_cnt_num'], reverse=True)
    for idx, item in enumerate(items):
        item['position'] = idx + 1
    print(f"[TikTok] fetched {len(items)} unique beauty items")
    return items

def detect_country(item):
    for field in ("input", "categoryUrl", "url"):
        val = item.get(field) or ""
        for domain, info in DOMAIN_MAP.items():
            if domain in val:
                return info
    return {"code": "US", "flag": "🇺🇸", "name": "United States"}

def _load_bundled_by_country(codes):
    """번들 캐시에서 특정 country_code 아이템만 반환"""
    if not os.path.exists(BUNDLED_CACHE):
        return []
    try:
        with open(BUNDLED_CACHE, "r", encoding="utf-8") as f:
            bundled = json.load(f)
        return [i for i in bundled.get("items", []) if i.get("_country_code") in codes]
    except Exception:
        return []

def fetch_from_apify(refresh=False):
    all_items = []
    # Amazon (US/UK/JP) — Apify 실패 시 번들 캐시 폴백
    amazon_items = []
    for label, run_id in ACTOR_RUNS.items():
        url = (f"https://api.apify.com/v2/actor-runs/{run_id}/dataset/items"
               f"?token={APIFY_TOKEN}&limit=500")
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            items = resp.json()
            for item in items:
                country = detect_country(item)
                price_raw = item.get("price") or {}
                item["_country_code"] = country["code"]
                item["_country_flag"] = country["flag"]
                item["_country_name"] = country["name"]
                item["_price_value"] = price_raw.get("value") if isinstance(price_raw, dict) else None
                item["_price_currency"] = price_raw.get("currency", "$") if isinstance(price_raw, dict) else "$"
            amazon_items.extend(items)
        except Exception as e:
            print(f"[Apify] {label} ({run_id}) failed: {e}")
    if not amazon_items:
        amazon_items = _load_bundled_by_country({"US", "UK", "JP"})
        print(f"[Amazon] Apify unavailable — loaded {len(amazon_items)} items from bundled cache")
    all_items.extend(amazon_items)
    # YesStyle 추가
    ys_items = fetch_yesstyle()
    print(f"[YesStyle] fetched {len(ys_items)} items")
    all_items.extend(ys_items)
    # OliveYoung 추가
    oy_items = fetch_oliveyoung()
    print(f"[OliveYoung] fetched {len(oy_items)} items")
    all_items.extend(oy_items)
    # Qoo10 Japan 추가 (Playwright 필요 — Vercel에서는 번들 캐시 폴백)
    qj_items = fetch_qoo10()
    if not qj_items:
        qj_items = _load_bundled_by_country({"QJ"})
        print(f"[Qoo10] Playwright unavailable — loaded {len(qj_items)} items from bundled cache")
    print(f"[Qoo10] fetched {len(qj_items)} items")
    all_items.extend(qj_items)
    # TikTok Shop 추가 — 실패 시 번들 캐시 폴백
    tt_items = fetch_tiktok()
    if not tt_items:
        tt_items = _load_bundled_by_country({"TT"})
        print(f"[TikTok] Apify unavailable — loaded {len(tt_items)} items from bundled cache")
    all_items.extend(tt_items)
    return all_items

def load_cache():
    # /tmp 캐시 → 번들 캐시 순으로 확인
    for path in [CACHE_FILE, BUNDLED_CACHE]:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return {}

def save_cache(data):
    cache = {"updated_at": datetime.utcnow().isoformat() + "Z", "items": data}
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[cache] write failed (expected on Vercel): {e}")
    save_ranking_snapshot(data)
    return cache

def _item_uid(item):
    asin    = item.get("asin") or ""
    country = item.get("_country_code", "")
    return f"{country}_{asin}" if asin else f"{country}_{(item.get('name') or '')[:40]}"

def _snap_paths(date_str):
    """Return list of candidate snapshot paths (writable first, then bundled)."""
    paths = [f"/tmp/rankings_{date_str}.json"]
    local = os.path.join(_SCRIPT_DIR, f"rankings_{date_str}.json")
    if local not in paths:
        paths.append(local)
    return paths

def save_ranking_snapshot(items):
    from datetime import timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Don't overwrite if already saved today in any location
    if any(os.path.exists(p) for p in _snap_paths(today)):
        return
    snap = [{"id": _item_uid(i), "name": i.get("name",""), "rank": i.get("position",0),
             "category": i.get("categoryName",""), "country": i.get("_country_code",""),
             "flag": i.get("_country_flag","")} for i in items]
    for path in _snap_paths(today):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"date": today, "items": snap}, f, ensure_ascii=False)
            print(f"[snapshot] saved {today} → {path}")
            return
        except Exception as e:
            print(f"[snapshot] write failed {path}: {e}")

def load_ranking_snapshot(date_str):
    for path in _snap_paths(date_str):
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f).get("items", [])
            except Exception:
                pass
    return None

def list_snapshot_dates():
    seen = set()
    dates = []
    for pattern in ["/tmp/rankings_*.json", os.path.join(_SCRIPT_DIR, "rankings_*.json")]:
        for p in glob.glob(pattern):
            d = os.path.basename(p).replace("rankings_","").replace(".json","")
            if d not in seen:
                seen.add(d)
                dates.append(d)
    return sorted(dates, reverse=True)

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    items = fetch_from_apify(refresh=True)
    cache = save_cache(items)
    return jsonify({"ok": True, "count": len(items), "updated_at": cache["updated_at"]})

@app.route("/api/data")
def api_data():
    cache = load_cache()
    if not cache:
        items = fetch_from_apify()
        cache = save_cache(items)
    else:
        # 오늘 스냅샷이 없으면 캐시 기반으로 즉시 저장
        save_ranking_snapshot(cache.get("items", []))
    return jsonify(cache)


@app.route("/api/dates")
def api_dates():
    files = sorted(glob.glob(os.path.join(_SCRIPT_DIR, "data_*.json")), reverse=True)
    return jsonify([os.path.basename(f).replace("data_","").replace(".json","") for f in files])

@app.route("/api/data/<date>")
def api_data_date(date):
    path = os.path.join(_SCRIPT_DIR, f"data_{date}.json")
    if not os.path.exists(path):
        return jsonify([])
    with open(path, encoding="utf-8") as f:
        return jsonify(json.load(f))

@app.route("/api/apify/usage")
def api_apify_usage():
    """Apify 이번 달 크레딧 사용량 조회"""
    token = os.getenv("APIFY_TOKEN", "")
    if not token:
        return jsonify({"error": "no token"})
    try:
        r = requests.get(f"https://api.apify.com/v2/users/me?token={token}", timeout=8)
        d = r.json().get("data", {})
        plan = d.get("plan", {})
        usage = d.get("monthlyUsage", {})
        limit = plan.get("monthlyUsageCreditsUsd", 0)
        used  = usage.get("totalUsd", 0)
        return jsonify({
            "used": round(used, 2),
            "limit": round(limit, 2),
            "remaining": round(limit - used, 2),
            "pct": round(used / limit * 100, 1) if limit else 0,
        })
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/run", methods=["POST"])
def api_run():
    """GitHub Actions workflow_dispatch 트리거 — Apify는 GitHub Actions에서만 실행"""
    gh_token = os.getenv("GH_PAT", "")
    if not gh_token:
        return jsonify({"ok": False, "error": "GH_PAT not set"}), 500
    try:
        resp = requests.post(
            "https://api.github.com/repos/creatus-team/Beauty-product-rankings/actions/workflows/daily_scrape.yml/dispatches",
            headers={"Authorization": f"token {gh_token}", "Accept": "application/vnd.github+json"},
            json={"ref": "main"},
            timeout=8,
        )
        if resp.status_code == 204:
            return jsonify({"ok": True, "msg": "GitHub Actions triggered"})
        return jsonify({"ok": False, "error": resp.text}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500



# ── Viral US Beauty (viral_us_daily.py 산출물) ───────────────────────────
@app.route("/api/viral_us/dates")
def api_viral_us_dates():
    files = sorted(glob.glob(os.path.join(_SCRIPT_DIR, "viral_us_data_*.json")), reverse=True)
    dates = [os.path.basename(f).replace("viral_us_data_","").replace(".json","") for f in files]
    return jsonify(dates)

@app.route("/api/viral_us/data/<date>")
def api_viral_us_data(date):
    path = os.path.join(_SCRIPT_DIR, f"viral_us_data_{date}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return jsonify(json.load(f))
    return jsonify([])

@app.route("/api/viral_us/latest")
def api_viral_us_latest():
    """최신 viral US 데이터 자동 반환."""
    files = sorted(glob.glob(os.path.join(_SCRIPT_DIR, "viral_us_data_*.json")), reverse=True)
    if not files:
        return jsonify({"date": None, "videos": []})
    latest = files[0]
    date_str = os.path.basename(latest).replace("viral_us_data_","").replace(".json","")
    with open(latest, encoding="utf-8") as f:
        return jsonify({"date": date_str, "videos": json.load(f)})

@app.route("/api/cron/refresh", methods=["GET","POST"])
def api_cron_refresh():
    """Vercel Cron이 매일 9시에 호출 — 상품 데이터 갱신"""
    items = fetch_from_apify(refresh=True)
    cache = save_cache(items)
    return jsonify({"ok": True, "count": len(items), "updated_at": cache["updated_at"]})


@app.route("/")
def index():
    resp = make_response(render_template_string(HTML))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🛒 Beauty Product Rankings</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&family=Noto+Sans+KR:wght@400;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --pink:#e8637a;--pink-light:#fce8ec;--pink-mid:#f5b8c4;--gold:#f5a623;
  --bg:#fdf6f8;--surface:#fff;--border:#f0e0e5;--text:#1a1a1a;--muted:#888;
  --shadow:0 2px 12px rgba(232,99,122,.08);
}
body{font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans KR',sans-serif;
     background:var(--bg);color:var(--text);min-height:100vh;
     -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;
     text-rendering:optimizeLegibility;font-feature-settings:'tnum','cv01';
     font-size:16px;line-height:1.55;letter-spacing:0;
     -webkit-text-size-adjust:100%;text-size-adjust:100%}
img{image-rendering:-webkit-optimize-contrast;image-rendering:crisp-edges}

/* header */
header{background:linear-gradient(135deg,#e8637a 0%,#c0445f 100%);color:#fff;
  padding:0 28px;height:62px;display:flex;align-items:center;
  justify-content:space-between;position:sticky;top:0;z-index:100;
  box-shadow:0 3px 16px rgba(192,68,95,.3)}
.h-left{display:flex;align-items:center;gap:12px}
.h-logo{font-size:1.5rem}
.h-title{font-size:1.1rem;font-weight:800}
.h-sub{font-size:.86rem;opacity:.82;margin-top:1px}
.h-right{display:flex;align-items:center;gap:10px}
.mode-switch{display:flex;background:rgba(255,255,255,.2);border-radius:10px;padding:3px;gap:3px}
.mode-btn{padding:7px 14px;border:none;border-radius:8px;cursor:pointer;font-weight:700;
  font-size:.86rem;transition:all .2s;background:transparent;color:rgba(255,255,255,.75)}
.mode-btn:hover{background:rgba(255,255,255,.15);color:#fff}
.mode-btn.active{background:#fff;color:var(--pink)}
.upd{font-size:.86rem;opacity:.75}
#refreshBtn{background:#fff;color:var(--pink);border:none;padding:8px 16px;
  border-radius:8px;font-weight:700;font-size:.86rem;cursor:pointer;
  transition:all .2s;display:flex;align-items:center;gap:5px}
#refreshBtn:hover{background:#fff5f7;transform:translateY(-1px)}
#refreshBtn:disabled{opacity:.55;cursor:not-allowed;transform:none}
@keyframes spin{to{transform:rotate(360deg)}}
.spin{display:inline-block;animation:spin .8s linear infinite}

/* country tabs */
.ctabs{background:var(--surface);border-bottom:1px solid var(--border);
  padding:0 24px;display:flex;gap:4px;overflow-x:auto;scrollbar-width:none}
.ctabs::-webkit-scrollbar{display:none}
.ctab{padding:12px 18px;border:none;background:none;cursor:pointer;
  font-size:.85rem;font-weight:600;color:var(--muted);
  border-bottom:3px solid transparent;white-space:nowrap;
  transition:all .2s;margin-bottom:-1px}
.ctab:hover{color:var(--pink);background:var(--pink-light);border-radius:8px 8px 0 0;transform:translateY(-2px)}
.ctab.active{color:var(--pink);border-bottom-color:var(--pink)}
.ctab .flag{font-size:1.1rem;margin-right:5px}
.ctab .cnt{background:var(--pink-light);color:var(--pink);
  font-size:.86rem;padding:1px 6px;border-radius:10px;margin-left:4px;font-weight:700}

/* toolbar */
.toolbar{padding:14px 24px;background:var(--surface);border-bottom:1px solid var(--border);
  display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.sw{position:relative;flex:1;min-width:200px;max-width:340px}
.sw input{width:100%;padding:9px 12px 9px 36px;border:1.5px solid var(--border);
  border-radius:9px;font-size:.88rem;background:var(--bg);outline:none;transition:border .2s}
.sw input:focus{border-color:var(--pink);background:#fff}
.sw .si{position:absolute;left:11px;top:50%;transform:translateY(-50%);
  color:var(--muted);pointer-events:none}
select.fs{padding:9px 12px;border:1.5px solid var(--border);border-radius:9px;
  font-size:.88rem;background:var(--bg);outline:none;cursor:pointer;
  transition:border .2s;color:var(--text)}
select.fs:focus{border-color:var(--pink);background:#fff}
.rc{margin-left:auto;font-size:.86rem;color:var(--muted)}

/* grid */
.gw{padding:20px 24px}
.cat-sec{margin-bottom:32px}
.cat-hdr{font-size:.86rem;font-weight:700;color:var(--pink);text-transform:uppercase;
  letter-spacing:.8px;margin-bottom:14px;padding-bottom:8px;
  border-bottom:2px solid var(--pink-light);display:flex;align-items:center;gap:8px}
.cat-cnt{background:var(--pink-light);color:var(--pink);font-size:.86rem;
  padding:2px 8px;border-radius:10px;font-weight:700}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:16px}
@media(max-width:600px){.grid{grid-template-columns:repeat(2,1fr);gap:10px}}

/* card */
.card{background:var(--surface);border-radius:14px;overflow:hidden;
  box-shadow:var(--shadow);border:1px solid var(--border);
  transition:transform .2s,box-shadow .2s;position:relative;display:flex;flex-direction:column}
.card:hover{transform:translateY(-3px);box-shadow:0 8px 24px rgba(232,99,122,.14)}
.card a{text-decoration:none;color:inherit;display:flex;flex-direction:column;flex:1}
.rank-b{position:absolute;top:8px;left:8px;z-index:2;width:28px;height:28px;
  border-radius:50%;display:flex;align-items:center;justify-content:center;
  font-size:.86rem;font-weight:900;color:#fff;box-shadow:0 2px 6px rgba(0,0,0,.25)}
.r1{background:linear-gradient(135deg,#f5a623,#e08800)}
.r2{background:linear-gradient(135deg,#aaa,#888)}
.r3{background:linear-gradient(135deg,#cd7f32,#a0622a)}
.rn{background:linear-gradient(135deg,#e8637a,#c0445f)}
.thumb{width:100%;padding-top:100%;position:relative;background:#f9f3f5;overflow:hidden}
.thumb img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;
  padding:8px;transition:transform .3s}
.card:hover .thumb img{transform:scale(1.04)}
.ph{position:absolute;inset:0;display:flex;align-items:center;
  justify-content:center;font-size:2.5rem;color:var(--pink-mid)}
.cbody{padding:10px 12px 12px;flex:1;display:flex;flex-direction:column;gap:5px}
.cname{font-size:.86rem;font-weight:600;line-height:1.35;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.cstars{display:flex;align-items:center;gap:4px}
.sv{color:var(--gold);font-size:.88rem;letter-spacing:-1px}
.sn{font-size:.86rem;color:var(--muted)}
.crev{font-size:.86rem;color:var(--muted)}
.cprice{font-size:.88rem;font-weight:800;color:var(--pink);margin-top:auto;padding-top:6px}
.cprice.np{color:var(--muted);font-weight:400;font-size:.88rem}
.casin{font-size:.86rem;color:#bbb;margin-top:2px}
.empty{text-align:center;padding:60px 20px;color:var(--muted)}
.empty .em{font-size:3rem;margin-bottom:12px}

/* Dashboard overview */
.dash{padding:20px 24px;display:flex;gap:14px;overflow-x:auto;align-items:flex-start}
.dash-col{flex:1;min-width:200px;max-width:260px;background:var(--surface);
  border-radius:14px;border:1px solid var(--border);overflow:hidden;
  box-shadow:var(--shadow);display:flex;flex-direction:column}
.dash-hdr{padding:11px 14px;display:flex;align-items:center;gap:8px;
  background:linear-gradient(135deg,var(--pink-light) 0%,#fff 100%);
  border-bottom:2px solid var(--pink-mid);font-weight:800;font-size:.86rem;color:var(--pink)}
.dash-flag{font-size:1.2rem}
.dash-item{display:flex;flex-direction:column;text-decoration:none;color:inherit;
  border-bottom:1px solid var(--border);transition:background .15s;cursor:pointer;
  position:relative;overflow:hidden}
.dash-item:hover{background:var(--pink-light)}
.dash-item:last-child{border-bottom:none}
.dash-thumb{width:100%;padding-top:85%;position:relative;background:#f9f3f5;overflow:hidden}
.dash-thumb img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;
  padding:10px;transition:transform .3s}
.dash-item:hover .dash-thumb img{transform:scale(1.05)}
.dash-ph{position:absolute;inset:0;display:flex;align-items:center;
  justify-content:center;font-size:2.2rem;color:var(--pink-mid)}
.dash-rnk{position:absolute;top:8px;left:8px;font-size:.86rem;font-weight:900;
  color:#fff;width:24px;height:24px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;
  box-shadow:0 2px 6px rgba(0,0,0,.25);z-index:2}
.dash-info{padding:8px 10px 10px;flex:1}
.dash-name{font-size:.88rem;font-weight:600;line-height:1.35;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;color:var(--text)}
.dash-price{font-size:.88rem;font-weight:800;color:var(--pink);margin-top:4px}
@media(max-width:700px){.dash{flex-wrap:nowrap}.dash-col{min-width:160px}}

/* Combined Dashboard layout */
.db-main{display:flex;gap:16px;padding:16px 20px;height:calc(100vh - 56px);overflow:hidden;box-sizing:border-box}
.db-left{flex:1.4;display:flex;flex-direction:column;overflow:hidden;min-width:0}
.db-right{width:370px;flex-shrink:0;display:flex;flex-direction:column;gap:12px;overflow-y:auto;padding-right:2px}
.db-section-hdr{font-size:.88rem;font-weight:800;color:var(--pink);margin-bottom:10px}
.dash-compact{display:flex;gap:10px;flex:1;overflow-x:auto;overflow-y:auto;align-items:flex-start;padding-bottom:4px}
.dash-mini-col{flex:1;min-width:160px;max-width:210px;background:var(--surface);border-radius:12px;border:1px solid var(--border);overflow:hidden;box-shadow:var(--shadow);display:flex;flex-direction:column;flex-shrink:0;cursor:pointer;transition:box-shadow .18s,border-color .18s}
.dash-mini-col:hover{box-shadow:0 4px 12px rgba(211,61,90,.12);border-color:var(--pink)}
.dash-mini-hdr{padding:8px 11px;display:flex;align-items:center;gap:6px;background:linear-gradient(135deg,var(--pink-light) 0%,#fff 100%);border-bottom:2px solid var(--pink-mid);font-weight:800;font-size:.88rem;color:var(--pink);white-space:nowrap}
.dash-mini-item{display:flex;flex-direction:column;text-decoration:none;color:inherit;border-bottom:1px solid var(--border);transition:background .15s;overflow:hidden}
.dash-mini-item:hover{background:var(--pink-light)}
.dash-mini-item:last-child{border-bottom:none}
.dash-mini-thumb{width:100%;padding-top:78%;position:relative;background:#ede9e7;overflow:hidden;border-bottom:1px solid #e0d9d6}
.dash-mini-thumb img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;padding:8px}
.dash-mini-ph{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;
  background:linear-gradient(135deg,#fee2e2 0%,#fef3c7 100%);color:#b91c1c;gap:4px;padding:8px}
.dash-mini-ph .ph-icon{font-size:1.6rem}
.dash-mini-ph .ph-label{font-size:0.7rem;font-weight:700;letter-spacing:0.5px;text-transform:uppercase;opacity:0.7}
.dash-mini-rank{position:absolute;top:7px;left:7px;font-size:.86rem;font-weight:900;color:#fff;width:22px;height:22px;border-radius:50%;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 6px rgba(0,0,0,.25);z-index:2}
.dash-mini-name{padding:8px 10px 4px;font-size:.88rem;font-weight:700;line-height:1.4;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;color:var(--text)}
.dash-mini-price{padding:0 10px 8px;font-size:.86rem;font-weight:800;color:var(--pink)}
.db-chart-card{background:var(--surface);border-radius:14px;border:1px solid var(--border);box-shadow:var(--shadow);padding:14px 16px;flex-shrink:0;cursor:pointer;transition:box-shadow .18s,border-color .18s}
.db-chart-card:hover{box-shadow:0 4px 12px rgba(211,61,90,.12);border-color:var(--pink)}
.db-card-title{font-size:.86rem;font-weight:800;color:var(--pink);margin-bottom:10px}
.db-pie-body{display:flex;gap:12px;align-items:flex-start}
.db-pie-legend{display:flex;flex-direction:column;gap:2px;flex:1;min-width:0;overflow-y:auto;max-height:190px}
.db-legend-grp{font-size:.86rem;font-weight:800;color:var(--text);margin-top:6px;margin-bottom:1px;border-left:2px solid var(--pink);padding-left:4px}
.db-legend-grp:first-child{margin-top:0}
.db-legend-item{display:flex;align-items:center;gap:5px;font-size:.86rem;padding:2px 3px;border-radius:4px}
.db-legend-dot{width:9px;height:9px;border-radius:2px;flex-shrink:0}
.db-legend-label{flex:1;font-weight:600;color:var(--text);overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.db-legend-pct{font-weight:800;color:var(--pink);font-size:.86rem;flex-shrink:0}
.db-ing-bars{display:flex;flex-direction:column;gap:5px}
.db-ing-row{display:flex;align-items:center;gap:8px}
.db-ing-label{width:98px;text-align:right;font-size:.86rem;font-weight:700;color:var(--text);flex-shrink:0;line-height:1.2}
.db-ing-track{flex:1;height:20px;background:var(--pink-light);border-radius:5px;overflow:hidden}
.db-ing-fill{height:100%;border-radius:5px;display:flex;align-items:center;padding:0 7px;transition:width .55s cubic-bezier(.4,0,.2,1);width:0%}
.db-ing-cnt{font-size:.86rem;font-weight:800;color:#fff;white-space:nowrap}
@media(max-width:900px){.db-main{flex-direction:column;height:auto;overflow:auto}.db-right{width:100%}}

/* YesStyle subcategory pills */
.ys-pills{display:flex;gap:6px;align-items:center}
.ys-pill{padding:6px 14px;border-radius:20px;border:1.5px solid var(--border);
  background:var(--bg);color:var(--muted);font-size:.86rem;font-weight:600;
  cursor:pointer;transition:all .18s;white-space:nowrap}
.ys-pill:hover{border-color:var(--pink);color:var(--pink)}
.ys-pill.active{background:var(--pink);border-color:var(--pink);color:#fff}

/* Chart view */
.chart-wrap{padding:24px;max-width:960px;margin:0 auto}
.chart-title{font-size:1.05rem;font-weight:800;color:var(--pink);margin-bottom:16px}
.chart-platform-filter{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:24px}
.ch-pill{padding:7px 16px;border-radius:20px;border:1.5px solid var(--border);
  background:var(--bg);color:var(--muted);font-size:.86rem;font-weight:600;
  cursor:pointer;transition:all .18s;white-space:nowrap}
.ch-pill:hover{border-color:var(--pink);color:var(--pink)}
.ch-pill.active{background:var(--pink);border-color:var(--pink);color:#fff}
.chart-body{display:flex;gap:36px;align-items:center;flex-wrap:wrap;justify-content:center;padding:8px 0}
.chart-legend{display:flex;flex-direction:column;gap:6px;min-width:180px}
.legend-item{display:flex;align-items:center;gap:8px;font-size:.86rem;padding:4px 6px;border-radius:7px;transition:background .15s}
.legend-item:hover{background:var(--pink-light)}
.legend-dot{width:13px;height:13px;border-radius:3px;flex-shrink:0}
.legend-label{flex:1;font-weight:600;color:var(--text)}
.legend-pct{font-weight:800;color:var(--pink);min-width:38px;text-align:right}
.legend-cnt{color:var(--muted);font-size:.86rem}
.legend-group-hdr{font-size:.88rem;font-weight:800;color:var(--text);
  margin-top:12px;margin-bottom:2px;padding:3px 6px;
  border-left:3px solid var(--pink);letter-spacing:.3px}
.legend-group-hdr:first-child{margin-top:0}
.legend-main-pct{color:var(--pink);font-size:.88rem;margin-left:4px}

/* Ingredient trend chart */
.ing-wrap{padding:24px;max-width:800px;margin:0 auto}
.ing-title{font-size:1.05rem;font-weight:800;color:var(--pink);margin-bottom:4px}
.ing-sub{font-size:.86rem;color:var(--muted);margin-bottom:18px}
.ing-group-legend{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:20px}
.ing-grp{display:flex;align-items:center;gap:5px;font-size:.88rem;font-weight:700;color:var(--text)}
.ing-grp-dot{width:10px;height:10px;border-radius:3px;flex-shrink:0}
.ing-bars{display:flex;flex-direction:column;gap:7px}
.ing-row{display:flex;align-items:center;gap:10px}
.ing-label{width:130px;text-align:right;font-size:.86rem;font-weight:700;color:var(--text);flex-shrink:0;line-height:1.2}
.ing-grp-tag{font-size:.86rem;font-weight:600;color:var(--muted);display:block}
.ing-track{flex:1;height:28px;background:var(--pink-light);border-radius:7px;overflow:hidden;position:relative}
.ing-fill{height:100%;border-radius:7px;display:flex;align-items:center;padding:0 10px;transition:width .55s cubic-bezier(.4,0,.2,1);width:0%}
.ing-fill-cnt{font-size:.86rem;font-weight:800;color:#fff;white-space:nowrap}
.ing-meta{width:72px;font-size:.88rem;color:var(--muted);flex-shrink:0;text-align:left}

/* loading */
#loading{position:fixed;inset:0;background:rgba(253,246,248,.92);
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  z-index:999;gap:14px}
.loader{width:44px;height:44px;border:4px solid var(--pink-light);
  border-top-color:var(--pink);border-radius:50%;animation:spin .8s linear infinite}
#loading p{font-size:.88rem;color:var(--pink);font-weight:600}

</style>
</head>
<body>

<div id="loading">
  <div class="loader"></div>
  <p id="loadMsg">랭킹 데이터 불러오는 중...</p>
</div>

<header>
  <div class="h-left">
    <span class="h-logo" id="mainLogo">🛒</span>
    <div>
      <div class="h-title" id="mainTitle">Beauty Product Rankings</div>
      <div class="h-sub" id="mainSub">나라별 뷰티 베스트셀러</div>
    </div>
  </div>
  <div class="h-right">
    <div class="mode-switch">
      <button class="mode-btn active" id="mode-product" onclick="switchMode('product')">📦 Product</button>
      <button class="mode-btn" id="mode-viral" onclick="switchMode('viral')">🎬 TikTok Viral</button>
    </div>
    <span class="upd" id="updLbl">—</span>
    <button id="refreshBtn" onclick="refreshData()">↻ 새로고침</button>
  </div>
</header>

<div id="product-hub">
<div class="ctabs" id="tabs"></div>

<div class="toolbar" id="toolbar">
  <div class="sw">
    <span class="si">🔍</span>
    <input type="text" id="searchIn" placeholder="제품명, ASIN 검색..." oninput="render()">
  </div>
  <select class="fs" id="sortSel" onchange="render()">
    <option value="rank">랭킹순</option>
    <option value="stars">평점 높은순</option>
    <option value="reviews">리뷰 많은순</option>
    <option value="price_asc">가격 낮은순</option>
    <option value="price_desc">가격 높은순</option>
  </select>
  <div class="ys-pills" id="ysPills" style="display:none">
    <button class="ys-pill active" data-sub="All Beauty" onclick="setYsSub(this)">All</button>
    <button class="ys-pill" data-sub="Skin Care" onclick="setYsSub(this)">Skin Care</button>
    <button class="ys-pill" data-sub="Makeup" onclick="setYsSub(this)">Makeup</button>
  </div>
  <div class="ys-pills" id="oyPills" style="display:none">
    <button class="ys-pill active" data-sub="All" onclick="setOySub(this)">All</button>
    <button class="ys-pill" data-sub="Top orders" onclick="setOySub(this)">Top orders</button>
    <button class="ys-pill" data-sub="Top in Korea" onclick="setOySub(this)">Top in Korea</button>
  </div>
  <div class="ys-pills" id="qjPills" style="display:none">
    <button class="ys-pill active" data-sub="All" onclick="setQjSub(this)">All</button>
    <button class="ys-pill" data-sub="スキンケア" onclick="setQjSub(this)">스킨케어</button>
    <button class="ys-pill" data-sub="メイクアップ" onclick="setQjSub(this)">메이크업</button>
    <button class="ys-pill" data-sub="ヘアケア" onclick="setQjSub(this)">헤어케어</button>
  </div>
  <div class="ys-pills" id="ttPills" style="display:none">
    <button class="ys-pill active" data-sub="All" onclick="setTtSub(this)">All</button>
    <button class="ys-pill" data-sub="Skincare" onclick="setTtSub(this)">스킨케어</button>
    <button class="ys-pill" data-sub="Makeup" onclick="setTtSub(this)">메이크업</button>
    <button class="ys-pill" data-sub="Hair Care" onclick="setTtSub(this)">헤어케어</button>
    <button class="ys-pill" data-sub="Body Care" onclick="setTtSub(this)">바디케어</button>
  </div>
  <div class="ys-pills" id="ttPeriodPills" style="display:none">
    <span style="font-size:.88rem;font-weight:700;color:var(--muted);margin-right:2px">기간:</span>
    <button class="ys-pill" data-period="total" onclick="setTtPeriod(this)">누적</button>
    <button class="ys-pill active" data-period="30d" onclick="setTtPeriod(this)">30일</button>
    <button class="ys-pill" data-period="7d" onclick="setTtPeriod(this)">7일</button>
  </div>
  <span class="rc" id="rc"></span>
</div>

<div class="gw" id="gw"></div>
</div><!-- /product-hub -->

<!-- ── US Viral Hub ─────────────────────────────────────────── -->
<div id="viral-hub" style="display:none">
  <div style="background:linear-gradient(135deg,#1e3a8a 0%,#dc2626 100%);color:#fff;padding:14px 28px;
       display:flex;justify-content:space-between;align-items:center">
    <div>
      <h2 style="font-size:1.15rem;font-weight:800;margin:0">🇺🇸 미국 틱톡 뷰티 — 7일 내 TOP 영상</h2>
      <div style="font-size:0.85rem;opacity:0.9;margin-top:3px">10만뷰 이상만 · 따라찍기용 카탈로그</div>
    </div>
    <div style="display:flex;gap:10px;align-items:center">
      <select id="viral-date-select" style="padding:7px 12px;border-radius:8px;border:none;
              font-size:0.86rem;font-weight:700;cursor:pointer">
        <option>로딩중...</option>
      </select>
      <span id="viral-count-label" style="font-size:0.86rem;opacity:0.9">—</span>
      <button id="viral-scrape-btn" onclick="viralRunScrape()" style="background:white;color:#dc2626;
              border:none;padding:8px 16px;border-radius:8px;font-size:0.86rem;font-weight:800;
              cursor:pointer;display:flex;align-items:center;gap:5px;white-space:nowrap">
        🔄 새 스크랩
      </button>
    </div>
  </div>

  <div id="viral-content" style="padding:24px 28px">
    <div id="viral-empty" style="text-align:center;padding:60px 20px;color:#888">
      <div style="font-size:3rem;margin-bottom:14px">📭</div>
      <h3 style="font-size:1.1rem;color:#444;margin-bottom:8px">아직 수집된 데이터가 없어</h3>
      <p style="font-size:0.92rem">우상단 <b>🔄 새 스크랩</b> 버튼을 누르면 데이터를 가져옵니다.</p>
      <p style="font-size:0.86rem;color:#aaa;margin-top:12px">⚠️ 1회당 Apify 약 $4 발생 · 5~7분 소요</p>
    </div>
    <div id="viral-grid" style="display:none;
         grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:18px"></div>
  </div>
</div>

<script>
let all = [], country = sessionStorage.getItem('kbCountry') || 'DB', ysSub = 'All Beauty', oySub = 'All', qjSub = 'All', ttSub = 'All', ttPeriod = '30d';

// country order: ALL first, then US, UK, JP, then others
const ORDER = ['DB','CH','IG','US','OY','TT','YS','JP','UK','QJ','DE','FR','CA','AU','IT','ES'];
const TAB_LABELS = {'DB':'📊 전체 대시보드','CH':'📈 카테고리 분석','IG':'🧪 성분 트렌드','ALL':'전체','YS':'YesStyle','OY':'🌿 OliveYoung','QJ':'🛒 Qoo10 Japan','TT':'🎵 TikTok Shop US'};

async function loadData() {
  show('랭킹 데이터 불러오는 중...');
  try {
    const res = await fetch('/api/data');
    const json = await res.json();
    all = json.items || [];
    setUpdated(json.updated_at);
    buildTabs();
    render();
  } catch(e) { console.error(e); }
  hide();
  const savedMode = sessionStorage.getItem('kbMode') || 'product';
  if (savedMode !== 'product') switchMode(savedMode);
}

async function refreshData() {
  const btn = document.getElementById('refreshBtn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spin">↻</span> 가져오는 중...';
  show('Apify에서 최신 랭킹 가져오는 중...');
  try {
    const res = await fetch('/api/refresh', {method:'POST'});
    const json = await res.json();
    if (json.ok) { all = []; await loadData(); return; }
  } catch(e) { console.error(e); }
  hide();
  btn.disabled = false;
  btn.innerHTML = '↻ 새로고침';
}

function show(msg) {
  document.getElementById('loadMsg').textContent = msg;
  document.getElementById('loading').style.display = 'flex';
}
function hide() { document.getElementById('loading').style.display = 'none'; }

function setUpdated(ts) {
  if (!ts) return;
  document.getElementById('updLbl').textContent = '업데이트: ' +
    new Date(ts).toLocaleString('ko-KR',{month:'numeric',day:'numeric',
      hour:'2-digit',minute:'2-digit',timeZone:'Asia/Seoul'}) + ' KST';
}

function goTab(code) {
  sessionStorage.setItem('kbCountry', code);
  country=code; ysSub='All Beauty'; oySub='All'; qjSub='All'; ttSub='All'; ttPeriod='30d';
  resetYsPills(); resetOyPills(); resetQjPills(); resetTtPills(); resetTtPeriodPills();
  buildTabs(); render();
  window.scrollTo({top:0, behavior:'smooth'});
}
function buildTabs() {
  // collect countries preserving ORDER preference
  const counts = {ALL: all.length};
  const seen = new Set();
  all.forEach(i => {
    const c = i._country_code;
    if (c) { counts[c] = (counts[c]||0)+1; seen.add(c); }
  });
  const codes = ['DB', 'CH', 'IG', ...ORDER.filter(c => c!=='DB' && c!=='CH' && c!=='IG' && seen.has(c)),
                 ...[...seen].filter(c => !ORDER.includes(c) && c!=='ALL')];

  const el = document.getElementById('tabs');
  el.innerHTML = '';
  codes.forEach(code => {
    const s = all.find(i => i._country_code === code);
    const flag = code==='ALL' ? '🌍' : code==='DB' ? '' : (s ? s._country_flag : '');
    const label = TAB_LABELS[code] || code;
    const btn = document.createElement('button');
    btn.className = 'ctab' + (code===country ? ' active' : '');
    const cntHtml = (code==='DB'||code==='CH'||code==='IG') ? '' : `<span class="cnt">${counts[code]||0}</span>`;
    btn.innerHTML = (flag ? `<span class="flag">${flag}</span>` : '') + label + cntHtml;
    btn.onclick = () => goTab(code);
    el.appendChild(btn);
  });
}


function setYsSub(btn) {
  ysSub = btn.dataset.sub;
  document.querySelectorAll('.ys-pill').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  render();
}

function resetYsPills() {
  ysSub = 'All Beauty';
  document.querySelectorAll('#ysPills .ys-pill').forEach(p => {
    p.classList.toggle('active', p.dataset.sub === 'All Beauty');
  });
}

function updateYsPills() {
  document.getElementById('ysPills').style.display = country === 'YS' ? 'flex' : 'none';
  document.getElementById('oyPills').style.display = country === 'OY' ? 'flex' : 'none';
  document.getElementById('qjPills').style.display = country === 'QJ' ? 'flex' : 'none';
  document.getElementById('ttPills').style.display = country === 'TT' ? 'flex' : 'none';
  document.getElementById('ttPeriodPills').style.display = country === 'TT' ? 'flex' : 'none';
}

function setOySub(btn) {
  oySub = btn.dataset.sub;
  document.querySelectorAll('#oyPills .ys-pill').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  render();
}

function resetOyPills() {
  oySub = 'All';
  document.querySelectorAll('#oyPills .ys-pill').forEach(p => {
    p.classList.toggle('active', p.dataset.sub === 'All');
  });
}

function setQjSub(btn) {
  qjSub = btn.dataset.sub;
  document.querySelectorAll('#qjPills .ys-pill').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  render();
}

function resetQjPills() {
  qjSub = 'All';
  document.querySelectorAll('#qjPills .ys-pill').forEach(p => {
    p.classList.toggle('active', p.dataset.sub === 'All');
  });
}

function setTtSub(btn) {
  ttSub = btn.dataset.sub;
  document.querySelectorAll('#ttPills .ys-pill').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  render();
}

function resetTtPills() {
  ttSub = 'All';
  document.querySelectorAll('#ttPills .ys-pill').forEach(p => {
    p.classList.toggle('active', p.dataset.sub === 'All');
  });
}

function setTtPeriod(btn) {
  ttPeriod = btn.dataset.period;
  document.querySelectorAll('#ttPeriodPills .ys-pill').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  render();
}

function resetTtPeriodPills() {
  ttPeriod = '30d';
  document.querySelectorAll('#ttPeriodPills .ys-pill').forEach(p => {
    p.classList.toggle('active', p.dataset.period === '30d');
  });
}

function starViz(n) {
  if (!n) return '';
  const f=Math.floor(n), h=n-f>=0.5?1:0;
  return '★'.repeat(f)+(h?'½':'')+'☆'.repeat(5-f-h);
}
function fmtPrice(v, cur) { return v==null ? null : (cur||'$')+Number(v).toFixed(2); }
function fmtN(n) {
  if (!n) return '';
  if (n>=1e6) return (n/1e6).toFixed(1)+'M';
  if (n>=1000) return (n/1000).toFixed(1)+'K';
  return String(n);
}

function getFiltered() {
  const q = document.getElementById('searchIn').value.toLowerCase();
  const sort = document.getElementById('sortSel').value;
  let items = all.filter(i => {
    if (country!=='ALL' && i._country_code!==country) return false;
    if (country==='YS' && ysSub!=='All Beauty' && i._ys_subcategory!==ysSub) return false;
    if (country==='OY' && oySub!=='All' && i._oy_subcategory!==oySub) return false;
    if (country==='QJ' && qjSub!=='All' && i._qj_subcategory!==qjSub) return false;
    if (country==='TT' && ttSub!=='All' && i._tt_subcategory!==ttSub) return false;
    if (q && !(i.name||'').toLowerCase().includes(q) && !(i.asin||'').toLowerCase().includes(q)) return false;
    return true;
  });
  items.sort((a,b) => {
    if (sort==='rank') {
      // TT 탭: 기간 필터에 따라 다른 판매량 기준으로 정렬
      if (country==='TT' || (country==='ALL' && a._country_code==='TT')) {
        const aVal = ttPeriod==='30d' ? (a._sale_30d_num||0) : ttPeriod==='7d' ? (a._sale_7d_num||0) : (a._sale_cnt_num||0);
        const bVal = ttPeriod==='30d' ? (b._sale_30d_num||0) : ttPeriod==='7d' ? (b._sale_7d_num||0) : (b._sale_cnt_num||0);
        if (a._country_code==='TT' && b._country_code==='TT') return bVal - aVal;
      }
      return (a.position||999)-(b.position||999);
    }
    if (sort==='stars')      return (b.stars||0)-(a.stars||0);
    if (sort==='reviews')    return (b.reviewsCount||0)-(a.reviewsCount||0);
    if (sort==='price_asc')  return (a._price_value||9999)-(b._price_value||9999);
    if (sort==='price_desc') return (b._price_value||0)-(a._price_value||0);
    return 0;
  });
  return items;
}

function toUSD(val, currency) {
  if (!val || isNaN(val)) return '';
  if (currency === '¥') return '$' + (val / 150).toFixed(2);
  if (currency === '£') return '$' + (val * 1.27).toFixed(2);
  return '';
}

function renderDashboard() {
  const platforms = [
    {code:'US', label:'Amazon US'},
    {code:'OY', label:'OliveYoung', sub:'Top orders'},
    {code:'TT', label:'TikTok Shop', sortKey:'_sale_7d_num'},
    {code:'YS', label:'YesStyle'},
    {code:'JP', label:'Amazon JP'},
    {code:'UK', label:'Amazon UK'},
    {code:'QJ', label:'Qoo10 JP'},
  ];
  let prodHtml = '';
  platforms.forEach(({code, label, sub, sortKey}) => {
    const flag = (all.find(i=>i._country_code===code)||{})._country_flag || '';
    const top5 = all.filter(i=>i._country_code===code && (!sub || i._oy_subcategory===sub))
      .sort((a,b) => sortKey ? (b[sortKey]||0)-(a[sortKey]||0) : (a.position||999)-(b.position||999))
      .slice(0,5);
    prodHtml += `<div class="dash-mini-col" onclick="goTab('${code}')" title="${label} 탭으로 이동"><div class="dash-mini-hdr"><span>${flag}</span>${label}</div>`;
    top5.forEach((item,idx) => {
      const r=idx+1, rc=r===1?'r1':r===2?'r2':r===3?'r3':'rn';
      const th=item.thumbnailUrl;
      const isTT = item._country_code === 'TT';
      const phIcon = isTT ? '🎵' : '🧴';
      const phLabel = isTT ? 'TikTok Shop' : '';
      const imgEl=th?`<img src="${th}" alt="" loading="eager" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`:'' ;
      const phEl=`<div class="dash-mini-ph" style="${th?'display:none':''}"><span class="ph-icon">${phIcon}</span>${phLabel?`<span class="ph-label">${phLabel}</span>`:''}</div>`;
      const rawPrice = item._price_value;
      const cur = item._price_currency || '';
      let priceDisplay = '';
      if (rawPrice) {
        if (cur === '¥') priceDisplay = `¥${rawPrice.toLocaleString()} (${toUSD(rawPrice, cur)})`;
        else if (cur === '£') priceDisplay = `£${rawPrice.toFixed(2)} (${toUSD(rawPrice, cur)})`;
        else if (cur === '$') priceDisplay = `$${rawPrice.toFixed(2)}`;
        else if (cur === '₩') priceDisplay = `₩${Math.round(rawPrice).toLocaleString()}`;
        else priceDisplay = `${cur}${rawPrice}`;
      }
      prodHtml+=`<a class="dash-mini-item" href="${item.url||'#'}" target="_blank" rel="noopener" onclick="event.stopPropagation()">
        <div class="dash-mini-thumb">
          ${imgEl}${phEl}
          <div class="dash-mini-rank ${rc}">${r}</div>
        </div>
        <div class="dash-mini-name">${item.name||'No Name'}</div>
        ${priceDisplay ? `<div class="dash-mini-price">${priceDisplay}</div>` : ''}
      </a>`;
    });
    prodHtml += '</div>';
  });
  return `<div class="db-main">
    <div class="db-left">
      <div class="db-section-hdr">🏆 플랫폼별 TOP 5</div>
      <div class="dash-compact">${prodHtml}</div>
    </div>
    <div class="db-right">
      <div class="db-chart-card" onclick="goTab('CH')" title="카테고리 분석으로 이동">
        <div class="db-card-title">📈 카테고리 분포 (전체) <span style="font-size:.86rem;font-weight:400;color:#bbb;margin-left:4px">↗ 클릭</span></div>
        <div class="db-pie-body">
          <canvas id="dbPieChart" width="185" height="185" style="flex-shrink:0"></canvas>
          <div id="dbPieLegend" class="db-pie-legend"></div>
        </div>
      </div>
      <div class="db-chart-card" onclick="goTab('IG')" title="성분 트렌드로 이동">
        <div class="db-card-title">🧪 트렌딩 성분 TOP 10 (전체) <span style="font-size:.86rem;font-weight:400;color:#bbb;margin-left:4px">↗ 클릭</span></div>
        <div class="db-ing-bars" id="dbIngBars"></div>
      </div>
      <div class="db-chart-card">
        <div class="db-card-title">🏷️ 브랜드 TOP 10 (전체)</div>
        <div class="db-ing-bars" id="dbBrandBars"></div>
      </div>
    </div>
  </div>`;
}

function render() {
  updateYsPills();
  const toolbar = document.getElementById('toolbar');
  if (country === 'DB') {
    toolbar.style.display = 'none';
    document.getElementById('gw').innerHTML = renderDashboard();
    setTimeout(()=>{ drawDbPieChart(); drawDbIngChart(); drawDbBrandChart(); }, 20);
    return;
  }
  if (country === 'CH') {
    toolbar.style.display = 'none';
    renderChart();
    return;
  }
  if (country === 'IG') {
    toolbar.style.display = 'none';
    renderIngredientChart();
    return;
  }
  toolbar.style.display = '';
  const items = getFiltered();
  document.getElementById('rc').textContent = items.length+'개 제품';
  const wrap = document.getElementById('gw');
  if (!items.length) {
    wrap.innerHTML='<div class="empty"><div class="em">🔍</div><p>조건에 맞는 제품이 없어요.</p></div>';
    return;
  }
  const bycat = {};
  items.forEach(i => { const k=i.categoryFullName||i.categoryName||'Others'; (bycat[k]=bycat[k]||[]).push(i); });
  let html = '';
  for (const [cat, list] of Object.entries(bycat)) {
    html += `<div class="cat-sec"><div class="cat-hdr">🏆 ${cat} <span class="cat-cnt">${list.length}</span></div><div class="grid">`;
    list.forEach(item => {
      const r=item.position||'—';
      const rc=r===1?'r1':r===2?'r2':r===3?'r3':'rn';
      const price=fmtPrice(item._price_value, item._price_currency);
      const usd = toUSD(item._price_value, item._price_currency);
      const priceStr = price ? (usd ? `${price} <span style="color:#aaa;font-weight:500">(${usd})</span>` : price) : null;
      const th=item.thumbnailUrl;
      const isTT2 = item._country_code === 'TT';
      const imgH=th?`<img src="${th}" alt="" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`:'';
      const ph=`<div class="ph" style="${th?'display:none':''}${isTT2?';background:linear-gradient(135deg,#fee2e2,#fef3c7);color:#b91c1c':''}">${isTT2?'🎵 TikTok':'🧴'}</div>`;
      html+=`<div class="card">
        <div class="rank-b ${rc}">${r}</div>
        <a href="${item.url||'#'}" target="_blank" rel="noopener">
          <div class="thumb">${imgH}${ph}</div>
          <div class="cbody">
            <div class="cname">${item.name||'No Name'}</div>
            ${item.stars?`<div class="cstars"><span class="sv">${starViz(item.stars)}</span><span class="sn">${item.stars.toFixed(1)}</span></div>`:''}
            ${item.reviewsCount?`<div class="crev">리뷰 ${fmtN(item.reviewsCount)}개</div>`:''}
            <div class="cprice${priceStr?'':' np'}">${priceStr||'가격 미정'}</div>
            ${item.asin?`<div class="casin">ASIN: ${item.asin}</div>`:''}
          </div>
        </a>
      </div>`;
    });
    html+='</div></div>';
  }
  wrap.innerHTML=html;
}

// ── Chart ─────────────────────────────────────────────────────────────────────
let chPlatform = 'ALL';
const MAIN_ORDER_CH = ['스킨케어','메이크업','헤어케어','기타'];
const MAIN_CAT_COLORS = {
  '스킨케어': ['#e8637a','#c0445f','#f07688','#a83352','#f5a0ae','#e05070','#d43060','#f9c4cc','#b83050'],
  '메이크업': ['#7c6fe0','#6352c8','#9b87f5','#5444b0','#b3a8f8','#4a3ab0','#c5bff9'],
  '헤어케어': ['#3bb89e','#2d9a84','#10b981','#0d9668','#5ecfb8'],
  '기타':     ['#aaa','#c8c8c8'],
};

function getDetailCategory(item) {
  const code = item._country_code;
  const name = (item.name||'').toLowerCase();
  if (code === 'YS') {
    const sub = item._ys_subcategory || '';
    if (sub === 'Makeup') {
      if (/lip(?!.?balm)/.test(name)) return {main:'메이크업', sub:'립메이크업'};
      if (/eye|mascara|liner/.test(name)) return {main:'메이크업', sub:'아이메이크업'};
      if (/foundation|bb|cc|cushion/.test(name)) return {main:'메이크업', sub:'파운데이션/BB'};
      return {main:'메이크업', sub:'기타 메이크업'};
    }
    if (sub === 'Skin Care') {
      if (/sun|spf/.test(name)) return {main:'스킨케어', sub:'선케어'};
      if (/toner|skin(?! care)/.test(name)) return {main:'스킨케어', sub:'토너/스킨'};
      if (/serum|ampoule|essence/.test(name)) return {main:'스킨케어', sub:'세럼/에센스'};
      if (/pad/.test(name)) return {main:'스킨케어', sub:'패드'};
      if (/mask/.test(name)) return {main:'스킨케어', sub:'마스크팩'};
      if (/cleanser|foam|wash/.test(name)) return {main:'스킨케어', sub:'클렌저'};
      if (/cream|moistur|lotion|emulsion/.test(name)) return {main:'스킨케어', sub:'로션/크림'};
      return {main:'스킨케어', sub:'기타 스킨케어'};
    }
    return {main:'기타', sub:'기타'};
  }
  if (code === 'QJ') {
    const sub = item._qj_subcategory || '';
    if (sub === 'ヘアケア') {
      if (/シャンプー|shampoo/.test(name)) return {main:'헤어케어', sub:'샴푸'};
      if (/コンディショナー|conditioner|トリートメント|treatment/.test(name)) return {main:'헤어케어', sub:'컨디셔너'};
      return {main:'헤어케어', sub:'헤어케어'};
    }
    if (sub === 'メイクアップ') {
      if (/リップ|lip/.test(name)) return {main:'메이크업', sub:'립메이크업'};
      if (/アイシャドウ|eyeshadow|アイライナー|eyeliner|マスカラ|mascara/.test(name)) return {main:'메이크업', sub:'아이메이크업'};
      if (/ファンデ|foundation|bb|cc|クッション|cushion/.test(name)) return {main:'메이크업', sub:'파운데이션/BB'};
      return {main:'메이크업', sub:'기타 메이크업'};
    }
    // スキンケア (default)
    if (/美容液|serum|essence|セラム/.test(name)) return {main:'스킨케어', sub:'세럼/에센스'};
    if (/化粧水|toner|lotion/.test(name)) return {main:'스킨케어', sub:'토너/스킨'};
    if (/日焼け|サンクリーム|sunscreen|spf|uv/.test(name)) return {main:'스킨케어', sub:'선케어'};
    if (/マスク|mask|パック|pack/.test(name)) return {main:'스킨케어', sub:'마스크팩'};
    if (/パッチ|patch/.test(name)) return {main:'스킨케어', sub:'패치'};
    if (/洗顔|クレンジング|cleanser|foam/.test(name)) return {main:'스킨케어', sub:'클렌저'};
    if (/クリーム|cream|moistur|emulsion|乳液/.test(name)) return {main:'스킨케어', sub:'로션/크림'};
    return {main:'스킨케어', sub:'기타 스킨케어'};
  }
  if (code === 'OY') {
    if (/shampoo|conditioner|hair/.test(name)) return {main:'헤어케어', sub:'헤어케어'};
    if (/sun.?screen|sun.?serum|\bspf\b/.test(name)) return {main:'스킨케어', sub:'선케어'};
    if (/serum|ampoule|essence/.test(name)) return {main:'스킨케어', sub:'세럼/에센스'};
    if (/toner|skin(?! care)/.test(name)) return {main:'스킨케어', sub:'토너/스킨'};
    if (/mask/.test(name)) return {main:'스킨케어', sub:'마스크팩'};
    if (/patch|acne.?patch|pimple.?patch/.test(name)) return {main:'스킨케어', sub:'패치'};
    if (/cleanser|foam|wash/.test(name)) return {main:'스킨케어', sub:'클렌저'};
    if (/eye.?cream|under.?eye/.test(name)) return {main:'스킨케어', sub:'아이크림'};
    if (/cream|moistur|lotion/.test(name)) return {main:'스킨케어', sub:'로션/크림'};
    if (/mascara|eyeliner|eyebrow|eyeshadow/.test(name)) return {main:'메이크업', sub:'아이메이크업'};
    if (/foundation|bb|cc|cushion/.test(name)) return {main:'메이크업', sub:'파운데이션/BB'};
    if (/lip(?!.?balm)/.test(name)) return {main:'메이크업', sub:'립메이크업'};
    if (/lip.?balm|lip.?care/.test(name)) return {main:'스킨케어', sub:'립케어'};
    return {main:'기타', sub:'기타'};
  }
  // Amazon: categoryName is often generic ("Beauty & Personal Care"), use name too
  const cat = ((item.categoryName||'') + ' ' + (item.categoryFullName||'')).toLowerCase();
  const t = cat + ' ' + name;  // combine category + product name for matching
  if (/shampoo/.test(t)) return {main:'헤어케어', sub:'샴푸'};
  if (/conditioner|hair.?treatment|hair.?mask/.test(t)) return {main:'헤어케어', sub:'컨디셔너'};
  if (/hair(?! removal)|scalp/.test(t)) return {main:'헤어케어', sub:'헤어케어'};
  if (/foundation|bb.?cream|cc.?cream|cushion.?compact/.test(t)) return {main:'메이크업', sub:'파운데이션/BB'};
  if (/mascara|eyeliner|eyebrow|eyeshadow/.test(t)) return {main:'메이크업', sub:'아이메이크업'};
  if (/lipstick|lip.?gloss|lip.?liner/.test(t)) return {main:'메이크업', sub:'립메이크업'};
  if (/blush|bronzer|highlighter|contour/.test(t)) return {main:'메이크업', sub:'치크/하이라이터'};
  if (/makeup.?remover|make.?up.?remover|micellar/.test(t)) return {main:'메이크업', sub:'클렌징'};
  if (/sun.?screen|sun.?block|\bspf\b/.test(t)) return {main:'스킨케어', sub:'선케어'};
  if (/\btoner\b|toning.?pad|toning.?wipe/.test(t)) return {main:'스킨케어', sub:'토너/스킨'};
  if (/serum|essence|ampoule|booster/.test(t)) return {main:'스킨케어', sub:'세럼/에센스'};
  if (/eye.?cream|under.?eye|eye.?gel/.test(t)) return {main:'스킨케어', sub:'아이크림'};
  if (/lip.?balm|lip.?care|lip.?treatment/.test(t)) return {main:'스킨케어', sub:'립케어'};
  if (/face.?mask|sheet.?mask|hydrogel.?mask|sleeping.?mask|overnight.?mask/.test(t)) return {main:'스킨케어', sub:'마스크팩'};
  if (/pimple.?patch|acne.?patch|hydrocolloid/.test(t)) return {main:'스킨케어', sub:'패치'};
  if (/glycolic|salicylic|lactic.?acid|\baha\b|\bbha\b|exfoliat|peeling|scrub/.test(t)) return {main:'스킨케어', sub:'각질케어'};
  if (/toner.?pad|facial.?pad|cotton.?pad|\bpad\b/.test(t)) return {main:'스킨케어', sub:'패드'};
  if (/foam.?cleanser|face.?wash|facial.?cleanser|cleansing/.test(t)) return {main:'스킨케어', sub:'클렌저'};
  if (/moisturiz|face.?lotion|face.?cream|day.?cream|night.?cream|emulsion/.test(t)) return {main:'스킨케어', sub:'로션/크림'};
  if (/niacinamide|retinol|vitamin.?c|peptide|brightening|whitening|anti.?aging/.test(t)) return {main:'스킨케어', sub:'기타 스킨케어'};
  return {main:'기타', sub:'기타'};
}

function drawChartForPlatform(code) {
  const items = code==='ALL' ? all : all.filter(i=>i._country_code===code);
  const subCounts = {};
  items.forEach(item => {
    const {main, sub} = getDetailCategory(item);
    if (!subCounts[main]) subCounts[main]={};
    subCounts[main][sub]=(subCounts[main][sub]||0)+1;
  });
  const slices=[];
  MAIN_ORDER_CH.forEach(main => {
    if (!subCounts[main]) return;
    const subs=Object.entries(subCounts[main]).sort((a,b)=>b[1]-a[1]);
    const colors=MAIN_CAT_COLORS[main];
    subs.forEach(([sub,count],i)=>slices.push({main,sub,count,color:colors[i%colors.length]}));
  });
  const total=slices.reduce((s,d)=>s+d.count,0);

  const canvas=document.getElementById('pieChart');
  if(!canvas) return;
  const dpr=window.devicePixelRatio||1;
  const W=canvas.offsetWidth||340,H=canvas.offsetHeight||340;
  canvas.width=W*dpr;canvas.height=H*dpr;
  canvas.style.width=W+'px';canvas.style.height=H+'px';
  const ctx=canvas.getContext('2d');
  ctx.scale(dpr,dpr);
  const cx=W/2,cy=H/2;
  const r=Math.min(cx,cy)-20,ri=r*0.44;
  let prog=0;
  const animate=()=>{
    ctx.clearRect(0,0,W,H);
    let angle=-Math.PI/2;
    slices.forEach(({count,color})=>{
      const slice=(count/total)*2*Math.PI*Math.min(prog,1);
      ctx.beginPath();ctx.moveTo(cx,cy);
      ctx.arc(cx,cy,r,angle,angle+slice);
      ctx.closePath();
      ctx.fillStyle=color;ctx.fill();
      ctx.strokeStyle='#fdf6f8';ctx.lineWidth=2.5;ctx.stroke();
      angle+=slice;
    });
    if(prog>=1){
      angle=-Math.PI/2;
      slices.forEach(({count})=>{
        const slice=(count/total)*2*Math.PI;
        if(count/total>0.04){
          const mid=angle+slice/2;
          ctx.fillStyle='#fff';ctx.font='bold 11px system-ui';
          ctx.textAlign='center';ctx.textBaseline='middle';
          ctx.fillText(Math.round(count/total*100)+'%',cx+(r*.7)*Math.cos(mid),cy+(r*.7)*Math.sin(mid));
        }
        angle+=slice;
      });
    }
    ctx.beginPath();ctx.arc(cx,cy,ri,0,2*Math.PI);
    ctx.fillStyle='#fdf6f8';ctx.fill();
    if(prog>=1){
      ctx.fillStyle='#e8637a';ctx.font='bold 22px system-ui';
      ctx.textAlign='center';ctx.textBaseline='middle';
      ctx.fillText(total,cx,cy-9);
      ctx.fillStyle='#888';ctx.font='11px system-ui';
      ctx.fillText('개 제품',cx,cy+10);
    }
    prog+=0.045;
    if(prog<1.05) requestAnimationFrame(animate);
  };
  animate();

  // Legend grouped by main category
  const legend=document.getElementById('chartLegend');
  let html='';
  MAIN_ORDER_CH.forEach(main=>{
    const mainSlices=slices.filter(s=>s.main===main);
    if(!mainSlices.length) return;
    const mainTotal=mainSlices.reduce((s,d)=>s+d.count,0);
    html+=`<div class="legend-group-hdr">${main}<span class="legend-main-pct">${Math.round(mainTotal/total*100)}%</span></div>`;
    mainSlices.forEach(({sub,count,color})=>{
      html+=`<div class="legend-item">
        <span class="legend-dot" style="background:${color}"></span>
        <span class="legend-label">${sub}</span>
        <span class="legend-pct">${Math.round(count/total*100)}%</span>
        <span class="legend-cnt">&nbsp;(${count})</span>
      </div>`;
    });
  });
  legend.innerHTML=html;
}

function setChPlatform(btn) {
  document.querySelectorAll('.ch-pill').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  chPlatform=btn.dataset.code;
  drawChartForPlatform(chPlatform);
}

function renderChart() {
  const platforms=[
    {code:'ALL',label:'전체'},
    {code:'US',label:'🇺🇸 Amazon US'},
    {code:'UK',label:'🇬🇧 Amazon UK'},
    {code:'JP',label:'🇯🇵 Amazon JP'},
    {code:'YS',label:'🍀 YesStyle'},
    {code:'OY',label:'🌿 OliveYoung'},
    {code:'QJ',label:'🛒 Qoo10 JP'},
  ];
  document.getElementById('gw').innerHTML=`
    <div class="chart-wrap">
      <div class="chart-title">품목별 카테고리 분포</div>
      <div class="chart-platform-filter">
        ${platforms.map(p=>`<button class="ch-pill${p.code===chPlatform?' active':''}" data-code="${p.code}" onclick="setChPlatform(this)">${p.label}</button>`).join('')}
      </div>
      <div class="chart-body">
        <canvas id="pieChart" width="340" height="340"></canvas>
        <div id="chartLegend" class="chart-legend"></div>
      </div>
    </div>`;
  setTimeout(()=>drawChartForPlatform(chPlatform),20);
}

// ── Ingredient Trend ──────────────────────────────────────────────────────────
let igPlatform = 'ALL';
const ING_GROUPS = {
  '보습': '#54b8f7',
  '안티에이징': '#7c6fe0',
  '미백/항산화': '#f5a623',
  '각질케어': '#3bb89e',
  '진정/트러블': '#e8637a',
  '천연/발효': '#10b981',
};
const INGREDIENT_LIST = [
  {label:'히알루론산', group:'보습', re:/hyaluronic|히알루론/i},
  {label:'세라마이드', group:'보습', re:/ceramide|세라마이드/i},
  {label:'판테놀(B5)', group:'보습', re:/panthenol|pantothenic|provitamin.?b5|판테놀/i},
  {label:'스쿠알란', group:'보습', re:/squalane|squalene|스쿠알/i},
  {label:'콜라겐', group:'보습', re:/collagen|콜라겐/i},
  {label:'레티놀', group:'안티에이징', re:/retinol|retinal|tretinoin|레티놀|레티날/i},
  {label:'펩타이드', group:'안티에이징', re:/peptide|펩타이드|펩티드/i},
  {label:'아데노신', group:'안티에이징', re:/adenosine|아데노신/i},
  {label:'EGF', group:'안티에이징', re:/\begf\b/i},
  {label:'나이아신아마이드', group:'미백/항산화', re:/niacinamide|나이아신아마이드/i},
  {label:'비타민C', group:'미백/항산화', re:/vitamin.?c|ascorbic|ascorbyl|비타민.?c/i},
  {label:'알부틴', group:'미백/항산화', re:/arbutin|알부틴/i},
  {label:'글루타치온', group:'미백/항산화', re:/glutathione|글루타치온/i},
  {label:'AHA(글리콜산)', group:'각질케어', re:/\baha\b|glycolic.?acid|lactic.?acid|mandelic/i},
  {label:'BHA(살리실산)', group:'각질케어', re:/\bbha\b|salicylic|살리실/i},
  {label:'PHA', group:'각질케어', re:/\bpha\b|gluconolactone/i},
  {label:'시카/센텔라', group:'진정/트러블', re:/centella|cica|madecassoside|시카|센텔라/i},
  {label:'티트리', group:'진정/트러블', re:/tea.?tree|티트리/i},
  {label:'알란토인', group:'진정/트러블', re:/allantoin|알란토인/i},
  {label:'녹차', group:'천연/발효', re:/green.?tea|camellia.?sinensis|녹차/i},
  {label:'발효(Ferment)', group:'천연/발효', re:/ferment|발효/i},
  {label:'프로폴리스', group:'천연/발효', re:/propolis|프로폴리스/i},
  {label:'알로에', group:'천연/발효', re:/aloe|알로에/i},
  {label:'로즈힙', group:'천연/발효', re:/rosehip|rose.?hip|로즈힙/i},
];

function drawIngredientChart(code) {
  const items = code==='ALL' ? all : all.filter(i=>i._country_code===code);
  const counts = {};
  items.forEach(item => {
    const text = (item.name||'') + ' ' + (item.categoryName||'') + ' ' + (item.categoryFullName||'');
    INGREDIENT_LIST.forEach(({label,group,re}) => {
      if (re.test(text)) {
        if (!counts[label]) counts[label]={count:0,group};
        counts[label].count++;
      }
    });
  });
  const sorted = Object.entries(counts)
    .map(([label,{count,group}])=>({label,count,group}))
    .sort((a,b)=>b.count-a.count)
    .slice(0,18);

  const maxCount = sorted[0]?.count || 1;
  const total = items.length;
  const container = document.getElementById('ingBars');
  if (!container) return;

  if (!sorted.length) {
    container.innerHTML='<div class="empty"><div class="em">🔬</div><p>성분 정보를 찾을 수 없어요.</p></div>';
    return;
  }
  container.innerHTML = sorted.map(({label,count,group})=>{
    const pct = Math.round(count/maxCount*100);
    const color = ING_GROUPS[group]||'#aaa';
    return `<div class="ing-row" data-w="${pct}">
      <div class="ing-label">${label}<span class="ing-grp-tag">${group}</span></div>
      <div class="ing-track">
        <div class="ing-fill" style="width:0%;background:${color}">
          <span class="ing-fill-cnt">${count}개</span>
        </div>
      </div>
      <div class="ing-meta">${Math.round(count/total*100)}% 제품</div>
    </div>`;
  }).join('');
  setTimeout(()=>{
    container.querySelectorAll('.ing-row').forEach(row=>{
      row.querySelector('.ing-fill').style.width = row.dataset.w + '%';
    });
  }, 40);
}

function setIgPlatform(btn) {
  document.querySelectorAll('.ig-pill').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  igPlatform = btn.dataset.code;
  drawIngredientChart(igPlatform);
}

function renderIngredientChart() {
  const platforms=[
    {code:'ALL',label:'전체'},
    {code:'US',label:'🇺🇸 Amazon US'},
    {code:'UK',label:'🇬🇧 Amazon UK'},
    {code:'JP',label:'🇯🇵 Amazon JP'},
    {code:'YS',label:'🍀 YesStyle'},
    {code:'OY',label:'🌿 OliveYoung'},
    {code:'QJ',label:'🛒 Qoo10 JP'},
  ];
  const grpLegend = Object.entries(ING_GROUPS).map(([g,c])=>
    `<div class="ing-grp"><span class="ing-grp-dot" style="background:${c}"></span>${g}</div>`
  ).join('');
  document.getElementById('gw').innerHTML=`
    <div class="ing-wrap">
      <div class="ing-title">🧪 트렌딩 성분 분석</div>
      <div class="ing-sub">제품명 기준 성분 키워드 추출 · 상위 18개 성분</div>
      <div class="chart-platform-filter">
        ${platforms.map(p=>`<button class="ch-pill ig-pill${p.code===igPlatform?' active':''}" data-code="${p.code}" onclick="setIgPlatform(this)">${p.label}</button>`).join('')}
      </div>
      <div class="ing-group-legend">${grpLegend}</div>
      <div class="ing-bars" id="ingBars"></div>
    </div>`;
  setTimeout(()=>drawIngredientChart(igPlatform), 20);
}

// ── Dashboard Charts (전체 데이터 기준) ───────────────────────────────────────
function drawDbPieChart() {
  const subCounts = {};
  all.forEach(item => {
    const {main, sub} = getDetailCategory(item);
    if (!subCounts[main]) subCounts[main]={};
    subCounts[main][sub]=(subCounts[main][sub]||0)+1;
  });
  const slices=[];
  MAIN_ORDER_CH.forEach(main => {
    if (!subCounts[main]) return;
    const subs=Object.entries(subCounts[main]).sort((a,b)=>b[1]-a[1]);
    const colors=MAIN_CAT_COLORS[main];
    subs.forEach(([sub,count],i)=>slices.push({main,sub,count,color:colors[i%colors.length]}));
  });
  const total=slices.reduce((s,d)=>s+d.count,0);
  if (!total) return;
  const canvas=document.getElementById('dbPieChart');
  if(!canvas) return;
  const dpr=window.devicePixelRatio||1;
  const W=canvas.offsetWidth||185,H=canvas.offsetHeight||185;
  canvas.width=W*dpr;canvas.height=H*dpr;
  canvas.style.width=W+'px';canvas.style.height=H+'px';
  const ctx=canvas.getContext('2d');
  ctx.scale(dpr,dpr);
  const cx=W/2,cy=H/2;
  const r=Math.min(cx,cy)-8,ri=r*0.44;
  let prog=0;
  const animate=()=>{
    ctx.clearRect(0,0,W,H);
    let angle=-Math.PI/2;
    slices.forEach(({count,color})=>{
      const slice=(count/total)*2*Math.PI*Math.min(prog,1);
      ctx.beginPath();ctx.moveTo(cx,cy);
      ctx.arc(cx,cy,r,angle,angle+slice);
      ctx.closePath();
      ctx.fillStyle=color;ctx.fill();
      ctx.strokeStyle='#fdf6f8';ctx.lineWidth=2;ctx.stroke();
      angle+=slice;
    });
    if(prog>=1){
      angle=-Math.PI/2;
      slices.forEach(({count})=>{
        const slice=(count/total)*2*Math.PI;
        if(count/total>0.05){
          const mid=angle+slice/2;
          ctx.fillStyle='#fff';ctx.font='bold 10px system-ui';
          ctx.textAlign='center';ctx.textBaseline='middle';
          ctx.fillText(Math.round(count/total*100)+'%',cx+(r*.7)*Math.cos(mid),cy+(r*.7)*Math.sin(mid));
        }
        angle+=slice;
      });
    }
    ctx.beginPath();ctx.arc(cx,cy,ri,0,2*Math.PI);
    ctx.fillStyle='#fdf6f8';ctx.fill();
    if(prog>=1){
      ctx.fillStyle='#e8637a';ctx.font='bold 17px system-ui';
      ctx.textAlign='center';ctx.textBaseline='middle';
      ctx.fillText(total,cx,cy-8);
      ctx.fillStyle='#888';ctx.font='10px system-ui';
      ctx.fillText('개 제품',cx,cy+9);
    }
    prog+=0.055;
    if(prog<1.05) requestAnimationFrame(animate);
  };
  animate();
  const legend=document.getElementById('dbPieLegend');
  if(!legend) return;
  let html='';
  MAIN_ORDER_CH.forEach(main=>{
    const mainSlices=slices.filter(s=>s.main===main);
    if(!mainSlices.length) return;
    const mainTotal=mainSlices.reduce((s,d)=>s+d.count,0);
    const mainDisplay = main === '기타' ? '기타 <span style="color:#aaa;font-weight:400;font-size:.86rem">(향수, 바디, 구강)</span>' : main;
    html+=`<div class="db-legend-grp">${mainDisplay} <span style="color:var(--pink);font-size:.86rem">${Math.round(mainTotal/total*100)}%</span></div>`;
    mainSlices.forEach(({sub,count,color})=>{
      html+=`<div class="db-legend-item">
        <span class="db-legend-dot" style="background:${color}"></span>
        <span class="db-legend-label">${sub}</span>
        <span class="db-legend-pct">${Math.round(count/total*100)}%</span>
      </div>`;
    });
  });
  legend.innerHTML=html;
}

const BRAND_LIST = [
  {label:'medicube', re:/\bmedicube\b/i},
  {label:'SKIN1004', re:/\bskin.?1004\b/i},
  {label:'Anua', re:/\banua\b/i},
  {label:'Torriden', re:/\btorriden\b/i},
  {label:'COSRX', re:/\bcosrx\b/i},
  {label:'Beauty of Joseon', re:/beauty.?of.?joseon/i},
  {label:'celimax', re:/\bcelimax\b/i},
  {label:'TIRTIR', re:/\btirtir\b/i},
  {label:'Numbuzin', re:/\bnumbuzin\b/i},
  {label:'MEDIHEAL', re:/\bmediheal\b/i},
  {label:'CeraVe', re:/\bcerave\b/i},
  {label:'Round Lab', re:/\bround.?lab\b/i},
  {label:'Mixsoon', re:/\bmixsoon\b/i},
  {label:'romand', re:/\bromand\b/i},
  {label:'Laneige', re:/\blaneige\b/i},
  {label:'innisfree', re:/\binnisfree\b/i},
  {label:'BIODANCE', re:/\bbiodance\b/i},
  {label:'Dr. Jart+', re:/dr\.?\s*jart/i},
  {label:'SOME BY MI', re:/some.?by.?mi/i},
  {label:'Neutrogena', re:/\bneutrogena\b/i},
  {label:'The Ordinary', re:/\bthe.?ordinary\b/i},
  {label:'Purito', re:/\bpurito\b/i},
  {label:'Isntree', re:/\bisntree\b/i},
  {label:'Sulwhasoo', re:/\bsulwhasoo\b/i},
  {label:'Dr.Melaxin', re:/dr\.?melaxin/i},
  {label:'Klairs', re:/\bklairs\b/i},
  {label:'Etude', re:/\betude\b/i},
  {label:'MISSHA', re:/\bmissha\b/i},
  {label:'Banila Co', re:/\bbanila\b/i},
  {label:'TonyMoly', re:/\btony.?moly\b/i},
  {label:'VT Cosmetics', re:/\bvt\s?cosme|\bvtcosme/i},
  {label:'Hero Cosmetics', re:/\bhero.?cosmetics\b|mighty.?patch/i},
  {label:'Biore', re:/\bbiore\b/i},
  {label:'Shiseido', re:/\bshiseido\b/i},
  {label:'Canmake', re:/\bcanmake\b/i},
  {label:'NIDA', re:/\bnida\b/i},
];
const BRAND_COLORS = ['#e8688a','#e88a9e','#d4768a','#c97d8a','#e89bb0','#d98ea0','#e0a0b0','#c4909a','#d0a0aa','#b8909a'];

function drawDbBrandChart() {
  const counts = {};
  all.forEach(item => {
    const text = item.name||'';
    BRAND_LIST.forEach(({label,re}) => {
      if (re.test(text)) {
        counts[label] = (counts[label]||0) + 1;
      }
    });
  });
  const sorted = Object.entries(counts)
    .map(([label,count])=>({label,count}))
    .sort((a,b)=>b.count-a.count)
    .slice(0,10);
  const maxCount = sorted[0]?.count || 1;
  const container = document.getElementById('dbBrandBars');
  if (!container) return;
  if (!sorted.length) {
    container.innerHTML='<div style="color:var(--muted);font-size:.86rem;text-align:center;padding:12px">브랜드 데이터 없음</div>';
    return;
  }
  container.innerHTML = sorted.map(({label,count},i)=>{
    const pct = Math.round(count/maxCount*100);
    const color = BRAND_COLORS[i%BRAND_COLORS.length];
    return `<div class="db-ing-row" data-w="${pct}">
      <div class="db-ing-label">${label}</div>
      <div class="db-ing-track">
        <div class="db-ing-fill" style="width:0%;background:${color}">
          <span class="db-ing-cnt">${count}</span>
        </div>
      </div>
    </div>`;
  }).join('');
  setTimeout(()=>{
    container.querySelectorAll('.db-ing-row').forEach(row=>{
      row.querySelector('.db-ing-fill').style.width = row.dataset.w + '%';
    });
  }, 40);
}

function drawDbIngChart() {
  const counts = {};
  all.forEach(item => {
    const text = (item.name||'') + ' ' + (item.categoryName||'') + ' ' + (item.categoryFullName||'');
    INGREDIENT_LIST.forEach(({label,group,re}) => {
      if (re.test(text)) {
        if (!counts[label]) counts[label]={count:0,group};
        counts[label].count++;
      }
    });
  });
  const sorted = Object.entries(counts)
    .map(([label,{count,group}])=>({label,count,group}))
    .sort((a,b)=>b.count-a.count)
    .slice(0,10);
  const maxCount = sorted[0]?.count || 1;
  const container = document.getElementById('dbIngBars');
  if (!container) return;
  if (!sorted.length) {
    container.innerHTML='<div style="color:var(--muted);font-size:.86rem;text-align:center;padding:12px">성분 데이터 없음</div>';
    return;
  }
  container.innerHTML = sorted.map(({label,count,group})=>{
    const pct = Math.round(count/maxCount*100);
    const color = ING_GROUPS[group]||'#aaa';
    return `<div class="db-ing-row" data-w="${pct}">
      <div class="db-ing-label">${label}</div>
      <div class="db-ing-track">
        <div class="db-ing-fill" style="width:0%;background:${color}">
          <span class="db-ing-cnt">${count}</span>
        </div>
      </div>
    </div>`;
  }).join('');
  setTimeout(()=>{
    container.querySelectorAll('.db-ing-row').forEach(row=>{
      row.querySelector('.db-ing-fill').style.width = row.dataset.w + '%';
    });
  }, 40);
}

loadData();

// ══════════════════════════════════════════════════════════════════════════
// MODE SWITCHER
// ══════════════════════════════════════════════════════════════════════════
let currentMode = 'product';
let videoHubInitialized = false;
let viralHubInitialized = false;
let viralAllVideos = [];

function switchMode(mode) {
  if (mode !== 'product' && mode !== 'viral') mode = 'product';
  sessionStorage.setItem('kbMode', mode);
  currentMode = mode;
  document.getElementById('product-hub').style.display = mode === 'product' ? '' : 'none';
  document.getElementById('viral-hub').style.display   = mode === 'viral'   ? 'block' : 'none';
  document.getElementById('mode-product').classList.toggle('active', mode === 'product');
  document.getElementById('mode-viral').classList.toggle('active', mode === 'viral');
  const title = document.getElementById('mainTitle');
  const sub   = document.getElementById('mainSub');
  const logo  = document.getElementById('mainLogo');
  const refreshBtn = document.getElementById('refreshBtn');
  if (mode === 'product') {
    title.textContent = 'Beauty Product Rankings';
    sub.textContent = '나라별 뷰티 베스트셀러';
    logo.textContent = '🛒';
    refreshBtn.style.display = '';
    document.getElementById('updLbl').style.display = '';
  } else {
    title.textContent = '🎬 TikTok Viral 카탈로그';
    sub.textContent = '7일 내 미국 틱톡 뷰티 TOP 영상 — 따라찍기용';
    logo.textContent = '🔥';
    refreshBtn.style.display = 'none';
    document.getElementById('updLbl').style.display = 'none';
    if (!viralHubInitialized) { initViralHub(); viralHubInitialized = true; }
  }
}

// ══════════════════════════════════════════════════════════════════════════
// VIRAL US HUB
// ══════════════════════════════════════════════════════════════════════════

function viralFmt(n) {
  n = parseInt(n) || 0;
  if (n >= 1e6) return (n/1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n/1e3).toFixed(1) + 'K';
  return String(n);
}

function viralDaysAgo(iso) {
  if (!iso) return '?';
  const dt = new Date(iso);
  if (isNaN(dt)) return '?';
  const diff = (Date.now() - dt.getTime()) / 1000;
  if (diff < 3600) return Math.max(1, Math.floor(diff/60)) + '분 전';
  if (diff < 86400) return Math.floor(diff/3600) + '시간 전';
  return Math.floor(diff/86400) + '일 전';
}

function viralEsc(s) {
  return String(s||'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function viralPlay(el) {
  const vid = el.dataset.vid;
  if (!vid || el.querySelector('iframe')) return;
  const iframe = document.createElement('iframe');
  // player/v1 = UI 최소화된 임베드, autoplay/loop/muted/controls 파라미터 지원
  iframe.src = `https://www.tiktok.com/player/v1/${vid}?autoplay=1&loop=1&music_info=0&description=0&closed_caption=0&rel=0`;
  iframe.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;border:0;background:#000';
  iframe.allow = 'autoplay; encrypted-media';
  iframe.setAttribute('allowfullscreen', '');
  el.appendChild(iframe);
}

function viralStop(el) {
  const iframe = el.querySelector('iframe');
  if (iframe) iframe.remove();
}

async function viralRunScrape() {
  const btn = document.getElementById('viral-scrape-btn');
  if (!confirm('새 스크랩을 시작할까요?\n\n· Apify 약 $4 비용 발생\n· 5~7분 소요\n· 끝나면 페이지 자동 새로고침'))
    return;
  btn.disabled = true;
  btn.style.opacity = '0.6';
  btn.innerHTML = '⏳ 트리거 중...';
  try {
    const res = await fetch('/api/run', {method: 'POST'});
    const data = await res.json();
    if (data.ok) {
      btn.innerHTML = '✅ 스크래핑 중 (5~7분)';
      alert('GitHub Actions 시작!\n5~7분 후 자동으로 페이지 새로고침됨.\nActions 페이지에서 진행 상황 확인 가능.');
      // 6분 후 자동 새로고침
      setTimeout(() => location.reload(), 6 * 60 * 1000);
    } else {
      alert('실패: ' + (data.error || 'Unknown'));
      btn.disabled = false;
      btn.style.opacity = '1';
      btn.innerHTML = '🔄 새 스크랩';
    }
  } catch (e) {
    alert('네트워크 오류: ' + e.message);
    btn.disabled = false;
    btn.style.opacity = '1';
    btn.innerHTML = '🔄 새 스크랩';
  }
}

async function initViralHub() {
  try {
    const dRes = await fetch('/api/viral_us/dates');
    const dates = await dRes.json();
    const sel = document.getElementById('viral-date-select');
    if (!dates || dates.length === 0) {
      sel.innerHTML = '<option>데이터 없음</option>';
      document.getElementById('viral-empty').style.display = '';
      document.getElementById('viral-grid').style.display = 'none';
      document.getElementById('viral-count-label').textContent = '0건';
      return;
    }
    sel.innerHTML = dates.map(d => `<option value="${d}">${d}</option>`).join('');
    sel.onchange = () => loadViralData(sel.value);
    await loadViralData(dates[0]);
  } catch (e) {
    console.error('viral init error', e);
  }
}

async function loadViralData(date) {
  try {
    const res = await fetch('/api/viral_us/data/' + date);
    const data = await res.json();
    // 7일 이내 영상만 (API/스크래퍼 안전망)
    const cutoff = Date.now() - 7 * 24 * 60 * 60 * 1000;
    viralAllVideos = (data || [])
      .filter(v => {
        if (!v.created_at) return false;
        const t = new Date(v.created_at).getTime();
        return !isNaN(t) && t >= cutoff;
      })
      .sort((a,b) => (b.stats?.views||0) - (a.stats?.views||0))
      .slice(0, 100);
    renderViralGrid();
  } catch (e) {
    console.error('viral load error', e);
  }
}

function renderViralGrid() {
  const grid = document.getElementById('viral-grid');
  const empty = document.getElementById('viral-empty');
  const lbl = document.getElementById('viral-count-label');
  if (!viralAllVideos.length) {
    grid.style.display = 'none';
    empty.style.display = '';
    lbl.textContent = '0건';
    return;
  }
  empty.style.display = 'none';
  grid.style.display = 'grid';
  lbl.textContent = `TOP ${viralAllVideos.length}건`;

  grid.innerHTML = viralAllVideos.map((v, i) => {
    const s = v.stats || {};
    const c = v.creator || {};
    const m = v.music || {};
    const cap = (v.caption || '').split('\n')[0].slice(0, 100);
    const tags = (v.hashtags || []).slice(0, 5).map(t => `<span style="background:#eef2ff;color:#3730a3;padding:2px 7px;border-radius:5px;font-size:0.74rem;font-weight:600">#${viralEsc(t)}</span>`).join(' ');
    const sound = m.title ? `${viralEsc(m.title)} — ${viralEsc(m.artist || '')}` : '(original sound)';
    const coverImg = v.cover ? `<img src="${viralEsc(v.cover)}" loading="lazy" style="width:100%;aspect-ratio:9/16;object-fit:cover;background:#f3f4f6" onerror="this.style.display='none'">` : `<div style="width:100%;aspect-ratio:9/16;background:#f3f4f6;display:flex;align-items:center;justify-content:center;font-size:2.5rem;color:#bbb">📹</div>`;
    const cover = `<div class="viral-thumb" data-vid="${viralEsc(v.id)}"
         onmouseenter="viralPlay(this)" onmouseleave="viralStop(this)"
         style="position:relative;width:100%;aspect-ratio:9/16;overflow:hidden;background:#000">
      ${coverImg}
    </div>`;

    return `
      <div style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 10px rgba(0,0,0,0.06);display:flex;flex-direction:column">
        <a href="${viralEsc(v.url)}" target="_blank" style="position:relative;display:block">
          ${cover}
          <div style="position:absolute;top:8px;left:8px;background:rgba(220,38,38,0.95);color:#fff;width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:0.82rem;font-weight:900">${i+1}</div>
          <div style="position:absolute;bottom:8px;left:8px;background:rgba(0,0,0,0.7);color:#fff;font-size:0.78rem;font-weight:700;padding:3px 8px;border-radius:5px">👁 ${viralFmt(s.views)}</div>
        </a>
        <div style="padding:12px 14px;display:flex;flex-direction:column;gap:6px;flex:1">
          <div style="display:flex;justify-content:space-between;font-size:0.82rem">
            <a href="${viralEsc(c.url)}" target="_blank" style="color:#1e3a8a;font-weight:700;text-decoration:none">@${viralEsc(c.username)}</a>
            <span style="color:#888">${viralDaysAgo(v.created_at)}</span>
          </div>
          <div style="font-size:0.8rem;color:#444;display:flex;gap:10px">
            <span>❤️ ${viralFmt(s.likes)}</span>
            <span>💬 ${viralFmt(s.comments)}</span>
            <span>🔖 ${viralFmt(s.saves)}</span>
          </div>
          ${cap ? `<div style="font-size:0.84rem;color:#333;line-height:1.45">${viralEsc(cap)}</div>` : ''}
          <div style="font-size:0.78rem;color:#666;display:flex;gap:5px;align-items:center">🎵 <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${sound}</span></div>
          ${tags ? `<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:auto">${tags}</div>` : ''}
        </div>
      </div>
    `;
  }).join('');
}

</script>
</body>
</html>
"""

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    print(f"🛒 Amazon Beauty Rankings → http://localhost:{port}")
    app.run(debug=True, host="0.0.0.0", port=port)
