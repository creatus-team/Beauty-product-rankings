from http.server import BaseHTTPRequestHandler
import json
import urllib.request
import os

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

FIELD_EMOJI = {
    "성함": "👤",
    "나이": "🎂",
    "전화번호": "📱",
    "요일": "📅",
    "시간": "📅",
    "SNS": "📲",
    "동의": "✅",
    "영상": "🎬",
}

def get_emoji(label):
    for key, emoji in FIELD_EMOJI.items():
        if key in label:
            return emoji
    return "•"


class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
            fields = data.get("data", {}).get("fields", [])
            submitted_at = data.get("data", {}).get("submittedAt", "")

            # 이름 추출 (헤더용)
            name = "새 지원자"
            for f in fields:
                if "성함" in f.get("label", ""):
                    v = f.get("value", "")
                    if v:
                        name = v
                    break

            blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"📬 새 지원서 도착! — {name}",
                        "emoji": True
                    }
                },
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": f"🕐 제출 시간: {submitted_at}"}
                    ]
                },
                {"type": "divider"}
            ]

            for field in fields:
                label = field.get("label", "")
                value = field.get("value", "")
                if not label:
                    continue

                # 값 정리
                if isinstance(value, list):
                    value = "\n".join(str(v) for v in value) if value else "—"
                elif not value and value != 0:
                    value = "—"

                emoji = get_emoji(label)
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"{emoji} *{label}*\n{value}"
                    }
                })

            blocks.append({"type": "divider"})
            blocks.append({
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": "via Tally Form → #브랜드-빌딩"}
                ]
            })

            payload = json.dumps({"blocks": blocks}).encode("utf-8")
            req = urllib.request.Request(
                SLACK_WEBHOOK_URL,
                data=payload,
                headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req)

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode())

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Tally Webhook is live!")
