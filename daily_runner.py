#!/usr/bin/env python3
"""
K-Beauty Daily Auto Runner
매일 오전 9시 자동 실행:
  1. Vercel /api/refresh  → Amazon US/UK/JP, OliveYoung, YesStyle, TikTok 갱신
  2. twitter_scraper.py   → 일본 Twitter K-beauty 트윗 수집
  3. kbeauty_xhs_scraper.py → XHS 수집 (로그인 세션 유지 필요)
"""

import subprocess
import sys
import os
import requests
from datetime import datetime

DIR     = os.path.dirname(os.path.abspath(__file__))
LOG     = os.path.join(DIR, "daily_runner.log")
PYTHON  = sys.executable
VERCEL_URL = "https://kbeautyresearch.vercel.app"


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def step_vercel_refresh():
    log("── Vercel 데이터 새로고침 시작 (Amazon/OY/YS/TT)...")
    try:
        r = requests.post(f"{VERCEL_URL}/api/refresh", timeout=120)
        data = r.json()
        if data.get("ok"):
            log(f"   ✅ Vercel 완료: {data.get('count', '?')}개 상품, updated_at={data.get('updated_at', '?')}")
        else:
            log(f"   ⚠️  Vercel 응답 이상: {data}")
    except Exception as e:
        log(f"   ❌ Vercel 새로고침 실패: {e}")


def step_run_script(name, script_path):
    log(f"── {name} 시작...")
    if not os.path.exists(script_path):
        log(f"   ❌ 스크립트 없음: {script_path}")
        return
    try:
        result = subprocess.run(
            [PYTHON, script_path],
            capture_output=True, text=True, timeout=600
        )
        for line in result.stdout.strip().splitlines()[-10:]:  # 마지막 10줄만 로그
            log(f"   {line}")
        if result.returncode != 0:
            log(f"   ⚠️  종료 코드 {result.returncode}")
            for line in result.stderr.strip().splitlines()[-5:]:
                log(f"   STDERR: {line}")
        else:
            log(f"   ✅ {name} 완료")
    except subprocess.TimeoutExpired:
        log(f"   ❌ {name} 타임아웃 (10분 초과)")
    except Exception as e:
        log(f"   ❌ {name} 오류: {e}")


def main():
    log("=" * 50)
    log("K-Beauty Daily Runner 시작")
    log("=" * 50)

    # 1. Vercel 상품 데이터 새로고침
    step_vercel_refresh()

    # 2. 일본 Twitter 수집
    step_run_script("Twitter JP 스크래퍼", os.path.join(DIR, "twitter_scraper.py"))

    # 3. XHS 수집 (로그인 세션 유지 시 자동, 아니면 건너뜀)
    step_run_script("XHS 스크래퍼", os.path.join(DIR, "kbeauty_xhs_scraper.py"))

    log("=" * 50)
    log("K-Beauty Daily Runner 완료")
    log("=" * 50)


if __name__ == "__main__":
    main()
