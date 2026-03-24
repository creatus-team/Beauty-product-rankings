"""
K-Beauty TikTok Slack Digest
- 매일 실행: views/followers >= 5배 영상 필터링 → Slack 발송
- 중복 방지: slack_sent.json 으로 이미 보낸 ID 추적
- 일 최대 10개 (ratio 높은 순)
"""

import json
import os
import glob
import requests
from datetime import datetime, timezone

WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
SENT_FILE   = "slack_sent.json"
RATIO_MIN   = 5.0    # views / followers 최소 배수
MIN_FOLLOWERS = 1000  # 팔로워 최소값 (봇 계정 제외)
MAX_PER_RUN = 10     # 1회 최대 발송 수


def load_sent_ids():
    if os.path.exists(SENT_FILE):
        with open(SENT_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_sent_ids(ids: set):
    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f, ensure_ascii=False)


def load_latest_data():
    files = sorted(glob.glob("data_*.json"))
    if not files:
        print("No data_*.json files found")
        return []
    latest = files[-1]
    print(f"Loading {latest}")
    with open(latest, encoding="utf-8") as f:
        return json.load(f)


def find_viral(data: list, sent_ids: set):
    viral = []
    for v in data:
        vid_id = v.get("id", "")
        if not vid_id or vid_id in sent_ids:
            continue
        views     = v.get("stats", {}).get("views", 0)
        followers = v.get("creator", {}).get("followers", 0)
        if followers < MIN_FOLLOWERS or views == 0:
            continue
        ratio = views / followers
        if ratio >= RATIO_MIN:
            viral.append({**v, "_ratio": ratio})

    # ratio 높은 순 정렬
    viral.sort(key=lambda x: x["_ratio"], reverse=True)
    return viral[:MAX_PER_RUN]


def format_num(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}K"
    return str(n)


def build_slack_block(v: dict) -> dict:
    creator   = v.get("creator", {})
    stats     = v.get("stats", {})
    ratio     = v.get("_ratio", 0)
    username  = creator.get("username", "")
    nickname  = creator.get("nickname", username)
    verified  = "✅" if creator.get("verified") else ""
    followers = creator.get("followers", 0)
    views     = stats.get("views", 0)
    likes     = stats.get("likes", 0)
    shares    = stats.get("shares", 0)
    saves     = stats.get("saves", 0)
    caption   = (v.get("caption") or "")[:120]
    hashtags  = " ".join(f"#{h}" for h in (v.get("hashtags") or [])[:6])
    url       = v.get("url", "")
    cover     = v.get("cover", "")
    region    = v.get("region", "")
    source    = v.get("source_tag", "")

    ratio_str = f"{ratio:.0f}x" if ratio >= 10 else f"{ratio:.1f}x"
    bar_filled = min(int(ratio / 50 * 10), 10)
    bar = "🟩" * bar_filled + "⬜" * (10 - bar_filled)

    blocks = [
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*🔥 {ratio_str} 배율 바이럴 영상 발견*\n"
                    f"{bar}\n"
                    f"*<{url}|@{username}>* {verified}  |  팔로워 {format_num(followers)}\n"
                    f"👁 {format_num(views)} 조회  ·  ❤️ {format_num(likes)}  ·  🔁 {format_num(shares)}  ·  🔖 {format_num(saves)}"
                ),
            },
            **({"accessory": {
                "type": "image",
                "image_url": cover,
                "alt_text": f"@{username} thumbnail",
            }} if cover else {}),
        },
    ]

    if caption or hashtags:
        body = ""
        if caption:
            body += f"_{caption}_\n"
        if hashtags:
            body += f"`{hashtags}`"
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": body.strip()},
        })

    meta_parts = []
    if region:
        meta_parts.append(f"📍 {region}")
    if source:
        meta_parts.append(f"#{source}")
    if meta_parts:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "  ".join(meta_parts)}],
        })

    blocks.append({
        "type": "actions",
        "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": "▶ TikTok에서 보기"},
            "url": url,
            "style": "primary",
        }],
    })

    return blocks


def send_to_slack(videos: list) -> bool:
    if not WEBHOOK_URL:
        print("SLACK_WEBHOOK_URL not set — skipping send")
        return False

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    header_block = {
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"📊 K-Beauty 바이럴 TikTok 리포트 · {today}",
        },
    }
    summary_block = {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                f"팔로워 대비 *{RATIO_MIN:.0f}배 이상* 조회된 영상 *{len(videos)}개* 발견됐어요 🚀\n"
                f"_ratio = 조회수 ÷ 팔로워 수_"
            ),
        },
    }

    all_blocks = [header_block, summary_block]
    for v in videos:
        all_blocks.extend(build_slack_block(v))

    # Slack 메시지 최대 블록 50개 제한 — 초과시 분할 발송
    def chunks(lst, n):
        for i in range(0, len(lst), n):
            yield lst[i:i+n]

    first = True
    for chunk in chunks(all_blocks, 48):
        payload = {"blocks": chunk if not first else chunk}
        if first:
            payload["text"] = f"K-Beauty 바이럴 TikTok {len(videos)}개 리포트"
            first = False
        resp = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code != 200:
            print(f"Slack error: {resp.status_code} {resp.text}")
            return False

    print(f"Sent {len(videos)} videos to Slack ✅")
    return True


def main():
    if not WEBHOOK_URL:
        print("⚠️  SLACK_WEBHOOK_URL not set. Set it in GitHub Secrets + Vercel env.")
        return

    sent_ids = load_sent_ids()
    data     = load_latest_data()
    viral    = find_viral(data, sent_ids)

    if not viral:
        print("No new viral videos found")
        return

    print(f"Found {len(viral)} new viral videos")
    for v in viral:
        print(f"  @{v['creator']['username']} | {v['_ratio']:.1f}x | {format_num(v['stats']['views'])} views")

    success = send_to_slack(viral)
    if success:
        new_ids = sent_ids | {v["id"] for v in viral}
        save_sent_ids(new_ids)
        print(f"Updated {SENT_FILE} with {len(viral)} new IDs")


if __name__ == "__main__":
    main()
