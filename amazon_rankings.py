#!/usr/bin/env python3
"""
Amazon Beauty Rankings Dashboard
- 나라별 아마존 뷰티/이너뷰티 베스트셀러 랭킹 대시보드
- Run: python3 amazon_rankings.py
"""

from flask import Flask, jsonify, render_template_string, request
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

def fetch_from_apify(refresh=False):
    all_items = []
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
            all_items.extend(items)
        except Exception as e:
            print(f"[Apify] {label} ({run_id}) failed: {e}")
    # YesStyle 추가
    ys_items = fetch_yesstyle()
    print(f"[YesStyle] fetched {len(ys_items)} items")
    all_items.extend(ys_items)
    # OliveYoung 추가
    oy_items = fetch_oliveyoung()
    print(f"[OliveYoung] fetched {len(oy_items)} items")
    all_items.extend(oy_items)
    # Qoo10 Japan 추가
    qj_items = fetch_qoo10()
    print(f"[Qoo10] fetched {len(qj_items)} items")
    all_items.extend(qj_items)
    # TikTok Shop 추가
    tt_items = fetch_tiktok()
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
    return cache

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

@app.route("/api/run", methods=["POST"])
def api_run():
    script = os.path.join(_SCRIPT_DIR, "kbeauty_daily.py")
    log    = open(os.path.join(_SCRIPT_DIR, "last_run.log"), "w")
    subprocess.Popen([sys.executable, script], stdout=log, stderr=subprocess.STDOUT)
    return jsonify({"ok": True})

@app.route("/api/x/dates")
def api_x_dates():
    files = sorted(glob.glob(os.path.join(_SCRIPT_DIR, "twitter_*.json")), reverse=True)
    return jsonify([os.path.basename(f).replace("twitter_","").replace(".json","") for f in files])

@app.route("/api/x/data/<date>")
def api_x_data_date(date):
    path = os.path.join(_SCRIPT_DIR, f"twitter_{date}.json")
    if not os.path.exists(path):
        return jsonify([])
    with open(path, encoding="utf-8") as f:
        return jsonify(json.load(f))

@app.route("/api/run/twitter", methods=["POST"])
def api_run_twitter():
    script = os.path.join(_SCRIPT_DIR, "twitter_scraper.py")
    log    = open(os.path.join(_SCRIPT_DIR, "last_twitter_run.log"), "w")
    subprocess.Popen([sys.executable, script], stdout=log, stderr=subprocess.STDOUT)
    return jsonify({"ok": True})

@app.route("/api/xhs/dates")
def api_xhs_dates():
    files = sorted(glob.glob(os.path.join(_SCRIPT_DIR, "xhs_data_*.json")), reverse=True)
    return jsonify([os.path.basename(f).replace("xhs_data_","").replace(".json","") for f in files])

@app.route("/api/xhs/data/<date>")
def api_xhs_data_date(date):
    path = os.path.join(_SCRIPT_DIR, f"xhs_data_{date}.json")
    if not os.path.exists(path):
        return jsonify([])
    with open(path, encoding="utf-8") as f:
        return jsonify(json.load(f))

@app.route("/api/run/xhs", methods=["POST"])
def api_run_xhs():
    script = os.path.join(_SCRIPT_DIR, "kbeauty_xhs_scraper.py")
    log    = open(os.path.join(_SCRIPT_DIR, "last_xhs_run.log"), "w")
    subprocess.Popen([sys.executable, script], stdout=log, stderr=subprocess.STDOUT)
    return jsonify({"ok": True})

@app.route("/")
def index():
    return render_template_string(HTML)

# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🛒 Beauty Product Rankings</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --pink:#e8637a;--pink-light:#fce8ec;--pink-mid:#f5b8c4;--gold:#f5a623;
  --bg:#fdf6f8;--surface:#fff;--border:#f0e0e5;--text:#1a1a1a;--muted:#888;
  --shadow:0 2px 12px rgba(232,99,122,.08);
}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans KR',sans-serif;
     background:var(--bg);color:var(--text);min-height:100vh}

/* header */
header{background:linear-gradient(135deg,#e8637a 0%,#c0445f 100%);color:#fff;
  padding:0 28px;height:62px;display:flex;align-items:center;
  justify-content:space-between;position:sticky;top:0;z-index:100;
  box-shadow:0 3px 16px rgba(192,68,95,.3)}
.h-left{display:flex;align-items:center;gap:12px}
.h-logo{font-size:1.5rem}
.h-title{font-size:1.1rem;font-weight:800}
.h-sub{font-size:.72rem;opacity:.82;margin-top:1px}
.h-right{display:flex;align-items:center;gap:10px}
.mode-switch{display:flex;background:rgba(255,255,255,.2);border-radius:10px;padding:3px;gap:3px}
.mode-btn{padding:7px 14px;border:none;border-radius:8px;cursor:pointer;font-weight:700;
  font-size:.82rem;transition:all .2s;background:transparent;color:rgba(255,255,255,.75)}
.mode-btn:hover{background:rgba(255,255,255,.15);color:#fff}
.mode-btn.active{background:#fff;color:var(--pink)}
.upd{font-size:.72rem;opacity:.75}
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
  font-size:.68rem;padding:1px 6px;border-radius:10px;margin-left:4px;font-weight:700}

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
.cat-cnt{background:var(--pink-light);color:var(--pink);font-size:.7rem;
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
  font-size:.72rem;font-weight:900;color:#fff;box-shadow:0 2px 6px rgba(0,0,0,.25)}
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
.sv{color:var(--gold);font-size:.75rem;letter-spacing:-1px}
.sn{font-size:.72rem;color:var(--muted)}
.crev{font-size:.7rem;color:var(--muted)}
.cprice{font-size:.88rem;font-weight:800;color:var(--pink);margin-top:auto;padding-top:6px}
.cprice.np{color:var(--muted);font-weight:400;font-size:.75rem}
.casin{font-size:.65rem;color:#bbb;margin-top:2px}
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
.dash-rnk{position:absolute;top:8px;left:8px;font-size:.7rem;font-weight:900;
  color:#fff;width:24px;height:24px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;
  box-shadow:0 2px 6px rgba(0,0,0,.25);z-index:2}
.dash-info{padding:8px 10px 10px;flex:1}
.dash-name{font-size:.75rem;font-weight:600;line-height:1.35;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;color:var(--text)}
.dash-price{font-size:.74rem;font-weight:800;color:var(--pink);margin-top:4px}
@media(max-width:700px){.dash{flex-wrap:nowrap}.dash-col{min-width:160px}}

/* Combined Dashboard layout */
.db-main{display:flex;gap:16px;padding:16px 20px;height:calc(100vh - 56px);overflow:hidden;box-sizing:border-box}
.db-left{flex:1.4;display:flex;flex-direction:column;overflow:hidden;min-width:0}
.db-right{width:370px;flex-shrink:0;display:flex;flex-direction:column;gap:12px;overflow-y:auto;padding-right:2px}
.db-section-hdr{font-size:.88rem;font-weight:800;color:var(--pink);margin-bottom:10px}
.dash-compact{display:flex;gap:10px;flex:1;overflow-x:auto;overflow-y:auto;align-items:flex-start;padding-bottom:4px}
.dash-mini-col{flex:1;min-width:160px;max-width:210px;background:var(--surface);border-radius:12px;border:1px solid var(--border);overflow:hidden;box-shadow:var(--shadow);display:flex;flex-direction:column;flex-shrink:0;cursor:pointer;transition:transform .18s,box-shadow .18s,border-color .18s}
.dash-mini-col:hover{transform:translateY(-3px);box-shadow:0 8px 24px rgba(211,61,90,.18);border-color:var(--pink)}
.dash-mini-hdr{padding:8px 11px;display:flex;align-items:center;gap:6px;background:linear-gradient(135deg,var(--pink-light) 0%,#fff 100%);border-bottom:2px solid var(--pink-mid);font-weight:800;font-size:.74rem;color:var(--pink);white-space:nowrap}
.dash-mini-item{display:flex;flex-direction:column;text-decoration:none;color:inherit;border-bottom:1px solid var(--border);transition:background .15s;overflow:hidden}
.dash-mini-item:hover{background:var(--pink-light)}
.dash-mini-item:last-child{border-bottom:none}
.dash-mini-thumb{width:100%;padding-top:78%;position:relative;background:#f9f3f5;overflow:hidden}
.dash-mini-thumb img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;padding:10px;transition:transform .3s}
.dash-mini-item:hover .dash-mini-thumb img{transform:scale(1.04)}
.dash-mini-ph{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:2rem;color:var(--pink-mid)}
.dash-mini-rank{position:absolute;top:7px;left:7px;font-size:.68rem;font-weight:900;color:#fff;width:22px;height:22px;border-radius:50%;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 6px rgba(0,0,0,.25);z-index:2}
.dash-mini-name{padding:8px 10px 10px;font-size:.76rem;font-weight:700;line-height:1.4;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;color:var(--text)}
.db-chart-card{background:var(--surface);border-radius:14px;border:1px solid var(--border);box-shadow:var(--shadow);padding:14px 16px;flex-shrink:0;cursor:pointer;transition:transform .18s,box-shadow .18s,border-color .18s}
.db-chart-card:hover{transform:translateY(-3px);box-shadow:0 8px 24px rgba(211,61,90,.18);border-color:var(--pink)}
.db-card-title{font-size:.82rem;font-weight:800;color:var(--pink);margin-bottom:10px}
.db-pie-body{display:flex;gap:12px;align-items:flex-start}
.db-pie-legend{display:flex;flex-direction:column;gap:2px;flex:1;min-width:0;overflow-y:auto;max-height:190px}
.db-legend-grp{font-size:.68rem;font-weight:800;color:var(--text);margin-top:6px;margin-bottom:1px;border-left:2px solid var(--pink);padding-left:4px}
.db-legend-grp:first-child{margin-top:0}
.db-legend-item{display:flex;align-items:center;gap:5px;font-size:.69rem;padding:2px 3px;border-radius:4px}
.db-legend-dot{width:9px;height:9px;border-radius:2px;flex-shrink:0}
.db-legend-label{flex:1;font-weight:600;color:var(--text);overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.db-legend-pct{font-weight:800;color:var(--pink);font-size:.68rem;flex-shrink:0}
.db-ing-bars{display:flex;flex-direction:column;gap:5px}
.db-ing-row{display:flex;align-items:center;gap:8px}
.db-ing-label{width:98px;text-align:right;font-size:.68rem;font-weight:700;color:var(--text);flex-shrink:0;line-height:1.2}
.db-ing-track{flex:1;height:20px;background:var(--pink-light);border-radius:5px;overflow:hidden}
.db-ing-fill{height:100%;border-radius:5px;display:flex;align-items:center;padding:0 7px;transition:width .55s cubic-bezier(.4,0,.2,1);width:0%}
.db-ing-cnt{font-size:.63rem;font-weight:800;color:#fff;white-space:nowrap}
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
.legend-cnt{color:var(--muted);font-size:.72rem}
.legend-group-hdr{font-size:.76rem;font-weight:800;color:var(--text);
  margin-top:12px;margin-bottom:2px;padding:3px 6px;
  border-left:3px solid var(--pink);letter-spacing:.3px}
.legend-group-hdr:first-child{margin-top:0}
.legend-main-pct{color:var(--pink);font-size:.75rem;margin-left:4px}

/* Ingredient trend chart */
.ing-wrap{padding:24px;max-width:800px;margin:0 auto}
.ing-title{font-size:1.05rem;font-weight:800;color:var(--pink);margin-bottom:4px}
.ing-sub{font-size:.78rem;color:var(--muted);margin-bottom:18px}
.ing-group-legend{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:20px}
.ing-grp{display:flex;align-items:center;gap:5px;font-size:.76rem;font-weight:700;color:var(--text)}
.ing-grp-dot{width:10px;height:10px;border-radius:3px;flex-shrink:0}
.ing-bars{display:flex;flex-direction:column;gap:7px}
.ing-row{display:flex;align-items:center;gap:10px}
.ing-label{width:130px;text-align:right;font-size:.8rem;font-weight:700;color:var(--text);flex-shrink:0;line-height:1.2}
.ing-grp-tag{font-size:.62rem;font-weight:600;color:var(--muted);display:block}
.ing-track{flex:1;height:28px;background:var(--pink-light);border-radius:7px;overflow:hidden;position:relative}
.ing-fill{height:100%;border-radius:7px;display:flex;align-items:center;padding:0 10px;transition:width .55s cubic-bezier(.4,0,.2,1);width:0%}
.ing-fill-cnt{font-size:.72rem;font-weight:800;color:#fff;white-space:nowrap}
.ing-meta{width:72px;font-size:.74rem;color:var(--muted);flex-shrink:0;text-align:left}

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
#video-hub .plat-btn.xhs-btn.active{color:#ff2442}
#video-hub .vh-subheader.xhs{background:linear-gradient(135deg,#ff6b7a 0%,#ff2442 100%)}
#video-hub .vh-subheader.xhs .run-btn{color:#ff2442}
#video-hub .vh-subheader.xhs .run-btn:hover{background:#fff0f2}
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
    <span style="font-size:.75rem;font-weight:700;color:var(--muted);margin-right:2px">기간:</span>
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
      <button class="plat-btn twitter-btn" id="btn-twitter" onclick="vSwitchPlatform('twitter')">𝕏 Twitter</button>
      <button class="plat-btn xhs-btn" id="btn-xhs" onclick="vSwitchPlatform('xhs')">📕 小红书</button>
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

<div id="v-xhs-hub" style="display:none">
  <div class="v-layout">
    <aside class="v-sidebar" style="border-right-color:#ffd0d5">
      <div class="filter-section">
        <span class="filter-label" style="color:#ff2442">카테고리</span>
        <div class="date-btns" style="flex-direction:column;gap:5px">
          <button class="date-btn active" data-cat="All" onclick="setXhsCat(this)">🌸 전체 K-Beauty</button>
          <button class="date-btn" data-cat="스킨케어" onclick="setXhsCat(this)">💧 스킨케어</button>
          <button class="date-btn" data-cat="메이크업" onclick="setXhsCat(this)">💄 메이크업</button>
          <button class="date-btn" data-cat="헤어케어" onclick="setXhsCat(this)">💆 헤어케어</button>
          <button class="date-btn" data-cat="종합" onclick="setXhsCat(this)">✨ 종합/바이럴</button>
        </div>
      </div>
      <hr class="divider">
      <div class="filter-section">
        <span class="filter-label" style="color:#ff2442">검색</span>
        <div class="filter-group">
          <input type="text" id="xhsf-creator" placeholder="크리에이터 이름..." oninput="vApplyXhsFilters()">
          <input type="text" id="xhsf-keyword" placeholder="제목 키워드..." oninput="vApplyXhsFilters()">
        </div>
      </div>
      <hr class="divider">
      <div class="filter-section">
        <span class="filter-label" style="color:#ff2442">콘텐츠 타입</span>
        <select id="xhsf-type" onchange="vApplyXhsFilters()">
          <option value="">🌐 전체</option>
          <option value="video">🎬 영상</option>
          <option value="image">📷 이미지 노트</option>
        </select>
      </div>
      <hr class="divider">
      <div class="filter-section">
        <span class="filter-label" style="color:#ff2442">정렬</span>
        <select id="xhs-sort-select" onchange="vRenderXhsPosts()">
          <option value="likes">❤️ 좋아요순</option>
          <option value="comments">💬 댓글순</option>
        </select>
      </div>
      <button class="clear-btn" onclick="vClearXhsFilters()">필터 초기화</button>
    </aside>
    <main class="v-main">
      <div class="stats-bar" id="xhs-stats-bar">
        <div class="stat-chip"><div class="n" id="xhss-posts" style="color:#ff2442">—</div><div class="l">Posts</div></div>
        <div class="stat-chip"><div class="n" id="xhss-likes" style="color:#ff2442">—</div><div class="l">Total Likes</div></div>
        <div class="stat-chip"><div class="n" id="xhss-creators" style="color:#ff2442">—</div><div class="l">Creators</div></div>
        <div class="stat-chip"><div class="n" id="xhss-videos" style="color:#ff2442">—</div><div class="l">Videos</div></div>
      </div>
      <div class="tabs" id="xhs-tabs">
        <button class="tab active" style="--ac:#ff2442" onclick="vSwitchXhsTab('posts', this)">🔥 Viral Posts</button>
        <button class="tab" style="--ac:#ff2442" onclick="vSwitchXhsTab('creators', this)">👤 Top Creators</button>
      </div>
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px;background:white;padding:10px 16px;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,0.05)">
        <span style="font-size:0.8rem;color:#888">데이터셋:</span>
        <select id="xhs-date-pick" onchange="vApplyXhsFilters()" style="width:auto;flex:1;padding:6px 10px;font-size:0.82rem">
          <option value="">— 전체 데이터셋 —</option>
        </select>
        <div id="xhs-result-count" style="margin-left:auto;font-size:0.8rem;color:#aaa"></div>
      </div>
      <div id="xhs-panel-posts"></div>
      <div id="xhs-panel-creators" style="display:none"></div>
    </main>
  </div>
</div>

<div id="v-toast"></div>
</div><!-- /video-hub -->

<script>
let all = [], country = 'DB', ysSub = 'All Beauty', oySub = 'All', qjSub = 'All', ttSub = 'All', ttPeriod = '30d';

// country order: ALL first, then US, UK, JP, then others
const ORDER = ['DB','CH','IG','US','UK','JP','YS','OY','QJ','TT','DE','FR','CA','AU','IT','ES'];
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

function renderDashboard() {
  const platforms = [
    {code:'US', label:'Amazon US'},
    {code:'UK', label:'Amazon UK'},
    {code:'JP', label:'Amazon JP'},
    {code:'YS', label:'YesStyle'},
    {code:'OY', label:'OliveYoung', sub:'Top orders'},
    {code:'QJ', label:'Qoo10 JP'},
    {code:'TT', label:'TikTok Shop', sortKey:'_sale_7d_num'},
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
      const imgEl=th?`<img src="${th}" alt="" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`:'' ;
      const phEl=`<div class="dash-mini-ph" style="${th?'display:none':''}">🧴</div>`;
      prodHtml+=`<a class="dash-mini-item" href="${item.url||'#'}" target="_blank" rel="noopener" onclick="event.stopPropagation()">
        <div class="dash-mini-thumb">
          ${imgEl}${phEl}
          <div class="dash-mini-rank ${rc}">${r}</div>
        </div>
        <div class="dash-mini-name">${item.name||'No Name'}</div>
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
        <div class="db-card-title">📈 카테고리 분포 (전체) <span style="font-size:.7rem;font-weight:400;color:#bbb;margin-left:4px">↗ 클릭</span></div>
        <div class="db-pie-body">
          <canvas id="dbPieChart" width="185" height="185" style="flex-shrink:0"></canvas>
          <div id="dbPieLegend" class="db-pie-legend"></div>
        </div>
      </div>
      <div class="db-chart-card" onclick="goTab('IG')" title="성분 트렌드로 이동">
        <div class="db-card-title">🧪 트렌딩 성분 TOP 10 (전체) <span style="font-size:.7rem;font-weight:400;color:#bbb;margin-left:4px">↗ 클릭</span></div>
        <div class="db-ing-bars" id="dbIngBars"></div>
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
    setTimeout(()=>{ drawDbPieChart(); drawDbIngChart(); }, 20);
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
            <div class="cprice${price?'':' np'}">${price||'가격 미정'}</div>
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
    const mainDisplay = main === '기타' ? '기타 <span style="color:#aaa;font-weight:400;font-size:.65rem">(향수, 바디, 구강)</span>' : main;
    html+=`<div class="db-legend-grp">${mainDisplay} <span style="color:var(--pink);font-size:.67rem">${Math.round(mainTotal/total*100)}%</span></div>`;
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
  currentMode = mode;
  document.getElementById('product-hub').style.display = mode === 'product' ? '' : 'none';
  document.getElementById('video-hub').style.display = mode === 'video' ? '' : 'none';
  document.getElementById('mode-product').classList.toggle('active', mode === 'product');
  document.getElementById('mode-video').classList.toggle('active', mode === 'video');
  const title = document.getElementById('mainTitle');
  const sub = document.getElementById('mainSub');
  const logo = document.getElementById('mainLogo');
  const refreshBtn = document.getElementById('refreshBtn');
  if (mode === 'product') {
    title.textContent = 'Beauty Product Rankings';
    sub.textContent = '나라별 뷰티 베스트셀러';
    logo.textContent = '🛒';
    refreshBtn.style.display = '';
    document.getElementById('updLbl').style.display = '';
  } else {
    title.textContent = 'K-Beauty Research Hub';
    sub.textContent = 'TikTok & X 바이럴 컨텐츠 분석';
    logo.textContent = '🔬';
    refreshBtn.style.display = 'none';
    document.getElementById('updLbl').style.display = 'none';
    if (!videoHubInitialized) { initVideoHub(); videoHubInitialized = true; }
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
let vxhsAllDates  = [];
let vxhsAllData   = [];
let vxhsFiltered  = [];
let vxhsDateRange = 'all';
let vxhsActiveTab = 'posts';
let vxhsInitialized = false;
let xhsCat    = 'All';
let xhsPeriod = 'all';

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

// ── Scrape trigger ──
async function vTriggerScrape() {
  const btn = document.getElementById('v-run-btn');
  btn.disabled = true; btn.textContent = 'Running...';
  const endpoint = vPlatform === 'twitter' ? '/api/run/twitter' : vPlatform === 'xhs' ? '/api/run/xhs' : '/api/run';
  vToast('Scrape started — takes ~5-10 min. Page will refresh when done.', 10000);
  const r = await fetch(endpoint, { method: 'POST' });
  await r.json();
  const poll = setInterval(async () => {
    if (vPlatform === 'tiktok') {
      const dr = await fetch('/api/dates');
      const dates = await dr.json();
      if (dates[0] !== vAllDates[0]) {
        clearInterval(poll);
        btn.disabled = false; btn.textContent = 'Run New Scrape';
        vAllDates = dates; await vLoadAllData(); vToast('New TikTok data loaded!', 4000);
      }
    } else if (vPlatform === 'xhs') {
      const dr = await fetch('/api/xhs/dates');
      const dates = await dr.json();
      if (dates[0] !== vxhsAllDates[0]) {
        clearInterval(poll);
        btn.disabled = false; btn.textContent = 'Run New Scrape';
        vxhsAllDates = dates; await vLoadXhsAllData(); vToast('New XHS data loaded!', 4000);
      }
    } else {
      const dr = await fetch('/api/x/dates');
      const dates = await dr.json();
      if (dates[0] !== vxAllDates[0]) {
        clearInterval(poll);
        btn.disabled = false; btn.textContent = 'Run New Scrape';
        vxAllDates = dates; await vLoadXAllData(); vToast('New Twitter data loaded!', 4000);
      }
    }
  }, 30000);
}

// ── Platform switcher ──
function vSwitchPlatform(p) {
  vPlatform = p;
  const hdr = document.getElementById('vh-subheader');
  const tiktokHub = document.getElementById('v-tiktok-layout');
  const twitterHub = document.getElementById('v-twitter-hub');
  const xhsHub = document.getElementById('v-xhs-hub');
  document.getElementById('btn-tiktok').classList.toggle('active', p === 'tiktok');
  document.getElementById('btn-twitter').classList.toggle('active', p === 'twitter');
  document.getElementById('btn-xhs').classList.toggle('active', p === 'xhs');
  tiktokHub.style.display = 'none';
  twitterHub.style.display = 'none';
  xhsHub.style.display = 'none';
  if (p === 'tiktok') {
    hdr.className = 'vh-subheader tiktok';
    document.getElementById('vh-hub-title').textContent = 'K-Beauty TikTok Hub';
    tiktokHub.style.display = 'flex';
  } else if (p === 'twitter') {
    hdr.className = 'vh-subheader twitter';
    document.getElementById('vh-hub-title').textContent = 'K-Beauty X (Twitter) Hub';
    twitterHub.style.display = 'block';
    if (!vxAllDates.length) vInitTwitter();
  } else {
    hdr.className = 'vh-subheader xhs';
    document.getElementById('vh-hub-title').textContent = 'K-Beauty 小红书 Hub';
    document.getElementById('vh-last-updated').textContent = 'Loading...';
    xhsHub.style.display = 'block';
    if (!vxhsInitialized) { vInitXhs(); vxhsInitialized = true; }
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
// XHS (小红书) HUB
// ══════════════════════════════════════════════════════════════════════════

async function vInitXhs() {
  const r = await fetch('/api/xhs/dates');
  vxhsAllDates = await r.json();
  const sel = document.getElementById('xhs-date-pick');
  // keep "전체 데이터셋" option, append dates
  sel.innerHTML = '<option value="">— 전체 데이터셋 —</option>';
  vxhsAllDates.forEach(d => {
    const o = document.createElement('option');
    o.value = d; o.textContent = d; sel.appendChild(o);
  });
  if (vxhsAllDates.length) {
    await vLoadXhsAllData();
  } else {
    document.getElementById('vh-last-updated').textContent = 'No XHS data yet — click Run New Scrape';
    document.getElementById('xhs-panel-posts').innerHTML =
      '<div class="empty"><div class="icon">📕</div><p>No 小红书 data yet.</p><p style="margin-top:8px">Click <b>Run New Scrape</b> to pull K-beauty posts.</p></div>';
  }
}

async function vLoadXhsAllData() {
  vxhsAllData = [];
  const seen = new Set();
  for (const date of vxhsAllDates) {
    const r = await fetch('/api/xhs/data/' + date);
    const items = await r.json();
    items.forEach(p => {
      if (!seen.has(p.id)) { seen.add(p.id); vxhsAllData.push({...p, _dataset: date}); }
    });
  }
  document.getElementById('vh-last-updated').textContent =
    vxhsAllData.length + ' posts across ' + vxhsAllDates.length + ' dataset(s) · Latest: ' + (vxhsAllDates[0]||'—');
  vApplyXhsFilters();
}

function setXhsCat(btn) {
  xhsCat = btn.dataset.cat;
  document.querySelectorAll('#v-xhs-hub [data-cat]').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  vApplyXhsFilters();
}

function vApplyXhsFilters() {
  const creator = document.getElementById('xhsf-creator').value.trim().toLowerCase();
  const keyword = document.getElementById('xhsf-keyword').value.trim().toLowerCase();
  const type    = document.getElementById('xhsf-type').value;
  const dataset = document.getElementById('xhs-date-pick').value;
  vxhsFiltered = vxhsAllData.filter(p => {
    if (dataset && p._dataset !== dataset) return false;
    if (xhsCat !== 'All' && p.category !== xhsCat) return false;
    if (type && p.type !== type) return false;
    if (creator && !(p.creator?.username||'').toLowerCase().includes(creator)) return false;
    if (keyword && !(p.title||'').toLowerCase().includes(keyword)) return false;
    return true;
  });
  vUpdateXhsStats();
  vRenderXhsActiveTab();
}

function vClearXhsFilters() {
  ['xhsf-creator','xhsf-keyword'].forEach(id => document.getElementById(id).value = '');
  document.getElementById('xhsf-type').value = '';
  document.getElementById('xhs-date-pick').value = '';
  xhsCat = 'All';
  document.querySelectorAll('#v-xhs-hub [data-cat]').forEach(b => b.classList.remove('active'));
  const allBtn = document.querySelector('#v-xhs-hub [data-cat="All"]');
  if (allBtn) allBtn.classList.add('active');
  vApplyXhsFilters();
}

function vUpdateXhsStats() {
  const totalLikes = vxhsFiltered.reduce((s,p) => s + (parseInt(p.stats?.likes)||0), 0);
  const creators   = new Set(vxhsFiltered.map(p => p.creator?.userId).filter(Boolean)).size;
  const videos     = vxhsFiltered.filter(p => p.type === 'video').length;
  document.getElementById('xhss-posts').textContent    = vFmt(vxhsFiltered.length);
  document.getElementById('xhss-likes').textContent    = vFmt(totalLikes);
  document.getElementById('xhss-creators').textContent = creators;
  document.getElementById('xhss-videos').textContent   = videos;
  document.getElementById('xhs-result-count').textContent = vxhsFiltered.length + ' posts';
}

function vSwitchXhsTab(tab, btn) {
  vxhsActiveTab = tab;
  document.querySelectorAll('#xhs-tabs .tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  vRenderXhsActiveTab();
}

function vRenderXhsActiveTab() {
  document.getElementById('xhs-panel-posts').style.display    = vxhsActiveTab === 'posts'    ? 'block' : 'none';
  document.getElementById('xhs-panel-creators').style.display = vxhsActiveTab === 'creators' ? 'block' : 'none';
  if (vxhsActiveTab === 'posts')    vRenderXhsPosts();
  if (vxhsActiveTab === 'creators') vRenderXhsCreators();
}

function vRenderXhsPosts() {
  const el = document.getElementById('xhs-panel-posts');
  if (!vxhsFiltered.length) {
    el.innerHTML = '<div class="empty"><div class="icon">📕</div><p>필터에 맞는 포스트가 없어요.</p></div>';
    return;
  }
  const sortBy = document.getElementById('xhs-sort-select').value;
  const sorted = [...vxhsFiltered].sort((a, b) => {
    if (sortBy === 'comments') return (parseInt(b.stats?.comments)||0) - (parseInt(a.stats?.comments)||0);
    return (parseInt(b.stats?.likes)||0) - (parseInt(a.stats?.likes)||0);
  });
  const catColor = {'스킨케어':'#4caf9e','메이크업':'#e91e8c','헤어케어':'#9c5bb5','종합':'#ff7043'};
  el.innerHTML = '<div class="video-grid">' + sorted.slice(0, 60).map((p, i) => {
    const likes    = vFmt(parseInt(p.stats?.likes)||0);
    const comments = vFmt(parseInt(p.stats?.comments)||0);
    const cover    = p.cover || '';
    const creator  = p.creator?.username || '—';
    const avatar   = p.creator?.avatar || '';
    const typeIcon = p.type === 'video' ? '🎬' : '📷';
    const catBadge = p.category
      ? '<span style="background:' + (catColor[p.category]||'#999') + ';color:white;font-size:0.65rem;padding:2px 7px;border-radius:10px;font-weight:700">' + vEsc(p.category) + '</span>'
      : '';
    const kwBadge = p.source_tag
      ? '<span style="background:#f3e5ff;color:#9c5bb5;font-size:0.65rem;padding:2px 7px;border-radius:10px">#' + vEsc(p.source_tag) + '</span>'
      : '';
    return '<div class="video-card">' +
      (cover
        ? '<a href="' + vEsc(p.url||'#') + '" target="_blank" class="card-thumb"><img src="' + vEsc(cover) + '" loading="lazy" onerror="this.parentElement.style.display=\'none\'"><div class="thumb-overlay"></div><div class="thumb-views" style="background:rgba(255,36,66,.75)">' + typeIcon + ' ' + likes + ' ❤️</div></a>'
        : '') +
      '<div class="card-body">' +
        '<div class="card-rank">#' + (i+1) + ' · ' + vEsc(p.source_tag||'xhs') + '</div>' +
        '<div class="card-creator">' +
          (avatar
            ? '<img class="creator-avatar" src="' + vEsc(avatar) + '" onerror="this.style.display=\'none\'" loading="lazy">'
            : '<div class="creator-avatar" style="display:flex;align-items:center;justify-content:center;font-size:14px;color:#ddd">👤</div>') +
          '<div class="creator-info"><span style="font-weight:700">' + vEsc(creator) + '</span></div></div>' +
        '<div class="card-caption">' + vEsc((p.title||'').slice(0, 100)) + '</div>' +
        '<div class="metrics-grid">' +
          '<div class="metric"><div class="mv" style="color:#ff2442">' + likes + '</div><div class="ml">좋아요</div></div>' +
          '<div class="metric"><div class="mv">' + comments + '</div><div class="ml">댓글</div></div>' +
        '</div>' +
        '<div class="tags-row" style="gap:4px;margin-top:6px">' + catBadge + ' ' + kwBadge + '</div>' +
        '<div class="card-footer"><a class="watch-btn" href="' + vEsc(p.url||'#') + '" target="_blank" style="background:#ff2442">小红书에서 보기 →</a></div>' +
      '</div></div>';
  }).join('') + '</div>';
}

function vRenderXhsCreators() {
  const el = document.getElementById('xhs-panel-creators');
  const stats = {};
  vxhsFiltered.forEach(p => {
    const uid = p.creator?.userId || p.creator?.username || '?';
    if (!stats[uid]) stats[uid] = { name: p.creator?.username || p.creator?.nickName || uid, avatar: p.creator?.avatar||'', posts:0, likes:0 };
    stats[uid].posts++;
    stats[uid].likes += parseInt(p.stats?.likes)||0;
  });
  const sorted = Object.values(stats).sort((a,b) => b.likes - a.likes).slice(0,30);
  if (!sorted.length) { el.innerHTML = '<div class="empty"><div class="icon">👤</div><p>No creator data.</p></div>'; return; }
  el.innerHTML = `<div class="creators-table-wrap"><table>
    <thead><tr><th>#</th><th>Creator</th><th>Posts</th><th>Total Likes</th></tr></thead>
    <tbody>${sorted.map((c,i) => `<tr>
      <td style="color:#ff2442;font-weight:700">${i+1}</td>
      <td><div style="display:flex;align-items:center;gap:8px">
        ${c.avatar?`<img src="${vEsc(c.avatar)}" style="width:28px;height:28px;border-radius:50%;object-fit:cover">`:'<div style="width:28px;height:28px;border-radius:50%;background:#ffd0d5;display:flex;align-items:center;justify-content:center;font-size:0.8rem">👤</div>'}
        <span style="font-weight:600">${vEsc(c.name)}</span>
      </div></td>
      <td>${c.posts}</td>
      <td style="color:#ff2442;font-weight:700">❤️ ${vFmt(c.likes)}</td>
    </tr>`).join('')}</tbody>
  </table></div>`;
}
</script>
</body>
</html>
"""

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    print(f"🛒 Amazon Beauty Rankings → http://localhost:{port}")
    app.run(debug=True, host="0.0.0.0", port=port)
