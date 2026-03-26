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

@app.route("/api/rankings/changes")
def api_rankings_changes():
    from datetime import date as _date, timedelta
    period = request.args.get("period", "1d")
    period_days = {"1d": 1, "1w": 7, "30d": 30, "90d": 90}.get(period, 1)
    country_filter = request.args.get("country", "")

    cache = load_cache()
    if not cache:
        return jsonify({"error": "no data", "changes": []})

    current_items = cache.get("items", [])
    today_str = datetime.utcnow().strftime("%Y-%m-%d")

    # 현재 순위를 딕셔너리로
    current = {}
    for item in current_items:
        uid = _item_uid(item)
        current[uid] = {
            "id": uid,
            "name": item.get("name", ""),
            "rank": item.get("position", 0),
            "category": item.get("categoryName", ""),
            "country": item.get("_country_code", ""),
            "flag": item.get("_country_flag", ""),
            "thumb": item.get("thumbnailUrl", ""),
        }

    # 비교 날짜 = target 이하의 가장 최근 스냅샷
    target_date = (_date.today() - timedelta(days=period_days)).isoformat()
    snap_dates = list_snapshot_dates()
    compare_date = None
    prev = {}
    for d in snap_dates:
        if d <= target_date:
            compare_date = d
            snap_items = load_ranking_snapshot(d)
            if snap_items:
                for it in snap_items:
                    prev[it["id"]] = it
            break

    if not prev:
        return jsonify({
            "period": period, "compare_date": None,
            "available_dates": snap_dates,
            "message": f"비교 데이터 없음 ({period_days}일 이전 스냅샷 필요)",
            "changes": []
        })

    changes = []
    for uid, cur in current.items():
        if country_filter and cur["country"] != country_filter:
            continue
        if uid in prev:
            delta = prev[uid]["rank"] - cur["rank"]  # 양수 = 순위 상승 (숫자 감소)
            if delta != 0:
                changes.append({**cur, "prev_rank": prev[uid]["rank"], "change": delta, "is_new": False})
        else:
            changes.append({**cur, "prev_rank": None, "change": None, "is_new": True})

    # 신규 제외 최대 상승/하락 각 30개 + 신규 20개
    risen  = sorted([c for c in changes if not c["is_new"] and c["change"] > 0], key=lambda x: -x["change"])[:30]
    fallen = sorted([c for c in changes if not c["is_new"] and c["change"] < 0], key=lambda x: x["change"])[:30]
    new    = [c for c in changes if c["is_new"]][:20]

    return jsonify({
        "period": period,
        "current_date": today_str,
        "compare_date": compare_date,
        "risen": risen,
        "fallen": fallen,
        "new": new,
    })

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

TWITTER_ACTOR_ID = "CJdippxWmn9uRfooo"
_TW_TMP = "/tmp/twitter_apify_cache.json"

def _normalize_tweet(item, source_tag="twitter"):
    try:
        if item.get("noResults") or not item.get("id"):
            return None
        author = item.get("author", {}) or {}
        media_url = ""
        for m in (item.get("extendedEntities", {}) or {}).get("media", []):
            u = m.get("media_url_https") or m.get("media_url", "")
            if u:
                media_url = u; break
        hashtags = [h.get("text","").lower() for h in (item.get("entities",{}) or {}).get("hashtags",[]) if h.get("text")]
        username = author.get("userName","") or author.get("username","")
        return {
            "id": str(item.get("id","")), "url": item.get("url","") or item.get("twitterUrl",""),
            "text": (item.get("text","") or "")[:500], "created_at": item.get("createdAt",""),
            "source_tag": source_tag, "lang": item.get("lang","ja"),
            "views": item.get("viewCount",0) or 0, "likes": item.get("likeCount",0) or 0,
            "retweets": item.get("retweetCount",0) or 0, "replies": item.get("replyCount",0) or 0,
            "bookmarks": item.get("bookmarkCount",0) or 0, "hashtags": hashtags, "media_url": media_url,
            "is_retweet": item.get("retweeted_tweet") is not None,
            "author": {"username": username, "name": author.get("name",""),
                       "followers": author.get("followers",0) or 0,
                       "verified": author.get("isBlueVerified",False) or author.get("isVerified",False),
                       "avatar": author.get("profilePicture","").replace("_normal.","_400x400."),
                       "url": author.get("url","") or f"https://x.com/{username}"},
        }
    except Exception:
        return None

def fetch_twitter_from_apify():
    """Apify 최신 Twitter 액터 실행 데이터 가져오기 (캐시: /tmp)"""
    # /tmp 캐시 확인 — 당일치면 재사용
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if os.path.exists(_TW_TMP):
        try:
            with open(_TW_TMP) as f:
                cached = json.load(f)
            if cached.get("date") == today:
                return cached.get("items", [])
        except Exception:
            pass
    if not APIFY_TOKEN:
        return []
    try:
        # 최신 실행 가져오기
        r = requests.get(
            f"https://api.apify.com/v2/acts/{TWITTER_ACTOR_ID}/runs"
            f"?token={APIFY_TOKEN}&limit=1&sortBy=startedAt&sortOrder=DESCENDING",
            timeout=15)
        r.raise_for_status()
        runs = r.json().get("data", {}).get("items", [])
        if not runs or runs[0].get("status") != "SUCCEEDED":
            return []
        dataset_id = runs[0]["defaultDatasetId"]
        dr = requests.get(
            f"https://api.apify.com/v2/datasets/{dataset_id}/items"
            f"?token={APIFY_TOKEN}&format=json&limit=2000",
            timeout=30)
        dr.raise_for_status()
        raw = dr.json()
        seen, tweets = set(), []
        for item in raw:
            t = _normalize_tweet(item, "twitter-jp")
            if t and t["id"] and t["id"] not in seen:
                seen.add(t["id"]); tweets.append(t)
        tweets.sort(key=lambda t: t["likes"] + t["retweets"]*3, reverse=True)
        try:
            with open(_TW_TMP, "w") as f:
                json.dump({"date": today, "items": tweets}, f, ensure_ascii=False)
        except Exception:
            pass
        return tweets
    except Exception as e:
        print(f"[Twitter/Apify] {e}")
        return []

@app.route("/api/x/dates")
def api_x_dates():
    files = sorted(glob.glob(os.path.join(_SCRIPT_DIR, "twitter_*.json")), reverse=True)
    dates = [os.path.basename(f).replace("twitter_","").replace(".json","") for f in files]
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if today not in dates:
        dates.insert(0, today)
    return jsonify(dates)

@app.route("/api/x/data/<date>")
def api_x_data_date(date):
    # 로컬 파일 우선
    path = os.path.join(_SCRIPT_DIR, f"twitter_{date}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return jsonify(json.load(f))
    # Apify 최신 데이터 폴백
    tweets = fetch_twitter_from_apify()
    return jsonify(tweets)

@app.route("/api/cron/refresh", methods=["GET","POST"])
def api_cron_refresh():
    """Vercel Cron이 매일 9시에 호출 — 상품 데이터 갱신"""
    items = fetch_from_apify(refresh=True)
    cache = save_cache(items)
    return jsonify({"ok": True, "count": len(items), "updated_at": cache["updated_at"]})

@app.route("/api/trends/timeline")
def api_trends_timeline():
    """Product-based: K-beauty 성분·타입 키워드별 마켓 상품 수 분포"""
    KBEAUTY_KWS = [
        "niacinamide","ceramide","hyaluronic","retinol","peptide",
        "snail","centella","cica","propolis","mugwort",
        "vitamin c","aha","bha","sunscreen","spf",
        "essence","ampoule","serum","sheet mask","toner",
        "collagen","ferment","galactomyces","tranexamic","bakuchiol",
    ]
    KW_KO = {
        "niacinamide":"나이아신아마이드","ceramide":"세라마이드","hyaluronic":"히알루론산",
        "retinol":"레티놀","peptide":"펩타이드","snail":"달팽이뮤신",
        "centella":"센텔라","cica":"시카","propolis":"프로폴리스","mugwort":"쑥",
        "vitamin c":"비타민C","aha":"AHA","bha":"BHA","sunscreen":"선크림","spf":"SPF",
        "essence":"에센스","ampoule":"앰플","serum":"세럼","sheet mask":"시트마스크","toner":"토너",
        "collagen":"콜라겐","ferment":"발효","galactomyces":"갈락토미세스",
        "tranexamic":"트라넥사믹","bakuchiol":"바쿠치올",
    }
    KW_CAT = {
        "niacinamide":"성분","ceramide":"성분","hyaluronic":"성분","retinol":"성분",
        "peptide":"성분","snail":"성분","centella":"성분","cica":"성분",
        "propolis":"성분","mugwort":"성분","vitamin c":"성분","aha":"성분","bha":"성분",
        "tranexamic":"성분","bakuchiol":"성분","collagen":"성분",
        "ferment":"성분","galactomyces":"성분",
        "sunscreen":"기능성","spf":"기능성",
        "essence":"제형","ampoule":"제형","serum":"제형","sheet mask":"제형","toner":"제형",
    }
    MARKETS = ["US","UK","JP","OY","TT","YS","QJ"]
    cache = load_cache()
    items = cache.get("items", [])
    data = {kw: {m: 0 for m in MARKETS} for kw in KBEAUTY_KWS}
    for item in items:
        market = item.get("_country_code", "")
        if market not in MARKETS:
            continue
        text = (item.get("name","") or "").lower() + " " + (item.get("categoryFullName","") or "").lower()
        for kw in KBEAUTY_KWS:
            if kw in text:
                data[kw][market] += 1
    result = {kw: counts for kw, counts in data.items() if sum(counts.values()) > 0}
    return jsonify({
        "keywords": list(result.keys()),
        "markets": MARKETS,
        "data": result,
        "labels": KW_KO,
        "categories": KW_CAT,
    })


@app.route("/api/trends/creators")
def api_trends_creators():
    """크리에이터 초기 신호 감지 — TikTok 팔로워 적은데 인게이지먼트 높은 크리에이터"""
    BEAUTY_TERMS = {
        "kbeauty","k-beauty","koreanbeauty","koreanskincare","korean skincare",
        "cosrx","anua","laneige","romand","skin1004","3ce","innisfree","missha",
        "skincare","serum","toner","moisturizer","sunscreen","niacinamide",
        "ceramide","retinol","hyaluronic","beauty of joseon","oliveyoung",
        "kbeautyroutine","kbeautyreview","viralkbeauty","kbeautyhaul",
        "skinbarrier","cica","pdrn","aha","bha","peptide","snail","centella",
        "skintok","glasskin","koreanskincareroutine",
    }
    files = sorted(glob.glob(os.path.join(_SCRIPT_DIR, "data_*.json")), reverse=True)[:14]
    creators = {}

    for fpath in files:
        try:
            with open(fpath, encoding="utf-8") as f:
                videos = json.load(f)
        except Exception:
            continue
        for v in videos:
            creator = v.get("creator") or {}
            username = (creator.get("username") or "").strip()
            if not username:
                continue
            followers = int(creator.get("followers") or 0)
            if followers < 100 or followers > 100_000:
                continue

            caption = (v.get("caption") or "").lower()
            tags_lower = " ".join(v.get("hashtags") or []).lower()
            beauty_check = caption + " " + tags_lower
            if not any(term in beauty_check for term in BEAUTY_TERMS):
                continue

            stats    = v.get("stats") or {}
            likes    = int(stats.get("likes",    0) or 0)
            comments = int(stats.get("comments", 0) or 0)
            shares   = int(stats.get("shares",   0) or 0)
            saves    = int(stats.get("saves",    0) or 0)
            views    = int(stats.get("views",    0) or 0)
            engagement = likes + comments * 2 + shares * 3 + saves * 2
            # TikTok은 뷰 기준 ER이 더 의미있음 (알고리즘 배포 특성)
            er = engagement / max(views, 1) * 100

            if username not in creators:
                creators[username] = {
                    "username":    username,
                    "name":        creator.get("nickname") or username,
                    "followers":   followers,
                    "verified":    bool(creator.get("verified")),
                    "avatar":      creator.get("avatar") or "",
                    "url":         creator.get("url") or f"https://www.tiktok.com/@{username}",
                    "video_count": 0,
                    "total_engagement": 0,
                    "total_er":    0.0,
                    "total_views": 0,
                    "keywords":    [],
                    "videos":      [],
                }
            c = creators[username]
            c["video_count"] += 1
            c["total_engagement"] += engagement
            c["total_er"] += er
            c["total_views"] += views
            tag = v.get("source_tag") or ""
            if tag and tag not in c["keywords"]:
                c["keywords"].append(tag)
            c["videos"].append({
                "caption":    (v.get("caption") or "")[:200],
                "likes":      likes,
                "comments":   comments,
                "shares":     shares,
                "saves":      saves,
                "views":      views,
                "url":        v.get("url") or "",
                "cover":      v.get("cover") or "",
                "engagement": engagement,
            })

    result = []
    for c in creators.values():
        if c["video_count"] < 1:
            continue
        avg_er = c["total_er"] / c["video_count"]
        discovery_bonus = 1 + (1 - min(c["followers"] / 50_000, 1)) * 0.6
        signal = round(avg_er * discovery_bonus, 2)
        top_video = sorted(c["videos"], key=lambda x: x["engagement"], reverse=True)[0]
        result.append({
            "username":    c["username"],
            "name":        c["name"],
            "followers":   c["followers"],
            "verified":    c["verified"],
            "avatar":      c["avatar"],
            "url":         c["url"],
            "video_count": c["video_count"],
            "avg_er":      round(avg_er, 2),
            "total_views": c["total_views"],
            "signal_score":signal,
            "keywords":    c["keywords"][:6],
            "top_video":   top_video,
        })

    result.sort(key=lambda x: x["signal_score"], reverse=True)
    return jsonify(result[:30])


@app.route("/api/trends/gaps")
def api_trends_gaps():
    """Product-based: 아시아(OY·JP·QJ) vs 서양(US·UK·TT·YS) 성분 공백 탐지"""
    KBEAUTY_KWS = [
        "niacinamide","ceramide","hyaluronic","retinol","peptide",
        "snail","centella","cica","propolis","mugwort",
        "vitamin c","aha","bha","sunscreen","spf",
        "essence","ampoule","serum","sheet mask","toner",
        "collagen","ferment","galactomyces","tranexamic","bakuchiol",
        "azelaic","bifida","pdrn","madecassoside","phytosphingosine",
    ]
    ASIAN = {"OY","JP","QJ"}
    WEST  = {"US","UK","TT","YS"}
    cache = load_cache()
    items = cache.get("items", [])
    data = {kw: {"asian":0,"western":0,"asian_d":{},"western_d":{}} for kw in KBEAUTY_KWS}
    for item in items:
        market = item.get("_country_code","")
        text = (item.get("name","") or "").lower() + " " + (item.get("categoryFullName","") or "").lower()
        for kw in KBEAUTY_KWS:
            if kw in text:
                d = data[kw]
                if market in ASIAN:
                    d["asian"] += 1
                    d["asian_d"][market] = d["asian_d"].get(market, 0) + 1
                elif market in WEST:
                    d["western"] += 1
                    d["western_d"][market] = d["western_d"].get(market, 0) + 1
    gaps = []
    for kw, d in data.items():
        total = d["asian"] + d["western"]
        if total < 1:
            continue
        gap_score = d["asian"] / (1 + d["western"] * 1.5)
        gaps.append({
            "keyword":       kw,
            "asian_count":   d["asian"],
            "western_count": d["western"],
            "total_count":   total,
            "asian_detail":  d["asian_d"],
            "western_detail":d["western_d"],
            "gap_score":     round(gap_score, 2),
        })
    gaps.sort(key=lambda x: x["gap_score"], reverse=True)
    return jsonify(gaps)


@app.route("/api/run/twitter", methods=["POST"])
def api_run_twitter():
    script = os.path.join(_SCRIPT_DIR, "twitter_scraper.py")
    log    = open(os.path.join(_SCRIPT_DIR, "last_twitter_run.log"), "w")
    subprocess.Popen([sys.executable, script], stdout=log, stderr=subprocess.STDOUT)
    return jsonify({"ok": True})


@app.route("/api/trends/brands")
def api_trends_brands():
    """바이럴 뷰티 영상(TikTok)에서 언급된 K-beauty 브랜드 분석"""
    BRANDS = [
        "cosrx","anua","laneige","romand","skin1004","3ce","innisfree","missha",
        "beauty of joseon","torriden","isntree","klairs","round lab","purito",
        "tirtir","numbuzin","mary&may","vt cosmetics","mediheal","beplain",
        "biodance","mixsoon","haruharu","axis-y","rovectin","some by mi",
        "dr.jart","etude","tony moly","nature republic","skinfood","medicube",
        "fwee","pyunkang","i'm from","papa recipe","d'alba","aestura",
        "holika holika","neogen","makeheal","by wishtrend","manyo","tocobo",
        "needly","glow recipe","dear klairs","ma:nyo","oliveyoung",
    ]
    files = sorted(glob.glob(os.path.join(_SCRIPT_DIR, "data_*.json")), reverse=True)[:14]
    all_videos = []
    for fpath in files:
        date_str = os.path.basename(fpath).replace("data_","").replace(".json","")
        try:
            with open(fpath, encoding="utf-8") as f:
                for v in json.load(f):
                    v["_fd"] = date_str
                    all_videos.append(v)
        except Exception:
            continue

    if not all_videos:
        return jsonify([])

    dates = sorted({v["_fd"] for v in all_videos}, reverse=True)
    recent = set(dates[:4]) if len(dates) >= 4 else set(dates)

    brand_data = {b: {"brand": b, "count": 0, "recent_count": 0,
                      "total_views": 0, "total_eng": 0, "top_video": None}
                  for b in BRANDS}

    for v in all_videos:
        cap  = (v.get("caption") or "").lower()
        tags = " ".join(v.get("hashtags") or []).lower()
        text = cap + " " + tags
        stats = v.get("stats") or {}
        views = int(stats.get("views", 0) or 0)
        eng   = int(v.get("engagement", 0) or 0)
        is_recent = v["_fd"] in recent
        for b in BRANDS:
            if b in text:
                d = brand_data[b]
                d["count"] += 1
                d["total_views"] += views
                d["total_eng"]   += eng
                if is_recent:
                    d["recent_count"] += 1
                tv = d["top_video"]
                if tv is None or views > tv["views"]:
                    cr = v.get("creator") or {}
                    d["top_video"] = {
                        "url":     v.get("url",""),
                        "caption": (v.get("caption") or "")[:120],
                        "cover":   v.get("cover",""),
                        "views":   views,
                        "likes":   int(stats.get("likes", 0) or 0),
                        "creator": cr.get("nickname","") or cr.get("username",""),
                    }

    result = [d for d in brand_data.values() if d["count"] > 0]
    result.sort(key=lambda x: x["total_views"], reverse=True)
    return jsonify(result)


@app.route("/api/trends/brands/videos")
def api_trends_brand_videos():
    """특정 브랜드가 언급된 TikTok 영상 목록 (뷰 순 최대 30개)"""
    brand = (request.args.get("brand") or "").lower().strip()
    if not brand:
        return jsonify([])
    files = sorted(glob.glob(os.path.join(_SCRIPT_DIR, "data_*.json")), reverse=True)[:14]
    videos = []
    for fpath in files:
        date_str = os.path.basename(fpath).replace("data_","").replace(".json","")
        try:
            with open(fpath, encoding="utf-8") as f:
                for v in json.load(f):
                    cap  = (v.get("caption") or "").lower()
                    tags = " ".join(v.get("hashtags") or []).lower()
                    if brand not in cap + " " + tags:
                        continue
                    stats = v.get("stats") or {}
                    cr    = v.get("creator") or {}
                    views = int(stats.get("views", 0) or 0)
                    videos.append({
                        "url":     v.get("url",""),
                        "caption": (v.get("caption") or "")[:200],
                        "cover":   v.get("cover",""),
                        "date":    date_str,
                        "views":   views,
                        "likes":   int(stats.get("likes", 0) or 0),
                        "comments":int(stats.get("comments", 0) or 0),
                        "saves":   int(stats.get("saves", 0) or 0),
                        "creator": cr.get("nickname","") or cr.get("username",""),
                        "creator_url": v.get("url",""),
                    })
        except Exception:
            continue
    videos.sort(key=lambda x: x["views"], reverse=True)
    return jsonify(videos[:30])


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
     font-size:15px;line-height:1.5;letter-spacing:-0.01em}
img{image-rendering:-webkit-optimize-contrast;image-rendering:crisp-edges}

/* header */
header{background:linear-gradient(135deg,#e8637a 0%,#c0445f 100%);color:#fff;
  padding:0 28px;height:62px;display:flex;align-items:center;
  justify-content:space-between;position:sticky;top:0;z-index:100;
  box-shadow:0 3px 16px rgba(192,68,95,.3)}
.h-left{display:flex;align-items:center;gap:12px}
.h-logo{font-size:1.5rem}
.h-title{font-size:1.1rem;font-weight:800}
.h-sub{font-size:.82rem;opacity:.82;margin-top:1px}
.h-right{display:flex;align-items:center;gap:10px}
.mode-switch{display:flex;background:rgba(255,255,255,.2);border-radius:10px;padding:3px;gap:3px}
.mode-btn{padding:7px 14px;border:none;border-radius:8px;cursor:pointer;font-weight:700;
  font-size:.82rem;transition:all .2s;background:transparent;color:rgba(255,255,255,.75)}
.mode-btn:hover{background:rgba(255,255,255,.15);color:#fff}
.mode-btn.active{background:#fff;color:var(--pink)}
.upd{font-size:.82rem;opacity:.75}
#refreshBtn{background:#fff;color:var(--pink);border:none;padding:8px 16px;
  border-radius:8px;font-weight:700;font-size:.82rem;cursor:pointer;
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
  font-size:.8rem;padding:1px 6px;border-radius:10px;margin-left:4px;font-weight:700}

/* toolbar */
.toolbar{padding:14px 24px;background:var(--surface);border-bottom:1px solid var(--border);
  display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.sw{position:relative;flex:1;min-width:200px;max-width:340px}
.sw input{width:100%;padding:9px 12px 9px 36px;border:1.5px solid var(--border);
  border-radius:9px;font-size:.84rem;background:var(--bg);outline:none;transition:border .2s}
.sw input:focus{border-color:var(--pink);background:#fff}
.sw .si{position:absolute;left:11px;top:50%;transform:translateY(-50%);
  color:var(--muted);pointer-events:none}
select.fs{padding:9px 12px;border:1.5px solid var(--border);border-radius:9px;
  font-size:.84rem;background:var(--bg);outline:none;cursor:pointer;
  transition:border .2s;color:var(--text)}
select.fs:focus{border-color:var(--pink);background:#fff}
.rc{margin-left:auto;font-size:.8rem;color:var(--muted)}

/* grid */
.gw{padding:20px 24px}
.cat-sec{margin-bottom:32px}
.cat-hdr{font-size:.78rem;font-weight:700;color:var(--pink);text-transform:uppercase;
  letter-spacing:.8px;margin-bottom:14px;padding-bottom:8px;
  border-bottom:2px solid var(--pink-light);display:flex;align-items:center;gap:8px}
.cat-cnt{background:var(--pink-light);color:var(--pink);font-size:.82rem;
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
  font-size:.82rem;font-weight:900;color:#fff;box-shadow:0 2px 6px rgba(0,0,0,.25)}
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
.cname{font-size:.8rem;font-weight:600;line-height:1.35;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.cstars{display:flex;align-items:center;gap:4px}
.sv{color:var(--gold);font-size:.84rem;letter-spacing:-1px}
.sn{font-size:.82rem;color:var(--muted)}
.crev{font-size:.82rem;color:var(--muted)}
.cprice{font-size:.88rem;font-weight:800;color:var(--pink);margin-top:auto;padding-top:6px}
.cprice.np{color:var(--muted);font-weight:400;font-size:.84rem}
.casin{font-size:.8rem;color:#bbb;margin-top:2px}
.empty{text-align:center;padding:60px 20px;color:var(--muted)}
.empty .em{font-size:3rem;margin-bottom:12px}

/* Dashboard overview */
.dash{padding:20px 24px;display:flex;gap:14px;overflow-x:auto;align-items:flex-start}
.dash-col{flex:1;min-width:200px;max-width:260px;background:var(--surface);
  border-radius:14px;border:1px solid var(--border);overflow:hidden;
  box-shadow:var(--shadow);display:flex;flex-direction:column}
.dash-hdr{padding:11px 14px;display:flex;align-items:center;gap:8px;
  background:linear-gradient(135deg,var(--pink-light) 0%,#fff 100%);
  border-bottom:2px solid var(--pink-mid);font-weight:800;font-size:.82rem;color:var(--pink)}
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
.dash-rnk{position:absolute;top:8px;left:8px;font-size:.82rem;font-weight:900;
  color:#fff;width:24px;height:24px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;
  box-shadow:0 2px 6px rgba(0,0,0,.25);z-index:2}
.dash-info{padding:8px 10px 10px;flex:1}
.dash-name{font-size:.84rem;font-weight:600;line-height:1.35;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;color:var(--text)}
.dash-price{font-size:.84rem;font-weight:800;color:var(--pink);margin-top:4px}
@media(max-width:700px){.dash{flex-wrap:nowrap}.dash-col{min-width:160px}}

/* Combined Dashboard layout */
.db-main{display:flex;gap:16px;padding:16px 20px;height:calc(100vh - 56px);overflow:hidden;box-sizing:border-box}
.db-left{flex:1.4;display:flex;flex-direction:column;overflow:hidden;min-width:0}
.db-right{width:370px;flex-shrink:0;display:flex;flex-direction:column;gap:12px;overflow-y:auto;padding-right:2px}
.db-section-hdr{font-size:.88rem;font-weight:800;color:var(--pink);margin-bottom:10px}
.dash-compact{display:flex;gap:10px;flex:1;overflow-x:auto;overflow-y:auto;align-items:flex-start;padding-bottom:4px}
.dash-mini-col{flex:1;min-width:160px;max-width:210px;background:var(--surface);border-radius:12px;border:1px solid var(--border);overflow:hidden;box-shadow:var(--shadow);display:flex;flex-direction:column;flex-shrink:0;cursor:pointer;transition:box-shadow .18s,border-color .18s}
.dash-mini-col:hover{box-shadow:0 4px 12px rgba(211,61,90,.12);border-color:var(--pink)}
.dash-mini-hdr{padding:8px 11px;display:flex;align-items:center;gap:6px;background:linear-gradient(135deg,var(--pink-light) 0%,#fff 100%);border-bottom:2px solid var(--pink-mid);font-weight:800;font-size:.84rem;color:var(--pink);white-space:nowrap}
.dash-mini-item{display:flex;flex-direction:column;text-decoration:none;color:inherit;border-bottom:1px solid var(--border);transition:background .15s;overflow:hidden}
.dash-mini-item:hover{background:var(--pink-light)}
.dash-mini-item:last-child{border-bottom:none}
.dash-mini-thumb{width:100%;padding-top:78%;position:relative;background:#ede9e7;overflow:hidden;border-bottom:1px solid #e0d9d6}
.dash-mini-thumb img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;padding:8px}
.dash-mini-ph{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:2rem;color:var(--pink-mid)}
.dash-mini-rank{position:absolute;top:7px;left:7px;font-size:.8rem;font-weight:900;color:#fff;width:22px;height:22px;border-radius:50%;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 6px rgba(0,0,0,.25);z-index:2}
.dash-mini-name{padding:8px 10px 4px;font-size:.84rem;font-weight:700;line-height:1.4;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;color:var(--text)}
.dash-mini-price{padding:0 10px 8px;font-size:.82rem;font-weight:800;color:var(--pink)}
.db-chart-card{background:var(--surface);border-radius:14px;border:1px solid var(--border);box-shadow:var(--shadow);padding:14px 16px;flex-shrink:0;cursor:pointer;transition:box-shadow .18s,border-color .18s}
.db-chart-card:hover{box-shadow:0 4px 12px rgba(211,61,90,.12);border-color:var(--pink)}
.db-card-title{font-size:.82rem;font-weight:800;color:var(--pink);margin-bottom:10px}
.db-pie-body{display:flex;gap:12px;align-items:flex-start}
.db-pie-legend{display:flex;flex-direction:column;gap:2px;flex:1;min-width:0;overflow-y:auto;max-height:190px}
.db-legend-grp{font-size:.8rem;font-weight:800;color:var(--text);margin-top:6px;margin-bottom:1px;border-left:2px solid var(--pink);padding-left:4px}
.db-legend-grp:first-child{margin-top:0}
.db-legend-item{display:flex;align-items:center;gap:5px;font-size:.8rem;padding:2px 3px;border-radius:4px}
.db-legend-dot{width:9px;height:9px;border-radius:2px;flex-shrink:0}
.db-legend-label{flex:1;font-weight:600;color:var(--text);overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.db-legend-pct{font-weight:800;color:var(--pink);font-size:.8rem;flex-shrink:0}
.db-ing-bars{display:flex;flex-direction:column;gap:5px}
.db-ing-row{display:flex;align-items:center;gap:8px}
.db-ing-label{width:98px;text-align:right;font-size:.8rem;font-weight:700;color:var(--text);flex-shrink:0;line-height:1.2}
.db-ing-track{flex:1;height:20px;background:var(--pink-light);border-radius:5px;overflow:hidden}
.db-ing-fill{height:100%;border-radius:5px;display:flex;align-items:center;padding:0 7px;transition:width .55s cubic-bezier(.4,0,.2,1);width:0%}
.db-ing-cnt{font-size:.8rem;font-weight:800;color:#fff;white-space:nowrap}
@media(max-width:900px){.db-main{flex-direction:column;height:auto;overflow:auto}.db-right{width:100%}}

/* YesStyle subcategory pills */
.ys-pills{display:flex;gap:6px;align-items:center}
.ys-pill{padding:6px 14px;border-radius:20px;border:1.5px solid var(--border);
  background:var(--bg);color:var(--muted);font-size:.8rem;font-weight:600;
  cursor:pointer;transition:all .18s;white-space:nowrap}
.ys-pill:hover{border-color:var(--pink);color:var(--pink)}
.ys-pill.active{background:var(--pink);border-color:var(--pink);color:#fff}

/* Chart view */
.chart-wrap{padding:24px;max-width:960px;margin:0 auto}
.chart-title{font-size:1.05rem;font-weight:800;color:var(--pink);margin-bottom:16px}
.chart-platform-filter{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:24px}
.ch-pill{padding:7px 16px;border-radius:20px;border:1.5px solid var(--border);
  background:var(--bg);color:var(--muted);font-size:.82rem;font-weight:600;
  cursor:pointer;transition:all .18s;white-space:nowrap}
.ch-pill:hover{border-color:var(--pink);color:var(--pink)}
.ch-pill.active{background:var(--pink);border-color:var(--pink);color:#fff}
.chart-body{display:flex;gap:36px;align-items:center;flex-wrap:wrap;justify-content:center;padding:8px 0}
.chart-legend{display:flex;flex-direction:column;gap:6px;min-width:180px}
.legend-item{display:flex;align-items:center;gap:8px;font-size:.82rem;padding:4px 6px;border-radius:7px;transition:background .15s}
.legend-item:hover{background:var(--pink-light)}
.legend-dot{width:13px;height:13px;border-radius:3px;flex-shrink:0}
.legend-label{flex:1;font-weight:600;color:var(--text)}
.legend-pct{font-weight:800;color:var(--pink);min-width:38px;text-align:right}
.legend-cnt{color:var(--muted);font-size:.82rem}
.legend-group-hdr{font-size:.84rem;font-weight:800;color:var(--text);
  margin-top:12px;margin-bottom:2px;padding:3px 6px;
  border-left:3px solid var(--pink);letter-spacing:.3px}
.legend-group-hdr:first-child{margin-top:0}
.legend-main-pct{color:var(--pink);font-size:.84rem;margin-left:4px}

/* Ingredient trend chart */
.ing-wrap{padding:24px;max-width:800px;margin:0 auto}
.ing-title{font-size:1.05rem;font-weight:800;color:var(--pink);margin-bottom:4px}
.ing-sub{font-size:.78rem;color:var(--muted);margin-bottom:18px}
.ing-group-legend{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:20px}
.ing-grp{display:flex;align-items:center;gap:5px;font-size:.84rem;font-weight:700;color:var(--text)}
.ing-grp-dot{width:10px;height:10px;border-radius:3px;flex-shrink:0}
.ing-bars{display:flex;flex-direction:column;gap:7px}
.ing-row{display:flex;align-items:center;gap:10px}
.ing-label{width:130px;text-align:right;font-size:.8rem;font-weight:700;color:var(--text);flex-shrink:0;line-height:1.2}
.ing-grp-tag{font-size:.8rem;font-weight:600;color:var(--muted);display:block}
.ing-track{flex:1;height:28px;background:var(--pink-light);border-radius:7px;overflow:hidden;position:relative}
.ing-fill{height:100%;border-radius:7px;display:flex;align-items:center;padding:0 10px;transition:width .55s cubic-bezier(.4,0,.2,1);width:0%}
.ing-fill-cnt{font-size:.82rem;font-weight:800;color:#fff;white-space:nowrap}
.ing-meta{width:72px;font-size:.84rem;color:var(--muted);flex-shrink:0;text-align:left}

/* loading */
#loading{position:fixed;inset:0;background:rgba(253,246,248,.92);
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  z-index:999;gap:14px}
.loader{width:44px;height:44px;border:4px solid var(--pink-light);
  border-top-color:var(--pink);border-radius:50%;animation:spin .8s linear infinite}
#loading p{font-size:.88rem;color:var(--pink);font-weight:600}

/* ── Video Hub Styles ─────────────────────────────────────────────────── */
#video-hub .vh-subheader{color:white;padding:10px 28px;display:flex;
  justify-content:space-between;align-items:center;
  box-shadow:0 2px 8px rgba(0,0,0,0.1);transition:background 0.3s}
#video-hub .vh-subheader.tiktok{background:linear-gradient(135deg,#e8a598 0%,#c97d8a 100%)}
#video-hub .vh-subheader.twitter{background:linear-gradient(135deg,#1d9bf0 0%,#0d6ebc 100%)}
#video-hub .vh-subheader h2{font-size:1.1rem;font-weight:800;letter-spacing:-0.5px}
#video-hub .vh-subheader .sub{font-size:0.72rem;opacity:0.85;margin-top:2px}
#video-hub .header-right{display:flex;align-items:center;gap:10px}
#video-hub .platform-switch{display:flex;background:rgba(255,255,255,0.2);
  border-radius:10px;padding:3px;gap:3px}
#video-hub .plat-btn{padding:7px 14px;border:none;border-radius:8px;cursor:pointer;
  font-weight:700;font-size:0.82rem;transition:all 0.2s;
  background:transparent;color:rgba(255,255,255,0.75)}
#video-hub .plat-btn:hover{background:rgba(255,255,255,0.15);color:white}
#video-hub .plat-btn.active{background:white}
#video-hub .plat-btn.tiktok-btn.active{color:#c97d8a}
#video-hub .plat-btn.twitter-btn.active{color:#1d9bf0}
#video-hub .run-btn{background:white;border:none;padding:8px 18px;
  border-radius:8px;font-weight:700;cursor:pointer;font-size:0.82rem;transition:all 0.2s}
#video-hub .vh-subheader.tiktok .run-btn{color:#c97d8a}
#video-hub .vh-subheader.tiktok .run-btn:hover{background:#fff0ed}
#video-hub .vh-subheader.twitter .run-btn{color:#1d9bf0}
#video-hub .vh-subheader.twitter .run-btn:hover{background:#e8f4ff}
#video-hub .run-btn:disabled{opacity:0.6;cursor:not-allowed;transform:none}
#video-hub .v-layout{display:flex;height:calc(100vh - 126px)}
#video-hub .v-sidebar{width:280px;min-width:280px;background:white;padding:20px;
  overflow-y:auto;border-right:1px solid #ede6e2;box-shadow:2px 0 8px rgba(0,0,0,0.04)}
#video-hub .v-main{flex:1;overflow-y:auto;padding:24px}
#video-hub .filter-section{margin-bottom:22px}
#video-hub .filter-label{font-size:0.72rem;font-weight:700;color:#c97d8a;
  text-transform:uppercase;letter-spacing:0.8px;margin-bottom:10px;display:block}
#video-hub .filter-group{display:flex;flex-direction:column;gap:6px}
#video-hub select,#video-hub input[type="text"],#video-hub input[type="number"]{
  width:100%;padding:8px 10px;border:1.5px solid #e8ddd9;
  border-radius:8px;font-size:0.85rem;color:#333;
  background:#fdfaf9;transition:border 0.2s;outline:none}
#video-hub select:focus,#video-hub input:focus{border-color:#c97d8a;background:white}
#video-hub .range-row{display:flex;gap:6px;align-items:center}
#video-hub .range-row input{flex:1}
#video-hub .range-row span{color:#bbb;font-size:0.8rem;flex-shrink:0}
#video-hub .date-btns{display:flex;flex-wrap:wrap;gap:5px}
#video-hub .date-btn{padding:5px 10px;border:1.5px solid #e8ddd9;border-radius:6px;
  font-size:0.78rem;cursor:pointer;background:#fdfaf9;color:#666;transition:all 0.15s}
#video-hub .date-btn:hover{border-color:#c97d8a;color:#c97d8a}
#video-hub .date-btn.active{background:#c97d8a;border-color:#c97d8a;color:white;font-weight:600}
#video-hub .apply-btn{width:100%;padding:10px;background:#c97d8a;color:white;
  border:none;border-radius:9px;font-weight:700;font-size:0.9rem;
  cursor:pointer;margin-top:8px;transition:background 0.2s}
#video-hub .apply-btn:hover{background:#b56a77}
#video-hub .clear-btn{width:100%;padding:8px;background:none;color:#aaa;
  border:1.5px solid #e8ddd9;border-radius:9px;font-size:0.82rem;
  cursor:pointer;margin-top:6px;transition:all 0.2s}
#video-hub .clear-btn:hover{border-color:#c97d8a;color:#c97d8a}
#video-hub hr.divider{border:none;border-top:1.5px solid #f0e8e4;margin:18px 0}
#video-hub .stats-bar{display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap}
#video-hub .stat-chip{background:white;border-radius:10px;padding:12px 18px;
  box-shadow:0 1px 4px rgba(0,0,0,0.06);flex:1;min-width:100px}
#video-hub .stat-chip .n{font-size:1.5rem;font-weight:800;color:#c97d8a}
#video-hub .stat-chip .l{font-size:0.72rem;color:#999;margin-top:2px}
#video-hub .tabs{display:flex;gap:4px;margin-bottom:20px}
#video-hub .tab{padding:9px 18px;border-radius:8px;font-size:0.85rem;font-weight:600;
  cursor:pointer;border:none;background:white;color:#999;
  box-shadow:0 1px 3px rgba(0,0,0,0.06);transition:all 0.15s}
#video-hub .tab:hover{color:#c97d8a}
#video-hub .tab.active{background:#c97d8a;color:white;box-shadow:0 2px 8px rgba(201,125,138,0.35)}
#video-hub .sort-bar{display:flex;align-items:center;gap:10px;margin-bottom:16px;
  background:white;padding:10px 16px;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,0.05)}
#video-hub .sort-bar label{font-size:0.8rem;color:#888;white-space:nowrap}
#video-hub .sort-bar select{width:auto;flex:1;padding:6px 10px;font-size:0.82rem}
#video-hub .result-count{margin-left:auto;font-size:0.8rem;color:#aaa;white-space:nowrap}
#video-hub .video-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}
#video-hub .video-card{background:white;border-radius:14px;overflow:hidden;
  box-shadow:0 1px 4px rgba(0,0,0,0.06);transition:transform 0.15s,box-shadow 0.15s;
  display:flex;flex-direction:column}
#video-hub .video-card:hover{transform:translateY(-2px);box-shadow:0 4px 16px rgba(0,0,0,0.1)}
#video-hub .card-thumb{position:relative;width:100%;aspect-ratio:9/16;max-height:200px;
  overflow:hidden;background:#f0e8e4;flex-shrink:0}
#video-hub .card-thumb img{width:100%;height:100%;object-fit:cover;display:block}
#video-hub .card-thumb .thumb-overlay{position:absolute;inset:0;
  background:linear-gradient(to top,rgba(0,0,0,0.5) 0%,transparent 50%);pointer-events:none}
#video-hub .card-thumb .thumb-duration{position:absolute;bottom:8px;right:8px;
  background:rgba(0,0,0,0.65);color:white;font-size:0.72rem;font-weight:600;padding:2px 7px;border-radius:6px}
#video-hub .card-thumb .thumb-views{position:absolute;bottom:8px;left:8px;
  background:rgba(0,0,0,0.65);color:white;font-size:0.72rem;font-weight:600;padding:2px 7px;border-radius:6px}
#video-hub .video-preview-popup{display:none;position:fixed;z-index:9999;
  width:340px;height:580px;background:#000;border-radius:16px;
  box-shadow:0 12px 48px rgba(0,0,0,0.35);overflow:hidden;pointer-events:none}
#video-hub .video-preview-popup.active{display:block}
#video-hub .video-preview-popup iframe{width:100%;height:100%;border:none}
#video-hub .card-thumb .hover-play{position:absolute;inset:0;
  display:flex;align-items:center;justify-content:center;
  opacity:0;transition:opacity 0.2s;background:rgba(0,0,0,0.25);pointer-events:none}
#video-hub .card-thumb:hover .hover-play{opacity:1}
#video-hub .hover-play-icon{width:48px;height:48px;background:rgba(255,255,255,0.9);
  border-radius:50%;display:flex;align-items:center;justify-content:center;
  font-size:1.4rem;color:#e8a598;box-shadow:0 4px 16px rgba(0,0,0,0.2)}
#video-hub .card-body{padding:14px;display:flex;flex-direction:column;gap:9px;flex:1}
#video-hub .card-rank{font-size:0.7rem;color:#ddd;font-weight:700}
#video-hub .card-creator{display:flex;align-items:center;gap:8px}
#video-hub .creator-avatar{width:32px;height:32px;border-radius:50%;object-fit:cover;
  border:2px solid #f5ece8;flex-shrink:0;background:#f0e8e4}
#video-hub .creator-info{flex:1;min-width:0}
#video-hub .card-creator a{font-weight:800;font-size:0.9rem;color:#c97d8a;text-decoration:none;
  display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#video-hub .card-creator a:hover{text-decoration:underline}
#video-hub .verified-badge{background:#e8f4ff;color:#4a9eff;font-size:0.65rem;
  padding:2px 6px;border-radius:10px;font-weight:700}
#video-hub .followers-tag{font-size:0.72rem;color:#aaa}
#video-hub .card-caption{font-size:0.82rem;color:#555;line-height:1.4;
  display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
#video-hub .metrics-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px}
#video-hub .metric{background:#fdf8f7;border-radius:8px;padding:8px 10px}
#video-hub .metric .mv{font-size:1rem;font-weight:800;color:#333}
#video-hub .metric .ml{font-size:0.68rem;color:#bbb;margin-top:1px}
#video-hub .tags-row{display:flex;flex-wrap:wrap;gap:4px}
#video-hub .hashtag{background:#fdf0ee;color:#c97d8a;font-size:0.7rem;
  padding:3px 8px;border-radius:12px;font-weight:500;cursor:pointer;transition:background 0.15s}
#video-hub .hashtag:hover{background:#c97d8a;color:white}
#video-hub .card-footer{display:flex;justify-content:space-between;align-items:center;
  padding-top:4px;border-top:1px solid #f5ece8}
#video-hub .source-tag{font-size:0.7rem;color:#ddd}
#video-hub .watch-btn{font-size:0.78rem;color:#c97d8a;text-decoration:none;font-weight:600}
#video-hub .watch-btn:hover{text-decoration:underline}
#video-hub .creators-table-wrap{background:white;border-radius:14px;overflow:hidden;
  box-shadow:0 1px 4px rgba(0,0,0,0.06)}
#video-hub table{width:100%;border-collapse:collapse}
#video-hub th{background:#fdf0ee;color:#c97d8a;padding:12px 16px;text-align:left;
  font-size:0.78rem;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;
  cursor:pointer;user-select:none;white-space:nowrap}
#video-hub th:hover{background:#fae6e2}
#video-hub th .sort-arrow{margin-left:4px;opacity:0.4}
#video-hub th.sorted .sort-arrow{opacity:1}
#video-hub td{padding:12px 16px;font-size:0.85rem;border-bottom:1px solid #f8f2f0}
#video-hub tr:last-child td{border-bottom:none}
#video-hub tr:hover td{background:#fffaf9}
#video-hub td a{color:#c97d8a;text-decoration:none;font-weight:600}
#video-hub td a:hover{text-decoration:underline}
#video-hub .tags-cloud{display:flex;flex-wrap:wrap;gap:8px}
#video-hub .tag-pill{background:white;border-radius:20px;padding:8px 14px;
  box-shadow:0 1px 4px rgba(0,0,0,0.06);cursor:pointer;
  transition:all 0.15s;border:1.5px solid transparent}
#video-hub .tag-pill:hover{border-color:#c97d8a}
#video-hub .tag-pill .tn{color:#c97d8a;font-weight:700;font-size:0.9rem}
#video-hub .tag-pill .ts{color:#aaa;font-size:0.75rem;margin-top:2px}
#video-hub .audio-table{background:white;border-radius:14px;overflow:hidden;
  box-shadow:0 1px 4px rgba(0,0,0,0.06)}
#video-hub .empty{text-align:center;padding:60px 20px;color:#ccc}
#video-hub .empty .icon{font-size:3rem;margin-bottom:12px}
#video-hub .empty p{font-size:0.9rem}
#video-hub .strategy-tab{background:linear-gradient(135deg,#e8a598,#c97d8a) !important;color:white !important}
#video-hub .strategy-tab.active{background:linear-gradient(135deg,#c97d8a,#a5566a) !important}
#video-hub .strategy-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:24px}
@media(max-width:900px){ #video-hub .strategy-grid{grid-template-columns:1fr} }
#video-hub .strategy-card{background:white;border-radius:14px;padding:20px;
  box-shadow:0 1px 4px rgba(0,0,0,0.06)}
#video-hub .strategy-card h3{font-size:0.95rem;font-weight:800;color:#333;
  margin-bottom:14px;padding-bottom:8px;border-bottom:2px solid #f5ece8}
#video-hub .format-bar{display:flex;flex-direction:column;gap:10px}
#video-hub .format-row{display:flex;align-items:center;gap:10px}
#video-hub .format-label{width:110px;font-size:0.8rem;font-weight:600;color:#555;flex-shrink:0}
#video-hub .format-track{flex:1;background:#f5ece8;border-radius:20px;height:8px;overflow:hidden}
#video-hub .format-fill{height:100%;border-radius:20px;background:linear-gradient(90deg,#e8a598,#c97d8a)}
#video-hub .format-stat{font-size:0.75rem;color:#aaa;width:60px;text-align:right;flex-shrink:0}
#video-hub .format-badge{display:inline-block;padding:2px 8px;border-radius:10px;
  font-size:0.68rem;font-weight:700;margin-right:4px}
.fmt-tutorial{background:#e8f4ff;color:#4a9eff}
.fmt-routine{background:#e8ffe8;color:#2d9a2d}
.fmt-review{background:#fff3e8;color:#e8832a}
.fmt-beforeafter{background:#f0e8ff;color:#8a4aef}
.fmt-haul{background:#ffe8f0;color:#e84a8a}
.fmt-grwm{background:#fff8e8;color:#c8a020}
.fmt-product{background:#fdf0ee;color:#c97d8a}
.fmt-other{background:#f5f5f5;color:#999}
#video-hub .product-list{display:flex;flex-direction:column;gap:8px}
#video-hub .product-row{display:flex;align-items:center;gap:10px}
#video-hub .product-name{font-size:0.85rem;font-weight:600;color:#333;width:120px;flex-shrink:0;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#video-hub .product-bar-wrap{flex:1;background:#f5ece8;border-radius:20px;height:7px;overflow:hidden}
#video-hub .product-bar{height:100%;border-radius:20px;background:linear-gradient(90deg,#e8a598,#c97d8a)}
#video-hub .product-count{font-size:0.72rem;color:#aaa;width:40px;text-align:right;flex-shrink:0}
#video-hub .idea-list{display:flex;flex-direction:column;gap:10px}
#video-hub .idea-card{background:#fdf8f7;border-radius:10px;padding:12px 14px;
  border-left:3px solid #c97d8a;cursor:pointer;transition:background 0.15s}
#video-hub .idea-card:hover{background:#fdf0ee}
#video-hub .idea-format{font-size:0.68rem;font-weight:700;color:#c97d8a;
  text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px}
#video-hub .idea-title{font-size:0.88rem;font-weight:700;color:#333;margin-bottom:4px}
#video-hub .idea-meta{font-size:0.72rem;color:#aaa}
#video-hub .duration-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
#video-hub .dur-card{background:#fdf8f7;border-radius:10px;padding:12px;
  text-align:center;border:2px solid transparent}
#video-hub .dur-card.best{border-color:#c97d8a;background:#fdf0ee}
#video-hub .dur-card .dv{font-size:1.4rem;font-weight:800;color:#c97d8a}
#video-hub .dur-card .dl{font-size:0.72rem;color:#aaa;margin-top:2px}
#video-hub .dur-card .ds{font-size:0.78rem;color:#888;margin-top:4px;font-weight:600}
#video-hub .modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:200;
  display:flex;align-items:center;justify-content:center}
#video-hub .modal{background:white;border-radius:16px;padding:28px;max-width:500px;width:90%;
  box-shadow:0 8px 40px rgba(0,0,0,0.2)}
#video-hub .modal h3{font-size:1.1rem;font-weight:800;margin-bottom:16px;color:#333}
#video-hub .modal-section{margin-bottom:14px}
#video-hub .modal-label{font-size:0.72rem;font-weight:700;color:#c97d8a;text-transform:uppercase;
  letter-spacing:0.5px;margin-bottom:6px;display:block}
#video-hub .modal-value{background:#fdf8f7;border-radius:8px;padding:10px 12px;
  font-size:0.85rem;color:#333;line-height:1.5}
#video-hub .modal-close{width:100%;padding:10px;background:#c97d8a;color:white;border:none;
  border-radius:9px;font-weight:700;cursor:pointer;margin-top:8px}
#video-hub .copy-fmt-btn{background:#fdf0ee;color:#c97d8a;border:none;padding:4px 10px;
  border-radius:6px;font-size:0.72rem;font-weight:700;cursor:pointer;transition:background 0.15s}
#video-hub .copy-fmt-btn:hover{background:#c97d8a;color:white}
#video-hub .tweet-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}
#video-hub .tweet-card{background:white;border-radius:14px;padding:18px;
  box-shadow:0 1px 4px rgba(0,0,0,0.06);transition:transform 0.15s,box-shadow 0.15s;
  display:flex;flex-direction:column;gap:10px;border-left:3px solid #1d9bf0}
#video-hub .tweet-card:hover{transform:translateY(-2px);box-shadow:0 4px 16px rgba(0,0,0,0.1)}
#video-hub .tweet-author{display:flex;align-items:center;gap:10px}
#video-hub .tweet-avatar{width:42px;height:42px;border-radius:50%;object-fit:cover;
  border:2px solid #e8f4ff;flex-shrink:0;background:#e8f4ff}
#video-hub .tweet-name{font-weight:800;font-size:0.92rem;color:#1a1a1a}
#video-hub .tweet-handle{font-size:0.78rem;color:#888}
#video-hub .tweet-text{font-size:0.88rem;color:#333;line-height:1.5}
#video-hub .tweet-img{width:100%;border-radius:10px;object-fit:cover;max-height:200px}
#video-hub .tweet-metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}
#video-hub .tweet-metric{background:#f0f8ff;border-radius:8px;padding:7px 8px;text-align:center}
#video-hub .tweet-metric .mv{font-size:0.95rem;font-weight:800;color:#1d9bf0}
#video-hub .tweet-metric .ml{font-size:0.65rem;color:#aaa;margin-top:1px}
#video-hub .tweet-tags{display:flex;flex-wrap:wrap;gap:4px}
#video-hub .tweet-tag{background:#e8f4ff;color:#1d9bf0;font-size:0.7rem;
  padding:3px 8px;border-radius:12px;font-weight:500;cursor:pointer}
#video-hub .tweet-tag:hover{background:#1d9bf0;color:white}
#video-hub .tweet-footer{display:flex;justify-content:space-between;align-items:center;
  padding-top:6px;border-top:1px solid #e8f4ff;font-size:0.75rem;color:#aaa}
#video-hub .tweet-link{color:#1d9bf0;text-decoration:none;font-weight:600;font-size:0.78rem}
#video-hub .tweet-link:hover{text-decoration:underline}
#video-hub .x-verified{background:#1d9bf0;color:white;font-size:0.65rem;
  padding:2px 6px;border-radius:10px;font-weight:700;margin-left:4px}
#video-hub #v-toast{position:fixed;bottom:24px;right:24px;background:#333;color:white;
  padding:12px 20px;border-radius:10px;font-size:0.85rem;
  display:none;z-index:999;box-shadow:0 4px 16px rgba(0,0,0,0.2);max-width:300px}
@media(max-width:768px){
  #video-hub .v-layout{flex-direction:column;height:auto}
  #video-hub .v-sidebar{width:100%;border-right:none;border-bottom:1px solid #ede6e2}
  #video-hub .v-main{padding:16px}
  #video-hub .video-grid{grid-template-columns:1fr}
  #video-hub .tweet-grid{grid-template-columns:1fr}
}

/* ── 트렌드 인사이트 허브 ─────────────────────────────── */
#trend-hub{display:none;min-height:calc(100vh - 56px);background:var(--bg)}
#trend-hub .tr-header{background:linear-gradient(135deg,#2d3561 0%,#1a1f3c 100%);
  padding:20px 28px;display:flex;align-items:center;justify-content:space-between;
  flex-wrap:wrap;gap:12px}
#trend-hub .tr-title{color:white;font-size:1.1rem;font-weight:800;display:flex;align-items:center;gap:8px}
#trend-hub .tr-sub{color:rgba(255,255,255,0.6);font-size:0.78rem;margin-top:2px}
#trend-hub .tr-tabs{display:flex;gap:8px}
#trend-hub .tr-tab{padding:8px 20px;border-radius:20px;border:none;font-size:0.82rem;
  font-weight:700;cursor:pointer;transition:all 0.2s;
  background:rgba(255,255,255,0.12);color:rgba(255,255,255,0.75)}
#trend-hub .tr-tab:hover{background:rgba(255,255,255,0.2);color:white}
#trend-hub .tr-tab.active{background:white;color:#2d3561}
#trend-hub .tr-body{padding:24px 28px}

/* 타임라인 섹션 */
#trend-hub .tl-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:18px}
#trend-hub .tl-sum-card{background:white;border-radius:12px;padding:14px 16px;border:1.5px solid #eee;text-align:center}
#trend-hub .tl-sum-card .sum-label{font-size:0.68rem;color:#aaa;font-weight:700;margin-bottom:4px;letter-spacing:0.04em}
#trend-hub .tl-sum-card .sum-value{font-size:1rem;font-weight:800;color:#1a1a1a}
#trend-hub .tl-sum-card .sum-sub{font-size:0.71rem;color:#888;margin-top:3px}
#trend-hub .tl-cat-btns{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
#trend-hub .tl-cat-btn{padding:6px 16px;border-radius:20px;border:1.5px solid var(--border);
  background:white;font-size:0.8rem;font-weight:700;cursor:pointer;color:#555;transition:all 0.15s}
#trend-hub .tl-cat-btn:hover{border-color:#2d3561;color:#2d3561}
#trend-hub .tl-cat-btn.active{background:#2d3561;color:white;border-color:#2d3561}
#trend-hub .chart-card{background:white;border-radius:16px;padding:24px;
  box-shadow:0 1px 6px rgba(0,0,0,0.06);margin-bottom:24px}
#trend-hub .chart-card canvas{max-height:420px}
#trend-hub .chart-label{font-size:0.78rem;font-weight:800;color:var(--muted);
  text-transform:uppercase;letter-spacing:0.6px;margin-bottom:12px}

/* 공백 탐지 섹션 */
#trend-hub .gap-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}
#trend-hub .gap-card{background:white;border-radius:14px;padding:18px;
  box-shadow:0 1px 4px rgba(0,0,0,0.06);border-left:4px solid #ccc;
  transition:transform 0.15s,box-shadow 0.15s}
#trend-hub .gap-card:hover{transform:translateY(-2px);box-shadow:0 4px 16px rgba(0,0,0,0.1)}
#trend-hub .gap-card.hot{border-left-color:#ef4444}
#trend-hub .gap-card.warm{border-left-color:#f59e0b}
#trend-hub .gap-card.cool{border-left-color:#3b82f6}
#trend-hub .gap-keyword{font-size:1rem;font-weight:800;color:#1a1a1a;margin-bottom:8px;
  display:flex;align-items:center;gap:6px}
#trend-hub .gap-badge{font-size:0.65rem;font-weight:700;padding:3px 8px;border-radius:10px;
  text-transform:uppercase;letter-spacing:0.5px}
#trend-hub .gap-badge.hot{background:#fee2e2;color:#ef4444}
#trend-hub .gap-badge.warm{background:#fef3c7;color:#d97706}
#trend-hub .gap-badge.cool{background:#dbeafe;color:#2563eb}
#trend-hub .gap-badge.covered{background:#f0fdf4;color:#16a34a}
#trend-hub .gap-metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:10px 0}
#trend-hub .gap-metric{text-align:center;background:#f9f9f9;border-radius:8px;padding:8px 6px}
#trend-hub .gap-metric .gv{font-size:0.92rem;font-weight:800;color:#333}
#trend-hub .gap-metric .gl{font-size:0.62rem;color:#aaa;margin-top:2px}
#trend-hub .gap-bar-wrap{margin-top:10px}
#trend-hub .gap-bar-label{display:flex;justify-content:space-between;font-size:0.72rem;color:#aaa;margin-bottom:4px}
#trend-hub .gap-bar-track{background:#f0f0f0;border-radius:10px;height:6px;overflow:hidden}
#trend-hub .gap-bar-fill{height:100%;border-radius:10px;transition:width 0.5s}
#trend-hub .gap-opportunity{font-size:0.75rem;color:#555;margin-top:10px;
  background:#fafafa;border-radius:8px;padding:8px 10px;line-height:1.5}
#trend-hub .gap-score-badge{display:inline-flex;align-items:center;gap:4px;
  font-size:0.7rem;font-weight:800;padding:3px 10px;border-radius:12px;
  background:linear-gradient(135deg,#2d3561,#1a1f3c);color:white;margin-bottom:8px}
#trend-hub .empty-state{text-align:center;padding:60px;color:#ccc}

/* ── 순위 변동 패널 ── */
#rank-change-panel{position:fixed;inset:0;z-index:8888;background:rgba(0,0,0,0.5);
  display:none;align-items:flex-start;justify-content:center;padding:20px;overflow-y:auto}
#rank-change-inner{background:white;border-radius:18px;width:100%;max-width:960px;
  margin:auto;padding:24px;box-shadow:0 20px 60px rgba(0,0,0,0.2)}
.rc-period-btns{display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap}
.rc-period-btn{padding:6px 18px;border-radius:20px;border:1.5px solid #e2e8f0;
  background:white;font-size:0.8rem;font-weight:700;cursor:pointer;color:#555;transition:all 0.15s}
.rc-period-btn.active{background:#2d3561;color:white;border-color:#2d3561}
.rc-country-filter{display:flex;gap:6px;margin-bottom:18px;flex-wrap:wrap}
.rc-ctry-btn{padding:4px 12px;border-radius:14px;border:1.5px solid #e2e8f0;
  background:white;font-size:0.75rem;font-weight:700;cursor:pointer;color:#555}
.rc-ctry-btn.active{background:#f1f5f9;border-color:#94a3b8;color:#1e293b}
.rc-section-title{font-size:0.85rem;font-weight:800;color:#1e293b;margin:18px 0 10px;
  display:flex;align-items:center;gap:6px}
.rc-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px;margin-bottom:8px}
.rc-card{background:#f8fafc;border-radius:12px;padding:12px 14px;display:flex;
  align-items:center;gap:12px;border:1px solid #e2e8f0;transition:box-shadow 0.1s}
.rc-card:hover{box-shadow:0 2px 8px rgba(0,0,0,0.08)}
.rc-thumb{width:48px;height:48px;object-fit:cover;border-radius:8px;flex-shrink:0}
.rc-thumb-ph{width:48px;height:48px;border-radius:8px;background:#e2e8f0;
  display:flex;align-items:center;justify-content:center;font-size:1.2rem;flex-shrink:0}
.rc-badge-up{background:#dcfce7;color:#16a34a;font-weight:800;font-size:0.8rem;
  padding:2px 8px;border-radius:10px;white-space:nowrap;flex-shrink:0}
.rc-badge-down{background:#fee2e2;color:#dc2626;font-weight:800;font-size:0.8rem;
  padding:2px 8px;border-radius:10px;white-space:nowrap;flex-shrink:0}
.rc-badge-new{background:#ede9fe;color:#7c3aed;font-weight:800;font-size:0.8rem;
  padding:2px 8px;border-radius:10px;white-space:nowrap;flex-shrink:0}
#trend-hub .empty-state .icon{font-size:3rem;margin-bottom:12px}
/* 브랜드 버즈 */
#trend-hub .br-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
#trend-hub .br-card{background:white;border-radius:14px;padding:16px;
  box-shadow:0 1px 6px rgba(0,0,0,0.06);border-top:3px solid #e5e7eb;
  transition:transform 0.15s,box-shadow 0.15s;cursor:default}
#trend-hub .br-card:hover{transform:translateY(-2px);box-shadow:0 4px 16px rgba(0,0,0,0.1)}
#trend-hub .br-header{display:flex;align-items:center;gap:10px;margin-bottom:10px}
#trend-hub .br-icon{width:40px;height:40px;border-radius:10px;display:flex;align-items:center;
  justify-content:center;font-size:1rem;font-weight:800;color:white;flex-shrink:0}
#trend-hub .br-name{font-size:0.95rem;font-weight:800;color:#1a1a1a;text-transform:capitalize}
#trend-hub .br-trend{font-size:0.65rem;font-weight:700;padding:2px 7px;border-radius:10px;
  margin-top:2px;display:inline-block}
#trend-hub .br-trend.hot{background:#fee2e2;color:#ef4444}
#trend-hub .br-trend.up{background:#fef3c7;color:#d97706}
#trend-hub .br-trend.stable{background:#f0f4ff;color:#4f46e5}
#trend-hub .br-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:10px}
#trend-hub .br-stat{text-align:center;background:#f9f9f9;border-radius:8px;padding:7px 4px}
#trend-hub .br-stat .bv{font-size:0.85rem;font-weight:800;color:#333}
#trend-hub .br-stat .bl{font-size:0.6rem;color:#aaa;margin-top:1px}
#trend-hub .br-video{border-radius:9px;overflow:hidden;background:#f0f0f0;display:flex;gap:0;margin-bottom:8px}
#trend-hub .br-thumb{width:72px;height:72px;object-fit:cover;flex-shrink:0}
#trend-hub .br-thumb-ph{width:72px;height:72px;background:#e5e7eb;display:flex;align-items:center;
  justify-content:center;font-size:1.4rem;flex-shrink:0}
#trend-hub .br-vcap{flex:1;padding:8px 10px;font-size:0.72rem;color:#555;
  line-height:1.4;overflow:hidden;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical}
#trend-hub .br-footer{display:flex;justify-content:space-between;align-items:center}
#trend-hub .br-creator{font-size:0.72rem;color:#aaa}
#trend-hub .br-link{color:#1d9bf0;text-decoration:none;font-size:0.75rem;font-weight:700}
#trend-hub .br-link:hover{text-decoration:underline}
#trend-hub .br-sort{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
#trend-hub .br-sort-btn{padding:5px 14px;border-radius:20px;border:1.5px solid var(--border);
  background:white;font-size:0.78rem;font-weight:700;cursor:pointer;color:#555;transition:all 0.15s}
#trend-hub .br-sort-btn.active{background:#2d3561;color:white;border-color:#2d3561}
#trend-hub .br-view-btn{padding:5px 14px;border-radius:20px;border:1.5px solid var(--border);
  background:white;font-size:0.78rem;font-weight:700;cursor:pointer;color:#555;transition:all 0.15s}
#trend-hub .br-view-btn.active{background:#2d3561;color:white;border-color:#2d3561}

/* 크리에이터 신호 카드 */
#trend-hub .cr-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
#trend-hub .cr-card{background:white;border-radius:14px;padding:18px;
  box-shadow:0 1px 4px rgba(0,0,0,0.06);border-top:3px solid #ccc;
  transition:transform 0.15s,box-shadow 0.15s}
#trend-hub .cr-card:hover{transform:translateY(-2px);box-shadow:0 4px 16px rgba(0,0,0,0.1)}
#trend-hub .cr-card.tier-breakthrough{border-top-color:#ef4444}
#trend-hub .cr-card.tier-rising{border-top-color:#f59e0b}
#trend-hub .cr-card.tier-emerging{border-top-color:#3b82f6}
#trend-hub .cr-header{display:flex;align-items:center;gap:12px;margin-bottom:12px}
#trend-hub .cr-avatar{width:48px;height:48px;border-radius:50%;object-fit:cover;
  background:#f0f0f0;flex-shrink:0;border:2px solid #eee}
#trend-hub .cr-avatar-ph{width:48px;height:48px;border-radius:50%;background:#f0f4ff;
  display:flex;align-items:center;justify-content:center;font-size:1.3rem;flex-shrink:0}
#trend-hub .cr-meta{flex:1;min-width:0}
#trend-hub .cr-name{font-weight:800;font-size:0.92rem;color:#1a1a1a;
  display:flex;align-items:center;gap:5px}
#trend-hub .cr-handle{font-size:0.75rem;color:#888;margin-top:1px}
#trend-hub .cr-signal-badge{font-size:0.62rem;font-weight:800;padding:2px 8px;
  border-radius:10px;white-space:nowrap}
#trend-hub .cr-signal-badge.tier-breakthrough{background:#fee2e2;color:#ef4444}
#trend-hub .cr-signal-badge.tier-rising{background:#fef3c7;color:#d97706}
#trend-hub .cr-signal-badge.tier-emerging{background:#dbeafe;color:#2563eb}
#trend-hub .cr-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px}
#trend-hub .cr-stat{text-align:center;background:#f9f9f9;border-radius:8px;padding:7px 5px}
#trend-hub .cr-stat .sv{font-size:0.88rem;font-weight:800;color:#333}
#trend-hub .cr-stat .sl{font-size:0.6rem;color:#aaa;margin-top:2px}
#trend-hub .cr-tweet{background:#f8f9ff;border-radius:9px;padding:10px 12px;
  font-size:0.8rem;color:#444;line-height:1.5;margin-bottom:10px;
  border-left:3px solid #c7d2fe;max-height:80px;overflow:hidden}
#trend-hub .cr-keywords{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:10px}
#trend-hub .cr-kw{background:#f0f4ff;color:#4f46e5;font-size:0.65rem;
  padding:2px 8px;border-radius:10px;font-weight:600}
#trend-hub .cr-footer{display:flex;justify-content:space-between;align-items:center}
#trend-hub .cr-link{color:#1d9bf0;text-decoration:none;font-size:0.75rem;font-weight:700}
#trend-hub .cr-link:hover{text-decoration:underline}
#trend-hub .cr-er{font-size:0.75rem;font-weight:800;color:#10b981}
@media(max-width:768px){
  #trend-hub .tr-body{padding:16px}
  #trend-hub .gap-grid{grid-template-columns:1fr}
}
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
      <button class="mode-btn" id="mode-video" onclick="switchMode('video')">🎬 Video</button>
      <button class="mode-btn" id="mode-trend" onclick="switchMode('trend')">📈 트렌드</button>
    </div>
    <span class="upd" id="updLbl">—</span>
    <button id="refreshBtn" onclick="refreshData()">↻ 새로고침</button>
    <button onclick="rcOpen()" style="padding:6px 14px;background:#f1f5f9;border:1.5px solid #e2e8f0;
      border-radius:9px;font-size:0.78rem;font-weight:700;cursor:pointer;color:#374151">
      📊 순위 변동
    </button>
  </div>
</header>

<!-- 순위 변동 패널 -->
<div id="rank-change-panel" onclick="if(event.target===this)rcClose()">
  <div id="rank-change-inner">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:18px">
      <div>
        <div style="font-weight:800;font-size:1.1rem;color:#1e293b">📊 순위 변동 추적</div>
        <div id="rc-compare-label" style="font-size:0.75rem;color:#94a3b8;margin-top:2px"></div>
      </div>
      <button onclick="rcClose()" style="border:none;background:none;font-size:1.4rem;cursor:pointer;color:#94a3b8">✕</button>
    </div>
    <div class="rc-period-btns">
      <button class="rc-period-btn active" data-p="1d" onclick="rcSetPeriod('1d',this)">1일</button>
      <button class="rc-period-btn" data-p="1w" onclick="rcSetPeriod('1w',this)">1주일</button>
      <button class="rc-period-btn" data-p="30d" onclick="rcSetPeriod('30d',this)">30일</button>
      <button class="rc-period-btn" data-p="90d" onclick="rcSetPeriod('90d',this)">90일</button>
    </div>
    <div class="rc-country-filter" id="rc-country-filter"></div>
    <div id="rc-body" style="min-height:200px"></div>
  </div>
</div>

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
    <span style="font-size:.84rem;font-weight:700;color:var(--muted);margin-right:2px">기간:</span>
    <button class="ys-pill" data-period="total" onclick="setTtPeriod(this)">누적</button>
    <button class="ys-pill active" data-period="30d" onclick="setTtPeriod(this)">30일</button>
    <button class="ys-pill" data-period="7d" onclick="setTtPeriod(this)">7일</button>
  </div>
  <span class="rc" id="rc"></span>
</div>

<div class="gw" id="gw"></div>
</div><!-- /product-hub -->

<!-- ══════════════════════════════════════════════════════════════════════ -->
<!-- VIDEO HUB (hidden until switched) -->
<!-- ══════════════════════════════════════════════════════════════════════ -->
<div id="video-hub" style="display:none">

<div class="vh-subheader tiktok" id="vh-subheader">
  <div>
    <h2 id="vh-hub-title">K-Beauty TikTok Hub</h2>
    <div class="sub" id="vh-last-updated">Loading data...</div>
  </div>
  <div class="header-right">
    <div class="platform-switch">
      <button class="plat-btn tiktok-btn active" id="btn-tiktok" onclick="vSwitchPlatform('tiktok')">🎵 TikTok</button>
      <button class="plat-btn twitter-btn" id="btn-twitter" onclick="vSwitchPlatform('twitter')">🇯🇵 JP Twitter</button>
    </div>
    <div id="apify-credit-badge" style="font-size:0.75rem;color:rgba(255,255,255,0.75);cursor:pointer;text-align:right;line-height:1.4" onclick="vLoadApifyUsage()" title="클릭하여 갱신">
      <div style="font-weight:700" id="apify-used">💳 로딩중...</div>
      <div id="apify-bar" style="width:80px;height:4px;background:rgba(255,255,255,0.2);border-radius:2px;margin-top:2px">
        <div id="apify-bar-fill" style="height:100%;border-radius:2px;background:#4ade80;width:0%;transition:width 0.5s"></div>
      </div>
    </div>
    <button class="run-btn" id="v-run-btn" onclick="vTriggerScrape()">Run New Scrape</button>
  </div>
</div>

<div class="v-layout" id="v-tiktok-layout">
  <!-- SIDEBAR FILTERS -->
  <aside class="v-sidebar">
    <div class="filter-section">
      <span class="filter-label">Date Range</span>
      <div class="date-btns">
        <button class="date-btn active" onclick="vSetDateRange('all', this)">All Time</button>
        <button class="date-btn" onclick="vSetDateRange(1, this)">Today</button>
        <button class="date-btn" onclick="vSetDateRange(7, this)">7 Days</button>
        <button class="date-btn" onclick="vSetDateRange(30, this)">30 Days</button>
        <button class="date-btn" onclick="vSetDateRange(90, this)">90 Days</button>
      </div>
    </div>
    <hr class="divider">
    <div class="filter-section">
      <span class="filter-label">Search</span>
      <div class="filter-group">
        <input type="text" id="vf-creator" placeholder="Creator username...">
        <input type="text" id="vf-hashtag" placeholder="Hashtag (e.g. glasskin)...">
        <input type="text" id="vf-keyword" placeholder="Caption keyword...">
      </div>
    </div>
    <hr class="divider">
    <div class="filter-section">
      <span class="filter-label">Min Views</span>
      <div class="range-row">
        <input type="number" id="vf-views-min" placeholder="e.g. 100000" min="0" value="100000">
        <span>+</span>
      </div>
    </div>
    <div class="filter-section">
      <span class="filter-label">Min Followers</span>
      <input type="number" id="vf-followers-min" placeholder="e.g. 10000" min="0" value="500">
    </div>
    <hr class="divider">
    <div class="filter-section">
      <span class="filter-label">Region / Country</span>
      <select id="vf-region">
        <option value="all">🌍 All Regions</option>
        <option value="🇺🇸 USA / UK">🇺🇸 USA / UK</option>
        <option value="🇰🇷 Korea">🇰🇷 Korea</option>
        <option value="🇨🇳 China">🇨🇳 China</option>
        <option value="🇹🇼 Taiwan">🇹🇼 Taiwan</option>
        <option value="🇯🇵 Japan">🇯🇵 Japan</option>
        <option value="🇮🇩 Indonesia">🇮🇩 Indonesia</option>
        <option value="🇸🇬 Singapore">🇸🇬 Singapore</option>
        <option value="🇹🇭 Thailand">🇹🇭 Thailand</option>
        <option value="🇻🇳 Vietnam">🇻🇳 Vietnam</option>
        <option value="🇵🇭 Philippines">🇵🇭 Philippines</option>
        <option value="🇧🇷 Brazil">🇧🇷 Brazil</option>
        <option value="🇪🇸 Spain / Mexico">🇪🇸 Spain / Mexico</option>
        <option value="🇬🇧 UK">🇬🇧 UK</option>
        <option value="🇦🇪 UAE">🇦🇪 UAE</option>
        <option value="🌍 Other">🌍 Other</option>
      </select>
    </div>
    <div class="filter-section">
      <span class="filter-label">Source Dataset</span>
      <select id="vf-dataset">
        <option value="all">All Datasets</option>
      </select>
    </div>
    <div class="filter-section">
      <span class="filter-label">Source Hashtag</span>
      <select id="vf-source-tag">
        <option value="all">All Hashtags</option>
      </select>
    </div>
    <button class="apply-btn" onclick="vApplyFilters()">Apply Filters</button>
    <button class="clear-btn" onclick="vClearFilters()">Clear All</button>
  </aside>

  <!-- MAIN CONTENT -->
  <main class="v-main">
    <div class="stats-bar" id="v-stats-bar">
      <div class="stat-chip"><div class="n" id="vs-videos">—</div><div class="l">Videos</div></div>
      <div class="stat-chip"><div class="n" id="vs-views">—</div><div class="l">Total Views</div></div>
      <div class="stat-chip"><div class="n" id="vs-likes">—</div><div class="l">Total Likes</div></div>
      <div class="stat-chip"><div class="n" id="vs-creators">—</div><div class="l">Creators</div></div>
    </div>
    <div class="tabs" id="v-tabs">
      <button class="tab active" onclick="vSwitchTab('videos', this)">Viral Videos</button>
      <button class="tab strategy-tab" onclick="vSwitchTab('strategy', this)">🎯 Strategy</button>
      <button class="tab" onclick="vSwitchTab('creators', this)">Creators</button>
      <button class="tab" onclick="vSwitchTab('hashtags', this)">Hashtags</button>
      <button class="tab" onclick="vSwitchTab('topchannels', this)">Top Channels</button>
      <button class="tab" onclick="vSwitchTab('audio', this)">Audio</button>
    </div>
    <div class="sort-bar" id="v-sort-bar">
      <label>Sort by:</label>
      <select id="v-sort-select" onchange="vRenderVideos()">
        <option value="views">Views</option>
        <option value="likes">Likes</option>
        <option value="comments">Comments</option>
        <option value="shares">Shares</option>
        <option value="saves">Saves</option>
        <option value="engagement">Engagement Score</option>
        <option value="followers">Creator Followers</option>
        <option value="date">Date (Newest)</option>
      </select>
      <div class="result-count" id="v-result-count"></div>
    </div>
    <div id="v-panel-videos"></div>
    <div id="v-panel-strategy" style="display:none"></div>
    <div id="v-panel-creators" style="display:none"></div>
    <div id="v-panel-hashtags" style="display:none"></div>
    <div id="v-panel-topchannels" style="display:none"></div>
    <div id="v-panel-audio" style="display:none"></div>
  </main>
</div>

<!-- Video hover preview popup -->
<div class="video-preview-popup" id="vVideoPreview"></div>

<!-- TWITTER HUB (hidden until switched) -->
<div id="v-twitter-hub" style="display:none">
  <div class="v-layout">
    <aside class="v-sidebar" style="border-right-color:#e8f4ff">
      <div class="filter-section">
        <span class="filter-label" style="color:#1d9bf0">Date Range</span>
        <div class="date-btns">
          <button class="date-btn active" onclick="vSetXDateRange('all', this)">All Time</button>
          <button class="date-btn" onclick="vSetXDateRange(1, this)">Today</button>
          <button class="date-btn" onclick="vSetXDateRange(7, this)">7 Days</button>
          <button class="date-btn" onclick="vSetXDateRange(30, this)">30 Days</button>
        </div>
      </div>
      <hr class="divider">
      <div class="filter-section">
        <span class="filter-label" style="color:#1d9bf0">Search</span>
        <div class="filter-group">
          <input type="text" id="vxf-author" placeholder="@username...">
          <input type="text" id="vxf-hashtag" placeholder="Hashtag (e.g. kbeauty)...">
          <input type="text" id="vxf-keyword" placeholder="Keyword in tweet...">
        </div>
      </div>
      <hr class="divider">
      <div class="filter-section">
        <span class="filter-label" style="color:#1d9bf0">Min Views</span>
        <input type="number" id="vxf-views-min" placeholder="e.g. 10000" min="0">
      </div>
      <div class="filter-section">
        <span class="filter-label" style="color:#1d9bf0">Min Retweets</span>
        <input type="number" id="vxf-rt-min" placeholder="e.g. 100" min="0">
      </div>
      <hr class="divider">
      <div class="filter-section">
        <span class="filter-label" style="color:#1d9bf0">Sort By</span>
        <select id="vx-sort-select" onchange="vRenderTweets()">
          <option value="views">Views</option>
          <option value="likes">Likes</option>
          <option value="retweets">Retweets</option>
          <option value="replies">Replies</option>
          <option value="bookmarks">Bookmarks</option>
          <option value="date">Date (Newest)</option>
        </select>
      </div>
      <button class="apply-btn" style="background:#1d9bf0" onclick="vApplyXFilters()">Apply Filters</button>
      <button class="clear-btn" onclick="vClearXFilters()">Clear All</button>
    </aside>
    <main class="v-main">
      <div class="stats-bar" id="vx-stats-bar">
        <div class="stat-chip"><div class="n" id="vxs-tweets" style="color:#1d9bf0">—</div><div class="l">Tweets</div></div>
        <div class="stat-chip"><div class="n" id="vxs-views" style="color:#1d9bf0">—</div><div class="l">Total Views</div></div>
        <div class="stat-chip"><div class="n" id="vxs-likes" style="color:#1d9bf0">—</div><div class="l">Total Likes</div></div>
        <div class="stat-chip"><div class="n" id="vxs-accounts" style="color:#1d9bf0">—</div><div class="l">Accounts</div></div>
      </div>
      <div class="tabs" id="vx-tabs">
        <button class="tab active" style="--ac:#1d9bf0" onclick="vSwitchXTab('tweets', this)">Viral Tweets</button>
        <button class="tab" onclick="vSwitchXTab('xcreators', this)">Top Accounts</button>
        <button class="tab" onclick="vSwitchXTab('xhashtags', this)">Trending Tags</button>
      </div>
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px;background:white;padding:10px 16px;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,0.05)">
        <span style="font-size:0.8rem;color:#888">Dataset:</span>
        <select id="vx-date-pick" onchange="vLoadXDate(this.value)" style="width:auto;flex:1;padding:6px 10px;font-size:0.82rem"></select>
        <div id="vx-result-count" style="margin-left:auto;font-size:0.8rem;color:#aaa"></div>
      </div>
      <div id="vx-panel-tweets"></div>
      <div id="vx-panel-xcreators" style="display:none"></div>
      <div id="vx-panel-xhashtags" style="display:none"></div>
    </main>
  </div>
</div>


<div id="v-toast"></div>
</div><!-- /video-hub -->

<!-- ── 트렌드 인사이트 허브 ──────────────────────────── -->
<div id="trend-hub">
  <div class="tr-header">
    <div>
      <div class="tr-title">📈 K-Beauty 트렌드 인사이트</div>
      <div class="tr-sub">소셜 버즈 × 상품 공백 분석 — 데이터 기반 PB 기회 탐지</div>
    </div>
    <div class="tr-tabs">
      <button class="tr-tab active" id="tr-tab-timeline" onclick="trSwitchTab('timeline',this)">📊 키워드 타임라인</button>
      <button class="tr-tab" id="tr-tab-gaps" onclick="trSwitchTab('gaps',this)">🔍 공백 시장 탐지</button>
      <button class="tr-tab" id="tr-tab-creators" onclick="trSwitchTab('creators',this)">🌱 크리에이터 신호</button>
      <button class="tr-tab" id="tr-tab-brands" onclick="trSwitchTab('brands',this)">🏷️ 브랜드 버즈</button>
    </div>
  </div>

  <!-- 타임라인 패널 -->
  <div id="tr-panel-timeline" class="tr-body">
    <div style="font-size:0.82rem;color:#666;line-height:1.6;margin-bottom:16px">
      K-beauty 성분·제형 키워드가 <strong>🌏 아시아 마켓</strong>(OliveYoung·Amazon JP·Qoo10) vs
      <strong>🌍 서양 마켓</strong>(Amazon US·UK·TikTok Shop·YesStyle)에 상품이 얼마나 있는지 비교해.
      <span style="color:#ef4444">아시아가 높으면</span> 아직 서양에 기회가 있다는 신호야.
    </div>
    <!-- 요약 카드 -->
    <div class="tl-summary" id="tl-summary"></div>
    <!-- 카테고리 필터 -->
    <div class="tl-cat-btns">
      <button class="tl-cat-btn active" onclick="trSetCat('전체',this)">전체</button>
      <button class="tl-cat-btn" onclick="trSetCat('성분',this)">💊 핵심 성분</button>
      <button class="tl-cat-btn" onclick="trSetCat('제형',this)">🧴 제형 타입</button>
      <button class="tl-cat-btn" onclick="trSetCat('기능성',this)">☀️ 기능성</button>
    </div>
    <div class="chart-card">
      <div class="chart-label">아시아 vs 서양 마켓 상품 수 비교 (많을수록 해당 마켓에서 인기)</div>
      <canvas id="tl-chart"></canvas>
    </div>
    <div id="tl-empty" class="empty-state" style="display:none">
      <div class="icon">📊</div>
      <p>상품 데이터가 없어. 새로고침 후 다시 확인해.</p>
    </div>
  </div>

  <!-- 공백 탐지 패널 -->
  <div id="tr-panel-gaps" class="tr-body" style="display:none">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px;flex-wrap:wrap">
      <div style="font-size:0.82rem;color:#666;line-height:1.6;max-width:640px">
        아시아 마켓(OliveYoung·JP·Qoo10)에서 상품이 많은데 서양 마켓(US·UK·TikTok Shop·YesStyle)에는 적은 성분 = K-beauty PB 진입 기회.<br>
        <span style="color:#ef4444;font-weight:700">🔴 Hot Gap</span> 아시아 독주, 서양 공백 &nbsp;
        <span style="color:#d97706;font-weight:700">🟡 Warm Gap</span> 균형점 &nbsp;
        <span style="color:#2563eb;font-weight:700">🔵 Well-covered</span> 서양 이미 포화
      </div>
      <button onclick="trLoadGaps()" style="margin-left:auto;padding:8px 18px;background:#2d3561;color:white;
        border:none;border-radius:9px;font-weight:700;font-size:0.82rem;cursor:pointer">↻ 새로 분석</button>
    </div>
    <div id="tr-gaps-grid" class="gap-grid"></div>
    <div id="tr-gaps-empty" class="empty-state" style="display:none">
      <div class="icon">🔍</div>
      <p>Twitter 데이터가 없어. 먼저 Twitter 수집 후 다시 확인해.</p>
    </div>
  </div>

  <!-- 크리에이터 신호 패널 -->
  <div id="tr-panel-creators" class="tr-body" style="display:none">
    <div style="display:flex;align-items:flex-start;gap:12px;margin-bottom:20px;flex-wrap:wrap">
      <div style="font-size:0.82rem;color:#666;line-height:1.6;max-width:680px">
        TikTok K-beauty 크리에이터 중 팔로워 적은데 인게이지먼트 높은 계정 = 아직 주류화 전 초기 신호.<br>
        <span style="color:#ef4444;font-weight:700">🔴 Breakthrough</span> ER 20%+ &nbsp;
        <span style="color:#d97706;font-weight:700">🟡 Rising</span> ER 5~20% &nbsp;
        <span style="color:#2563eb;font-weight:700">🔵 Emerging</span> ER 5% 미만<br>
        <span style="font-size:0.73rem;color:#aaa">ER = (좋아요 + 댓글×2 + 공유×3 + 저장×2) ÷ 뷰 × 100. TikTok은 알고리즘 배포 특성상 뷰 기준이 더 정확. 팔로워 100~10만 계정만 포함.</span>
      </div>
      <button onclick="trLoadCreators()" style="margin-left:auto;padding:8px 18px;background:#2d3561;color:white;
        border:none;border-radius:9px;font-weight:700;font-size:0.82rem;cursor:pointer">↻ 새로 분석</button>
    </div>
    <div id="tr-creators-grid" class="cr-grid"></div>
    <div id="tr-creators-empty" class="empty-state" style="display:none">
      <div class="icon">🌱</div>
      <p>K-beauty 크리에이터 데이터가 없어. 먼저 TikTok 수집 후 다시 확인해.</p>
    </div>
  </div>

  <!-- 브랜드 버즈 패널 -->
  <div id="tr-panel-brands" class="tr-body" style="display:none">
    <div style="display:flex;align-items:flex-start;gap:12px;margin-bottom:16px;flex-wrap:wrap">
      <div style="font-size:0.82rem;color:#666;line-height:1.6;max-width:680px">
        지금 터지는 K-beauty 바이럴 영상(캡션·해시태그)에서 언급된 브랜드 순위.
        총 뷰가 높을수록 바이럴 영상들에 더 많이 노출된 브랜드야.<br>
        <span style="color:#ef4444;font-weight:700">🔥 급상승</span> 최근 급증 &nbsp;
        <span style="color:#d97706;font-weight:700">↑ 상승 중</span> 꾸준히 증가 &nbsp;
        <span style="color:#4f46e5;font-weight:700">→ 안정적</span> 기존 강자
      </div>
      <button onclick="trLoadBrands()" style="margin-left:auto;padding:8px 18px;background:#2d3561;color:white;
        border:none;border-radius:9px;font-weight:700;font-size:0.82rem;cursor:pointer">↻ 새로 분석</button>
    </div>
    <!-- 정렬 + 뷰 토글 -->
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:16px">
      <div class="br-sort" style="margin-bottom:0">
        <button class="br-sort-btn active" onclick="brSetSort('views',this)">👁 총 뷰 순</button>
        <button class="br-sort-btn" onclick="brSetSort('count',this)">📹 영상 수 순</button>
        <button class="br-sort-btn" onclick="brSetSort('trend',this)">🔥 트렌드 순</button>
      </div>
      <div style="display:flex;gap:6px">
        <button class="br-view-btn active" id="br-view-card" onclick="brSetView('card',this)">📋 카드</button>
        <button class="br-view-btn" id="br-view-chart" onclick="brSetView('chart',this)">📊 차트</button>
      </div>
    </div>
    <div id="tr-brands-grid" class="br-grid"></div>
    <div id="tr-brands-chart" style="display:none;background:white;border-radius:14px;padding:20px;box-shadow:0 1px 6px rgba(0,0,0,0.07)">
      <canvas id="br-chart-canvas"></canvas>
    </div>
    <div id="tr-brands-empty" class="empty-state" style="display:none">
      <div class="icon">🏷️</div>
      <p>TikTok 데이터가 없어. data_*.json 파일을 확인해.</p>
    </div>
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
      const imgEl=th?`<img src="${th}" alt="" loading="eager" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`:'' ;
      const phEl=`<div class="dash-mini-ph" style="${th?'display:none':''}">🧴</div>`;
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
        <div class="db-card-title">📈 카테고리 분포 (전체) <span style="font-size:.82rem;font-weight:400;color:#bbb;margin-left:4px">↗ 클릭</span></div>
        <div class="db-pie-body">
          <canvas id="dbPieChart" width="185" height="185" style="flex-shrink:0"></canvas>
          <div id="dbPieLegend" class="db-pie-legend"></div>
        </div>
      </div>
      <div class="db-chart-card" onclick="goTab('IG')" title="성분 트렌드로 이동">
        <div class="db-card-title">🧪 트렌딩 성분 TOP 10 (전체) <span style="font-size:.82rem;font-weight:400;color:#bbb;margin-left:4px">↗ 클릭</span></div>
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
      const imgH=th?`<img src="${th}" alt="" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`:'';
      const ph=`<div class="ph" style="${th?'display:none':''}">🧴</div>`;
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
    const mainDisplay = main === '기타' ? '기타 <span style="color:#aaa;font-weight:400;font-size:.8rem">(향수, 바디, 구강)</span>' : main;
    html+=`<div class="db-legend-grp">${mainDisplay} <span style="color:var(--pink);font-size:.8rem">${Math.round(mainTotal/total*100)}%</span></div>`;
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
    container.innerHTML='<div style="color:var(--muted);font-size:.78rem;text-align:center;padding:12px">브랜드 데이터 없음</div>';
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
    container.innerHTML='<div style="color:var(--muted);font-size:.78rem;text-align:center;padding:12px">성분 데이터 없음</div>';
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

function switchMode(mode) {
  sessionStorage.setItem('kbMode', mode);
  currentMode = mode;
  document.getElementById('product-hub').style.display = mode === 'product' ? '' : 'none';
  document.getElementById('video-hub').style.display   = mode === 'video'   ? '' : 'none';
  document.getElementById('trend-hub').style.display   = mode === 'trend'   ? 'block' : 'none';
  document.getElementById('mode-product').classList.toggle('active', mode === 'product');
  document.getElementById('mode-video').classList.toggle('active', mode === 'video');
  document.getElementById('mode-trend').classList.toggle('active', mode === 'trend');
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
  } else if (mode === 'video') {
    title.textContent = 'K-Beauty Research Hub';
    sub.textContent = 'TikTok & X 바이럴 컨텐츠 분석';
    logo.textContent = '🔬';
    refreshBtn.style.display = 'none';
    document.getElementById('updLbl').style.display = 'none';
    if (!videoHubInitialized) { initVideoHub(); videoHubInitialized = true; }
  } else {
    title.textContent = 'K-Beauty 트렌드 인사이트';
    sub.textContent = '소셜 버즈 × 공백 시장 분석';
    logo.textContent = '📈';
    refreshBtn.style.display = 'none';
    document.getElementById('updLbl').style.display = 'none';
    if (!trInitialized) { trInit(); trInitialized = true; }
  }
}

// ══════════════════════════════════════════════════════════════════════════
// VIDEO HUB JAVASCRIPT (all functions prefixed with v)
// ══════════════════════════════════════════════════════════════════════════

// ── State ──
let vAllData    = [];
let vFiltered   = [];
let vDateRange  = 'all';
let vActiveTab  = 'videos';
let vAllDates   = [];
let vxAllData   = [];
let vxFiltered  = [];
let vxDateRange = 'all';
let vxActiveTab = 'tweets';
let vxAllDates  = [];
let vPlatform   = 'tiktok';

// ── Utilities ──
function vFmt(n) {
  n = parseInt(n) || 0;
  if (n >= 1e9)  return (n/1e9).toFixed(1)  + 'B';
  if (n >= 1e6)  return (n/1e6).toFixed(1)  + 'M';
  if (n >= 1e3)  return (n/1e3).toFixed(1)  + 'K';
  return n.toString();
}
function vToast(msg, ms=4000) {
  const el = document.getElementById('v-toast');
  el.textContent = msg; el.style.display = 'block';
  clearTimeout(el._t);
  el._t = setTimeout(() => el.style.display = 'none', ms);
}
function vEsc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Data loading ──
async function initVideoHub() {
  const r = await fetch('/api/dates');
  vAllDates = await r.json();
  const sel = document.getElementById('vf-dataset');
  vAllDates.forEach(d => {
    const o = document.createElement('option');
    o.value = d; o.textContent = d;
    sel.appendChild(o);
  });
  if (!vAllDates.length) {
    vToast('No data yet. Click "Run New Scrape" to start.', 8000);
    document.getElementById('vh-last-updated').textContent = 'No data available';
    return;
  }
  await vLoadAllData();
  vLoadApifyUsage();
}

async function vLoadAllData() {
  vAllData = [];
  const seen = new Set();
  for (const date of vAllDates) {
    const r = await fetch('/api/data/' + date);
    const items = await r.json();
    items.forEach(v => {
      const key = v.id || (v.url + v.creator?.username);
      if (!seen.has(key)) { seen.add(key); vAllData.push({...v, _dataset: date}); }
    });
  }
  document.getElementById('vh-last-updated').textContent =
    vAllData.length + ' videos across ' + vAllDates.length + ' dataset(s) · Latest: ' + vAllDates[0];
  const tagSel = document.getElementById('vf-source-tag');
  const sourceTags = [...new Set(vAllData.map(v => v.source_tag).filter(Boolean))].sort();
  sourceTags.forEach(t => {
    const o = document.createElement('option');
    o.value = t; o.textContent = '#' + t;
    tagSel.appendChild(o);
  });
  vApplyFilters();
}

// ── Filtering ──
function vSetDateRange(val, btn) {
  vDateRange = val;
  document.querySelectorAll('#v-tiktok-layout .date-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}

function vApplyFilters() {
  const creator  = document.getElementById('vf-creator').value.trim().toLowerCase();
  const hashtag  = document.getElementById('vf-hashtag').value.trim().toLowerCase().replace('#','');
  const keyword  = document.getElementById('vf-keyword').value.trim().toLowerCase();
  const viewsMin = parseInt(document.getElementById('vf-views-min').value) || 0;
  const follMin  = parseInt(document.getElementById('vf-followers-min').value) || 0;
  const region   = document.getElementById('vf-region').value;
  const dataset  = document.getElementById('vf-dataset').value;
  const srcTag   = document.getElementById('vf-source-tag').value;
  const now    = Date.now();
  const dayMs  = 86400000;
  const cutoff = vDateRange === 'all' ? 0 : now - (vDateRange * dayMs);
  vFiltered = vAllData.filter(v => {
    const s = v.stats || {};
    const c = v.creator || {};
    if (vDateRange !== 'all') {
      const ts = v.created_at ? new Date(v.created_at).getTime() : 0;
      if (ts && ts < cutoff) return false;
    }
    if (creator  && !(c.username||'').toLowerCase().includes(creator)) return false;
    if (hashtag  && !(v.hashtags||[]).some(t => t.toLowerCase().includes(hashtag))) return false;
    if (keyword  && !(v.caption||'').toLowerCase().includes(keyword)) return false;
    if ((s.views    || 0) < viewsMin) return false;
    if ((c.followers|| 0) < follMin)  return false;
    if (region !== 'all' && v.region !== region) return false;
    if (dataset !== 'all' && v._dataset !== dataset) return false;
    if (srcTag  !== 'all' && v.source_tag !== srcTag) return false;
    return true;
  });
  vUpdateStats();
  vRenderActiveTab();
}

function vClearFilters() {
  document.getElementById('vf-creator').value = '';
  document.getElementById('vf-hashtag').value = '';
  document.getElementById('vf-keyword').value = '';
  document.getElementById('vf-views-min').value = '100000';
  document.getElementById('vf-followers-min').value = '500';
  document.getElementById('vf-region').value = 'all';
  document.getElementById('vf-dataset').value = 'all';
  document.getElementById('vf-source-tag').value = 'all';
  vDateRange = 'all';
  document.querySelectorAll('#v-tiktok-layout .date-btn').forEach(b => b.classList.remove('active'));
  document.querySelector('#v-tiktok-layout .date-btn').classList.add('active');
  vApplyFilters();
}

// ── Stats ──
function vUpdateStats() {
  const totalViews = vFiltered.reduce((s,v) => s + (v.stats?.views||0), 0);
  const totalLikes = vFiltered.reduce((s,v) => s + (v.stats?.likes||0), 0);
  const creators   = new Set(vFiltered.map(v => v.creator?.username)).size;
  document.getElementById('vs-videos').textContent   = vFmt(vFiltered.length);
  document.getElementById('vs-views').textContent    = vFmt(totalViews);
  document.getElementById('vs-likes').textContent    = vFmt(totalLikes);
  document.getElementById('vs-creators').textContent = creators;
}

// ── Tab switching ──
function vSwitchTab(tab, btn) {
  vActiveTab = tab;
  document.querySelectorAll('#v-tabs .tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('v-sort-bar').style.display = tab === 'videos' ? 'flex' : 'none';
  vRenderActiveTab();
}

function vRenderActiveTab() {
  if (vActiveTab === 'videos')      vRenderVideos();
  if (vActiveTab === 'strategy')    vRenderStrategy();
  if (vActiveTab === 'creators')    vRenderCreators();
  if (vActiveTab === 'topchannels') vRenderTopChannels();
  if (vActiveTab === 'hashtags')    vRenderHashtags();
  if (vActiveTab === 'audio')       vRenderAudio();
  ['videos','strategy','creators','topchannels','hashtags','audio'].forEach(t => {
    document.getElementById('v-panel-' + t).style.display = t === vActiveTab ? 'block' : 'none';
  });
}

// ── Format detection ──
function vDetectFormat(v) {
  const text = (v.caption||'').toLowerCase();
  const tags  = (v.hashtags||[]).join(' ').toLowerCase();
  const all   = text + ' ' + tags;
  if (/\bgrwm\b|get ready with me/.test(all))                      return 'GRWM';
  if (/before.?after|transformation|glow.?up/.test(all))           return 'Before & After';
  if (/\bhaul\b|shopping|bought|unboxing/.test(all))               return 'Haul';
  if (/tutorial|how.?to|step.?by.?step|diy/.test(all))            return 'Tutorial';
  if (/routine|morning|night|pm |am |daily|evening/.test(all))     return 'Routine';
  if (/review|honest|tried|worth it|rating|thoughts/.test(all))    return 'Review';
  if (/recommend|must have|favorite|fave|top \d|best/.test(all))   return 'Recommendation';
  return 'Product Showcase';
}

const V_FORMAT_CLASS = {
  'Tutorial': 'fmt-tutorial', 'Routine': 'fmt-routine', 'Review': 'fmt-review',
  'Before & After': 'fmt-beforeafter', 'Haul': 'fmt-haul', 'GRWM': 'fmt-grwm',
  'Recommendation': 'fmt-product', 'Product Showcase': 'fmt-other'
};

const V_KBEAUTY_BRANDS = [
  'cosrx','laneige','innisfree','etude','missha','some by mi','skin1004','anua','isntree',
  'beauty of joseon','round lab','torriden','dr jart','klavuu','rom&nd','peripera',
  'sulwhasoo','hera','espoir','3ce','nacific','apieu','tocobo','goodal','abib','ma:nyo',
  'heimish','klairs','belif','banobagi','bano','medicube','vt cosmetics','tirtir',
  'axis-y','by wishtrend','haruharu','purito','mixsoon','numbuzin','rovectin',
  'farmacy','glow recipe','tatcha','dermalogica','cerave','the ordinary',
  'matrigen','snail','cica','centella','niacinamide','ceramide','retinol',
  'hyaluronic acid','vitamin c','aha','bha','pha','sunscreen','spf'
];

function vExtractProducts(caption, hashtags) {
  const text = ((caption||'') + ' ' + (hashtags||[]).join(' ')).toLowerCase();
  return V_KBEAUTY_BRANDS.filter(b => text.includes(b));
}

// ── Strategy panel ──
function vRenderStrategy() {
  const formatStats = {};
  vFiltered.forEach(v => {
    const fmt = vDetectFormat(v);
    if (!formatStats[fmt]) formatStats[fmt] = { count:0, views:0, likes:0 };
    formatStats[fmt].count++;
    formatStats[fmt].views += v.stats?.views||0;
    formatStats[fmt].likes += v.stats?.likes||0;
  });
  const fmtSorted = Object.entries(formatStats).sort((a,b) => b[1].views - a[1].views);
  const maxFmtViews = fmtSorted[0]?.[1].views || 1;
  const durBuckets = { '0–15s':[], '15–30s':[], '30–60s':[], '60s+':[] };
  vFiltered.forEach(v => {
    const d = v.duration||0;
    if      (d <= 15) durBuckets['0–15s'].push(v);
    else if (d <= 30) durBuckets['15–30s'].push(v);
    else if (d <= 60) durBuckets['30–60s'].push(v);
    else              durBuckets['60s+'].push(v);
  });
  const durAvgViews = Object.entries(durBuckets).map(([label, vids]) => ({
    label, count: vids.length,
    avg: vids.length ? Math.round(vids.reduce((s,v)=>s+(v.stats?.views||0),0)/vids.length) : 0
  }));
  const bestDur = [...durAvgViews].sort((a,b)=>b.avg-a.avg)[0]?.label;
  const productCount = {};
  const productViews = {};
  vFiltered.forEach(v => {
    const prods = vExtractProducts(v.caption, v.hashtags);
    prods.forEach(p => {
      productCount[p] = (productCount[p]||0) + 1;
      productViews[p] = (productViews[p]||0) + (v.stats?.views||0);
    });
  });
  const topProducts = Object.entries(productCount)
    .sort((a,b) => (productViews[b[0]]||0) - (productViews[a[0]]||0)).slice(0, 12);
  const maxProd = topProducts[0]?.[1] || 1;
  const ideas = vGenerateIdeas(fmtSorted, topProducts, productViews);

  document.getElementById('v-panel-strategy').innerHTML = `
    <div style="margin-bottom:16px;padding:14px 18px;background:linear-gradient(135deg,#fdf0ee,#fff8f7);
         border-radius:12px;border-left:4px solid #c97d8a">
      <div style="font-weight:800;color:#c97d8a;margin-bottom:4px">Your Content Strategy Engine</div>
      <div style="font-size:0.83rem;color:#777">Based on ${vFiltered.length} viral K-beauty videos.
      Find a winning format → duplicate it in a new language → post daily.</div>
    </div>
    <div class="strategy-grid">
      <div class="strategy-card">
        <h3>🏆 Winning Formats (by total views)</h3>
        <div class="format-bar">
          ${fmtSorted.map(([fmt, s]) => `
            <div class="format-row" onclick="vFilterByFormat('${vEsc(fmt)}')" style="cursor:pointer;border-radius:8px;padding:4px 6px;transition:background 0.15s"
                 onmouseenter="this.style.background='#fdf0ee'" onmouseleave="this.style.background='transparent'">
              <div class="format-label">
                <span class="format-badge ${V_FORMAT_CLASS[fmt]||'fmt-other'}">${fmt}</span>
              </div>
              <div class="format-track">
                <div class="format-fill" style="width:${Math.round(s.views/maxFmtViews*100)}%"></div>
              </div>
              <div class="format-stat">${vFmt(s.views)}<br><span style="font-size:0.65rem">${s.count} vids</span></div>
            </div>`).join('')}
        </div>
      </div>
      <div class="strategy-card">
        <h3>⏱ Best Video Duration (avg views)</h3>
        <div class="duration-grid">
          ${durAvgViews.map(d => `
            <div class="dur-card ${d.label===bestDur?'best':''}">
              <div class="dv">${d.label}</div>
              <div class="dl">${d.count} videos</div>
              <div class="ds">${vFmt(d.avg)} avg views${d.label===bestDur?' 🏆':''}</div>
            </div>`).join('')}
        </div>
      </div>
      <div class="strategy-card">
        <h3>💄 Trending Products & Ingredients</h3>
        ${topProducts.length ? `<div class="product-list">
          ${topProducts.map(([prod, count]) => `
            <div class="product-row">
              <div class="product-name" title="${vEsc(prod)}">${vEsc(prod)}</div>
              <div class="product-bar-wrap">
                <div class="product-bar" style="width:${Math.round(count/maxProd*100)}%"></div>
              </div>
              <div class="product-count">${count}×</div>
            </div>`).join('')}
        </div>` : '<div style="color:#ccc;font-size:0.85rem">No product data in current filter.</div>'}
      </div>
      <div class="strategy-card">
        <h3>💡 Ready-to-Use Video Ideas</h3>
        <div style="font-size:0.75rem;color:#aaa;margin-bottom:10px">Click any idea to see its full template</div>
        <div class="idea-list">
          ${ideas.map(idea => `
            <div class="idea-card" onclick='vShowIdeaModal(${JSON.stringify(idea)})'>
              <div class="idea-format">
                <span class="format-badge ${V_FORMAT_CLASS[idea.format]||'fmt-other'}">${idea.format}</span>
              </div>
              <div class="idea-title">${vEsc(idea.title)}</div>
              <div class="idea-meta">${idea.duration} · ${idea.hashtags.slice(0,3).map(h=>'#'+h).join(' ')}</div>
            </div>`).join('')}
        </div>
      </div>
    </div>
    <div class="strategy-card" style="margin-bottom:20px">
      <h3>🎭 Faceless K-Beauty Channels to Study</h3>
      <div style="font-size:0.75rem;color:#aaa;margin-bottom:14px">
        Channels detected as faceless based on caption style — product-focused, no personal face
        references, high avg views.
      </div>
      ${vRenderFacelessChannels()}
    </div>
    <div class="strategy-card" style="margin-bottom:20px">
      <h3>🎬 Top Video Per Format — Your Winning Templates</h3>
      <div style="font-size:0.75rem;color:#aaa;margin-bottom:14px">
        Each of these is a proven format. Click "Copy Format" to get the exact template.
      </div>
      <div class="video-grid">${vRenderTopPerFormat()}</div>
    </div>`;
}

function vGenerateIdeas(fmtSorted, topProducts, productViews) {
  const prod1  = topProducts[0]?.[0] || 'K-Beauty Product';
  const prod2  = topProducts[1]?.[0] || 'Korean skincare';
  const prod3  = topProducts[2]?.[0] || 'glass skin routine';
  return [
    { format:'Tutorial', title:'How I Get Glass Skin Using '+prod1, duration:'30–60s',
      hashtags:['glasskin','kbeauty','skincareroutine','koreanskincare'],
      hook:'POV: I finally figured out how to get glass skin with '+prod1,
      structure:'Hook (3s) → Before skin (3s) → Apply product step by step → After reveal → CTA' },
    { format:'Review', title:'I Tried '+prod1+' For 7 Days — Honest Results', duration:'30–60s',
      hashtags:['kbeautyreview','skincare','kbeauty','honest'],
      hook:'I used '+prod1+' every day for 7 days. Here\'s what actually happened.',
      structure:'Hook → Day 1 skin → Product application → Daily progression → Day 7 result → Verdict' },
    { format:'Routine', title:'My Korean Night Routine Using Only '+prod2, duration:'45–90s',
      hashtags:['koreanskincare','nightroutine','kbeauty','skincareroutine'],
      hook:'My full Korean skincare routine using '+prod2+' — takes only 5 minutes',
      structure:'Hook → Cleanse → Tone → Serum → Moisturize → SPF (if AM) → Final look' },
    { format:'Before & After', title:prod1+' Before & After — 30 Day Results', duration:'15–30s',
      hashtags:['beforeandafter','glowup','kbeauty','skincaretransformation'],
      hook:'30 days of '+prod1+'. The results shocked me.',
      structure:'Hook → Close-up before (3s) → Text: "30 days later" → Close-up after → Product reveal' },
    { format:'Recommendation', title:'Top 3 Korean Skincare Products Under $20', duration:'30–45s',
      hashtags:['kbeauty','affordable','koreanskincare','skincareproducts'],
      hook:'3 Korean skincare products that changed my skin — all under $20',
      structure:'Hook → Product 1 + result → Product 2 + result → Product 3 + result → Where to buy' },
    { format:'GRWM', title:'GRWM: Korean Glass Skin Makeup Routine', duration:'60–90s',
      hashtags:['grwm','kbeautymakeup','koreanmakeup','glasskin'],
      hook:'GRWM using only Korean skincare + makeup for the glass skin look',
      structure:'Hook → Skincare base → Primer → Foundation → Blush → Highlight → Final look' },
    { format:'Haul', title:'K-Beauty Haul: Best Finds From '+prod3, duration:'30–60s',
      hashtags:['kbeautyhaul','kbeauty','skincarehaul','koreanbeauty'],
      hook:'I spent $50 on Korean beauty products so you don\'t have to',
      structure:'Hook → Unbox each product → Quick demo → Rating → Total cost + where to buy' },
  ];
}

function vShowIdeaModal(idea) {
  const existing = document.getElementById('v-idea-modal');
  if (existing) existing.remove();
  const div = document.createElement('div');
  div.id = 'v-idea-modal';
  div.className = 'modal-overlay';
  div.onclick = e => { if(e.target===div) div.remove(); };
  div.innerHTML = '<div class="modal">' +
    '<h3><span class="format-badge '+(V_FORMAT_CLASS[idea.format]||'fmt-other')+'">'+vEsc(idea.format)+'</span>' +
    '&nbsp;'+vEsc(idea.title)+'</h3>' +
    '<div class="modal-section"><span class="modal-label">Hook (Opening Line)</span><div class="modal-value">'+vEsc(idea.hook)+'</div></div>' +
    '<div class="modal-section"><span class="modal-label">Video Structure</span><div class="modal-value">'+vEsc(idea.structure)+'</div></div>' +
    '<div class="modal-section"><span class="modal-label">Target Duration</span><div class="modal-value">'+vEsc(idea.duration)+'</div></div>' +
    '<div class="modal-section"><span class="modal-label">Hashtags to Use</span><div class="modal-value">'+idea.hashtags.map(h=>'#'+vEsc(h)).join('  ')+'</div></div>' +
    '<button class="modal-close" onclick="document.getElementById(\'v-idea-modal\').remove()">Close</button></div>';
  document.body.appendChild(div);
}

function vCopyFormat(data) {
  const existing = document.getElementById('v-idea-modal');
  if (existing) existing.remove();
  const div = document.createElement('div');
  div.id = 'v-idea-modal';
  div.className = 'modal-overlay';
  div.onclick = e => { if(e.target===div) div.remove(); };
  div.innerHTML = '<div class="modal">' +
    '<h3>Format Template <span class="format-badge '+(V_FORMAT_CLASS[data.format]||'fmt-other')+'" style="margin-left:8px">'+vEsc(data.format)+'</span></h3>' +
    '<div class="modal-section"><span class="modal-label">Original Caption</span><div class="modal-value">'+vEsc(data.caption)+'</div></div>' +
    '<div class="modal-section"><span class="modal-label">Duration</span><div class="modal-value">'+vEsc(data.duration)+'</div></div>' +
    '<div class="modal-section"><span class="modal-label">Hashtags Used</span><div class="modal-value">'+(data.hashtags||[]).map(h=>'#'+vEsc(h)).join('  ')+'</div></div>' +
    '<div class="modal-section"><span class="modal-label">Performance</span><div class="modal-value">'+vFmt(data.views)+' views · '+vFmt(data.likes)+' likes · by @'+vEsc(data.creator)+'</div></div>' +
    '<div class="modal-section"><span class="modal-label">How to Replicate</span><div class="modal-value">1. Study the original video structure<br>2. Recreate the same format in your language<br>3. Keep same duration ('+vEsc(data.duration)+')<br>4. Use same hashtags + add your language hashtags<br>5. Adapt caption tone — keep the hook style</div></div>' +
    '<a href="'+vEsc(data.url)+'" target="_blank" style="display:block;text-align:center;color:#c97d8a;font-size:0.85rem;margin-bottom:10px">Watch Original on TikTok →</a>' +
    '<button class="modal-close" onclick="document.getElementById(\'v-idea-modal\').remove()">Close</button></div>';
  document.body.appendChild(div);
}

// ── Faceless channel detection ──
const V_FACELESS_SIGNALS = [
  'review','rating','product','this product','try this','honest','worth it',
  'recommendation','must have','top ','best ','affordable','dupes','dupe',
  'before after','results','comparison','ingredients','what is','did you know',
  'hack','tip','trick','secret','tutorial','how to','step by step',
  'unboxing','haul','ranking'
];
const V_FACE_SIGNALS = [
  'my face','my skin','my routine','i woke up','my morning','my night',
  'come with me','follow me','my journey','my story','grwm','get ready with me',
  'storytime','vlog','day in my life'
];

function vIsFacelessCaption(caption) {
  const text = (caption||'').toLowerCase();
  const faceScore    = V_FACE_SIGNALS.filter(s => text.includes(s)).length;
  const productScore = V_FACELESS_SIGNALS.filter(s => text.includes(s)).length;
  return productScore > faceScore && productScore >= 1;
}

function vRenderFacelessChannels() {
  const map = {};
  vFiltered.forEach(v => {
    const c = v.creator||{};
    const u = c.username;
    if (!u) return;
    if (!map[u]) map[u] = { ...c, videos: [], totalViews: 0, facelessCount: 0, totalCount: 0 };
    map[u].videos.push(v);
    map[u].totalViews += v.stats?.views||0;
    map[u].totalCount++;
    if (vIsFacelessCaption(v.caption)) map[u].facelessCount++;
  });
  const candidates = Object.values(map)
    .filter(c => {
      const ratio = c.facelessCount / Math.max(c.totalCount, 1);
      const avgViews = c.totalViews / Math.max(c.totalCount, 1);
      return ratio >= 0.5 && avgViews >= 50000;
    })
    .sort((a, b) => {
      const avgA = a.totalViews / Math.max(a.totalCount, 1);
      const avgB = b.totalViews / Math.max(b.totalCount, 1);
      return avgB - avgA;
    })
    .slice(0, 12);
  if (!candidates.length) {
    return '<div style="color:#ccc;font-size:0.85rem;padding:20px;text-align:center">Not enough data yet.</div>';
  }
  return '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse"><thead><tr style="background:#fdf0ee">' +
    '<th style="padding:10px 14px;font-size:0.75rem;color:#c97d8a;text-align:left;font-weight:700">Channel</th>' +
    '<th style="padding:10px 14px;font-size:0.75rem;color:#c97d8a;text-align:left;font-weight:700">Avg Views</th>' +
    '<th style="padding:10px 14px;font-size:0.75rem;color:#c97d8a;text-align:left;font-weight:700">Total Views</th>' +
    '<th style="padding:10px 14px;font-size:0.75rem;color:#c97d8a;text-align:left;font-weight:700">Videos</th>' +
    '<th style="padding:10px 14px;font-size:0.75rem;color:#c97d8a;text-align:left;font-weight:700">Followers</th>' +
    '<th style="padding:10px 14px;font-size:0.75rem;color:#c97d8a;text-align:left;font-weight:700">Faceless Score</th>' +
    '</tr></thead><tbody>' +
    candidates.map(c => {
      const avgViews = Math.round(c.totalViews / Math.max(c.totalCount, 1));
      const score    = Math.round(c.facelessCount / Math.max(c.totalCount, 1) * 100);
      return '<tr style="border-bottom:1px solid #f5ece8">' +
        '<td style="padding:12px 14px"><div style="display:flex;align-items:center;gap:10px">' +
          (c.avatar ? '<img src="'+vEsc(c.avatar)+'" style="width:36px;height:36px;border-radius:50%;object-fit:cover;border:2px solid #f5ece8;flex-shrink:0" onerror="this.style.display=\'none\'" loading="lazy">' : '') +
          '<div><a href="'+vEsc(c.url)+'" target="_blank" style="color:#c97d8a;font-weight:700;text-decoration:none">@'+vEsc(c.username)+'</a>' +
          (c.verified ? '<span style="color:#4a9eff;font-size:0.7rem"> ✓</span>' : '') +
        '</div></div></td>' +
        '<td style="padding:12px 14px;font-weight:800;font-size:1rem;color:#c97d8a">'+vFmt(avgViews)+'</td>' +
        '<td style="padding:12px 14px;font-size:0.85rem">'+vFmt(c.totalViews)+'</td>' +
        '<td style="padding:12px 14px;font-size:0.85rem">'+c.totalCount+'</td>' +
        '<td style="padding:12px 14px;font-size:0.85rem">'+vFmt(c.followers)+'</td>' +
        '<td style="padding:12px 14px"><div style="display:flex;align-items:center;gap:6px">' +
          '<div style="flex:1;background:#f5ece8;border-radius:20px;height:6px;overflow:hidden"><div style="height:100%;border-radius:20px;background:linear-gradient(90deg,#e8a598,#c97d8a);width:'+score+'%"></div></div>' +
          '<span style="font-size:0.75rem;color:#888;width:32px">'+score+'%</span></div></td></tr>';
    }).join('') + '</tbody></table></div>';
}

function vRenderTopPerFormat() {
  const byFormat = {};
  vFiltered.forEach(v => {
    const fmt = vDetectFormat(v);
    if (!byFormat[fmt] || (v.stats?.views||0) > (byFormat[fmt].stats?.views||0)) {
      byFormat[fmt] = v;
    }
  });
  return Object.entries(byFormat).map(([fmt, v]) => {
    const s = v.stats||{}; const c = v.creator||{};
    const thumb = v.cover||'';
    return '<div class="video-card">' +
      (thumb ? '<a href="'+vEsc(v.url)+'" target="_blank" class="card-thumb"><img src="'+vEsc(thumb)+'" loading="lazy" onerror="this.parentElement.style.display=\'none\'"><div class="thumb-overlay"></div><div class="thumb-views">👁 '+vFmt(s.views)+'</div><div class="thumb-duration">'+(v.duration||0)+'s</div></a>' : '') +
      '<div class="card-body">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">' +
          '<span class="format-badge '+(V_FORMAT_CLASS[fmt]||'fmt-other')+'">'+fmt+'</span>' +
          '<button class="copy-fmt-btn" onclick=\'vCopyFormat('+JSON.stringify({format:fmt,caption:v.caption,duration:(v.duration||0)+"s",hashtags:v.hashtags||[],views:s.views,likes:s.likes,creator:c.username,url:v.url})+')\'>' +
          'Copy Format</button></div>' +
        '<div class="card-creator">' +
          (c.avatar ? '<img class="creator-avatar" src="'+vEsc(c.avatar)+'" loading="lazy" onerror="this.style.display=\'none\'">' : '') +
          '<div class="creator-info"><a href="'+vEsc(c.url)+'" target="_blank">@'+vEsc(c.username)+'</a>' +
          '<div style="font-size:0.72rem;color:#aaa">'+vFmt(c.followers)+' followers</div></div></div>' +
        '<div class="card-caption">'+vEsc(v.caption)+'</div>' +
        '<div class="stats-row" style="margin-top:8px"><span><b>'+vFmt(s.views)+'</b> views</span> <span><b>'+vFmt(s.likes)+'</b> likes</span> <span><b>'+vFmt(s.shares)+'</b> shares</span></div>' +
      '</div></div>';
  }).join('');
}

// ── Video panel ──
function vRenderVideos() {
  const sortBy = document.getElementById('v-sort-select').value;
  const sorted = [...vFiltered].sort((a,b) => {
    if (sortBy === 'date') return new Date(b.created_at||0) - new Date(a.created_at||0);
    if (sortBy === 'followers') return (b.creator?.followers||0) - (a.creator?.followers||0);
    if (sortBy === 'engagement') return (b.engagement||0) - (a.engagement||0);
    return (b.stats?.[sortBy]||0) - (a.stats?.[sortBy]||0);
  });
  document.getElementById('v-result-count').textContent = sorted.length + ' videos';
  if (!sorted.length) {
    document.getElementById('v-panel-videos').innerHTML =
      '<div class="empty"><div class="icon">🔍</div><p>No videos match your filters.</p></div>';
    return;
  }
  document.getElementById('v-panel-videos').innerHTML =
    '<div class="video-grid">' +
    sorted.map((v, i) => {
      const s = v.stats || {};
      const c = v.creator || {};
      const tags = (v.hashtags||[]).slice(0,6).map(t =>
        '<span class="hashtag" onclick="vFilterByTag(\''+vEsc(t)+'\')">#'+vEsc(t)+'</span>').join('');
      const date = v.created_at ? new Date(v.created_at).toLocaleDateString() : '';
      const thumb = v.cover || '';
      const avatar = c.avatar || '';
      return '<div class="video-card">' +
        (thumb
          ? '<a href="'+vEsc(v.url)+'" target="_blank" class="card-thumb" data-video-url="'+vEsc(v.url)+'" onmouseenter="vShowVideoPreview(this, event)" onmousemove="vMoveVideoPreview(event)" onmouseleave="vHideVideoPreview()"><img src="'+vEsc(thumb)+'" alt="video thumbnail" loading="lazy" onerror="this.parentElement.style.display=\'none\'"><div class="thumb-overlay"></div><div class="hover-play"><div class="hover-play-icon">▶</div></div><div class="thumb-views">👁 '+vFmt(s.views)+'</div><div class="thumb-duration">'+vFmt(v.duration)+'s</div></a>'
          : '') +
        '<div class="card-body">' +
          '<div class="card-rank">#'+(i+1)+' · via #'+vEsc(v.source_tag)+' '+(date ? '· '+date : '')+' '+(v.region ? '· '+vEsc(v.region) : '')+'</div>' +
          '<div class="card-creator">' +
            (avatar
              ? '<img class="creator-avatar" src="'+vEsc(avatar)+'" alt="avatar" onerror="this.style.display=\'none\'" loading="lazy">'
              : '<div class="creator-avatar" style="display:flex;align-items:center;justify-content:center;font-size:14px;color:#ddd">👤</div>') +
            '<div class="creator-info"><a href="'+vEsc(c.url)+'" target="_blank">@'+vEsc(c.username)+'</a>' +
              '<div style="display:flex;align-items:center;gap:6px;margin-top:1px">' +
                (c.verified ? '<span class="verified-badge">✓ Verified</span>' : '') +
                '<span class="followers-tag">'+vFmt(c.followers)+' followers</span></div></div></div>' +
          '<div class="card-caption">'+vEsc(v.caption)+'</div>' +
          '<div class="metrics-grid">' +
            '<div class="metric"><div class="mv">'+vFmt(s.likes)+'</div><div class="ml">Likes</div></div>' +
            '<div class="metric"><div class="mv">'+vFmt(s.comments)+'</div><div class="ml">Comments</div></div>' +
            '<div class="metric"><div class="mv">'+vFmt(s.shares)+'</div><div class="ml">Shares</div></div>' +
            '<div class="metric"><div class="mv">'+vFmt(s.saves||s.collectCount)+'</div><div class="ml">Saves</div></div></div>' +
          '<div class="tags-row">'+tags+'</div>' +
          '<div class="card-footer"><span class="source-tag">Dataset: '+vEsc(v._dataset||'')+'</span>' +
            '<a class="watch-btn" href="'+vEsc(v.url)+'" target="_blank">Watch on TikTok →</a></div>' +
        '</div></div>';
    }).join('') + '</div>';
}

function vFilterByTag(tag) {
  document.getElementById('vf-hashtag').value = tag;
  vApplyFilters();
  vToast('Filtered by #'+tag);
}

function vFilterByFormat(format) {
  const formatVideos = vFiltered.filter(v => vDetectFormat(v) === format);
  const sorted = formatVideos.sort((a,b) => (b.stats?.views||0) - (a.stats?.views||0));
  vActiveTab = 'videos';
  document.querySelectorAll('#v-tabs .tab').forEach(t => t.classList.remove('active'));
  document.querySelector('#v-tabs .tab').classList.add('active');
  document.getElementById('v-sort-bar').style.display = 'flex';
  ['videos','strategy','creators','topchannels','hashtags','audio'].forEach(t => {
    document.getElementById('v-panel-' + t).style.display = t === 'videos' ? 'block' : 'none';
  });
  document.getElementById('v-result-count').textContent = sorted.length + ' videos ('+format+')';
  if (!sorted.length) {
    document.getElementById('v-panel-videos').innerHTML =
      '<div class="empty"><div class="icon">🔍</div><p>No "'+format+'" videos found.</p></div>';
    return;
  }
  document.getElementById('v-panel-videos').innerHTML =
    '<div style="margin-bottom:16px;padding:12px 18px;background:#fdf0ee;border-radius:10px;display:flex;align-items:center;justify-content:space-between"><div><span class="format-badge '+(V_FORMAT_CLASS[format]||'fmt-other')+'" style="font-size:0.9rem;padding:4px 12px">'+format+'</span><span style="font-size:0.85rem;color:#888;margin-left:10px">'+sorted.length+' videos</span></div><button onclick="vRenderVideos()" style="background:#c97d8a;color:white;border:none;padding:6px 14px;border-radius:8px;font-weight:700;font-size:0.8rem;cursor:pointer">Show All</button></div><div class="video-grid">' +
    sorted.map((v, i) => {
      const s = v.stats || {}; const c = v.creator || {};
      const tags = (v.hashtags||[]).slice(0,6).map(t => '<span class="hashtag" onclick="vFilterByTag(\''+vEsc(t)+'\')">#'+vEsc(t)+'</span>').join('');
      const date = v.created_at ? new Date(v.created_at).toLocaleDateString() : '';
      const thumb = v.cover || ''; const avatar = c.avatar || '';
      return '<div class="video-card">' +
        (thumb ? '<a href="'+vEsc(v.url)+'" target="_blank" class="card-thumb" data-video-url="'+vEsc(v.url)+'" onmouseenter="vShowVideoPreview(this, event)" onmousemove="vMoveVideoPreview(event)" onmouseleave="vHideVideoPreview()"><img src="'+vEsc(thumb)+'" loading="lazy" onerror="this.parentElement.style.display=\'none\'"><div class="thumb-overlay"></div><div class="hover-play"><div class="hover-play-icon">▶</div></div><div class="thumb-views">👁 '+vFmt(s.views)+'</div><div class="thumb-duration">'+vFmt(v.duration)+'s</div></a>' : '') +
        '<div class="card-body"><div class="card-rank">#'+(i+1)+' · <span class="format-badge '+(V_FORMAT_CLASS[format]||'fmt-other')+'">'+format+'</span> · '+(date||'')+' '+(v.region ? '· '+vEsc(v.region) : '')+'</div><div class="card-creator">' +
          (avatar ? '<img class="creator-avatar" src="'+vEsc(avatar)+'" onerror="this.style.display=\'none\'" loading="lazy">' : '<div class="creator-avatar" style="display:flex;align-items:center;justify-content:center;font-size:14px;color:#ddd">👤</div>') +
          '<div class="creator-info"><a href="'+vEsc(c.url)+'" target="_blank">@'+vEsc(c.username)+'</a><div style="display:flex;align-items:center;gap:6px;margin-top:1px">'+(c.verified?'<span class="verified-badge">✓ Verified</span>':'')+'<span class="followers-tag">'+vFmt(c.followers)+' followers</span></div></div></div>' +
          '<div class="card-caption">'+vEsc(v.caption)+'</div>' +
          '<div class="metrics-grid"><div class="metric"><div class="mv">'+vFmt(s.likes)+'</div><div class="ml">Likes</div></div><div class="metric"><div class="mv">'+vFmt(s.comments)+'</div><div class="ml">Comments</div></div><div class="metric"><div class="mv">'+vFmt(s.shares)+'</div><div class="ml">Shares</div></div><div class="metric"><div class="mv">'+vFmt(s.saves||s.collectCount)+'</div><div class="ml">Saves</div></div></div>' +
          '<div class="tags-row">'+tags+'</div><div class="card-footer"><span class="source-tag">Dataset: '+vEsc(v._dataset||'')+'</span><a class="watch-btn" href="'+vEsc(v.url)+'" target="_blank">Watch on TikTok →</a></div></div></div>';
    }).join('') + '</div>';
  vToast('Showing '+sorted.length+' "'+format+'" videos');
}

// ── Creators panel ──
let vCreatorSortCol = 'totalViews';
let vCreatorSortDir = -1;

function vRenderCreators() {
  const map = {};
  vFiltered.forEach(v => {
    const c = v.creator||{}; const u = c.username;
    if (!u) return;
    if (!map[u]) map[u] = { ...c, totalViews:0, totalLikes:0, totalShares:0, totalSaves:0, videoCount:0, tags:new Set() };
    map[u].totalViews  += v.stats?.views||0;
    map[u].totalLikes  += v.stats?.likes||0;
    map[u].totalShares += v.stats?.shares||0;
    map[u].totalSaves  += v.stats?.saves||v.stats?.collectCount||0;
    map[u].videoCount++;
    (v.hashtags||[]).forEach(t => map[u].tags.add(t));
  });
  let rows = Object.values(map).sort((a,b) => vCreatorSortDir * (a[vCreatorSortCol] - b[vCreatorSortCol]));
  const arrow = dir => dir === -1 ? '↓' : '↑';
  const th = (col, label) => '<th onclick="vSortCreators(\''+col+'\')" class="'+(vCreatorSortCol===col?'sorted':'')+'">' +
    label+' <span class="sort-arrow">'+(vCreatorSortCol===col?arrow(vCreatorSortDir):'↕')+'</span></th>';
  document.getElementById('v-panel-creators').innerHTML =
    '<div class="creators-table-wrap"><table><thead><tr>' +
      th('username','Creator')+th('followers','Followers')+th('totalViews','Total Views') +
      th('totalLikes','Total Likes')+th('totalShares','Total Shares')+th('totalSaves','Total Saves') +
      th('videoCount','Videos')+'</tr></thead><tbody>' +
    rows.map(c => {
      const tags = [...c.tags].slice(0,3).map(t=>'<span style="font-size:0.7rem;color:#c97d8a">#'+vEsc(t)+'</span>').join(' ');
      const av = c.avatar || '';
      return '<tr><td><div style="display:flex;align-items:center;gap:10px">' +
          (av ? '<img src="'+vEsc(av)+'" style="width:36px;height:36px;border-radius:50%;object-fit:cover;border:2px solid #f5ece8;flex-shrink:0" onerror="this.style.display=\'none\'" loading="lazy">' : '') +
          '<div><a href="'+vEsc(c.url)+'" target="_blank">@'+vEsc(c.username)+'</a>' +
          (c.verified?'<span style="color:#4a9eff;font-size:0.7rem"> ✓</span>':'') +
          '<div style="margin-top:2px">'+tags+'</div></div></div></td>' +
        '<td>'+vFmt(c.followers)+'</td><td><b>'+vFmt(c.totalViews)+'</b></td>' +
        '<td>'+vFmt(c.totalLikes)+'</td><td>'+vFmt(c.totalShares)+'</td>' +
        '<td>'+vFmt(c.totalSaves)+'</td><td>'+c.videoCount+'</td></tr>';
    }).join('') + '</tbody></table></div>';
}

function vSortCreators(col) {
  if (vCreatorSortCol === col) vCreatorSortDir *= -1;
  else { vCreatorSortCol = col; vCreatorSortDir = -1; }
  vRenderCreators();
}

// ── Top Channels panel ──
let vTcSortCol = 'avgViews';
let vTcSortDir = -1;

function vRenderTopChannels() {
  const map = {};
  vFiltered.forEach(v => {
    const c = v.creator||{}; const u = c.username;
    if (!u) return;
    if (!map[u]) map[u] = { ...c, totalViews:0, totalLikes:0, totalEng:0, videoCount:0, tags:new Set(), videos:[] };
    map[u].totalViews += v.stats?.views||0;
    map[u].totalLikes += v.stats?.likes||0;
    map[u].totalEng   += v.engagement||0;
    map[u].videoCount++;
    map[u].videos.push(v);
    (v.hashtags||[]).forEach(t => map[u].tags.add(t));
  });
  Object.values(map).forEach(c => {
    c.avgViews = Math.round(c.totalViews / Math.max(c.videoCount, 1));
    c.avgLikes = Math.round(c.totalLikes / Math.max(c.videoCount, 1));
    c.engRate  = c.totalViews > 0 ? ((c.totalLikes / c.totalViews) * 100) : 0;
  });
  let rows = Object.values(map).filter(c => c.videoCount >= 2);
  rows.sort((a,b) => vTcSortDir * (a[vTcSortCol] - b[vTcSortCol]));
  const arrow = dir => dir === -1 ? '↓' : '↑';
  const th = (col, label) => '<th onclick="vSortTopChannels(\''+col+'\')" class="'+(vTcSortCol===col?'sorted':'')+'">' +
    label+' <span class="sort-arrow">'+(vTcSortCol===col?arrow(vTcSortDir):'↕')+'</span></th>';
  if (!rows.length) {
    document.getElementById('v-panel-topchannels').innerHTML =
      '<div style="color:#ccc;padding:40px;text-align:center">No channels with 2+ videos found.</div>';
    return;
  }
  document.getElementById('v-panel-topchannels').innerHTML =
    '<div style="margin-bottom:16px"><span style="font-size:0.85rem;color:#888">Videos 2개 이상인 채널만 표시</span></div>' +
    '<div class="creators-table-wrap"><table><thead><tr>' +
      th('username','Channel')+th('avgViews','Avg Views')+th('avgLikes','Avg Likes') +
      th('engRate','Eng Rate')+th('totalViews','Total Views')+th('followers','Followers') +
      th('videoCount','Videos')+'</tr></thead><tbody>' +
    rows.map(c => {
      const tags = [...c.tags].slice(0,3).map(t=>'<span style="font-size:0.7rem;color:#c97d8a">#'+vEsc(t)+'</span>').join(' ');
      const av = c.avatar || '';
      const topVid = c.videos.sort((a,b)=>(b.stats?.views||0)-(a.stats?.views||0))[0];
      return '<tr><td><div style="display:flex;align-items:center;gap:10px">' +
          (av ? '<img src="'+vEsc(av)+'" style="width:36px;height:36px;border-radius:50%;object-fit:cover;border:2px solid #f5ece8;flex-shrink:0" onerror="this.style.display=\'none\'" loading="lazy">' : '') +
          '<div><a href="'+vEsc(c.url)+'" target="_blank">@'+vEsc(c.username)+'</a>' +
          (c.verified?'<span style="color:#4a9eff;font-size:0.7rem"> ✓</span>':'') +
          '<div style="margin-top:2px">'+tags+'</div></div></div></td>' +
        '<td style="font-weight:800;color:#c97d8a;font-size:1rem">'+vFmt(c.avgViews)+'</td>' +
        '<td>'+vFmt(c.avgLikes)+'</td><td>'+c.engRate.toFixed(1)+'%</td>' +
        '<td>'+vFmt(c.totalViews)+'</td><td>'+vFmt(c.followers)+'</td>' +
        '<td>'+c.videoCount+(topVid ? ' <a href="'+vEsc(topVid.url)+'" target="_blank" style="font-size:0.7rem;color:#c97d8a;margin-left:4px">top▶</a>' : '')+'</td></tr>';
    }).join('') + '</tbody></table></div>';
}

function vSortTopChannels(col) {
  if (vTcSortCol === col) vTcSortDir *= -1;
  else { vTcSortCol = col; vTcSortDir = -1; }
  vRenderTopChannels();
}

// ── Hashtags panel ──
const V_BASE_TAGS = new Set(['kbeauty','koreanbeauty','kbeautyroutine','koreanskincare',
  'glasskin','kbeautytips','koreanmakeup','kbeautyreview','kbeautyskincare','koreanskincaret']);

function vRenderHashtags() {
  const counts = {}, views = {}, likes = {};
  vFiltered.forEach(v => {
    (v.hashtags||[]).forEach(t => {
      if (!t) return;
      counts[t] = (counts[t]||0)+1;
      views[t]  = (views[t]||0) + (v.stats?.views||0);
      likes[t]  = (likes[t]||0) + (v.stats?.likes||0);
    });
  });
  const sorted = Object.entries(counts).sort((a,b) => (views[b[0]]||0) - (views[a[0]]||0));
  document.getElementById('v-panel-hashtags').innerHTML =
    '<div style="margin-bottom:16px"><span style="font-size:0.8rem;color:#888">Click a hashtag to filter videos by it.</span></div>' +
    '<div class="tags-cloud">' +
    sorted.map(([tag, count]) =>
      '<div class="tag-pill" onclick="vFilterByTag(\''+vEsc(tag)+'\')">' +
         '<div class="tn">#'+vEsc(tag)+'</div>' +
         '<div class="ts">'+count+' videos · '+vFmt(views[tag])+' views</div></div>'
    ).join('') + '</div>';
}

// ── Audio panel ──
function vRenderAudio() {
  const map = {};
  vFiltered.forEach(v => {
    const m = v.music||{};
    if (!m.title || m.original) return;
    const key = m.title + ' — ' + (m.artist||'Unknown');
    if (!map[key]) map[key] = { count:0, views:0, likes:0 };
    map[key].count++;
    map[key].views += v.stats?.views||0;
    map[key].likes += v.stats?.likes||0;
  });
  const sorted = Object.entries(map).sort((a,b) => b[1].views - a[1].views);
  if (!sorted.length) {
    document.getElementById('v-panel-audio').innerHTML =
      '<div class="empty"><div class="icon">🎵</div><p>No audio data in current filter.</p></div>';
    return;
  }
  document.getElementById('v-panel-audio').innerHTML =
    '<div class="audio-table"><table><thead><tr>' +
      '<th>#</th><th>Track</th><th>Used in</th><th>Total Views</th><th>Total Likes</th>' +
    '</tr></thead><tbody>' +
    sorted.map(([track, d], i) =>
      '<tr><td style="color:#ddd;font-weight:700">'+(i+1)+'</td>' +
       '<td><b>'+vEsc(track)+'</b></td>' +
       '<td>'+d.count+' videos</td>' +
       '<td>'+vFmt(d.views)+'</td>' +
       '<td>'+vFmt(d.likes)+'</td></tr>'
    ).join('') + '</tbody></table></div>';
}

// ── Apify 크레딧 표시 ──
async function vLoadApifyUsage() {
  try {
    const d = await fetch('/api/apify/usage').then(r=>r.json());
    if (d.error) { document.getElementById('apify-used').textContent = '💳 —'; return; }
    const pct = d.pct;
    const color = pct > 85 ? '#f87171' : pct > 60 ? '#fbbf24' : '#4ade80';
    document.getElementById('apify-used').textContent =
      `💳 $${d.used}/$${d.limit} (${pct}%)`;
    document.getElementById('apify-bar-fill').style.width = Math.min(pct,100)+'%';
    document.getElementById('apify-bar-fill').style.background = color;
  } catch(e) {}
}

// ── Scrape status banner ──
function vShowScrapeBar(msg, done=false) {
  let bar = document.getElementById('v-scrape-bar');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'v-scrape-bar';
    bar.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);z-index:9999;' +
      'background:#1e293b;color:#fff;padding:14px 24px;border-radius:12px;font-size:0.88rem;' +
      'font-weight:600;display:flex;align-items:center;gap:10px;box-shadow:0 4px 20px rgba(0,0,0,0.3);' +
      'min-width:280px;justify-content:center;transition:opacity 0.4s';
    document.body.appendChild(bar);
  }
  bar.style.opacity = '1';
  bar.style.background = done ? '#16a34a' : '#1e293b';
  bar.innerHTML = done
    ? '✅ ' + msg
    : '<span style="display:inline-block;animation:spin 1s linear infinite;font-size:1rem">⏳</span> ' + msg;
  if (done) setTimeout(() => { bar.style.opacity='0'; setTimeout(()=>bar.remove(),400); }, 4000);
}
function vHideScrapeBar() {
  const bar = document.getElementById('v-scrape-bar');
  if (bar) { bar.style.opacity='0'; setTimeout(()=>bar.remove(),400); }
}

// ── Scrape trigger ──
async function vTriggerScrape() {
  const btn = document.getElementById('v-run-btn');
  btn.disabled = true; btn.textContent = '수집 중...';
  const endpoint = vPlatform === 'twitter' ? '/api/run/twitter' : '/api/run';

  // 브라우저 알림 권한 요청
  if (Notification.permission === 'default') await Notification.requestPermission();

  const r = await fetch(endpoint, { method: 'POST' });
  const result = await r.json();
  if (!result.ok) {
    vShowScrapeBar('오류: ' + (result.error || '실행 실패'), true);
    btn.disabled = false; btn.textContent = 'Run New Scrape'; return;
  }

  const startTime = Date.now();
  vShowScrapeBar('GitHub Actions 수집 중 — 완료 시 자동 업데이트');

  // 경과 시간 표시 타이머
  const ticker = setInterval(() => {
    const elapsed = Math.floor((Date.now() - startTime) / 1000);
    const mm = String(Math.floor(elapsed/60)).padStart(2,'0');
    const ss = String(elapsed%60).padStart(2,'0');
    vShowScrapeBar(`GitHub Actions 수집 중 — ${mm}:${ss} 경과`);
  }, 1000);

  // 최대 20분간 30초마다 새 데이터 감지
  let checks = 0;
  const poll = setInterval(async () => {
    checks++;
    if (checks > 40) { // 20분 타임아웃
      clearInterval(poll); clearInterval(ticker);
      vShowScrapeBar('시간 초과 — GitHub Actions 탭에서 확인하세요', true);
      btn.disabled = false; btn.textContent = 'Run New Scrape'; return;
    }
    try {
      if (vPlatform === 'tiktok') {
        const dates = await fetch('/api/dates').then(r=>r.json());
        if (dates[0] !== vAllDates[0]) {
          clearInterval(poll); clearInterval(ticker);
          btn.disabled = false; btn.textContent = 'Run New Scrape';
          vAllDates = dates; await vLoadAllData();
          vShowScrapeBar('새 TikTok 데이터 로드 완료!', true);
          if (Notification.permission === 'granted')
            new Notification('K-Beauty Hub', { body: '✅ TikTok 수집 완료! 새 데이터가 업데이트됐어요.', icon: '/favicon.ico' });
        }
      } else {
        const dates = await fetch('/api/x/dates').then(r=>r.json());
        if (dates[0] !== vxAllDates[0]) {
          clearInterval(poll); clearInterval(ticker);
          btn.disabled = false; btn.textContent = 'Run New Scrape';
          vxAllDates = dates; await vLoadXAllData();
          vShowScrapeBar('새 Twitter 데이터 로드 완료!', true);
          if (Notification.permission === 'granted')
            new Notification('K-Beauty Hub', { body: '✅ Twitter 수집 완료! 새 데이터가 업데이트됐어요.', icon: '/favicon.ico' });
        }
      }
    } catch(e) {}
  }, 30000);
}

// ── Platform switcher ──
function vSwitchPlatform(p) {
  vPlatform = p;
  const hdr = document.getElementById('vh-subheader');
  const tiktokHub = document.getElementById('v-tiktok-layout');
  const twitterHub = document.getElementById('v-twitter-hub');
  document.getElementById('btn-tiktok').classList.toggle('active', p === 'tiktok');
  document.getElementById('btn-twitter').classList.toggle('active', p === 'twitter');
  tiktokHub.style.display = 'none';
  twitterHub.style.display = 'none';
  if (p === 'tiktok') {
    hdr.className = 'vh-subheader tiktok';
    document.getElementById('vh-hub-title').textContent = 'K-Beauty TikTok Hub';
    tiktokHub.style.display = 'flex';
  } else {
    hdr.className = 'vh-subheader twitter';
    document.getElementById('vh-hub-title').textContent = '🇯🇵 K-Beauty Japan X (Twitter) Hub';
    twitterHub.style.display = 'block';
    if (!vxAllDates.length) vInitTwitter();
  }
  document.getElementById('v-run-btn').textContent = 'Run New Scrape';
}

// ── Twitter init & data ──
async function vInitTwitter() {
  const r = await fetch('/api/x/dates');
  vxAllDates = await r.json();
  const sel = document.getElementById('vx-date-pick');
  sel.innerHTML = '';
  vxAllDates.forEach(d => {
    const o = document.createElement('option');
    o.value = d; o.textContent = d; sel.appendChild(o);
  });
  if (vxAllDates.length) {
    await vLoadXAllData();
  } else {
    document.getElementById('vh-last-updated').textContent = 'No Twitter data yet — click Run New Scrape';
    document.getElementById('vx-panel-tweets').innerHTML =
      '<div class="empty"><div class="icon">𝕏</div><p>No Twitter data yet.</p><p style="margin-top:8px">Click <b>Run New Scrape</b> to pull K-beauty tweets.</p></div>';
  }
}

async function vLoadXAllData() {
  vxAllData = [];
  const seen = new Set();
  for (const date of vxAllDates) {
    const r = await fetch('/api/x/data/' + date);
    const items = await r.json();
    items.forEach(t => {
      if (!seen.has(t.id)) { seen.add(t.id); vxAllData.push({...t, _dataset: date}); }
    });
  }
  document.getElementById('vh-last-updated').textContent =
    vxAllData.length + ' tweets across ' + vxAllDates.length + ' dataset(s) · Latest: ' + (vxAllDates[0]||'—');
  vApplyXFilters();
}

async function vLoadXDate(date) { vApplyXFilters(); }

function vSetXDateRange(val, btn) {
  vxDateRange = val;
  document.querySelectorAll('#v-twitter-hub .date-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}

const V_KBEAUTY_KEYWORDS = new Set([
  'kbeauty','k-beauty','koreanbeauty','korean beauty','koreanskincare','korean skincare',
  'glasskin','glass skin','koreanmakeup','korean makeup','kbeautyroutine','skincare routine',
  'sunscreen','toner','ampoule','essence','serum','snail','cica','centella','niacinamide',
  'ceramide','hyaluronic','retinol','gua sha','sheet mask','pore','brightening','whitening',
  'cosrx','laneige','innisfree','etude','missha','some by mi','skin1004','anua','isntree',
  'beauty of joseon','round lab','torriden','dr jart','klavuu','rom&nd','peripera',
  'sulwhasoo','hera','espoir','3ce','nacific','apieu','tocobo','goodal'
]);

function vIsKbeautyRelevant(t) {
  const text = (t.text||'').toLowerCase();
  const tags = (t.hashtags||[]).map(h => h.toLowerCase());
  for (const kw of V_KBEAUTY_KEYWORDS) { if (text.includes(kw)) return true; }
  for (const tag of tags) { if (V_KBEAUTY_KEYWORDS.has(tag)) return true; }
  return false;
}

function vApplyXFilters() {
  const author   = document.getElementById('vxf-author').value.trim().toLowerCase();
  const hashtag  = document.getElementById('vxf-hashtag').value.trim().toLowerCase().replace('#','');
  const keyword  = document.getElementById('vxf-keyword').value.trim().toLowerCase();
  const viewsMin = parseInt(document.getElementById('vxf-views-min').value) || 0;
  const rtMin    = parseInt(document.getElementById('vxf-rt-min').value) || 0;
  const dataset  = document.getElementById('vx-date-pick').value;
  const now = Date.now(); const dayMs = 86400000;
  const cutoff = vxDateRange === 'all' ? 0 : now - (vxDateRange * dayMs);
  vxFiltered = vxAllData.filter(t => {
    if (!vIsKbeautyRelevant(t)) return false;
    if (vxDateRange !== 'all' && t.created_at) {
      if (new Date(t.created_at).getTime() < cutoff) return false;
    }
    if (author  && !(t.author?.username||'').toLowerCase().includes(author)) return false;
    if (hashtag && !(t.hashtags||[]).some(h => h.toLowerCase().includes(hashtag))) return false;
    if (keyword && !(t.text||'').toLowerCase().includes(keyword)) return false;
    if ((t.views    ||0) < viewsMin) return false;
    if ((t.retweets ||0) < rtMin)    return false;
    if (dataset && dataset !== 'all' && t._dataset !== dataset) return false;
    return true;
  });
  vUpdateXStats();
  vRenderXActiveTab();
}

function vClearXFilters() {
  ['vxf-author','vxf-hashtag','vxf-keyword','vxf-views-min','vxf-rt-min'].forEach(id => {
    document.getElementById(id).value = '';
  });
  vxDateRange = 'all';
  document.querySelectorAll('#v-twitter-hub .date-btn').forEach(b => b.classList.remove('active'));
  document.querySelector('#v-twitter-hub .date-btn').classList.add('active');
  vApplyXFilters();
}

function vUpdateXStats() {
  const totalViews = vxFiltered.reduce((s,t) => s+(t.views||0), 0);
  const totalLikes = vxFiltered.reduce((s,t) => s+(t.likes||0), 0);
  const accounts   = new Set(vxFiltered.map(t => t.author?.username)).size;
  document.getElementById('vxs-tweets').textContent   = vFmt(vxFiltered.length);
  document.getElementById('vxs-views').textContent    = vFmt(totalViews);
  document.getElementById('vxs-likes').textContent    = vFmt(totalLikes);
  document.getElementById('vxs-accounts').textContent = accounts;
  document.getElementById('vx-result-count').textContent = vxFiltered.length + ' tweets';
}

function vSwitchXTab(tab, btn) {
  vxActiveTab = tab;
  document.querySelectorAll('#vx-tabs .tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  vRenderXActiveTab();
}

function vRenderXActiveTab() {
  if (vxActiveTab === 'tweets')    vRenderTweets();
  if (vxActiveTab === 'xcreators') vRenderXCreators();
  if (vxActiveTab === 'xhashtags') vRenderXHashtags();
  ['tweets','xcreators','xhashtags'].forEach(t => {
    document.getElementById('vx-panel-' + t).style.display = t === vxActiveTab ? 'block' : 'none';
  });
}

function vRenderTweets() {
  const sortBy = document.getElementById('vx-sort-select').value;
  const sorted = [...vxFiltered].sort((a,b) => {
    if (sortBy === 'date') return new Date(b.created_at||0) - new Date(a.created_at||0);
    return (b[sortBy]||0) - (a[sortBy]||0);
  });
  if (!sorted.length) {
    document.getElementById('vx-panel-tweets').innerHTML =
      '<div class="empty"><div class="icon">𝕏</div><p>No tweets match your filters.</p></div>';
    return;
  }
  document.getElementById('vx-panel-tweets').innerHTML =
    '<div class="tweet-grid">' +
    sorted.map((t, i) => {
      const a = t.author || {};
      const tags = (t.hashtags||[]).slice(0,5).map(h =>
        '<span class="tweet-tag" onclick="vFilterXByTag(\''+vEsc(h)+'\')">#'+vEsc(h)+'</span>').join('');
      const img = t.media_url
        ? '<img class="tweet-img" src="'+vEsc(t.media_url)+'" loading="lazy" onerror="this.style.display=\'none\'">'
        : '';
      const date = t.created_at ? new Date(t.created_at).toLocaleDateString() : '';
      return '<div class="tweet-card"><div class="tweet-author">' +
          (a.avatar ? '<img class="tweet-avatar" src="'+vEsc(a.avatar)+'" loading="lazy" onerror="this.style.display=\'none\'">' : '<div class="tweet-avatar" style="display:flex;align-items:center;justify-content:center;font-size:18px">𝕏</div>') +
          '<div><div class="tweet-name">'+vEsc(a.name||a.username)+(a.verified ? '<span class="x-verified">✓</span>' : '')+'</div>' +
          '<div class="tweet-handle">@'+vEsc(a.username)+' · '+vFmt(a.followers)+' followers · '+date+'</div></div>' +
          '<span style="margin-left:auto;font-size:0.72rem;color:#ddd;font-weight:700">#'+(i+1)+'</span></div>' +
        '<div class="tweet-text">'+vEsc(t.text||'')+'</div>'+img+
        '<div class="tweet-metrics">' +
          '<div class="tweet-metric"><div class="mv">'+vFmt(t.views)+'</div><div class="ml">Views</div></div>' +
          '<div class="tweet-metric"><div class="mv">'+vFmt(t.likes)+'</div><div class="ml">Likes</div></div>' +
          '<div class="tweet-metric"><div class="mv">'+vFmt(t.retweets)+'</div><div class="ml">Retweets</div></div>' +
          '<div class="tweet-metric"><div class="mv">'+vFmt(t.replies)+'</div><div class="ml">Replies</div></div></div>' +
        '<div class="tweet-tags">'+tags+'</div>' +
        '<div class="tweet-footer"><span>via #'+vEsc(t.source_tag||'')+' · '+vEsc(t._dataset||'')+'</span>' +
          '<a class="tweet-link" href="'+vEsc(t.url)+'" target="_blank">View on X →</a></div></div>';
    }).join('') + '</div>';
}

function vFilterXByTag(tag) {
  document.getElementById('vxf-hashtag').value = tag;
  vApplyXFilters();
  vToast('Filtered by #'+tag);
}

function vRenderXCreators() {
  const map = {};
  vxFiltered.forEach(t => {
    const a = t.author||{}; const u = a.username;
    if (!u) return;
    if (!map[u]) map[u] = { ...a, totalViews:0, totalLikes:0, totalRt:0, tweetCount:0, tags:new Set() };
    map[u].totalViews += t.views||0;
    map[u].totalLikes += t.likes||0;
    map[u].totalRt    += t.retweets||0;
    map[u].tweetCount++;
    (t.hashtags||[]).forEach(h => map[u].tags.add(h));
  });
  const rows = Object.values(map).sort((a,b) => b.totalViews - a.totalViews);
  document.getElementById('vx-panel-xcreators').innerHTML =
    '<div class="creators-table-wrap"><table><thead><tr>' +
      '<th>Account</th><th>Followers</th><th>Total Views</th>' +
      '<th>Total Likes</th><th>Retweets</th><th>Tweets</th>' +
    '</tr></thead><tbody>' +
    rows.map(a => {
      const av = a.avatar || '';
      const tags = [...a.tags].slice(0,3).map(t=>'<span style="font-size:0.7rem;color:#1d9bf0">#'+vEsc(t)+'</span>').join(' ');
      return '<tr><td><div style="display:flex;align-items:center;gap:10px">' +
        (av ? '<img src="'+vEsc(av)+'" style="width:36px;height:36px;border-radius:50%;object-fit:cover;border:2px solid #e8f4ff;flex-shrink:0" onerror="this.style.display=\'none\'" loading="lazy">' : '') +
        '<div><a href="https://x.com/'+vEsc(a.username)+'" target="_blank" style="color:#1d9bf0">@'+vEsc(a.username)+'</a>' +
        (a.verified?'<span style="color:#1d9bf0;font-size:0.7rem"> ✓</span>':'') +
        '<div style="margin-top:2px">'+tags+'</div></div></div></td>' +
        '<td>'+vFmt(a.followers)+'</td><td><b>'+vFmt(a.totalViews)+'</b></td>' +
        '<td>'+vFmt(a.totalLikes)+'</td><td>'+vFmt(a.totalRt)+'</td>' +
        '<td>'+a.tweetCount+'</td></tr>';
    }).join('') + '</tbody></table></div>';
}

function vRenderXHashtags() {
  const counts={}, views={};
  vxFiltered.forEach(t => {
    (t.hashtags||[]).forEach(h => {
      if (!h) return;
      counts[h] = (counts[h]||0)+1;
      views[h]  = (views[h]||0)+(t.views||0);
    });
  });
  const sorted = Object.entries(counts).sort((a,b) => (views[b[0]]||0)-(views[a[0]]||0));
  document.getElementById('vx-panel-xhashtags').innerHTML =
    '<div style="margin-bottom:16px"><span style="font-size:0.8rem;color:#888">Click to filter.</span></div>' +
    '<div class="tags-cloud">' +
    sorted.map(([tag,count]) =>
      '<div class="tag-pill" onclick="vFilterXByTag(\''+vEsc(tag)+'\')" style="border-color:#e8f4ff">' +
         '<div class="tn" style="color:#1d9bf0">#'+vEsc(tag)+'</div>' +
         '<div class="ts">'+count+' tweets · '+vFmt(views[tag])+' views</div></div>'
    ).join('') + '</div>';
}

// ── Video hover preview ──
let vPreviewTimer = null;
let vPreviewActive = false;

function vExtractVideoId(url) {
  const m = url.match(/video\/(\d+)/);
  return m ? m[1] : null;
}

function vShowVideoPreview(el, e) {
  const url = el.dataset.videoUrl;
  if (!url) return;
  const videoId = vExtractVideoId(url);
  if (!videoId) return;
  const previewEl = document.getElementById('vVideoPreview');
  vPreviewTimer = setTimeout(() => {
    previewEl.innerHTML = '<iframe src="https://www.tiktok.com/player/v1/'+videoId+'?autoplay=1&mute=1&loop=1&controls=0" allow="autoplay; encrypted-media" loading="lazy"></iframe>';
    vPositionPreview(e);
    previewEl.classList.add('active');
    vPreviewActive = true;
  }, 400);
}

function vMoveVideoPreview(e) {
  if (vPreviewActive) vPositionPreview(e);
}

function vPositionPreview(e) {
  const previewEl = document.getElementById('vVideoPreview');
  const pw = 340, ph = 580;
  let x = e.clientX + 20;
  let y = e.clientY - ph / 2;
  if (x + pw > window.innerWidth) x = e.clientX - pw - 20;
  if (y < 10) y = 10;
  if (y + ph > window.innerHeight - 10) y = window.innerHeight - ph - 10;
  previewEl.style.left = x + 'px';
  previewEl.style.top = y + 'px';
}

function vHideVideoPreview() {
  clearTimeout(vPreviewTimer);
  const previewEl = document.getElementById('vVideoPreview');
  previewEl.classList.remove('active');
  previewEl.innerHTML = '';
  vPreviewActive = false;
}

// ══════════════════════════════════════════════════════════════════════════
// 트렌드 인사이트 HUB
// ══════════════════════════════════════════════════════════════════════════
let trInitialized = false;
let trTimelineData = {};
let trGapsData = [];
let trCreatorsData = [];
let trCreatorsLoaded = false;
let trBrandsData = [];
let trBrandsLoaded = false;
let trBrandsSort = 'views';
let trBrandsView = 'card';
let brChartInstance = null;
let trActiveKws = new Set();
let trChart = null;
let trActiveCat = '전체';

const TR_COLORS = [
  '#6366f1','#ec4899','#f59e0b','#10b981','#3b82f6',
  '#ef4444','#8b5cf6','#14b8a6','#f97316','#06b6d4',
  '#84cc16','#a78bfa','#fb7185','#34d399','#fbbf24',
];

async function trInit() {
  await trLoadTimeline();
  trLoadGaps();
  const savedTab = sessionStorage.getItem('kbTrTab') || 'timeline';
  if (savedTab !== 'timeline') {
    const btn = document.getElementById('tr-tab-' + savedTab);
    if (btn) trSwitchTab(savedTab, btn);
  }
}

function trSwitchTab(tab, btn) {
  sessionStorage.setItem('kbTrTab', tab);
  document.getElementById('tr-panel-timeline').style.display = tab === 'timeline' ? '' : 'none';
  document.getElementById('tr-panel-gaps').style.display     = tab === 'gaps'     ? '' : 'none';
  document.getElementById('tr-panel-creators').style.display = tab === 'creators' ? '' : 'none';
  document.getElementById('tr-panel-brands').style.display   = tab === 'brands'   ? '' : 'none';
  document.querySelectorAll('#trend-hub .tr-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  if (tab === 'creators' && !trCreatorsLoaded) { trLoadCreators(); trCreatorsLoaded = true; }
  if (tab === 'brands'   && !trBrandsLoaded)   { trLoadBrands();   trBrandsLoaded = true; }
}

// ── Timeline ──────────────────────────────────────────────────────────────

const MARKET_COLORS = {
  'US':'#3b82f6','UK':'#8b5cf6','JP':'#ef4444',
  'OY':'#10b981','TT':'#1a1a1a','YS':'#f59e0b','QJ':'#ec4899'
};

async function trLoadTimeline() {
  try {
    const r = await fetch('/api/trends/timeline');
    trTimelineData = await r.json();
  } catch(e) { trTimelineData = {}; }

  const allKws = trTimelineData.keywords || [];
  if (!allKws.length) {
    document.getElementById('tl-empty').style.display = '';
    document.querySelector('#tr-panel-timeline .chart-card').style.display = 'none';
    return;
  }
  trRenderTimeline();
}

function trSetCat(cat, btn) {
  trActiveCat = cat;
  document.querySelectorAll('.tl-cat-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  trRenderTimeline();
}

function trRenderTimeline() {
  const allKws    = trTimelineData.keywords || [];
  const data      = trTimelineData.data || {};
  const labels    = trTimelineData.labels || {};
  const categories = trTimelineData.categories || {};

  const ASIAN   = ['OY','JP','QJ'];
  const WESTERN = ['US','UK','TT','YS'];
  const MARKET_NAMES = { OY:'OliveYoung', JP:'Amazon JP', QJ:'Qoo10', US:'Amazon US', UK:'Amazon UK', TT:'TikTok Shop', YS:'YesStyle' };

  // 카테고리 필터
  const filtered = trActiveCat === '전체'
    ? allKws
    : allKws.filter(kw => (categories[kw] || '성분') === trActiveCat);

  // 아시아/서양 합계 계산 + 정렬
  const withTotals = filtered.map(kw => {
    const d = data[kw] || {};
    const asian   = ASIAN.reduce((s, m) => s + (d[m] || 0), 0);
    const western = WESTERN.reduce((s, m) => s + (d[m] || 0), 0);
    return { kw, asian, western, total: asian + western, d };
  }).filter(x => x.total > 0).sort((a, b) => b.total - a.total);

  // 요약 카드
  const summaryEl = document.getElementById('tl-summary');
  if (summaryEl && withTotals.length > 0) {
    const top1 = withTotals[0];
    const mostAsian  = [...withTotals].sort((a, b) => (b.asian / (b.total||1)) - (a.asian / (a.total||1)))[0];
    const mostWest   = [...withTotals].sort((a, b) => (b.western / (b.total||1)) - (a.western / (a.total||1)))[0];
    const ko = kw => labels[kw] || kw;
    summaryEl.innerHTML = `
      <div class="tl-sum-card">
        <div class="sum-label">🏆 전체 1위</div>
        <div class="sum-value">${ko(top1.kw)}</div>
        <div class="sum-sub">전 마켓 합산 ${top1.total}개 상품</div>
      </div>
      <div class="tl-sum-card" style="border-color:#fca5a5">
        <div class="sum-label">🌏 아시아 집중</div>
        <div class="sum-value">${ko(mostAsian.kw)}</div>
        <div class="sum-sub">아시아 ${mostAsian.asian}개 · 서양 ${mostAsian.western}개</div>
      </div>
      <div class="tl-sum-card" style="border-color:#93c5fd">
        <div class="sum-label">🌍 서양 강세</div>
        <div class="sum-value">${ko(mostWest.kw)}</div>
        <div class="sum-sub">서양 ${mostWest.western}개 · 아시아 ${mostWest.asian}개</div>
      </div>`;
  }

  if (!withTotals.length) return;

  // Top 15만 차트에 표시
  const top = withTotals.slice(0, 15);

  // Y축 레이블: 한국어명 (영문) 형태
  const kwLabels = top.map(x => labels[x.kw] ? `${labels[x.kw]}  (${x.kw})` : x.kw);

  const ctx = document.getElementById('tl-chart');
  if (trChart) { trChart.destroy(); trChart = null; }
  trChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: kwLabels,
      datasets: [
        {
          label: '🌏 아시아 (OY·JP·Qoo10)',
          data: top.map(x => x.asian),
          backgroundColor: '#fca5a5bb',
          borderColor: '#ef4444',
          borderWidth: 1.5,
          borderRadius: 4,
        },
        {
          label: '🌍 서양 (US·UK·TikTok·YesStyle)',
          data: top.map(x => x.western),
          backgroundColor: '#93c5fdbb',
          borderColor: '#3b82f6',
          borderWidth: 1.5,
          borderRadius: 4,
        },
      ]
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { position: 'top', labels: { font: { size: 11, weight: '700' }, padding: 14 } },
        tooltip: {
          callbacks: {
            afterBody: items => {
              const x = top[items[0]?.dataIndex];
              if (!x) return '';
              const lines = ['', '── 마켓별 상세 ──'];
              Object.entries(MARKET_NAMES).forEach(([code, name]) => {
                const cnt = x.d[code] || 0;
                if (cnt) lines.push(`  ${name}: ${cnt}개`);
              });
              return lines;
            }
          }
        }
      },
      scales: {
        x: {
          stacked: false,
          grid: { color: '#f0f0f0' },
          ticks: { font: { size: 10 }, callback: v => `${v}개` }
        },
        y: {
          stacked: false,
          grid: { display: false },
          ticks: { font: { size: 11 } }
        }
      }
    }
  });
}

// ── Gap Detection ──────────────────────────────────────────────────────────

async function trLoadGaps() {
  const grid = document.getElementById('tr-gaps-grid');
  const empty = document.getElementById('tr-gaps-empty');
  grid.innerHTML = '<div style="padding:40px;text-align:center;color:#aaa;font-size:0.85rem">분석 중...</div>';
  empty.style.display = 'none';

  try {
    const r = await fetch('/api/trends/gaps');
    trGapsData = await r.json();
  } catch(e) { trGapsData = []; }

  if (!trGapsData.length) {
    grid.innerHTML = '';
    empty.style.display = '';
    return;
  }

  const maxScore = trGapsData[0]?.gap_score || 1;

  grid.innerHTML = trGapsData.map(g => {
    const pct = Math.round(g.gap_score / maxScore * 100);
    let tier, tierLabel;
    if (pct >= 60)      { tier = 'hot';  tierLabel = '🔴 Hot Gap'; }
    else if (pct >= 30) { tier = 'warm'; tierLabel = '🟡 Warm Gap'; }
    else                { tier = 'cool'; tierLabel = '🔵 Well-covered'; }

    const barCol = tier === 'hot' ? '#ef4444' : tier === 'warm' ? '#f59e0b' : '#3b82f6';
    const asianPct = Math.round(g.asian_count / Math.max(g.total_count, 1) * 100);
    const opportunity = tier === 'hot'
      ? '아시아 마켓 독주 — 서양 진출 공백, PB 진입 최우선 검토'
      : tier === 'warm'
      ? '아시아↔서양 균형점 — 차별화 전략으로 진입 가능'
      : '서양 마켓 이미 포화 — 가격·포지셔닝 차별화 필요';

    const asianDetail = Object.entries(g.asian_detail || {}).map(([k,v]) => `${k}:${v}`).join(' · ');
    const westDetail  = Object.entries(g.western_detail || {}).map(([k,v]) => `${k}:${v}`).join(' · ');

    return `<div class="gap-card ${tier}">
      <div class="gap-score-badge">GAP ${pct}</div>
      <div class="gap-keyword">
        <span>${g.keyword}</span>
        <span class="gap-badge ${tier}">${tierLabel}</span>
      </div>
      <div class="gap-metrics">
        <div class="gap-metric"><div class="gv">${g.asian_count}</div><div class="gl">아시아 상품${asianDetail ? '<br><span style="font-size:0.58rem;color:#bbb">'+asianDetail+'</span>' : ''}</div></div>
        <div class="gap-metric"><div class="gv">${g.western_count}</div><div class="gl">서양 상품${westDetail ? '<br><span style="font-size:0.58rem;color:#bbb">'+westDetail+'</span>' : ''}</div></div>
        <div class="gap-metric"><div class="gv">${g.total_count}</div><div class="gl">전체 상품</div></div>
      </div>
      <div class="gap-bar-wrap">
        <div class="gap-bar-label"><span>아시아 비중</span><span>${asianPct}%</span></div>
        <div class="gap-bar-track"><div class="gap-bar-fill" style="width:${asianPct}%;background:${barCol}"></div></div>
      </div>
      <div class="gap-opportunity">${opportunity}</div>
    </div>`;
  }).join('');
}

// ── Creator Signal Detection ───────────────────────────────────────────────

async function trLoadCreators() {
  const grid  = document.getElementById('tr-creators-grid');
  const empty = document.getElementById('tr-creators-empty');
  grid.innerHTML = '<div style="padding:40px;text-align:center;color:#aaa;font-size:0.85rem">분석 중...</div>';
  empty.style.display = 'none';
  try {
    const r = await fetch('/api/trends/creators');
    trCreatorsData = await r.json();
  } catch(e) { trCreatorsData = []; }

  if (!trCreatorsData.length) {
    grid.innerHTML = '';
    empty.style.display = '';
    return;
  }

  const maxSignal = trCreatorsData[0]?.signal_score || 1;

  grid.innerHTML = trCreatorsData.map((c, i) => {
    const er = c.avg_er;
    let tier, tierLabel, tierEmoji;
    if (er >= 20)     { tier = 'tier-breakthrough'; tierLabel = 'Breakthrough'; tierEmoji = '🔴'; }
    else if (er >= 5) { tier = 'tier-rising';       tierLabel = 'Rising';       tierEmoji = '🟡'; }
    else              { tier = 'tier-emerging';      tierLabel = 'Emerging';     tierEmoji = '🔵'; }

    const avatar = c.avatar
      ? `<img class="cr-avatar" src="${vEsc(c.avatar)}" onerror="this.style.display='none'">`
      : `<div class="cr-avatar-ph">👤</div>`;

    const verified = c.verified
      ? `<span style="background:#1d9bf0;color:white;font-size:0.6rem;padding:1px 6px;border-radius:8px;font-weight:700">✓</span>`
      : '';

    const kwHtml = (c.keywords || []).map(k =>
      `<span class="cr-kw">${vEsc(k)}</span>`
    ).join('');

    const vid = c.top_video || {};
    const caption = (vid.caption || '').replace(/https?:\/\/\S+/g, '').trim().slice(0, 120);
    const cover = vid.cover || '';

    const signalPct = Math.round(c.signal_score / maxSignal * 100);

    return `<div class="cr-card ${tier}">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px">
        <span class="cr-signal-badge ${tier}">${tierEmoji} ${tierLabel} · SIGNAL ${signalPct}</span>
        <span style="font-size:0.7rem;color:#aaa">#${i+1}</span>
      </div>
      <div class="cr-header">
        ${avatar}
        <div class="cr-meta">
          <div class="cr-name">${vEsc(c.name)}${verified}</div>
          <div class="cr-handle">@${vEsc(c.username)} · ${trFmt(c.followers)} 팔로워</div>
        </div>
      </div>
      <div class="cr-stats">
        <div class="cr-stat"><div class="sv">${er.toFixed(1)}%</div><div class="sl">평균 ER</div></div>
        <div class="cr-stat"><div class="sv">${trFmt(vid.likes||0)}</div><div class="sl">베스트 좋아요</div></div>
        <div class="cr-stat"><div class="sv">${trFmt(c.total_views||0)}</div><div class="sl">총 뷰</div></div>
      </div>
      ${cover ? `<a href="${vEsc(vid.url||'#')}" target="_blank" style="display:block;margin-bottom:8px"><img src="${vEsc(cover)}" style="width:100%;border-radius:10px;object-fit:cover;max-height:140px" onerror="this.style.display='none'"></a>` : ''}
      ${caption ? `<div class="cr-tweet">${vEsc(caption)}</div>` : ''}
      ${kwHtml ? `<div class="cr-keywords">${kwHtml}</div>` : ''}
      <div style="display:flex;gap:12px;font-size:0.72rem;color:#aaa;margin-bottom:8px">
        <span>👁 ${trFmt(vid.views||0)}</span>
        <span>❤️ ${trFmt(vid.likes||0)}</span>
        <span>💬 ${trFmt(vid.comments||0)}</span>
        <span>🔖 ${trFmt(vid.saves||0)}</span>
      </div>
      <div class="cr-footer">
        <a class="cr-link" href="${vEsc(c.url||'#')}" target="_blank">TikTok에서 보기 →</a>
        <span class="cr-er">ER ${er.toFixed(1)}%</span>
      </div>
    </div>`;
  }).join('');
}

function trFmt(n) {
  n = parseInt(n) || 0;
  if (n >= 1e6) return (n/1e6).toFixed(1)+'M';
  if (n >= 1e3) return (n/1e3).toFixed(1)+'K';
  return n.toString();
}

/* ── 순위 변동 패널 ──────────────────────────────────── */
let rcPeriod = '1d';
let rcCountry = '';
let rcData = null;

function rcOpen() {
  // 현재 보고 있는 국가 탭에 맞게 자동 필터
  const cur = (typeof country !== 'undefined' && country !== 'ALL' && country !== 'DB') ? country : '';
  if (rcCountry !== cur) { rcData = null; }
  rcCountry = cur;
  document.getElementById('rank-change-panel').style.display = 'flex';
  document.body.style.overflow = 'hidden';
  if (!rcData) rcLoad();
}
function rcClose() {
  document.getElementById('rank-change-panel').style.display = 'none';
  document.body.style.overflow = '';
}
function rcSetPeriod(p, btn) {
  rcPeriod = p;
  rcData = null;
  rcCountry = '';
  document.querySelectorAll('.rc-period-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  rcLoad();
}
function rcSetCountry(c, btn) {
  rcCountry = c;
  document.querySelectorAll('.rc-ctry-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  rcRender();
}

const SHOP_META = {
  'US': {name:'Amazon US',  icon:'🛒', flag:'🇺🇸'},
  'UK': {name:'Amazon UK',  icon:'🛒', flag:'🇬🇧'},
  'JP': {name:'Amazon JP',  icon:'🛒', flag:'🇯🇵'},
  'OY': {name:'OliveYoung', icon:'🌿', flag:'🌿'},
  'YS': {name:'YesStyle',   icon:'✨', flag:'✨'},
  'TT': {name:'TikTok Shop',icon:'🎵', flag:'🎵'},
  'QJ': {name:'Qoo10 Japan',icon:'🛍️', flag:'🛍️'},
};
function rcShopLabel(cc) {
  const m = SHOP_META[cc];
  return m ? `${m.flag} ${m.name}` : cc;
}

async function rcLoad() {
  const body = document.getElementById('rc-body');
  body.innerHTML = '<div style="text-align:center;padding:40px;color:#aaa">분석 중...</div>';
  try {
    const r = await fetch('/api/rankings/changes?period=' + rcPeriod);
    rcData = await r.json();
  } catch(e) { rcData = {risen:[],fallen:[],new:[]}; }

  if (rcData.compare_date) {
    document.getElementById('rc-compare-label').textContent =
      `비교: ${rcData.compare_date} → ${rcData.current_date || '현재'}`;
  } else {
    document.getElementById('rc-compare-label').textContent =
      rcData.message || '이전 스냅샷 없음 — 매일 새로고침 시 자동 누적됩니다';
  }

  // 샵 필터 버튼 생성 (데이터에 있는 샵만)
  const allItems = [...(rcData.risen||[]), ...(rcData.fallen||[]), ...(rcData.new||[])];
  const shopCodes = [...new Set(allItems.map(c => c.country).filter(Boolean))];
  // 고정 순서: US, UK, JP, OY, YS, TT, QJ
  const ordered = ['US','UK','JP','OY','YS','TT','QJ'].filter(c => shopCodes.includes(c));
  const filter = document.getElementById('rc-country-filter');
  filter.innerHTML = ['', ...ordered].map((cc, i) => {
    const label = cc ? rcShopLabel(cc) : '전체';
    const active = (i === 0 && !rcCountry) || cc === rcCountry ? ' active' : '';
    return `<button class="rc-ctry-btn${active}" onclick="rcSetCountry('${cc}',this)">${label}</button>`;
  }).join('');

  rcRender();
}

function rcRender() {
  const body = document.getElementById('rc-body');
  if (!rcData) return;

  if (!rcData.compare_date) {
    body.innerHTML = `<div style="text-align:center;padding:50px;color:#94a3b8">
      <div style="font-size:2rem;margin-bottom:12px">📸</div>
      <div style="font-weight:700;margin-bottom:6px">아직 비교할 이전 데이터가 없어요</div>
      <div style="font-size:0.82rem">매일 새로고침하면 자동으로 스냅샷이 쌓여요.<br>내일부터 1일 변동, 7일 후엔 1주일 변동을 볼 수 있어요!</div>
    </div>`;
    return;
  }

  const risen  = rcData.risen  || [];
  const fallen = rcData.fallen || [];
  const newE   = rcData.new    || [];

  const card = (c) => {
    const m = SHOP_META[c.country] || {};
    const shopLabel = m.name || c.country;
    const shopIcon  = m.icon || c.flag || '';
    const thumb = c.thumb
      ? `<img class="rc-thumb" src="${vEsc(c.thumb)}" onerror="this.style.display='none'">`
      : `<div class="rc-thumb-ph">🛒</div>`;
    let badge = '';
    if (c.is_new) badge = `<span class="rc-badge-new">🆕 신규</span>`;
    else if (c.change > 0) badge = `<span class="rc-badge-up">▲ ${c.change}</span>`;
    else badge = `<span class="rc-badge-down">▼ ${Math.abs(c.change)}</span>`;
    const rank = c.is_new ? `현재 #${c.rank}` : `#${c.prev_rank} → #${c.rank}`;
    return `<div class="rc-card">
      ${thumb}
      <div style="flex:1;min-width:0">
        <div style="font-size:0.7rem;color:#94a3b8;margin-bottom:2px">${shopIcon} ${shopLabel} · ${vEsc(c.category||'')}</div>
        <div style="font-size:0.82rem;font-weight:700;color:#1e293b;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${vEsc(c.name||'')}">${vEsc((c.name||'').slice(0,55))}</div>
        <div style="font-size:0.72rem;color:#64748b;margin-top:2px">${rank}</div>
      </div>
      ${badge}
    </div>`;
  };

  // 특정 샵 선택 시: 해당 샵만 상승/하락/신규 3섹션
  if (rcCountry) {
    const f = c => c.country === rcCountry;
    const rr = risen.filter(f), ff = fallen.filter(f), nn = newE.filter(f);
    let html = '';
    if (rr.length) html += `<div class="rc-section-title"><span style="color:#16a34a">▲</span> 순위 상승 (${rr.length}개)</div><div class="rc-grid">${rr.map(card).join('')}</div>`;
    if (ff.length) html += `<div class="rc-section-title"><span style="color:#dc2626">▼</span> 순위 하락 (${ff.length}개)</div><div class="rc-grid">${ff.map(card).join('')}</div>`;
    if (nn.length) html += `<div class="rc-section-title"><span style="color:#7c3aed">🆕</span> 신규 진입 (${nn.length}개)</div><div class="rc-grid">${nn.map(card).join('')}</div>`;
    if (!html) html = '<div style="text-align:center;padding:40px;color:#aaa">변동 없음</div>';
    body.innerHTML = html;
    return;
  }

  // 전체 보기: 샵별 섹션으로 구조화
  const allItems = [...risen, ...fallen, ...newE];
  const shopCodes = ['US','UK','JP','OY','YS','TT','QJ'].filter(cc =>
    allItems.some(c => c.country === cc)
  );

  if (!shopCodes.length) {
    body.innerHTML = '<div style="text-align:center;padding:40px;color:#aaa">변동 없음</div>';
    return;
  }

  let html = '';
  for (const cc of shopCodes) {
    const m = SHOP_META[cc] || {};
    const rr = risen.filter(c => c.country === cc).slice(0, 5);
    const ff = fallen.filter(c => c.country === cc).slice(0, 5);
    const nn = newE.filter(c => c.country === cc).slice(0, 3);
    if (!rr.length && !ff.length && !nn.length) continue;
    const total = rr.length + ff.length + nn.length;
    html += `<div style="margin-bottom:24px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;padding-bottom:8px;border-bottom:2px solid #f1f5f9">
        <div style="font-weight:800;font-size:0.95rem;color:#1e293b">${m.icon||''} ${m.name||cc}</div>
        <div style="font-size:0.72rem;color:#94a3b8">
          ${rr.length ? `<span style="color:#16a34a">▲${rr.length}</span>` : ''}
          ${ff.length ? `<span style="color:#dc2626;margin-left:6px">▼${ff.length}</span>` : ''}
          ${nn.length ? `<span style="color:#7c3aed;margin-left:6px">🆕${nn.length}</span>` : ''}
          <button style="margin-left:10px;font-size:0.7rem;padding:2px 8px;border-radius:8px;border:1px solid #e2e8f0;background:white;cursor:pointer;color:#2d3561"
            onclick="rcSetCountry('${cc}',null)">전체 보기</button>
        </div>
      </div>
      <div class="rc-grid">${[...rr,...ff,...nn].map(card).join('')}</div>
    </div>`;
  }
  body.innerHTML = html || '<div style="text-align:center;padding:40px;color:#aaa">변동 없음</div>';
}

function brRenderChart(sorted) {
  const top = sorted.slice(0, 15);
  const labels = top.map(d => d.brand);

  const metricKey = trBrandsSort === 'count' ? 'count'
                  : trBrandsSort === 'trend'  ? 'recent_count'
                  : 'total_views';
  const values = top.map(d => d[metricKey]);

  const colors = top.map(d => {
    const ratio = d.recent_count / Math.max(d.count - d.recent_count, 1);
    if (ratio >= 2) return 'rgba(239,68,68,0.75)';
    if (ratio >= 1) return 'rgba(217,119,6,0.75)';
    return 'rgba(79,70,229,0.75)';
  });

  const axisLabel = metricKey === 'total_views' ? '총 뷰'
                  : metricKey === 'count'        ? '영상 수'
                  : '최근 영상 수';

  if (brChartInstance) { brChartInstance.destroy(); brChartInstance = null; }

  const canvas = document.getElementById('br-chart-canvas');
  canvas.height = Math.max(320, top.length * 36);

  brChartInstance = new Chart(canvas, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: axisLabel,
        data: values,
        backgroundColor: colors,
        borderRadius: 6,
        borderSkipped: false,
      }]
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => {
              const v = ctx.raw;
              if (metricKey === 'total_views') return ' ' + trFmt(v) + ' 뷰';
              return ' ' + v + '개';
            }
          }
        }
      },
      scales: {
        x: {
          ticks: {
            callback: v => metricKey === 'total_views' ? trFmt(v) : v,
            font: { size: 11 }
          },
          grid: { color: 'rgba(0,0,0,0.05)' }
        },
        y: {
          ticks: { font: { size: 12, weight: '700' } },
          grid: { display: false }
        }
      }
    }
  });
}

/* ── 브랜드 버즈 ──────────────────────────────────────── */
async function trLoadBrands() {
  const grid  = document.getElementById('tr-brands-grid');
  const empty = document.getElementById('tr-brands-empty');
  grid.innerHTML = '<div style="padding:40px;text-align:center;color:#aaa;font-size:0.85rem">브랜드 분석 중...</div>';
  empty.style.display = 'none';
  try {
    const r = await fetch('/api/trends/brands');
    trBrandsData = await r.json();
  } catch(e) { trBrandsData = []; }
  brRenderBrands();
}

function brSetSort(key, btn) {
  trBrandsSort = key;
  document.querySelectorAll('.br-sort-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  brRenderBrands();
}

function brSetView(view, btn) {
  trBrandsView = view;
  document.querySelectorAll('.br-view-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  brRenderBrands();
}

function brRenderBrands() {
  const grid  = document.getElementById('tr-brands-grid');
  const chartBox = document.getElementById('tr-brands-chart');
  const empty = document.getElementById('tr-brands-empty');

  if (!trBrandsData.length) {
    grid.style.display = 'none';
    chartBox.style.display = 'none';
    empty.style.display = '';
    return;
  }
  empty.style.display = 'none';

  const sorted = [...trBrandsData].sort((a, b) => {
    if (trBrandsSort === 'count') return b.count - a.count;
    if (trBrandsSort === 'trend') {
      const ts = d => d.recent_count / Math.max(d.count - d.recent_count, 1);
      return ts(b) - ts(a);
    }
    return b.total_views - a.total_views;
  });

  if (trBrandsView === 'chart') {
    grid.style.display = 'none';
    chartBox.style.display = '';
    brRenderChart(sorted);
    return;
  }
  chartBox.style.display = 'none';
  grid.style.display = '';

  const BRAND_COLORS = [
    '#e91e63','#9c27b0','#3f51b5','#2196f3','#00bcd4',
    '#009688','#4caf50','#ff9800','#ff5722','#795548',
  ];

  grid.innerHTML = sorted.map((d, i) => {
    const ratio = d.recent_count / Math.max(d.count - d.recent_count, 1);
    let trendCls, trendLabel;
    if (ratio >= 2)      { trendCls = 'hot';    trendLabel = '🔥 급상승'; }
    else if (ratio >= 1) { trendCls = 'up';     trendLabel = '↑ 상승중'; }
    else                 { trendCls = 'stable'; trendLabel = '→ 안정적'; }

    const color = BRAND_COLORS[i % BRAND_COLORS.length];
    const initials = d.brand.replace(/[^a-z0-9]/gi,'').slice(0,2).toUpperCase();
    const tv = d.top_video || {};
    const caption = (tv.caption || '').replace(/https?:\/\/\S+/g,'').trim().slice(0,100);

    return `<div class="br-card" onclick="brShowVideos('${d.brand.replace(/'/g,"\\'")}')"
      style="cursor:pointer" title="${vEsc(d.brand)} 영상 보기">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <div style="display:flex;align-items:center;gap:10px">
          <div style="width:38px;height:38px;border-radius:50%;background:${color};color:white;
               display:flex;align-items:center;justify-content:center;font-weight:800;font-size:0.85rem">
            ${initials}
          </div>
          <div>
            <div style="font-weight:700;font-size:0.92rem;color:#1e293b">${vEsc(d.brand)}</div>
            <div style="font-size:0.7rem;color:#94a3b8">#${i+1} · ${d.recent_count}개 최근영상</div>
          </div>
        </div>
        <span class="br-trend ${trendCls}">${trendLabel}</span>
      </div>

      <div class="br-stats">
        <div class="br-stat"><div class="sv">${trFmt(d.total_views)}</div><div class="sl">총 뷰</div></div>
        <div class="br-stat"><div class="sv">${d.count}</div><div class="sl">영상 수</div></div>
        <div class="br-stat"><div class="sv">${d.recent_count}</div><div class="sl">최근 7일</div></div>
      </div>

      ${tv.cover ? `<a href="${vEsc(tv.url||'#')}" target="_blank">
        <img class="br-thumb" src="${vEsc(tv.cover)}" onerror="this.style.display='none'">
      </a>` : ''}
      ${caption ? `<div class="br-vcap">${vEsc(caption)}</div>` : ''}

      <div class="br-footer">
        ${tv.url ? `<a class="cr-link" href="${vEsc(tv.url)}" target="_blank">TikTok에서 보기 →</a>` : '<span></span>'}
        ${tv.creator ? `<span style="font-size:0.72rem;color:#94a3b8">@${vEsc(tv.creator)}</span>` : ''}
      </div>
    </div>`;
  }).join('');
}

/* ── 브랜드 영상 모달 ──────────────────────────────────── */
async function brShowVideos(brand) {
  const modal = document.getElementById('br-modal');
  const title = document.getElementById('br-modal-title');
  const body  = document.getElementById('br-modal-body');
  title.textContent = '🏷️ ' + brand + ' 언급 영상';
  body.innerHTML = '<div style="text-align:center;padding:40px;color:#aaa">불러오는 중...</div>';
  modal.style.display = 'flex';
  document.body.style.overflow = 'hidden';

  try {
    const r = await fetch('/api/trends/brands/videos?brand=' + encodeURIComponent(brand));
    const videos = await r.json();
    if (!videos.length) {
      body.innerHTML = '<div style="text-align:center;padding:40px;color:#aaa">영상 없음</div>';
      return;
    }
    body.innerHTML = videos.map(v => {
      const cap = (v.caption || '').replace(/https?:\/\/\S+/g,'').trim().slice(0,160);
      return `<div style="display:flex;gap:14px;padding:16px 0;border-bottom:1px solid #f1f5f9">
        ${v.cover ? `<a href="${vEsc(v.url||'#')}" target="_blank" style="flex-shrink:0">
          <img src="${vEsc(v.cover)}" style="width:90px;height:120px;object-fit:cover;border-radius:10px" onerror="this.style.display='none'">
        </a>` : ''}
        <div style="flex:1;min-width:0">
          <div style="font-size:0.72rem;color:#94a3b8;margin-bottom:4px">${vEsc(v.date)} · @${vEsc(v.creator||'')}</div>
          ${cap ? `<div style="font-size:0.83rem;color:#334155;line-height:1.5;margin-bottom:8px">${vEsc(cap)}</div>` : ''}
          <div style="display:flex;gap:14px;font-size:0.75rem;color:#64748b;flex-wrap:wrap">
            <span>👁 ${trFmt(v.views)}</span>
            <span>❤️ ${trFmt(v.likes)}</span>
            <span>💬 ${trFmt(v.comments)}</span>
            <span>🔖 ${trFmt(v.saves)}</span>
          </div>
          ${v.url ? `<a href="${vEsc(v.url)}" target="_blank"
            style="display:inline-block;margin-top:8px;font-size:0.75rem;color:#2d3561;font-weight:700;text-decoration:none">
            TikTok에서 보기 →</a>` : ''}
        </div>
      </div>`;
    }).join('');
  } catch(e) {
    body.innerHTML = '<div style="text-align:center;padding:40px;color:#aaa">로드 실패</div>';
  }
}

function brCloseModal() {
  document.getElementById('br-modal').style.display = 'none';
  document.body.style.overflow = '';
}

</script>

<!-- 브랜드 영상 모달 -->
<div id="br-modal" style="display:none;position:fixed;inset:0;z-index:9999;
  background:rgba(0,0,0,0.55);align-items:center;justify-content:center;padding:16px"
  onclick="if(event.target===this)brCloseModal()">
  <div style="background:white;border-radius:18px;width:100%;max-width:560px;
    max-height:85vh;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,0.25)">
    <div style="display:flex;align-items:center;justify-content:space-between;
      padding:18px 20px;border-bottom:1px solid #f1f5f9;flex-shrink:0">
      <div id="br-modal-title" style="font-weight:800;font-size:1rem;color:#1e293b"></div>
      <button onclick="brCloseModal()"
        style="border:none;background:none;font-size:1.3rem;cursor:pointer;color:#94a3b8;line-height:1">✕</button>
    </div>
    <div id="br-modal-body" style="overflow-y:auto;padding:0 20px 20px"></div>
  </div>
</div>

</body>
</html>
"""

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    print(f"🛒 Amazon Beauty Rankings → http://localhost:{port}")
    app.run(debug=True, host="0.0.0.0", port=port)
