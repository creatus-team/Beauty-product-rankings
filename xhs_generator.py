#!/usr/bin/env python3
"""
XHS 콘텐츠 자동 생성기
제품명 입력 → 이미지 + 한국어/중국어 캡션 자동 생성
Run: python3 xhs_generator.py
"""

from flask import Flask, render_template_string, jsonify, request, send_file
from google import genai
from google.genai import types
import os, base64, random, json, threading, uuid, zipfile, io, re, html as html_module
import urllib.request, urllib.error, urllib.parse, ssl, hmac, hashlib, time
import glob as glob_module
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(DATA_DIR, "xhs_templates")
CAPTION_REFS_PATH = os.path.join(DATA_DIR, "xhs_caption_refs.json")
os.makedirs(TEMPLATES_DIR, exist_ok=True)

# 배치 작업 상태 추적
JOBS = {}
JOBS_LOCK = threading.Lock()

# ── 레퍼런스 스타일 (스크린샷 추가되면 여기 업데이트) ──────────────────────
XHS_STYLE_GUIDE = """
[실제 레퍼런스 캡션 예시 - 이 스타일을 그대로 따를 것]
제목 예시:
- "国贸柜姐说这个好用。。。。"
- "韩国oliveyoung买过最猛的痘印消失剂"
- "🇰🇷 小韩巨好用的底妆神器！！！！"
- "世界上最最最最可爱的两支唇膏💄！！"
- "韩国地陪推荐的，真的巨巨巨好用啊啊啊！！"
- "后悔去韩国🇰🇷olive没多带几瓶回来。。。"
- "强推一个小韩的嫩脸剂！！！"
- "韩国oliveyoung必吃榜！！！"
- "在韩国的姐妹一定要去oliveyoung多拿几瓶"
- "干皮用完穷到爆的两个！！！"
- "好好用啊..."
- "韩国oliveyoung这个都去买啊啊啊啊啊"
- "小韩热门喷雾锐评（oliveyoung版）"

[OP티어 포스트 전문 예시 - 스토리텔링형]

예시1: Abib 제리코로즈크림
제목: "卡粉人去韩国一定要买"
본문: 比很多oliveyoung爆款更惊艳我... 随手一抹就是韩女粉白皮的感觉，后续上妆不带一点卡 丝滑到几乎感觉不到摩擦力🐑，出门也是无痕到像妈生素颜，连续用了一周没冒痘，可以晋升年度爱用了
해시태그: #韩国olive必买 #abib #上妆服帖不卡粉

예시2: Torriden 앰플
제목: "好好用啊..."
본문: 每次用一滴管涂脸上揉两下就可以了，刚开始很明显能feel到连面中粗大的毛孔都肉眼可见的从O缩到o，现在不化妆脸蛋子都细腻的离谱... 自己过去加上找代 我真的用掉了不下5瓶了🥹
해시태그: #韩国olive必买 #torriden #精华

예시3: Forest 세럼패드
제목: "韩国棉片好用到有点颠覆认知了"
본문: 每次就全脸敷敷10分钟，揭掉后脸子透亮的像刷了层韩女水光釉面，而且用久了之后出油也少了，以前鼻头全是坑坑洼注进得毛孔，现在素颜怼镜都找不着毛孔...好神奇🥹
해시태그: #forest #韩国olive必买 #缩毛孔

예시4: Rataplan 선크림
제목: "求你们去韩国一定要买这个"
본문: 刚落地就在ins刷到，从没用过这么清爽的防晒啊啊啊啊做为混油我真的爽了🏄 而且看到是敏肌专用直接秒买。。抹开就是真的一点刺激灼热感都没有，逛一天清潭洞下来没晒黑皮肤也没有麻麻赖赖的，昨晚熬夜的红印子都反而淡了，这不火真的不是它的原因......
해시태그: #防晒 #韩国olive必买 #rataplan #冷门好物

예시5: AFU 아이오일 (스토리텔링형)
제목: "国贸柜姐说这个好用。。。。"
본문: 两手空空进阿芙专柜，转身拎着他们家枚杖眼油出来...柜姐用小电动棒蘸了点眼油，在我眼周按摩了一圈 爽到我把上半辈子最开心的事都想了一遍🥹当下什么效果不效果的我都没考虑，单纯觉得太舒服了就买了...
回家后用了小半瓶，真的不是错觉，最近我能明显感觉到我的双眼皮变宽了，朋友问我是不是贴了双眼皮贴！！而且眼下的深纹也淡了好多 现在素颜眼睛都看着大了一圈😌...
해시태그: #眼油 #眼油推荐 #冷门 #以油养肤

[핵심 스타일 패턴]
제목 공식 (하나 선택):
- 피부타입 타겟팅: "卡粉人去韩国一定要买" / "混油必备" / "干皮救星"
- 강요형: "求你们去韩国一定要买这个" / "姐妹们一定要冲"
- 감탄형: "好好用啊..." / "颠覆认知了" / "真的绝了！！"
- 여행 맥락: "刚落地就买了" / "逛清潭洞发现的"
- 자조 유머: "用完穷到爆" / "这不火真的不是它的原因......"
- 권위: "柜姐推荐" / "冷门好物"

본문 구조:
1. 구매 계기 (여행/ins/추천) → 2. 첫 인상 → 3. 구체적 효과 변화 → 4. 놀람 표현
- 피부 타입 언급 필수: 混油 / 干皮 / 卡粉人 / 敏肌 / 黄气重
- 구체적 디테일: "用掉了不下5瓶" / "连续用了一周" / "逛一天清潭洞"
- 감각적 묘사: "丝滑到几乎感觉不到摩擦力" / "从O缩到o" / "韩女水光釉面"
- 사회적 증거: "朋友问我是不是..." / "ins上说是高允真拍戏用的同款"
- 여운 마무리: "这不火真的不是它的原因......" / "原来在小韩买个素颜霜都这么神奇的吗！！！！"

구매처 언급:
- oliveyoung / 清潭洞 / 면세점 / ins에서 봤다

해시태그 (4~5개):
- #韩国olive必买 #冷门好物 + 제품명 + 피부고민 (#缩毛孔 #防晒 #精华 등)

[한국어 캡션]
훅 예시:
- "카분러 한국 가면 무조건 사야 하는 거"
- "한국 여행 가서 인스에서 보고 바로 구매함"
- "올리브영에서 발견한 역대급 냉문템"
- "써보고 거지됨 근데 후회없음"
- "이거 모르면 진짜 손해야 언니들아"
- "왜 이제야 알았지 진짜로"
구조: 구매계기 → 첫인상 → 구체적 변화 → 감탄
감탄사: 진짜 / 미쳤다 / 역대급 / 거지됨 / 대박 / 후회없음
"""

HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>XHS 콘텐츠 생성기</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Noto Sans KR', sans-serif;
    background: #fdf6f0;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 30px 20px 60px;
  }

  /* Template section */
  .template-section {
    width: 100%; max-width: 520px;
    background: white;
    border-radius: 16px;
    border: 1.5px solid #f0e0d8;
    margin-bottom: 28px;
    overflow: hidden;
  }
  .template-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 18px;
    cursor: pointer;
    user-select: none;
  }
  .template-header:hover { background: #fdf6f0; }
  .template-title {
    font-size: 14px; font-weight: 700; color: #1a1a1a;
    display: flex; align-items: center; gap: 8px;
  }
  .template-badge {
    font-size: 11px; font-weight: 700;
    background: #e8795a; color: white;
    border-radius: 20px; padding: 2px 9px;
  }
  .template-badge.empty { background: #ddd; }
  .toggle-icon { font-size: 12px; color: #aaa; transition: transform 0.2s; }
  .toggle-icon.open { transform: rotate(180deg); }
  .template-body {
    display: none;
    padding: 0 18px 18px;
    border-top: 1.5px solid #f0e8e4;
  }
  .template-body.open { display: block; }
  .template-drop {
    margin-top: 14px;
    border: 2px dashed #e8ddd7;
    border-radius: 12px;
    padding: 28px 20px;
    text-align: center;
    cursor: pointer;
    transition: border-color 0.2s, background 0.2s;
    background: #fdfaf9;
    position: relative;
  }
  .template-drop:hover, .template-drop.drag-over { border-color: #e8795a; background: #fff8f5; }
  .template-drop input[type="file"] {
    position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%;
  }
  .template-drop-text { font-size: 13px; color: #aaa; pointer-events: none; }
  .template-drop-text span { color: #e8795a; font-weight: 600; }
  .template-progress {
    margin-top: 12px;
    font-size: 12px; color: #999;
    display: none;
  }
  .template-progress.visible { display: block; }
  .template-grid {
    margin-top: 14px;
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 6px;
    max-height: 320px;
    overflow-y: auto;
  }
  .template-thumb-wrap {
    position: relative;
    aspect-ratio: 1;
  }
  .template-thumb {
    aspect-ratio: 1;
    border-radius: 8px;
    object-fit: cover;
    width: 100%;
    height: 100%;
    border: 1.5px solid #f0e8e4;
    display: block;
  }
  .template-thumb-del {
    position: absolute; top: 3px; right: 3px;
    width: 20px; height: 20px;
    background: rgba(0,0,0,0.55); color: white;
    border: none; border-radius: 50%;
    font-size: 11px; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    opacity: 0; transition: opacity 0.15s;
    line-height: 1;
  }
  .template-thumb-wrap:hover .template-thumb-del { opacity: 1; }
  .template-thumb-del:hover { background: #e8795a; }
  .template-info {
    margin-top: 10px;
    font-size: 11px; color: #bbb;
    text-align: center;
  }
  .analyze-btn {
    width: 100%; margin-top: 12px;
    padding: 10px; background: #fff3ef;
    border: 1.5px solid #f0c5b5; border-radius: 10px;
    font-size: 13px; font-weight: 600; color: #e8795a;
    cursor: pointer; transition: background 0.2s;
  }
  .analyze-btn:hover { background: #ffe8e0; }
  .analyze-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .profile-status {
    margin-top: 8px; font-size: 11px; color: #aaa; text-align: center;
  }
  .profile-status.done { color: #5ab87a; font-weight: 600; }
  h1 {
    font-size: 22px;
    font-weight: 700;
    color: #1a1a1a;
    margin-bottom: 6px;
    letter-spacing: -0.5px;
  }
  .subtitle {
    font-size: 13px;
    color: #999;
    margin-bottom: 24px;
  }

  /* Caption refs section */
  .caption-section {
    width: 100%; max-width: 520px;
    background: white;
    border-radius: 16px;
    border: 1.5px solid #f0e0d8;
    margin-bottom: 20px;
    overflow: hidden;
  }
  .caption-section .template-header { cursor: pointer; }
  .caption-section .template-header:hover { background: #fdf6f0; }
  .caption-body {
    display: none;
    padding: 0 18px 18px;
    border-top: 1.5px solid #f0e8e4;
  }
  .caption-body.open { display: block; }
  .caption-textarea {
    width: 100%; margin-top: 14px;
    padding: 12px 14px;
    border: 1.5px solid #e8ddd7; border-radius: 10px;
    font-size: 13px; line-height: 1.7;
    font-family: inherit; resize: vertical;
    min-height: 120px; outline: none;
    background: #fdfaf9; color: #333;
    transition: border 0.2s;
  }
  .caption-textarea:focus { border-color: #e8795a; background: white; }
  .caption-textarea::placeholder { color: #ccc; }
  .caption-add-btn {
    width: 100%; margin-top: 8px;
    padding: 10px; background: #e8795a;
    border: none; border-radius: 10px;
    font-size: 13px; font-weight: 700; color: white;
    cursor: pointer; transition: background 0.2s;
  }
  .caption-add-btn:hover { background: #d4613e; }
  .caption-list {
    margin-top: 14px;
    display: flex; flex-direction: column; gap: 8px;
    max-height: 260px; overflow-y: auto;
  }
  .caption-item {
    background: #fdf6f0;
    border-radius: 10px; padding: 10px 12px;
    font-size: 12px; line-height: 1.6; color: #444;
    white-space: pre-wrap; word-break: break-word;
    position: relative;
    border: 1px solid #f0e0d8;
  }
  .caption-item-del {
    position: absolute; top: 8px; right: 8px;
    background: none; border: none;
    font-size: 13px; color: #ccc; cursor: pointer;
  }
  .caption-item-del:hover { color: #e8795a; }
  .caption-empty { font-size: 12px; color: #ccc; text-align: center; margin-top: 12px; }
  .caption-tabs {
    display: flex; gap: 6px; margin-top: 14px;
  }
  .caption-tab {
    flex: 1; padding: 8px; border-radius: 9px;
    font-size: 12px; font-weight: 700; cursor: pointer;
    border: 1.5px solid #e8ddd7; background: #fdfaf9;
    color: #aaa; transition: all 0.15s;
  }
  .caption-tab.active { background: #e8795a; border-color: #e8795a; color: white; }
  .caption-tab-panel { display: none; }
  .caption-tab-panel.active { display: block; }
  .caption-img-drop {
    margin-top: 10px;
    border: 2px dashed #e8ddd7; border-radius: 12px;
    padding: 24px; text-align: center; cursor: pointer;
    background: #fdfaf9; position: relative;
    transition: border-color 0.2s, background 0.2s;
  }
  .caption-img-drop:hover, .caption-img-drop.drag-over { border-color: #e8795a; background: #fff8f5; }
  .caption-img-drop input[type="file"] {
    position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%;
  }
  .caption-img-drop-text { font-size: 13px; color: #aaa; pointer-events: none; }
  .caption-img-drop-text span { color: #e8795a; font-weight: 600; }
  .caption-extract-status {
    margin-top: 8px; font-size: 12px; color: #999; text-align: center; min-height: 18px;
  }
  .caption-extract-status.ok { color: #5ab87a; font-weight: 600; }
  .caption-extract-status.err { color: #e05a3a; }
  .input-wrap {
    display: flex;
    gap: 10px;
    width: 100%;
    max-width: 520px;
    margin-bottom: 36px;
  }
  input[type="text"] {
    flex: 1;
    padding: 14px 18px;
    border: 1.5px solid #e8ddd7;
    border-radius: 12px;
    font-size: 15px;
    background: #fff;
    outline: none;
    transition: border 0.2s;
  }
  input[type="text"]:focus { border-color: #e8795a; }
  button.gen-btn {
    padding: 14px 24px;
    background: #e8795a;
    color: white;
    border: none;
    border-radius: 12px;
    font-size: 15px;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.2s, transform 0.1s;
    white-space: nowrap;
  }
  button.gen-btn:hover { background: #d4613e; }
  button.gen-btn:active { transform: scale(0.97); }
  button.gen-btn:disabled { background: #ccc; cursor: not-allowed; }

  .result-wrap {
    display: none;
    width: 100%;
    max-width: 520px;
    gap: 20px;
    flex-direction: column;
  }
  .result-wrap.visible { display: flex; }

  .card {
    background: #fff;
    border-radius: 18px;
    overflow: hidden;
    box-shadow: 0 2px 16px rgba(0,0,0,0.07);
  }
  .card img {
    width: 100%;
    display: block;
    border-radius: 18px 18px 0 0;
  }
  .caption-block {
    padding: 20px;
    position: relative;
  }
  .caption-label {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    color: #e8795a;
    margin-bottom: 10px;
    text-transform: uppercase;
  }
  .caption-text {
    font-size: 14px;
    line-height: 1.75;
    color: #2a2a2a;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .copy-btn {
    position: absolute;
    top: 16px;
    right: 16px;
    padding: 6px 14px;
    background: #f3ece8;
    border: none;
    border-radius: 8px;
    font-size: 12px;
    color: #666;
    cursor: pointer;
    transition: background 0.2s;
  }
  .copy-btn:hover { background: #e8ddd7; }
  .divider {
    height: 1px;
    background: #f0e8e4;
    margin: 0 20px;
  }
  .result-btns {
    display: flex;
    gap: 8px;
    margin: 16px 20px 4px;
  }
  .result-btns .dl-btn,
  .result-btns .video-btn {
    flex: 1;
    margin: 0;
  }
  .video-btn {
    display: block;
    padding: 12px;
    background: #1a1a2e;
    border: 1.5px solid #7c6fff;
    color: #b8b0ff;
    font-size: 13px;
    font-weight: 600;
    border-radius: 10px;
    cursor: pointer;
    text-align: center;
    transition: background 0.2s;
  }
  .video-btn:hover { background: #2a2050; }
  .video-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .video-area {
    margin: 4px 20px 12px;
  }
  .video-loader {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 14px;
    background: #1a1a2e;
    border-radius: 10px;
    margin-top: 8px;
  }
  .video-spinner {
    width: 18px; height: 18px;
    border: 2px solid #7c6fff44;
    border-top-color: #7c6fff;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    flex-shrink: 0;
  }
  .video-loader-text { font-size: 13px; color: #b8b0ff; }
  .dl-btn {
    display: block;
    margin: 16px 20px 20px;
    padding: 12px;
    background: #f9f3f0;
    border: 1.5px solid #e8ddd7;
    border-radius: 10px;
    text-align: center;
    font-size: 13px;
    color: #666;
    cursor: pointer;
    text-decoration: none;
    transition: background 0.2s;
  }
  .dl-btn:hover { background: #f0e8e4; }

  .loader {
    display: none;
    flex-direction: column;
    align-items: center;
    gap: 14px;
    padding: 40px;
    color: #999;
    font-size: 14px;
  }
  .loader.visible { display: flex; }
  .spinner {
    width: 36px; height: 36px;
    border: 3px solid #f0e8e4;
    border-top-color: #e8795a;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .error-msg {
    color: #e05a3a;
    font-size: 13px;
    padding: 12px 16px;
    background: #fff0ec;
    border-radius: 10px;
    display: none;
    max-width: 520px;
    width: 100%;
  }
  .error-msg.visible { display: block; }

  /* ── 배치 모드 ── */
  .mode-tabs {
    display: flex; gap: 0; width: 100%; max-width: 520px;
    background: #f0e8e4; border-radius: 12px; padding: 3px;
    margin-bottom: 20px;
  }
  .mode-tab {
    flex: 1; padding: 9px; border: none; border-radius: 10px;
    font-size: 13px; font-weight: 700; cursor: pointer;
    background: transparent; color: #aaa; transition: all 0.15s;
  }
  .mode-tab.active { background: white; color: #e8795a; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }

  .mode-panel { display: none; width: 100%; max-width: 520px; }
  .mode-panel.active { display: block; }

  .batch-drop {
    border: 2px dashed #e8ddd7; border-radius: 14px;
    padding: 28px; text-align: center; cursor: pointer;
    background: white; position: relative; margin-bottom: 14px;
    transition: border-color 0.2s, background 0.2s;
  }
  .batch-drop:hover, .batch-drop.drag-over { border-color: #e8795a; background: #fff8f5; }
  .batch-drop input[type="file"] { position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%; }
  .batch-drop-text { font-size: 13px; color: #aaa; pointer-events: none; }
  .batch-drop-text span { color: #e8795a; font-weight: 600; }

  .batch-list { display: flex; flex-direction: column; gap: 8px; margin-bottom: 14px; }
  .batch-item {
    display: flex; align-items: center; gap: 10px;
    background: white; border-radius: 10px; padding: 10px 12px;
    border: 1.5px solid #f0e8e4;
  }
  .batch-thumb { width: 44px; height: 44px; border-radius: 8px; object-fit: cover; flex-shrink: 0; }
  .batch-name-input {
    flex: 1; border: 1px solid #e8ddd7; border-radius: 8px;
    padding: 7px 10px; font-size: 13px; outline: none;
    transition: border 0.2s; background: #fdfaf9;
  }
  .batch-name-input:focus { border-color: #e8795a; background: white; }
  .batch-item-del { background: none; border: none; color: #ccc; cursor: pointer; font-size: 16px; flex-shrink: 0; }
  .batch-item-del:hover { color: #e8795a; }

  .batch-run-btn {
    width: 100%; padding: 13px; background: #e8795a; color: white;
    border: none; border-radius: 12px; font-size: 15px; font-weight: 700;
    cursor: pointer; transition: background 0.2s; margin-bottom: 16px;
  }
  .batch-run-btn:hover { background: #d4613e; }
  .batch-run-btn:disabled { background: #ccc; cursor: not-allowed; }

  .batch-progress { display: flex; flex-direction: column; gap: 6px; margin-bottom: 14px; }
  .batch-progress-item {
    display: flex; align-items: center; gap: 10px;
    font-size: 12px; color: #666; background: white;
    border-radius: 8px; padding: 8px 12px; border: 1px solid #f0e8e4;
  }
  .batch-progress-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .batch-progress-dot.queued { background: #ddd; }
  .batch-progress-dot.generating_image, .batch-progress-dot.generating_caption { background: #f5a623; animation: pulse 1s infinite; }
  .batch-progress-dot.done { background: #5ab87a; }
  .batch-progress-dot.error { background: #e05a3a; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
  .batch-status-text { flex: 1; }

  .batch-actions { display: flex; gap: 8px; margin-bottom: 16px; }
  .batch-dl-btn {
    flex: 1; padding: 11px; background: white; border: 1.5px solid #e8795a;
    color: #e8795a; border-radius: 10px; font-weight: 700; font-size: 13px;
    cursor: pointer; transition: all 0.15s;
  }
  .batch-dl-btn:hover { background: #fff3ef; }
  .batch-dl-btn:disabled { opacity: 0.4; cursor: not-allowed; }

  /* 배치 결과 갤러리 */
  .batch-gallery { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .gallery-card {
    background: white; border-radius: 14px; overflow: hidden;
    box-shadow: 0 1px 6px rgba(0,0,0,0.06);
  }
  .gallery-card img { width: 100%; display: block; aspect-ratio: 3/4; object-fit: cover; }
  .gallery-card-footer { padding: 8px 10px; }
  .gallery-card-name { font-size: 11px; font-weight: 700; color: #555; margin-bottom: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .gallery-card-actions { display: flex; gap: 4px; }
  .gallery-card-btn {
    flex: 1; padding: 5px; border: 1px solid #e8ddd7; border-radius: 6px;
    font-size: 11px; cursor: pointer; background: #fdfaf9; color: #666;
    transition: all 0.15s;
  }
  .gallery-card-btn:hover { border-color: #e8795a; color: #e8795a; }
  .gallery-caption-preview {
    font-size: 11px; color: #999; line-height: 1.5; padding: 6px 10px 10px;
    white-space: pre-wrap; word-break: break-word; max-height: 80px; overflow: hidden;
    cursor: pointer;
  }
  .gallery-caption-preview.expanded { max-height: none; }

  .url-fetch-area {
    width: 100%; max-width: 520px;
    margin-bottom: 12px;
  }
  .url-fetch-area .url-row {
    display: flex; gap: 8px;
  }
  .url-fetch-area input[type="text"] {
    flex: 1; font-size: 13px; padding: 11px 14px;
  }
  .url-fetch-btn {
    padding: 11px 16px; background: #e8795a; color: white;
    border: none; border-radius: 12px; font-size: 13px; font-weight: 700;
    cursor: pointer; white-space: nowrap; transition: background 0.2s;
  }
  .url-fetch-btn:hover { background: #d4613e; }
  .url-fetch-btn:disabled { background: #ccc; cursor: not-allowed; }
  .fetch-status {
    font-size: 12px; color: #aaa; margin-top: 5px; min-height: 16px;
  }
  .fetch-status.ok { color: #5ab87a; font-weight: 600; }
  .fetch-status.err { color: #e05a3a; }
  .url-divider {
    display: flex; align-items: center; gap: 8px;
    margin-bottom: 12px; font-size: 11px; color: #ccc;
  }
  .url-divider::before, .url-divider::after {
    content: ''; flex: 1; height: 1px; background: #f0e8e4;
  }
  .url-batch-area {
    background: white; border: 1.5px solid #f0e8e4; border-radius: 12px;
    padding: 14px; margin-bottom: 14px;
  }
  .url-batch-label {
    font-size: 13px; font-weight: 700; color: #1a1a1a; margin-bottom: 10px;
  }
  .url-batch-textarea {
    width: 100%; min-height: 90px; padding: 10px 12px;
    border: 1.5px solid #e8ddd7; border-radius: 10px;
    font-size: 12px; line-height: 1.7; font-family: inherit;
    resize: vertical; outline: none; background: #fdfaf9; color: #333;
    transition: border 0.2s;
  }
  .url-batch-textarea:focus { border-color: #e8795a; background: white; }
  .url-batch-textarea::placeholder { color: #ccc; }

  .upload-area {
    width: 100%;
    max-width: 520px;
    margin-bottom: 16px;
    border: 2px dashed #e8ddd7;
    border-radius: 14px;
    padding: 20px;
    text-align: center;
    cursor: pointer;
    transition: border-color 0.2s, background 0.2s;
    background: #fff;
    position: relative;
  }
  .upload-area:hover, .upload-area.drag-over { border-color: #e8795a; background: #fff8f5; }
  .upload-area input[type="file"] {
    position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%; z-index: 1;
  }
  .upload-area input[type="file"].hidden { pointer-events: none; }
  .upload-label { font-size: 13px; color: #aaa; pointer-events: none; }
  .upload-label span { color: #e8795a; font-weight: 600; }
  .preview-img {
    display: none;
    max-height: 120px;
    border-radius: 10px;
    margin: 0 auto;
    object-fit: contain;
  }
  .preview-img.visible { display: block; }
  .remove-img {
    display: none;
    margin-top: 8px;
    font-size: 12px;
    color: #e8795a;
    cursor: pointer;
    background: none;
    border: none;
    position: relative;
    z-index: 10;
  }
  .remove-img.visible { display: inline-block; }
</style>
</head>
<body>

<h1>✦ XHS 콘텐츠 생성기</h1>
<p class="subtitle">제품 사진 → 레퍼런스 스왑 → 한/중 캡션 자동 생성</p>

<div class="mode-tabs">
  <button class="mode-tab active" id="modeSingle" onclick="switchMode('single')">1개 생성</button>
  <button class="mode-tab" id="modeBatch" onclick="switchMode('batch')">📦 배치 (여러 개)</button>
</div>

<!-- 템플릿 DB 섹션 -->
<div class="template-section">
  <div class="template-header" onclick="toggleTemplates()">
    <div class="template-title">
      📂 레퍼런스 템플릿
      <span class="template-badge empty" id="templateBadge">0장</span>
    </div>
    <span class="toggle-icon" id="toggleIcon">▼</span>
  </div>
  <div class="template-body" id="templateBody">
    <div class="template-drop" id="templateDrop">
      <input type="file" id="templateInput" accept="image/*" multiple onchange="uploadTemplates(event)" />
      <div class="template-drop-text">📷 UGC 레퍼런스 사진 <span>여러 장 동시 업로드</span><br><small style="color:#ccc;font-size:11px">클릭 또는 드래그 — jpg/png/webp 모두 가능</small></div>
    </div>
    <div class="template-progress" id="templateProgress"></div>
    <div class="template-grid" id="templateGrid"></div>
    <div class="template-info" id="templateInfo"></div>
    <button class="analyze-btn" id="analyzeBtn" onclick="analyzeStyle()">✦ AI 스타일 분석 (생성 품질 향상)</button>
    <div class="profile-status" id="profileStatus"></div>
  </div>
</div>

<!-- 캡션 레퍼런스 섹션 -->
<div class="caption-section">
  <div class="template-header" onclick="toggleCaptions()">
    <div class="template-title">
      ✍️ 캡션 레퍼런스
      <span class="template-badge empty" id="captionBadge">0개</span>
    </div>
    <span class="toggle-icon" id="captionToggleIcon">▼</span>
  </div>
  <div class="caption-body" id="captionBody">
    <div class="caption-tabs">
      <button class="caption-tab active" id="tabText" onclick="switchCaptionTab('text')">✏️ 텍스트 붙여넣기</button>
      <button class="caption-tab" id="tabImg" onclick="switchCaptionTab('img')">📷 캡처 업로드</button>
    </div>

    <!-- 텍스트 탭 -->
    <div class="caption-tab-panel active" id="panelText">
      <textarea class="caption-textarea" id="captionInput"
        placeholder="레퍼런스 캡션 전체를 붙여넣어줘 (제목 + 본문 + 해시태그 포함)&#10;&#10;여러 개 추가하면 모두 학습해서 그 스타일로 써줘"></textarea>
      <button class="caption-add-btn" onclick="addCaption()">+ 레퍼런스 추가</button>
    </div>

    <!-- 이미지 탭 -->
    <div class="caption-tab-panel" id="panelImg">
      <div class="caption-img-drop" id="captionImgDrop">
        <input type="file" id="captionImgInput" accept="image/*" multiple onchange="uploadCaptionImages(event)" />
        <div class="caption-img-drop-text">📷 XHS 포스트 캡처 <span>여러 장 동시 업로드</span><br><small style="color:#ccc;font-size:11px">Gemini가 텍스트 자동 추출 → 레퍼런스로 저장</small></div>
      </div>
      <div class="caption-extract-status" id="captionExtractStatus"></div>
    </div>

    <div class="caption-list" id="captionList"></div>
  </div>
</div>

<!-- ── 싱글 모드 ── -->
<div class="mode-panel active" id="panelSingle">
  <!-- URL 가져오기 -->
  <div class="url-fetch-area">
    <div class="url-row">
      <input type="text" id="productUrl" placeholder="🔗 올리브영, 쿠팡 등 제품 링크 붙여넣기" />
      <button class="url-fetch-btn" id="fetchBtn" onclick="fetchFromUrl()">가져오기</button>
    </div>
    <div class="fetch-status" id="fetchStatus"></div>
  </div>
  <div class="url-divider">또는 직접 업로드</div>
  <!-- 제품 사진 업로드 -->
  <div class="upload-area" id="uploadArea">
    <input type="file" id="productImg" accept="image/*" onchange="onFileChange(event)" />
    <img class="preview-img" id="previewImg" src="" alt="미리보기" />
    <div class="upload-label" id="uploadLabel">📷 <b>제품 사진</b> 업로드 <span>클릭 또는 드래그</span><br><small style="color:#ccc;font-size:11px">레퍼런스 스왑 모드</small></div>
    <button class="remove-img" id="removeImg" onclick="removeImage(event)">✕ 사진 제거</button>
  </div>
  <div class="input-wrap">
    <input type="text" id="productInput" placeholder="예: 라네즈 립 슬리핑 마스크" />
    <button class="gen-btn" id="genBtn" onclick="generate()">생성 ㄱㄱ</button>
  </div>
  <div class="error-msg" id="errorMsg"></div>
  <div class="loader" id="loader">
    <div class="spinner"></div>
    <span id="loaderText">이미지 + 캡션 생성 중... (15~30초)</span>
  </div>
  <div class="result-wrap" id="resultWrap">
    <div class="card">
      <img id="resultImg" src="" alt="생성된 이미지" />
      <div class="result-btns">
        <a class="dl-btn" id="dlBtn" download="xhs_image.jpg">⬇ 이미지 저장</a>
        <button class="video-btn" id="videoBtn" onclick="generateVideo()">🎬 영상 만들기</button>
      </div>
      <!-- 영상 영역 -->
      <div class="video-area" id="videoArea" style="display:none">
        <div class="video-loader" id="videoLoader">
          <div class="video-spinner"></div>
          <div class="video-loader-text" id="videoLoaderText">영상 생성 중... (1~3분)</div>
        </div>
        <div class="video-result" id="videoResult" style="display:none">
          <video id="resultVideo" controls playsinline style="width:100%;border-radius:12px;margin-top:10px"></video>
          <a class="dl-btn" id="dlVideoBtn" download="xhs_video.mp4" style="margin-top:8px">⬇ 영상 저장</a>
        </div>
        <div class="video-error" id="videoError" style="display:none;color:#ff6b6b;font-size:13px;text-align:center;padding:10px"></div>
      </div>
      <div class="divider"></div>
      <div class="caption-block">
        <div class="caption-label">🇰🇷 한국어 캡션</div>
        <button class="copy-btn" onclick="copy('ko')">복사</button>
        <div class="caption-text" id="captionKo"></div>
      </div>
      <div class="divider"></div>
      <div class="caption-block">
        <div class="caption-label">🇨🇳 중국어 캡션</div>
        <button class="copy-btn" onclick="copy('zh')">복사</button>
        <div class="caption-text" id="captionZh"></div>
      </div>
    </div>
  </div>
</div>

<!-- ── 배치 모드 ── -->
<div class="mode-panel" id="panelBatch">
  <!-- URL 일괄 추가 -->
  <div class="url-batch-area">
    <div class="url-batch-label">🔗 URL로 일괄 추가</div>
    <textarea class="url-batch-textarea" id="batchUrls"
      placeholder="제품 링크를 한 줄에 하나씩 붙여넣기&#10;예: https://www.oliveyoung.co.kr/store/goods/...&#10;     https://www.coupang.com/vp/products/..."></textarea>
    <button class="batch-run-btn" id="urlBatchBtn" onclick="addBatchFromUrls()" style="margin-top:8px;margin-bottom:0">🔗 URL로 추가</button>
    <div class="fetch-status" id="urlBatchStatus" style="text-align:center;margin-top:6px;"></div>
  </div>
  <div class="url-divider">또는 이미지 직접 업로드</div>
  <div class="batch-drop" id="batchDrop">
    <input type="file" id="batchInput" accept="image/*" multiple onchange="addBatchFiles(event)" />
    <div class="batch-drop-text">📦 제품 사진 <span>여러 장 한번에 업로드</span><br><small style="color:#ccc;font-size:11px">클릭 또는 드래그 — 파일명이 제품명으로 자동 입력됨</small></div>
  </div>
  <div class="batch-list" id="batchList"></div>
  <button class="batch-run-btn" id="batchRunBtn" onclick="runBatch()" style="display:none">⚡ 전체 생성 ㄱㄱ</button>
  <div class="batch-progress" id="batchProgress"></div>
  <div class="batch-actions" id="batchActions" style="display:none">
    <button class="batch-dl-btn" id="batchZipBtn" onclick="downloadZip()">⬇ ZIP 다운로드</button>
    <button class="batch-dl-btn" onclick="copyAllCaptions()">📋 캡션 전체 복사</button>
  </div>
  <div class="batch-gallery" id="batchGallery"></div>
</div>

<script>
let productImgB64 = null;
let productImgMime = null;
let productDescription = '';

// ── 템플릿 관련 ──────────────────────────────────────
// ── 모드 전환 ──────────────────────────────────────
function switchMode(mode) {
  document.getElementById('modeSingle').classList.toggle('active', mode === 'single');
  document.getElementById('modeBatch').classList.toggle('active', mode === 'batch');
  document.getElementById('panelSingle').classList.toggle('active', mode === 'single');
  document.getElementById('panelBatch').classList.toggle('active', mode === 'batch');
}

// ── 배치 모드 ──────────────────────────────────────
let batchItems = []; // [{file, name, b64, mime}]
let batchJobIds = [];
let batchPollTimer = null;

function addBatchFiles(e) {
  const files = Array.from(e.target.files);
  files.forEach(file => {
    const reader = new FileReader();
    reader.onload = ev => {
      const b64 = ev.target.result.split(',')[1];
      const name = file.name.replace(/\.[^.]+$/, '').replace(/_/g, ' ');
      batchItems.push({ file, name, b64, mime: file.type, thumb: ev.target.result });
      renderBatchList();
    };
    reader.readAsDataURL(file);
  });
  document.getElementById('batchInput').value = '';
}

// 배치 드롭존
const bDrop = document.getElementById('batchDrop');
bDrop.addEventListener('dragover', e => { e.preventDefault(); bDrop.classList.add('drag-over'); });
bDrop.addEventListener('dragleave', () => bDrop.classList.remove('drag-over'));
bDrop.addEventListener('drop', e => {
  e.preventDefault(); bDrop.classList.remove('drag-over');
  const files = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith('image/'));
  if (files.length) addBatchFiles({ target: { files } });
});

function renderBatchList() {
  const list = document.getElementById('batchList');
  const btn = document.getElementById('batchRunBtn');
  list.innerHTML = batchItems.map((item, i) => `
    <div class="batch-item">
      <img class="batch-thumb" src="${item.thumb}" />
      <input class="batch-name-input" value="${item.name}" onchange="batchItems[${i}].name=this.value" placeholder="제품명" />
      <button class="batch-item-del" onclick="removeBatchItem(${i})">✕</button>
    </div>
  `).join('');
  btn.style.display = batchItems.length ? 'block' : 'none';
}

function removeBatchItem(i) {
  batchItems.splice(i, 1);
  renderBatchList();
}

async function runBatch() {
  if (!batchItems.length) return;
  const btn = document.getElementById('batchRunBtn');
  btn.disabled = true;
  document.getElementById('batchActions').style.display = 'none';
  document.getElementById('batchGallery').innerHTML = '';

  const items = batchItems.map(item => ({
    product_name: item.name,
    image_b64: item.b64,
    image_mime: item.mime,
  }));

  const res = await fetch('/batch-generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items }),
  });
  const data = await res.json();
  batchJobIds = data.job_ids || [];
  renderBatchProgress(batchJobIds.map((id, i) => ({
    id, status: 'queued', product_name: batchItems[i]?.name || id
  })));
  btn.disabled = false;
  pollBatchStatus();
}

function renderBatchProgress(jobs) {
  const statusLabel = { queued: '대기중', generating_image: '이미지 생성중...', generating_caption: '캡션 생성중...', done: '완료', error: '오류' };
  document.getElementById('batchProgress').innerHTML = jobs.map(j => `
    <div class="batch-progress-item">
      <div class="batch-progress-dot ${j.status}"></div>
      <div class="batch-status-text">${j.product_name}</div>
      <div style="font-size:11px;color:#aaa">${statusLabel[j.status] || j.status}</div>
    </div>
  `).join('');
}

async function pollBatchStatus() {
  if (!batchJobIds.length) return;
  clearTimeout(batchPollTimer);
  const res = await fetch('/batch-status?ids=' + batchJobIds.join(','));
  const data = await res.json();
  const jobs = batchJobIds.map(id => ({ id, ...data[id] }));
  renderBatchProgress(jobs);

  const done = jobs.filter(j => j.status === 'done');
  const allFinished = jobs.every(j => j.status === 'done' || j.status === 'error');

  // 갤러리 업데이트
  const gallery = document.getElementById('batchGallery');
  gallery.innerHTML = done.map(j => `
    <div class="gallery-card">
      <img src="data:image/jpeg;base64,${j.image_b64}" />
      <div class="gallery-card-footer">
        <div class="gallery-card-name">${j.product_name}</div>
        <div class="gallery-card-actions">
          <button class="gallery-card-btn" onclick="downloadSingle('${j.id}','${j.product_name}')">⬇ 이미지</button>
          <button class="gallery-card-btn" onclick="copyCaptionKo('${j.id}')">KO 복사</button>
          <button class="gallery-card-btn" onclick="copyCaptionZh('${j.id}')">ZH 복사</button>
        </div>
      </div>
      <div class="gallery-caption-preview" id="cap_${j.id}" onclick="this.classList.toggle('expanded')">${(j.caption_ko||'').substring(0,80)}...</div>
    </div>
  `).join('');

  if (allFinished && done.length > 0) {
    document.getElementById('batchActions').style.display = 'flex';
  }
  if (!allFinished) {
    batchPollTimer = setTimeout(pollBatchStatus, 3000);
  }
}

// 저장된 job 데이터 캐시
const jobCache = {};
async function ensureJobData(id) {
  if (jobCache[id]) return jobCache[id];
  const res = await fetch('/batch-status?ids=' + id);
  const data = await res.json();
  jobCache[id] = data[id];
  return data[id];
}

async function downloadSingle(id, name) {
  const job = await ensureJobData(id);
  if (!job?.image_b64) return;
  const a = document.createElement('a');
  a.href = 'data:image/jpeg;base64,' + job.image_b64;
  a.download = (name || id) + '_xhs.jpg';
  a.click();
}

async function copyCaptionKo(id) {
  const job = await ensureJobData(id);
  navigator.clipboard.writeText(job?.caption_ko || '');
}
async function copyCaptionZh(id) {
  const job = await ensureJobData(id);
  navigator.clipboard.writeText(job?.caption_zh || '');
}

async function downloadZip() {
  const res = await fetch('/batch-download', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_ids: batchJobIds }),
  });
  const blob = await res.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'xhs_batch.zip';
  a.click();
}

async function copyAllCaptions() {
  const res = await fetch('/batch-status?ids=' + batchJobIds.join(','));
  const data = await res.json();
  const text = batchJobIds.filter(id => data[id]?.status === 'done').map(id => {
    const j = data[id];
    return `【${j.product_name}】\n[KO]\n${j.caption_ko}\n\n[ZH]\n${j.caption_zh}`;
  }).join('\n\n' + '─'.repeat(30) + '\n\n');
  navigator.clipboard.writeText(text);
}

function toggleTemplates() {
  const body = document.getElementById('templateBody');
  const icon = document.getElementById('toggleIcon');
  body.classList.toggle('open');
  icon.classList.toggle('open');
}

async function loadTemplateCount() {
  try {
    const res = await fetch('/templates/list');
    const data = await res.json();
    const count = data.count;
    const badge = document.getElementById('templateBadge');
    badge.textContent = count + '장';
    badge.className = 'template-badge' + (count === 0 ? ' empty' : '');
    document.getElementById('templateInfo').textContent = count > 0 ? `총 ${count}장 저장됨 — 생성 시 랜덤 선택` : '';
    const grid = document.getElementById('templateGrid');
    grid.innerHTML = '';
    (data.items || []).forEach(item => {
      const wrap = document.createElement('div');
      wrap.className = 'template-thumb-wrap';
      const img = document.createElement('img');
      img.className = 'template-thumb';
      img.src = item.thumb;
      const del = document.createElement('button');
      del.className = 'template-thumb-del';
      del.textContent = '✕';
      del.title = item.filename;
      del.onclick = () => deleteTemplate(item.filename);
      wrap.appendChild(img);
      wrap.appendChild(del);
      grid.appendChild(wrap);
    });
  } catch(e) {}
}

async function deleteTemplate(filename) {
  try {
    await fetch('/templates/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename })
    });
    loadTemplateCount();
  } catch(e) {}
}

async function uploadTemplates(e) {
  const files = Array.from(e.target.files);
  if (!files.length) return;
  const progress = document.getElementById('templateProgress');
  progress.textContent = `업로드 중... (0/${files.length})`;
  progress.classList.add('visible');

  // Upload in batches of 10
  const BATCH = 10;
  let done = 0;
  for (let i = 0; i < files.length; i += BATCH) {
    const batch = files.slice(i, i + BATCH);
    const fd = new FormData();
    batch.forEach(f => fd.append('templates', f));
    await fetch('/upload-templates', { method: 'POST', body: fd });
    done += batch.length;
    progress.textContent = `업로드 중... (${Math.min(done, files.length)}/${files.length})`;
  }
  progress.textContent = `완료! ${files.length}장 업로드됨`;
  setTimeout(() => progress.classList.remove('visible'), 3000);
  document.getElementById('templateInput').value = '';
  loadTemplateCount();
}

// 템플릿 드롭존 드래그앤드롭
const tDrop = document.getElementById('templateDrop');
tDrop.addEventListener('dragover', e => { e.preventDefault(); tDrop.classList.add('drag-over'); });
tDrop.addEventListener('dragleave', () => tDrop.classList.remove('drag-over'));
tDrop.addEventListener('drop', e => {
  e.preventDefault(); tDrop.classList.remove('drag-over');
  const files = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith('image/'));
  if (files.length) {
    uploadTemplates({ target: { files } });
  }
});

// ── 스타일 분석 ──────────────────────────────────────
async function analyzeStyle() {
  const btn = document.getElementById('analyzeBtn');
  const status = document.getElementById('profileStatus');
  btn.disabled = true;
  btn.textContent = '분석 중... (20~40초)';
  status.textContent = '';
  status.className = 'profile-status';
  try {
    const res = await fetch('/analyze-style', { method: 'POST' });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    status.textContent = '✓ 스타일 프로필 저장 완료 — 이후 생성에 자동 적용';
    status.className = 'profile-status done';
    btn.textContent = '✦ 스타일 재분석';
  } catch(e) {
    status.textContent = '오류: ' + e.message;
    btn.textContent = '✦ AI 스타일 분석 (생성 품질 향상)';
  }
  btn.disabled = false;
}

async function checkProfileStatus() {
  try {
    const res = await fetch('/analyze-style/status');
    const data = await res.json();
    if (data.exists) {
      const status = document.getElementById('profileStatus');
      status.textContent = '✓ 스타일 프로필 있음 — 생성에 자동 적용 중';
      status.className = 'profile-status done';
      document.getElementById('analyzeBtn').textContent = '✦ 스타일 재분석';
    }
  } catch(e) {}
}

// ── 제품 사진 ──────────────────────────────────────
function onFileChange(e) {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = ev => {
    const dataUrl = ev.target.result;
    productImgMime = file.type;
    productImgB64 = dataUrl.split(',')[1];
    document.getElementById('previewImg').src = dataUrl;
    document.getElementById('previewImg').classList.add('visible');
    document.getElementById('uploadLabel').style.display = 'none';
    document.getElementById('removeImg').classList.add('visible');
    document.getElementById('productImg').classList.add('hidden');
  };
  reader.readAsDataURL(file);
}

function removeImage(e) {
  e.stopPropagation();
  e.preventDefault();
  productImgB64 = null;
  productImgMime = null;
  document.getElementById('productImg').value = '';
  document.getElementById('productImg').classList.remove('hidden');
  document.getElementById('previewImg').classList.remove('visible');
  document.getElementById('uploadLabel').style.display = '';
  document.getElementById('removeImg').classList.remove('visible');
}

// ── 캡션 레퍼런스 ──────────────────────────────────────
function switchCaptionTab(tab) {
  document.getElementById('tabText').classList.toggle('active', tab === 'text');
  document.getElementById('tabImg').classList.toggle('active', tab === 'img');
  document.getElementById('panelText').classList.toggle('active', tab === 'text');
  document.getElementById('panelImg').classList.toggle('active', tab === 'img');
}

function toggleCaptions() {
  document.getElementById('captionBody').classList.toggle('open');
  document.getElementById('captionToggleIcon').classList.toggle('open');
}

async function loadCaptionRefs() {
  try {
    const res = await fetch('/caption-refs');
    const data = await res.json();
    renderCaptionList(data.refs || []);
  } catch(e) {}
}

function renderCaptionList(refs) {
  const badge = document.getElementById('captionBadge');
  badge.textContent = refs.length + '개';
  badge.className = 'template-badge' + (refs.length === 0 ? ' empty' : '');

  const list = document.getElementById('captionList');
  if (refs.length === 0) {
    list.innerHTML = '<div class="caption-empty">아직 레퍼런스가 없어요</div>';
    return;
  }
  list.innerHTML = refs.map((r, i) => `
    <div class="caption-item">
      <button class="caption-item-del" onclick="deleteCaption(${i})">✕</button>
      ${r.replace(/</g,'&lt;').replace(/>/g,'&gt;')}
    </div>
  `).join('');
}

async function uploadCaptionImages(e) {
  const files = Array.from(e.target.files);
  if (!files.length) return;
  const status = document.getElementById('captionExtractStatus');
  status.className = 'caption-extract-status';
  status.textContent = `추출 중... (0/${files.length})`;

  let done = 0, failed = 0;
  for (const file of files) {
    const reader = new FileReader();
    const b64 = await new Promise(res => {
      reader.onload = ev => res(ev.target.result.split(',')[1]);
      reader.readAsDataURL(file);
    });
    try {
      const r = await fetch('/caption-refs/add-image', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_b64: b64, image_mime: file.type })
      });
      const data = await r.json();
      if (data.error) { failed++; }
      else { done++; renderCaptionList(data.refs || []); }
    } catch { failed++; }
    status.textContent = `추출 중... (${done + failed}/${files.length})`;
  }
  status.className = 'caption-extract-status ' + (failed === 0 ? 'ok' : 'err');
  status.textContent = failed === 0
    ? `✓ ${done}개 추출 완료`
    : `${done}개 완료, ${failed}개 실패`;
  document.getElementById('captionImgInput').value = '';
}

// 캡처 드롭존 드래그앤드롭
const cDrop = document.getElementById('captionImgDrop');
cDrop.addEventListener('dragover', e => { e.preventDefault(); cDrop.classList.add('drag-over'); });
cDrop.addEventListener('dragleave', () => cDrop.classList.remove('drag-over'));
cDrop.addEventListener('drop', e => {
  e.preventDefault(); cDrop.classList.remove('drag-over');
  const files = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith('image/'));
  if (files.length) uploadCaptionImages({ target: { files } });
});

async function addCaption() {
  const text = document.getElementById('captionInput').value.trim();
  if (!text) return;
  try {
    const res = await fetch('/caption-refs/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text })
    });
    const data = await res.json();
    renderCaptionList(data.refs || []);
    document.getElementById('captionInput').value = '';
  } catch(e) {}
}

async function deleteCaption(idx) {
  try {
    const res = await fetch('/caption-refs/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ index: idx })
    });
    const data = await res.json();
    renderCaptionList(data.refs || []);
  } catch(e) {}
}

// 제품 사진 드래그앤드롭
const area = document.getElementById('uploadArea');
area.addEventListener('dragover', e => { e.preventDefault(); area.classList.add('drag-over'); });
area.addEventListener('dragleave', () => area.classList.remove('drag-over'));
area.addEventListener('drop', e => {
  e.preventDefault(); area.classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file && file.type.startsWith('image/')) {
    const dt = new DataTransfer(); dt.items.add(file);
    document.getElementById('productImg').files = dt.files;
    onFileChange({ target: { files: [file] } });
  }
});

// ── 생성 ──────────────────────────────────────
async function generate() {
  const product = document.getElementById('productInput').value.trim();
  if (!product) {
    const err = document.getElementById('errorMsg');
    err.textContent = '제품명을 입력하거나 URL로 가져오기를 먼저 해주세요.';
    err.classList.add('visible');
    return;
  }

  document.getElementById('genBtn').disabled = true;
  document.getElementById('loader').classList.add('visible');
  document.getElementById('resultWrap').classList.remove('visible');
  document.getElementById('errorMsg').classList.remove('visible');

  const badge = document.getElementById('templateBadge');
  const hasTemplates = !badge.classList.contains('empty');
  document.getElementById('loaderText').textContent = hasTemplates && productImgB64
    ? '템플릿 스왑 중... (10~20초)'
    : 'AI 이미지 생성 중... (20~40초)';

  try {
    const res = await fetch('/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        product_name: product,
        image_b64: productImgB64,
        image_mime: productImgMime,
        product_description: productDescription
      })
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    document.getElementById('resultImg').src = 'data:image/jpeg;base64,' + data.image_b64;
    document.getElementById('dlBtn').href = 'data:image/jpeg;base64,' + data.image_b64;
    document.getElementById('dlBtn').download = product + '_xhs.jpg';
    document.getElementById('captionKo').textContent = data.caption_ko;
    document.getElementById('captionZh').textContent = data.caption_zh;
    document.getElementById('resultWrap').classList.add('visible');
  } catch (e) {
    const err = document.getElementById('errorMsg');
    err.textContent = '오류: ' + e.message;
    err.classList.add('visible');
  } finally {
    document.getElementById('loader').classList.remove('visible');
    document.getElementById('genBtn').disabled = false;
  }
}

function copy(lang) {
  const text = document.getElementById(lang === 'ko' ? 'captionKo' : 'captionZh').textContent;
  navigator.clipboard.writeText(text).then(() => {
    const btns = document.querySelectorAll('.copy-btn');
    const btn = lang === 'ko' ? btns[0] : btns[1];
    btn.textContent = '완료 ✓';
    setTimeout(() => btn.textContent = '복사', 1500);
  });
}

document.getElementById('productInput').addEventListener('keydown', e => {
  if (e.key === 'Enter') generate();
});

loadTemplateCount();
checkProfileStatus();
loadCaptionRefs();

// ── URL 가져오기 ──────────────────────────────────────
async function fetchFromUrl() {
  const url = document.getElementById('productUrl').value.trim();
  if (!url) return;
  const btn = document.getElementById('fetchBtn');
  const status = document.getElementById('fetchStatus');
  btn.disabled = true;
  status.className = 'fetch-status';
  status.textContent = '가져오는 중... (제품 정보 조회 중)';
  try {
    const res = await fetch('/fetch-product', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url })
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    if (data.title) document.getElementById('productInput').value = data.title;
    productDescription = data.product_description || '';

    const verifiedName = data.title ? `"${data.title}"` : '(제품명 없음)';
    if (data.image_b64) {
      productImgB64 = data.image_b64;
      productImgMime = data.image_mime || 'image/jpeg';
      const dataUrl = `data:${productImgMime};base64,${data.image_b64}`;
      document.getElementById('previewImg').src = dataUrl;
      document.getElementById('previewImg').classList.add('visible');
      document.getElementById('uploadLabel').style.display = 'none';
      document.getElementById('removeImg').classList.add('visible');
      document.getElementById('productImg').classList.add('hidden');
      status.className = 'fetch-status ok';
      status.textContent = `✓ ${verifiedName} — 맞으면 생성 ㄱㄱ, 아니면 수정 후 생성`;
    } else {
      status.className = 'fetch-status ok';
      status.textContent = `✓ ${verifiedName} (이미지는 AI 생성) — 맞으면 생성 ㄱㄱ, 아니면 수정 후 생성`;
    }
  } catch(e) {
    status.className = 'fetch-status err';
    status.textContent = '오류: ' + e.message;
  }
  btn.disabled = false;
}

document.getElementById('productUrl').addEventListener('keydown', e => {
  if (e.key === 'Enter') fetchFromUrl();
});

async function addBatchFromUrls() {
  const raw = document.getElementById('batchUrls').value.trim();
  if (!raw) return;
  const urls = raw.split('\n').map(u => u.trim()).filter(u => u.startsWith('http'));
  if (!urls.length) return;

  const btn = document.getElementById('urlBatchBtn');
  const status = document.getElementById('urlBatchStatus');
  btn.disabled = true;
  status.className = 'fetch-status';
  status.textContent = `가져오는 중... (0/${urls.length})`;

  let done = 0, failed = 0;
  for (const url of urls) {
    try {
      const res = await fetch('/fetch-product', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url })
      });
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      batchItems.push({
        name: data.title || url,
        b64: data.image_b64 || '',
        mime: data.image_mime || 'image/jpeg',
        thumb: data.image_b64 ? `data:${data.image_mime};base64,${data.image_b64}` : ''
      });
      done++;
    } catch { failed++; }
    status.textContent = `가져오는 중... (${done + failed}/${urls.length})`;
  }
  renderBatchList();
  status.className = 'fetch-status ' + (failed === 0 ? 'ok' : 'err');
  status.textContent = failed === 0
    ? `✓ ${done}개 추가 완료`
    : `${done}개 완료, ${failed}개 실패`;
  document.getElementById('batchUrls').value = '';
  btn.disabled = false;
}

// ── 영상 만들기 (Kling AI) ──────────────────────────────────────
let videoPollTimer = null;

async function generateVideo() {
  const btn = document.getElementById('videoBtn');
  const area = document.getElementById('videoArea');
  const loader = document.getElementById('videoLoader');
  const result = document.getElementById('videoResult');
  const errEl = document.getElementById('videoError');
  const product = document.getElementById('productInput').value.trim();
  const imgSrc = document.getElementById('resultImg').src;

  // resultImg src에서 b64 추출
  const b64Match = imgSrc.match(/^data:([^;]+);base64,(.+)$/);
  if (!b64Match) return;
  const [, mime, b64] = b64Match;

  btn.disabled = true;
  btn.textContent = '⏳ 생성 중...';
  area.style.display = 'block';
  loader.style.display = 'flex';
  result.style.display = 'none';
  errEl.style.display = 'none';

  try {
    const res = await fetch('/generate-video', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_b64: b64, image_mime: mime, product_name: product })
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    pollVideoStatus(data.task_id);
  } catch(e) {
    loader.style.display = 'none';
    errEl.textContent = '오류: ' + e.message;
    errEl.style.display = 'block';
    btn.disabled = false;
    btn.textContent = '🎬 영상 만들기';
  }
}

function pollVideoStatus(taskId) {
  let elapsed = 0;
  const loaderText = document.getElementById('videoLoaderText');
  const loader = document.getElementById('videoLoader');
  const result = document.getElementById('videoResult');
  const errEl = document.getElementById('videoError');
  const btn = document.getElementById('videoBtn');

  clearInterval(videoPollTimer);
  videoPollTimer = setInterval(async () => {
    elapsed += 5;
    loaderText.textContent = `영상 생성 중... (${elapsed}초 경과, 보통 1~3분)`;
    try {
      const res = await fetch(`/video-status/${taskId}`);
      const data = await res.json();

      if (data.status === 'succeed') {
        clearInterval(videoPollTimer);
        loader.style.display = 'none';
        const video = document.getElementById('resultVideo');
        video.src = data.video_url;
        document.getElementById('dlVideoBtn').href = data.video_url;
        result.style.display = 'block';
        btn.disabled = false;
        btn.textContent = '🎬 영상 만들기';
      } else if (data.status === 'failed' || data.error) {
        clearInterval(videoPollTimer);
        loader.style.display = 'none';
        errEl.textContent = '영상 생성 실패: ' + (data.error || '알 수 없는 오류');
        errEl.style.display = 'block';
        btn.disabled = false;
        btn.textContent = '🎬 다시 시도';
      }
    } catch(e) {
      // 네트워크 오류 — 계속 폴링
    }
  }, 5000);
}
</script>
</body>
</html>
"""


ALLOWED_EXT = {'.jpg', '.jpeg', '.png', '.webp'}


def get_template_files():
    files = []
    for ext in ALLOWED_EXT:
        files.extend(glob_module.glob(os.path.join(TEMPLATES_DIR, f"*{ext}")))
        files.extend(glob_module.glob(os.path.join(TEMPLATES_DIR, f"*{ext.upper()}")))
    return files


STYLE_PROFILE_PATH = os.path.join(DATA_DIR, "xhs_style_profile.json")
N_REFS = 4  # 생성 시 참고할 템플릿 수


def load_style_profile() -> str:
    """저장된 스타일 프로필 텍스트 반환 (없으면 빈 문자열)"""
    if os.path.exists(STYLE_PROFILE_PATH):
        try:
            with open(STYLE_PROFILE_PATH) as f:
                data = json.load(f)
            return data.get("profile", "")
        except Exception:
            pass
    return ""


def make_swap_prompt() -> str:
    # 첫 번째 이미지 = 제품(왼쪽), 두 번째 이미지 = 레퍼런스(오른쪽)
    # → Gemini UI에서 유저가 쓰는 방식과 동일
    return (
        "두 장의 이미지가 있어. "
        "첫 번째 이미지는 교체할 제품 사진이고, "
        "두 번째 이미지는 실제 UGC 라이프스타일 사진이야. "
        "두 번째 이미지를 기반으로, 두 번째 이미지 속 제품을 첫 번째 이미지의 제품으로 교체해줘. "
        "손 위치, 피부톤, 배경, 조명, 구도, 분위기는 두 번째 이미지 그대로. "
        "제품만 자연스럽게 교체. 실제 UGC 사진처럼 완성해줘."
    )


def make_image_prompt_no_ref(product_name: str = "") -> str:
    product_hint = f" The product is specifically '{product_name}'." if product_name else ""
    return (
        f"A candid UGC-style photo of Korean woman's hands holding a K-beauty skincare product.{product_hint} "
        "Render the actual product packaging clearly and accurately. "
        "Casual home setting, natural window light, cozy bedroom or bathroom background, "
        "authentic personal review feel, no face shown, close-up of hands and product, "
        "xiaohongshu aesthetic, soft natural lighting, "
        "everyday real life feel, vertical photo, phone camera quality"
    )


def load_caption_refs() -> list:
    """저장된 레퍼런스 캡션 목록 반환"""
    if os.path.exists(CAPTION_REFS_PATH):
        try:
            with open(CAPTION_REFS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_caption_refs(refs: list):
    with open(CAPTION_REFS_PATH, "w", encoding="utf-8") as f:
        json.dump(refs, f, ensure_ascii=False, indent=2)


def make_caption_prompt(product_name: str, product_description: str = "") -> str:
    user_refs = load_caption_refs()

    if user_refs:
        # 레퍼런스를 먼저, 지시는 뒤에
        ref_block = "\n\n".join(f"포스트 {i+1}:\n{r}" for i, r in enumerate(user_refs[-15:]))
        return f"""아래는 내가 실제로 올린 포스트들이야:

{ref_block}

---

위 포스트들을 쓴 사람이 '{product_name}'에 대해 쓴다면 어떻게 쓸지 써줘.
{f"[제품 정보 참고] {product_description}" if product_description else ""}

지켜야 할 것:
- 문장 끝맺음 방식, 줄바꿈 위치, 감탄사 빈도, 이모지 사용, 해시태그 개수/형식 — 위 포스트들과 최대한 똑같이
- 제품만 바뀌고 목소리는 그 사람 그대로
- 위 포스트에 나온 특유의 표현, 어투, 리듬을 그대로 살려서
- 본문은 3~5문장 이내로 짧고 임팩트 있게 — 위 레퍼런스 포스트 길이 기준으로
- 중국어 캡션 본문 마지막 줄에 반드시 저장 유도 문구 추가: "觉得有用记得收藏⭐ 下次去韩国直接找这个！" (자연스럽게 변형 가능)

제목 패턴 — 매번 다른 유형 선택 (같은 패턴 반복 금지):
- 발견 스토리형: "随便拿的，竟然是...", "去韩国随手买的，没想到..."
- 기간/결과형: "用了X周，XXX真的变了", "连续用了一个月之后..."
- 현지인 추천형: "韩国皮肤科医生一直在用的...", "韩国女生人手一个的..."
- 감탄형: "啊啊啊啊这个真的绝了！！", "韩国买到最最最好用的..."
- 반전형: "本来不抱希望的，结果...", "最便宜的那个，反而是..."

절대 쓰지 말아야 할 것:
- "먼저", "그리고", "또한", "이처럼", "이와 같이" 같은 접속사
- "추천합니다", "효과적입니다", "도움이 됩니다" 같은 존댓말 설명체
- 불릿 포인트(•, -, *), 번호 매기기, 소제목 형식
- "이번에 소개할" "오늘은" 같은 유튜브 시작 멘트
- 제목에 "最恐怖的" 단독 패턴 과다 사용

[KO]
한국어 캡션

[ZH]
중국어 캡션 (한국어와 같은 느낌, 샤오홍슈 구어체 — 위 포스트 중 중국어 있으면 그 스타일 그대로)

[KO], [ZH] 태그만 유지. 캡션 텍스트만 출력, 다른 말 없이."""
    else:
        # 레퍼런스 없으면 기존 스타일 가이드 사용
        return f"""아래는 샤오홍슈(小红书) K-뷰티 UGC 포스트 레퍼런스야. 이 스타일로 '{product_name}' 포스트를 써줘.

{XHS_STYLE_GUIDE}

절대 쓰지 말아야 할 것:
- "먼저", "그리고", "또한" 같은 접속사
- "추천합니다", "효과적입니다" 같은 설명체
- 불릿 포인트, 번호 매기기, 소제목

[KO]
한국어 캡션

[ZH]
중국어 캡션 (샤오홍슈 현지 구어체)

[KO], [ZH] 태그만 유지. 캡션만 출력."""


def _tpl_mime(path: str) -> str:
    ext = str(os.path.splitext(path)[1]).lower().lstrip('.')
    return "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"


def _build_swap_contents(product_b64: str, product_mime: str, tpl_path: str) -> list:
    """제품 이미지(첫번째) + 레퍼런스(두번째) + 스왑 프롬프트 — Gemini UI 방식과 동일"""
    product_bytes = base64.b64decode(product_b64)
    with open(tpl_path, "rb") as f:
        tpl_bytes = f.read()
    return [
        types.Part.from_bytes(data=product_bytes, mime_type=product_mime),
        types.Part.from_bytes(data=tpl_bytes, mime_type=_tpl_mime(tpl_path)),
        types.Part.from_text(text=make_swap_prompt()),
    ]


def _call_image_api(contents) -> bytes:
    resp = client.models.generate_content(
        model="gemini-2.5-flash-image",
        contents=contents,
        config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
    )
    for part in resp.candidates[0].content.parts:
        if part.inline_data:
            return part.inline_data.data
    raise ValueError("이미지 데이터 없음")


def _call_caption_api(product_name: str, product_description: str = "") -> tuple:
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=make_caption_prompt(product_name, product_description),
        config=types.GenerateContentConfig(temperature=0.7),
    )
    raw = resp.text or ""
    if not raw.strip():
        raise RuntimeError("Gemini API 응답이 비어있습니다. 잠시 후 다시 시도해주세요.")
    if "[KO]" in raw and "[ZH]" in raw:
        ko = raw.split("[KO]")[1].split("[ZH]")[0].strip()
        zh = raw.split("[ZH]")[1].strip()
    else:
        ko, zh = raw, "(중국어 생성 실패)"
    return ko, zh


def process_single_job(job_id: str, product_name: str,
                       image_b64: str, image_mime: str, tpl_path: str):
    """배치 작업 단위 — 스레드에서 실행"""
    try:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "generating_image"

        template_files = get_template_files()
        if template_files and image_b64:
            contents = _build_swap_contents(image_b64, image_mime, tpl_path)
        else:
            contents = make_image_prompt_no_ref()

        image_bytes = _call_image_api(contents)
        out_b64 = base64.b64encode(image_bytes).decode()

        with JOBS_LOCK:
            JOBS[job_id]["status"] = "generating_caption"

        ko, zh = _call_caption_api(product_name)

        with JOBS_LOCK:
            JOBS[job_id].update({
                "status": "done",
                "image_b64": out_b64,
                "caption_ko": ko,
                "caption_zh": zh,
            })
    except Exception as e:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = str(e)


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/upload-templates", methods=["POST"])
def upload_templates():
    files = request.files.getlist("templates")
    saved = 0
    for f in files:
        if f and f.filename:
            ext = str(os.path.splitext(f.filename)[1]).lower()
            if ext in ALLOWED_EXT:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                fname = f"tpl_{ts}_{saved}{ext}"
                f.save(os.path.join(TEMPLATES_DIR, fname))
                saved = saved + 1
    total = len(get_template_files())
    return jsonify({"saved": saved, "total": total})


@app.route("/templates/list")
def templates_list():
    files = get_template_files()
    items = []
    for fpath in sorted(files):
        try:
            with open(fpath, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            ext = os.path.splitext(fpath)[1].lower().lstrip('.')
            mime = "jpeg" if ext in ("jpg", "jpeg") else ext
            items.append({
                "thumb": f"data:image/{mime};base64,{b64}",
                "filename": os.path.basename(fpath)
            })
        except Exception:
            pass
    return jsonify({"count": len(files), "items": items})


@app.route("/templates/delete", methods=["POST"])
def templates_delete():
    filename = (request.get_json() or {}).get("filename", "")
    if not filename or "/" in filename or "\\" in filename:
        return jsonify({"error": "invalid filename"})
    fpath = os.path.join(TEMPLATES_DIR, filename)
    if os.path.exists(fpath):
        os.remove(fpath)
    files = get_template_files()
    return jsonify({"count": len(files)})


@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json()
    product_name = data.get("product_name", "").strip()
    image_b64 = data.get("image_b64")
    image_mime = data.get("image_mime", "image/jpeg")
    product_description = data.get("product_description", "").strip()
    if not product_name:
        return jsonify({"error": "제품명을 입력해주세요"})

    try:
        template_files = get_template_files()
        if template_files and image_b64:
            tpl_path = random.choice(template_files)
            contents = _build_swap_contents(image_b64, image_mime, tpl_path)
        else:
            contents = make_image_prompt_no_ref(product_name)
        image_bytes = _call_image_api(contents)
        out_b64 = base64.b64encode(image_bytes).decode("utf-8")
    except Exception as e:
        return jsonify({"error": f"이미지 생성 실패: {str(e)}"})

    try:
        ko_part, zh_part = _call_caption_api(product_name, product_description)
    except Exception as e:
        return jsonify({"error": f"캡션 생성 실패: {str(e)}"})

    return jsonify({"image_b64": out_b64, "caption_ko": ko_part, "caption_zh": zh_part})


@app.route("/analyze-style/status")
def analyze_style_status():
    return jsonify({"exists": os.path.exists(STYLE_PROFILE_PATH)})


@app.route("/analyze-style", methods=["POST"])
def analyze_style():
    template_files = get_template_files()
    if not template_files:
        return jsonify({"error": "템플릿이 없어요. 먼저 레퍼런스 사진을 업로드해주세요."})

    # 최대 8장 랜덤 샘플링해서 분석
    picks = random.sample(template_files, min(8, len(template_files)))
    contents = []
    for tpl_path in picks:
        with open(tpl_path, "rb") as tf:
            tpl_bytes = tf.read()
        tpl_ext = str(os.path.splitext(tpl_path)[1]).lower().lstrip('.')
        tpl_mime = "image/jpeg" if tpl_ext in ("jpg", "jpeg") else f"image/{tpl_ext}"
        contents.append(types.Part.from_bytes(data=tpl_bytes, mime_type=tpl_mime))

    contents.append(types.Part.from_text(text=(
        "These are UGC reference photos from a K-beauty Xiaohongshu content creator. "
        "Analyze the visual style across all images and describe it in detail for use as a creative brief. "
        "Cover: lighting style & quality, typical backgrounds/settings, hand pose styles, "
        "composition patterns, color temperature & mood, product placement, "
        "photo texture (grain, sharpness), and any recurring props or elements. "
        "Be specific and actionable — this description will be fed to an image generation AI. "
        "Write in English, 150-200 words."
    )))

    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
        )
        profile_text = resp.text.strip()
        with open(STYLE_PROFILE_PATH, "w") as f:
            json.dump({"profile": profile_text, "sample_count": len(picks)}, f, ensure_ascii=False, indent=2)
        return jsonify({"ok": True, "profile": profile_text})
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/caption-refs")
def caption_refs_get():
    return jsonify({"refs": load_caption_refs()})


@app.route("/caption-refs/add", methods=["POST"])
def caption_refs_add():
    text = (request.get_json() or {}).get("text", "").strip()
    if not text:
        return jsonify({"error": "캡션이 비어있어요"})
    refs = load_caption_refs()
    refs.append(text)
    save_caption_refs(refs)
    return jsonify({"refs": refs})


@app.route("/caption-refs/add-image", methods=["POST"])
def caption_refs_add_image():
    body = request.get_json() or {}
    image_b64 = body.get("image_b64", "")
    image_mime = body.get("image_mime", "image/jpeg")
    if not image_b64:
        return jsonify({"error": "이미지가 없어요"})
    try:
        img_bytes = base64.b64decode(image_b64)
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(data=img_bytes, mime_type=image_mime),
                types.Part.from_text(text=(
                    "이 이미지는 샤오홍슈(小红书) 또는 한국 SNS 뷰티 포스트 캡처야. "
                    "포스트의 제목, 본문 텍스트, 해시태그를 원문 그대로 추출해줘. "
                    "이미지에 보이는 텍스트만 정확하게 옮겨적어. "
                    "UI 버튼, 좋아요 수, 닉네임 같은 UI 요소는 제외하고 "
                    "캡션 내용(제목+본문+해시태그)만 출력해. "
                    "다른 설명 없이 캡션 텍스트만 출력할 것."
                )),
            ],
        )
        extracted = resp.text.strip()
        if not extracted:
            return jsonify({"error": "텍스트 추출 실패"})
        refs = load_caption_refs()
        refs.append(extracted)
        save_caption_refs(refs)
        return jsonify({"refs": refs, "extracted": extracted})
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/caption-refs/delete", methods=["POST"])
def caption_refs_delete():
    idx = (request.get_json() or {}).get("index", -1)
    refs = load_caption_refs()
    if 0 <= idx < len(refs):
        refs.pop(idx)
        save_caption_refs(refs)
    return jsonify({"refs": refs})


@app.route("/batch-generate", methods=["POST"])
def batch_generate():
    """여러 제품 한번에 처리 — 각 항목을 별도 스레드에서 병렬 실행"""
    items = (request.get_json() or {}).get("items", [])
    if not items:
        return jsonify({"error": "항목이 없어요"})

    template_files = get_template_files()
    job_ids = []

    for item in items:
        job_id = str(uuid.uuid4())[:8]
        product_name = item.get("product_name", "").strip()
        image_b64 = item.get("image_b64", "")
        image_mime = item.get("image_mime", "image/jpeg")
        tpl_path = random.choice(template_files) if template_files and image_b64 else ""

        with JOBS_LOCK:
            JOBS[job_id] = {
                "status": "queued",
                "product_name": product_name,
                "image_b64": None,
                "caption_ko": None,
                "caption_zh": None,
                "error": None,
            }

        t = threading.Thread(
            target=process_single_job,
            args=(job_id, product_name, image_b64, image_mime, tpl_path),
            daemon=True,
        )
        t.start()
        job_ids.append(job_id)

    return jsonify({"job_ids": job_ids})


@app.route("/batch-status")
def batch_status():
    job_ids = request.args.get("ids", "").split(",")
    with JOBS_LOCK:
        result = {
            jid: {
                "status": JOBS[jid]["status"],
                "product_name": JOBS[jid]["product_name"],
                "error": JOBS[jid].get("error"),
                # 완료됐을 때만 이미지/캡션 포함
                **({"image_b64": JOBS[jid]["image_b64"],
                    "caption_ko": JOBS[jid]["caption_ko"],
                    "caption_zh": JOBS[jid]["caption_zh"]}
                   if JOBS[jid]["status"] == "done" else {})
            }
            for jid in job_ids if jid in JOBS
        }
    return jsonify(result)


@app.route("/batch-download", methods=["POST"])
def batch_download():
    """완료된 작업들을 ZIP으로 묶어 다운로드"""
    job_ids = (request.get_json() or {}).get("job_ids", [])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        with JOBS_LOCK:
            for jid in job_ids:
                job = JOBS.get(jid)
                if not job or job["status"] != "done":
                    continue
                name = job["product_name"].replace(" ", "_")[:30] or jid
                # 이미지
                if job["image_b64"]:
                    zf.writestr(f"{name}_{jid}.jpg", base64.b64decode(job["image_b64"]))
                # 캡션 텍스트
                caption_text = (
                    f"[KO]\n{job['caption_ko']}\n\n[ZH]\n{job['caption_zh']}"
                )
                zf.writestr(f"{name}_{jid}_caption.txt", caption_text.encode("utf-8"))
    buf.seek(0)
    return send_file(buf, mimetype="application/zip",
                     as_attachment=True, download_name="xhs_batch.zip")


def _fetch_og_tags(url: str) -> dict:
    """URL에서 og:title, og:image 추출 (stdlib만 사용)"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,zh-CN;q=0.8,en;q=0.7",
    }
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=12, context=ssl_ctx) as resp:
        raw = resp.read(500_000)
        charset = resp.headers.get_content_charset() or "utf-8"
        final_url = resp.url
    text = raw.decode(charset, errors="replace")

    def get_og(prop):
        for pat in [
            rf'<meta[^>]+property=["\']og:{prop}["\'][^>]+content=["\']([^"\']+)["\']',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:{prop}["\']',
        ]:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                return html_module.unescape(m.group(1).strip())
        return ""

    title = get_og("title")
    image_url = get_og("image")

    if not title:
        m = re.search(r"<title>([^<]+)</title>", text, re.IGNORECASE)
        if m:
            title = html_module.unescape(m.group(1).strip())

    # 사이트 접두사 제거 (Amazon.com :, Coupang:, 올리브영 - 등)
    title = re.sub(r'^(Amazon\.com\s*:\s*|Amazon\s*:\s*|Coupang\s*:\s*|쿠팡\s*:\s*|올리브영\s*[-|]\s*|YesStyle\s*[-|:]\s*|iHerb\s*[-|:]\s*)', '', title, flags=re.IGNORECASE).strip()

    # 상대/프로토콜 상대 URL → 절대 URL
    if image_url and image_url.startswith("//"):
        image_url = "https:" + image_url
    elif image_url and image_url.startswith("/"):
        parsed = urllib.parse.urlparse(final_url)
        image_url = f"{parsed.scheme}://{parsed.netloc}{image_url}"

    # 아마존: 더 큰 이미지로 교체 (._SX38_ 같은 썸네일 → 원본)
    if image_url and "amazon" in image_url:
        image_url = re.sub(r'\._[A-Z]{2}\d+_', '', image_url)

    return {"title": title, "image_url": image_url, "final_url": final_url}


@app.route("/fetch-product", methods=["POST"])
def fetch_product():
    url = (request.get_json() or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "URL을 입력해주세요"})
    if not url.startswith("http"):
        url = "https://" + url
    try:
        og = _fetch_og_tags(url)
        title = og.get("title", "")
        image_url = og.get("image_url", "")
        final_url = og.get("final_url", url)

        # URL slug에서 제품명 추출 — final_url 사용 (리다이렉트 후 URL에 제품명 있음)
        if not title:
            for try_url in [final_url, url]:
                path = urllib.parse.urlparse(try_url).path
                parts = [p for p in path.split("/") if len(p) > 5 and re.search(r"[a-zA-Z]", p) and not re.fullmatch(r"[A-Z0-9]{10}", p)]
                if parts:
                    slug = max(parts, key=len)
                    candidate = re.sub(r"[-_]", " ", slug)
                    candidate = re.sub(r"\b\d+\b", "", candidate).strip().title()
                    if len(candidate) > 3:
                        title = candidate
                        break

        # 제목 못 가져오면 Gemini에게 ASIN/URL로 제품 식별 요청
        product_description = ""
        if not title:
            try:
                asin_m = re.search(r"/dp/([A-Z0-9]{10})", url)
                asin_hint = f"Amazon ASIN: {asin_m.group(1)}\n" if asin_m else ""
                gemini_resp = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=(
                        f"{asin_hint}URL: {url}\n\n"
                        "이 URL/ASIN의 K-beauty 제품을 식별해줘. 아래 형식으로만 답해줘:\n"
                        "NAME: [브랜드 + 정확한 제품명]\n"
                        "DESC: [주요 성분, 핵심 효과, 타겟 피부 고민 2문장]\n\n"
                        "모르면: NAME: UNKNOWN"
                    ),
                    config=types.GenerateContentConfig(temperature=0.1),
                )
                raw = gemini_resp.text.strip()
                name_m2 = re.search(r"NAME:\s*(.+)", raw)
                desc_m = re.search(r"DESC:\s*(.+)", raw, re.DOTALL)
                if name_m2:
                    candidate = name_m2.group(1).strip()
                    if candidate.upper() != "UNKNOWN":
                        title = candidate
                if desc_m:
                    product_description = desc_m.group(1).strip()
            except Exception:
                pass

        img_b64 = ""
        img_mime = "image/jpeg"
        if image_url:
            try:
                img_headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Referer": url,
                    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                }
                img_ssl = ssl.create_default_context()
                img_ssl.check_hostname = False
                img_ssl.verify_mode = ssl.CERT_NONE
                img_req = urllib.request.Request(image_url, headers=img_headers)
                with urllib.request.urlopen(img_req, timeout=10, context=img_ssl) as img_resp:
                    img_bytes = img_resp.read()
                    img_mime = img_resp.headers.get_content_type() or "image/jpeg"
                img_b64 = base64.b64encode(img_bytes).decode()
            except Exception:
                pass  # 이미지 실패해도 계속

        # 제목 있으면 추가로 제품 설명 조회 (이미 Gemini 조회한 경우 스킵)
        if title and not product_description:
            try:
                info_resp = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=(
                        f"K-beauty product: {title}\n\n"
                        "이 제품에 대해 2~3문장으로 설명해줘: 주요 성분, 핵심 효과, 타겟 피부 고민. "
                        "모르면 이 종류 제품이 일반적으로 어떤 효과인지 설명해도 돼. "
                        "한국어로, 간결하게."
                    ),
                    config=types.GenerateContentConfig(temperature=0.3),
                )
                product_description = info_resp.text.strip()
            except Exception:
                pass

        return jsonify({
            "title": title,
            "image_b64": img_b64,
            "image_mime": img_mime,
            "product_description": product_description,
            "image_fetched": bool(img_b64),
        })
    except Exception as e:
        return jsonify({"error": f"가져오기 실패: {str(e)}"})


KLING_BASE = "https://api.klingai.com"

def _kling_jwt() -> str:
    access_key = os.getenv("KLING_ACCESS_KEY", "")
    secret_key = os.getenv("KLING_SECRET_KEY", "")
    now = int(time.time())
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps({"iss": access_key, "exp": now + 1800, "nbf": now - 5}).encode()
    ).rstrip(b"=").decode()
    sig_input = f"{header}.{payload}".encode()
    sig = hmac.new(secret_key.encode(), sig_input, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
    return f"{header}.{payload}.{sig_b64}"


def _kling_request(method: str, path: str, body: dict = None):
    token = _kling_jwt()
    url = KLING_BASE + path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
        return json.loads(resp.read().decode())


@app.route("/generate-video", methods=["POST"])
def generate_video():
    data = request.get_json() or {}
    image_b64 = data.get("image_b64", "")
    image_mime = data.get("image_mime", "image/jpeg")
    product_name = data.get("product_name", "K-beauty product")
    if not image_b64:
        return jsonify({"error": "이미지가 없어요"})
    try:
        prompt = (
            "Almost still image. A hand holding this skincare product, barely moving. "
            "Very subtle natural breathing motion only. Product stays perfectly intact and undistorted. "
            "Gentle soft focus shift. Natural indoor window light. "
            "Casual UGC phone footage feel, minimal movement."
        )
        result = _kling_request("POST", "/v1/videos/image2video", {
            "model_name": "kling-v1",
            "image": image_b64,
            "prompt": prompt,
            "negative_prompt": "fast motion, rotation, morphing, distortion, bending, warping, deformation, dramatic movement, zoom, pan, text overlay, watermark",
            "cfg_scale": 0.5,
            "mode": "std",
            "duration": "5",
        })
        task_id = result.get("data", {}).get("task_id") or result.get("task_id")
        if not task_id:
            return jsonify({"error": f"task_id 없음: {result}"})
        return jsonify({"task_id": task_id})
    except Exception as e:
        return jsonify({"error": f"Kling 요청 실패: {str(e)}"})


@app.route("/video-status/<task_id>", methods=["GET"])
def video_status(task_id):
    try:
        result = _kling_request("GET", f"/v1/videos/image2video/{task_id}")
        task = result.get("data", result)
        status = task.get("task_status", "")
        if status == "succeed":
            videos = task.get("task_result", {}).get("videos", [])
            video_url = videos[0].get("url") if videos else ""
            return jsonify({"status": "succeed", "video_url": video_url})
        elif status == "failed":
            msg = task.get("task_status_msg", "알 수 없는 오류")
            return jsonify({"status": "failed", "error": msg})
        else:
            return jsonify({"status": status})
    except Exception as e:
        return jsonify({"error": str(e)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5002))
    print(f"✦ XHS 생성기 실행 중 → http://localhost:{port}")
    app.run(debug=True, port=port)
