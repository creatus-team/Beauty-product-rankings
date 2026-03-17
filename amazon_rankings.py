#!/usr/bin/env python3
"""
Amazon Beauty Rankings Dashboard
- 나라별 아마존 뷰티/이너뷰티 베스트셀러 랭킹 대시보드
- Run: python3 amazon_rankings.py
"""

from flask import Flask, jsonify, render_template_string
import json, os, requests
from datetime import datetime

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

# AliExpress actor (piotrv1001~aliexpress-listings-scraper)
ALIEXPRESS_ACTOR = "piotrv1001~aliexpress-listings-scraper"
ALIEXPRESS_SEARCH_URL = "https://www.aliexpress.com/wholesale?SearchText=beauty+skincare+makeup&SortType=total_tranRank_asc"
_aliexpress_run_id = "qLIPASr5oFbA6fSrl"  # latest completed run (updated on refresh)

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

def fetch_aliexpress(trigger_new=False):
    """AliExpress 뷰티 베스트셀러 (Apify piotrv1001~aliexpress-listings-scraper)"""
    global _aliexpress_run_id
    import time
    if trigger_new:
        payload = {
            "searchUrls": [{"url": ALIEXPRESS_SEARCH_URL}],
            "maxItems": 100,
        }
        try:
            r = requests.post(
                f"https://api.apify.com/v2/acts/{ALIEXPRESS_ACTOR}/runs?token={APIFY_TOKEN}",
                json=payload, timeout=15
            )
            r.raise_for_status()
            new_run_id = r.json().get("data", {}).get("id")
            if new_run_id:
                print(f"[AliExpress] new run started: {new_run_id}")
                # Wait up to 3 minutes for completion
                for _ in range(36):
                    time.sleep(5)
                    rs = requests.get(
                        f"https://api.apify.com/v2/actor-runs/{new_run_id}?token={APIFY_TOKEN}",
                        timeout=10
                    ).json().get("data", {}).get("status")
                    if rs in ("SUCCEEDED", "FAILED", "ABORTED"):
                        break
                if rs == "SUCCEEDED":
                    _aliexpress_run_id = new_run_id
                    print(f"[AliExpress] run succeeded: {new_run_id}")
        except Exception as e:
            print(f"[AliExpress] trigger failed: {e}")

    # Read from latest run
    try:
        url = (f"https://api.apify.com/v2/actor-runs/{_aliexpress_run_id}/dataset/items"
               f"?token={APIFY_TOKEN}&limit=200")
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        raw = resp.json()
        items = []
        for rank, p in enumerate(raw, 1):
            items.append({
                "name": p.get("title", ""),
                "url": f"https://www.aliexpress.com/item/{p['id']}.html" if p.get("id") else "#",
                "asin": p.get("id"),
                "position": rank,
                "thumbnailUrl": p.get("imageUrl"),
                "stars": p.get("rating"),
                "reviewsCount": None,
                "categoryName": "Beauty Bestsellers",
                "categoryFullName": "AliExpress Beauty Bestsellers",
                "_country_code": "AX",
                "_country_flag": "🛍️",
                "_country_name": "AliExpress",
                "_price_value": p.get("price"),
                "_price_currency": "$",
                "_total_sold": p.get("totalSold"),
            })
        print(f"[AliExpress] {len(items)} items from run {_aliexpress_run_id}")
        return items
    except Exception as e:
        print(f"[AliExpress] fetch failed: {e}")
        return []

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
    # AliExpress 추가
    ax_items = fetch_aliexpress(trigger_new=refresh)
    all_items.extend(ax_items)
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
.ctab:hover{color:var(--pink)}
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
</style>
</head>
<body>

<div id="loading">
  <div class="loader"></div>
  <p id="loadMsg">랭킹 데이터 불러오는 중...</p>
</div>

<header>
  <div class="h-left">
    <span class="h-logo">🛒</span>
    <div>
      <div class="h-title">Beauty Product Rankings</div>
      <div class="h-sub">나라별 뷰티 베스트셀러</div>
    </div>
  </div>
  <div class="h-right">
    <span class="upd" id="updLbl">—</span>
    <button id="refreshBtn" onclick="refreshData()">↻ 새로고침</button>
  </div>
</header>

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
  <span class="rc" id="rc"></span>
</div>

<div class="gw" id="gw"></div>

<script>
let all = [], country = 'DB', ysSub = 'All Beauty';

// country order: ALL first, then US, UK, JP, then others
const ORDER = ['DB','CH','IG','ALL','US','UK','JP','YS','AX','DE','FR','CA','AU','IT','ES'];
const TAB_LABELS = {'DB':'📊 대시보드','CH':'📈 카테고리 분석','IG':'🧪 성분 트렌드','ALL':'전체','YS':'YesStyle','AX':'AliExpress'};

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

function buildTabs() {
  // collect countries preserving ORDER preference
  const counts = {ALL: all.length};
  const seen = new Set();
  all.forEach(i => {
    const c = i._country_code;
    if (c) { counts[c] = (counts[c]||0)+1; seen.add(c); }
  });
  const codes = ['DB', 'CH', 'IG', 'ALL', ...ORDER.filter(c => c!=='ALL' && c!=='DB' && c!=='CH' && c!=='IG' && seen.has(c)),
                 ...[...seen].filter(c => !ORDER.includes(c))];

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
    btn.onclick = () => { country=code; ysSub='All Beauty'; resetYsPills(); buildTabs(); render(); };
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
  document.querySelectorAll('.ys-pill').forEach(p => {
    p.classList.toggle('active', p.dataset.sub === 'All Beauty');
  });
}

function updateYsPills() {
  const pills = document.getElementById('ysPills');
  pills.style.display = country === 'YS' ? 'flex' : 'none';
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
    if (q && !(i.name||'').toLowerCase().includes(q) && !(i.asin||'').toLowerCase().includes(q)) return false;
    return true;
  });
  items.sort((a,b) => {
    if (sort==='rank')       return (a.position||999)-(b.position||999);
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
    {code:'AX', label:'AliExpress'},
  ];
  let html = '<div class="dash">';
  platforms.forEach(({code, label}) => {
    const flag = (all.find(i=>i._country_code===code)||{})._country_flag || '';
    const top5 = all.filter(i=>i._country_code===code)
      .sort((a,b)=>(a.position||999)-(b.position||999)).slice(0,5);
    html += `<div class="dash-col"><div class="dash-hdr"><span class="dash-flag">${flag}</span>${label}</div>`;
    top5.forEach((item,idx) => {
      const r=idx+1, rc=r===1?'r1':r===2?'r2':r===3?'r3':'rn';
      const price=fmtPrice(item._price_value,item._price_currency);
      const th=item.thumbnailUrl;
      const imgH=th?`<img src="${th}" alt="" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`:'' ;
      html+=`<a class="dash-item" href="${item.url||'#'}" target="_blank" rel="noopener">
        <div class="dash-thumb">
          ${imgH}<div class="dash-ph" style="${th?'display:none':''}">🧴</div>
          <div class="dash-rnk ${rc}">${r}</div>
        </div>
        <div class="dash-info">
          <div class="dash-name">${item.name||'No Name'}</div>
          ${price?`<div class="dash-price">${price}</div>`:''}
        </div></a>`;
    });
    html += '</div>';
  });
  html += '</div>';
  return html;
}

function render() {
  updateYsPills();
  const toolbar = document.getElementById('toolbar');
  if (country === 'DB') {
    toolbar.style.display = 'none';
    document.getElementById('gw').innerHTML = renderDashboard();
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
  if (code === 'AX') {
    if (/shampoo|conditioner|hair/.test(name)) return {main:'헤어케어', sub:'헤어케어'};
    if (/serum|ampoule|essence/.test(name)) return {main:'스킨케어', sub:'세럼/에센스'};
    if (/toner/.test(name)) return {main:'스킨케어', sub:'토너/스킨'};
    if (/sunscreen|spf/.test(name)) return {main:'스킨케어', sub:'선케어'};
    if (/cream|moistur|lotion/.test(name)) return {main:'스킨케어', sub:'로션/크림'};
    if (/mask/.test(name)) return {main:'스킨케어', sub:'마스크팩'};
    if (/mascara|eyeliner/.test(name)) return {main:'메이크업', sub:'아이메이크업'};
    if (/foundation|bb|cc/.test(name)) return {main:'메이크업', sub:'파운데이션/BB'};
    if (/lip/.test(name)) return {main:'메이크업', sub:'립메이크업'};
    return {main:'기타', sub:'기타'};
  }
  // Amazon: categoryName is granular
  const cat = ((item.categoryName||'') + ' ' + (item.categoryFullName||'')).toLowerCase();
  if (/hair|shampoo|conditioner|scalp/.test(cat)) {
    if (/shampoo/.test(cat)) return {main:'헤어케어', sub:'샴푸'};
    if (/conditioner|treatment/.test(cat)) return {main:'헤어케어', sub:'컨디셔너'};
    return {main:'헤어케어', sub:'헤어케어'};
  }
  if (/foundation|bb.?cream|cc.?cream/.test(cat)) return {main:'메이크업', sub:'파운데이션/BB'};
  if (/mascara|eyeliner|eyebrow|eyeshadow/.test(cat)) return {main:'메이크업', sub:'아이메이크업'};
  if (/lip(?!.?balm)|lipstick/.test(cat)) return {main:'메이크업', sub:'립메이크업'};
  if (/blush|bronzer|highlighter|contour/.test(cat)) return {main:'메이크업', sub:'치크/하이라이터'};
  if (/makeup|cosmetic/.test(cat)) return {main:'메이크업', sub:'기타 메이크업'};
  if (/sun.?screen|sun.?block|spf/.test(cat)) return {main:'스킨케어', sub:'선케어'};
  if (/toner|astringent/.test(cat)) return {main:'스킨케어', sub:'토너/스킨'};
  if (/serum|essence|ampoule/.test(cat)) return {main:'스킨케어', sub:'세럼/에센스'};
  if (/eye.?cream|under.?eye/.test(cat)) return {main:'스킨케어', sub:'아이크림'};
  if (/lip.?balm|lip.?care/.test(cat)) return {main:'스킨케어', sub:'립케어'};
  if (/mask|sheet.?mask/.test(cat)) return {main:'스킨케어', sub:'마스크팩'};
  if (/cleanser|cleansing|face.?wash|foam/.test(cat)) return {main:'스킨케어', sub:'클렌저'};
  if (/exfoli|peeling|scrub/.test(cat)) return {main:'스킨케어', sub:'각질케어'};
  if (/pad/.test(cat)) return {main:'스킨케어', sub:'패드'};
  if (/moistur|lotion|cream|emulsion/.test(cat)) return {main:'스킨케어', sub:'로션/크림'};
  if (/skin.?care|brightening|whitening|anti.?aging|retinol|vitamin/.test(cat)) return {main:'스킨케어', sub:'기타 스킨케어'};
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
  const ctx=canvas.getContext('2d');
  const W=canvas.width,H=canvas.height,cx=W/2,cy=H/2;
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
    {code:'AX',label:'🛍️ AliExpress'},
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
    {code:'AX',label:'🛍️ AliExpress'},
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

loadData();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    print(f"🛒 Amazon Beauty Rankings → http://localhost:{port}")
    app.run(debug=True, host="0.0.0.0", port=port)
