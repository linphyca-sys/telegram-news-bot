# -*- coding: utf-8 -*-
"""
텔레그램 키워드 뉴스봇
- 설정한 키워드로 뉴스를 주기적으로 검색해서 새 기사만 텔레그램으로 전송
- 뉴스 소스: 네이버 뉴스 검색 API (키가 있으면) / 구글 뉴스 RSS (키가 없으면)
"""

import csv
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# 파이프/백그라운드 실행 시에도 로그가 바로 보이게 라인 버퍼링
try:
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
except AttributeError:
    pass

# ── 설정 ──────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

def parse_keyword_line(line: str) -> dict:
    """'검색어 | 제외: a, b | 포함: x, y | 보호: p, q' 형식 파싱. 필터는 선택사항."""
    parts = [p.strip() for p in line.split("|")]
    kw = {"query": parts[0], "include": [], "exclude": [], "protect": []}
    for part in parts[1:]:
        if ":" not in part:
            continue
        label, words = part.split(":", 1)
        words = [w.strip() for w in words.split(",") if w.strip()]
        if label.strip() in ("제외", "exclude"):
            kw["exclude"] = words
        elif label.strip() in ("포함", "include"):
            kw["include"] = words
        elif label.strip() in ("보호", "protect"):
            kw["protect"] = words
    return kw

KEYWORDS = [
    parse_keyword_line(k.strip())
    for k in os.getenv("KEYWORDS", "").split(",")
    if k.strip()
]
if not KEYWORDS:
    # 환경변수에 없으면 keywords.txt에서 읽기 (한 줄에 하나씩)
    kw_file = BASE_DIR / "keywords.txt"
    if kw_file.exists():
        KEYWORDS = [
            parse_keyword_line(line.strip())
            for line in kw_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

def passes_filter(article: dict, kw: dict) -> bool:
    """제외 단어가 있으면 탈락 — 단, 보호 단어가 함께 있으면 통과.
    포함 단어가 지정됐으면 하나는 있어야 통과."""
    text = f"{article['title']} {article['description']}"
    if any(w in text for w in kw["exclude"]) and not any(w in text for w in kw["protect"]):
        return False
    if kw["include"] and not any(w in text for w in kw["include"]):
        return False
    return True
INTERVAL_MINUTES = float(os.getenv("INTERVAL_MINUTES", "5"))
MAX_PER_KEYWORD = int(os.getenv("MAX_PER_KEYWORD", "5"))      # 1회 검색당 키워드별 최대 전송 수 (0 이하 = 무제한)
FIRST_RUN_SEND = int(os.getenv("FIRST_RUN_SEND", "3"))        # 최초 실행 시 키워드별 전송 수
MAX_AGE_HOURS = float(os.getenv("MAX_AGE_HOURS", "6"))        # 이보다 오래된 기사는 기록만 하고 전송 안 함

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "").strip()
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "").strip()
USE_NAVER = bool(NAVER_CLIENT_ID and NAVER_CLIENT_SECRET)

SEEN_FILE = BASE_DIR / "seen_links.json"
CSV_FILE = BASE_DIR / "articles.csv"
KST = timezone(timedelta(hours=9))

def log_to_csv(keyword: str, article: dict) -> None:
    """전송한 기사를 articles.csv에 누적 기록 (엑셀에서 바로 열림)"""
    new_file = not CSV_FILE.exists()
    # 새 파일일 때만 BOM(utf-8-sig)을 써서 엑셀이 한글을 제대로 인식하게 함
    with CSV_FILE.open("a", newline="", encoding="utf-8-sig" if new_file else "utf-8") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(["전송시각", "키워드", "기사시각", "제목", "요약", "링크"])
        writer.writerow([
            datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
            keyword,
            article["published"].strftime("%Y-%m-%d %H:%M"),
            article["title"],
            article["description"],
            article["link"],
        ])

# ── 본 기사 중복 관리 ─────────────────────────────────────────────
def load_seen() -> dict:
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}

def save_seen(seen: dict) -> None:
    # 오래된 항목부터 정리해서 파일이 무한히 커지지 않게 함
    if len(seen) > 3000:
        items = sorted(seen.items(), key=lambda x: x[1])[-2000:]
        seen = dict(items)
    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False), encoding="utf-8")

# ── 뉴스 검색 ─────────────────────────────────────────────────────
def clean_text(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    # 일부 언론사는 이중 이스케이프된 제목을 줌 (&amp;quot; → &quot;) — 안정될 때까지 해제
    for _ in range(3):
        unescaped = html.unescape(s)
        if unescaped == s:
            break
        s = unescaped
    return s.strip()

# 네이버 API가 긴 제목을 "..."로 잘라서 주므로, 기사 페이지의 og:title에서 원제목을 가져옴
def _og_meta(head: str, prop: str):
    # content 속성값은 여는 따옴표와 같은 종류의 따옴표까지 읽음
    # (제목 안에 다른 종류 따옴표가 있어도 잘리지 않게)
    for pattern in (
        re.compile(rf'<meta[^>]+property=["\']og:{prop}["\'][^>]+content=(["\'])(?P<v>.*?)\1', re.I),
        re.compile(rf'<meta[^>]+content=(["\'])(?P<v>.*?)\1[^>]+property=["\']og:{prop}["\']', re.I),
    ):
        m = pattern.search(head)
        if m:
            value = clean_text(m.group("v"))
            if value:
                return value
    return None

def strip_site_name(title: str, site_name) -> str:
    """제목 끝의 '| 매체명' / '- 매체명' 꼬리표 제거.
    페이지가 밝힌 매체명(og:site_name)과 정확히 일치할 때만 잘라서 오탐 방지."""
    if not site_name:
        return title
    m = re.match(r"^(.*\S)\s*[|\-–—:]\s*(.+?)$", title)
    if m and m.group(2).strip().lower() == site_name.strip().lower():
        return m.group(1).strip(" |-–—:")
    return title

# 일부 언론사가 서버(데이터센터) 접속을 차단하므로 실제 브라우저처럼 요청
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

def _fetch_og_title(url: str):
    try:
        resp = requests.get(url, timeout=10, stream=True, headers=BROWSER_HEADERS)
        if not resp.ok:
            resp.close()
            return None
        chunk = next(resp.iter_content(65536), b"") or b""
        resp.close()
    except requests.RequestException:
        return None
    for enc in ("utf-8", "euc-kr"):
        head = chunk.decode(enc, errors="ignore")
        title = _og_meta(head, "title")
        if title:
            return strip_site_name(title, _og_meta(head, "site_name"))
    return None

def fetch_full_title(article: dict):
    """기사 원문 → (실패 시) 네이버 뉴스 페이지 순으로 og:title 추출. 실패하면 None."""
    title = _fetch_og_title(article["link"])
    if title:
        return title
    naver_link = article.get("naver_link")
    if naver_link and naver_link != article["link"]:
        title = _fetch_og_title(naver_link)
        if title:
            return title
    print(f"  [!] 제목 복원 실패: {article['link']}")
    return None

def search_naver(keyword: str) -> list:
    """네이버 뉴스 검색 API (최신순)"""
    resp = requests.get(
        "https://openapi.naver.com/v1/search/news.json",
        params={"query": keyword, "display": 100, "sort": "date"},
        headers={
            "X-Naver-Client-Id": NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
        },
        timeout=15,
    )
    resp.raise_for_status()
    articles = []
    for item in resp.json().get("items", []):
        try:
            pub = parsedate_to_datetime(item["pubDate"]).astimezone(KST)
        except (KeyError, ValueError, TypeError):
            pub = datetime.now(KST)
        articles.append({
            "title": clean_text(item.get("title")),
            "description": clean_text(item.get("description")),
            "link": item.get("originallink") or item.get("link"),
            "naver_link": item.get("link"),  # 제목 복원 2차 시도용
            "published": pub,
        })
    return articles

def search_google_rss(keyword: str) -> list:
    """구글 뉴스 RSS 검색 (API 키 불필요)"""
    import feedparser
    url = (
        "https://news.google.com/rss/search"
        f"?q={requests.utils.quote(keyword)}&hl=ko&gl=KR&ceid=KR:ko"
    )
    feed = feedparser.parse(url)
    articles = []
    for entry in feed.entries[:20]:
        if getattr(entry, "published_parsed", None):
            pub = datetime.fromtimestamp(
                time.mktime(entry.published_parsed), tz=timezone.utc
            ).astimezone(KST)
        else:
            pub = datetime.now(KST)
        articles.append({
            "title": clean_text(entry.get("title", "")),
            "description": clean_text(entry.get("summary", ""))[:200],
            "link": entry.get("link", ""),
            "published": pub,
        })
    articles.sort(key=lambda a: a["published"], reverse=True)
    return articles

def search_news(keyword: str) -> list:
    return search_naver(keyword) if USE_NAVER else search_google_rss(keyword)

# ── 텔레그램 전송 ─────────────────────────────────────────────────
def format_kst(dt: datetime) -> str:
    """예: 2026.07.02. 오전 9:04"""
    ampm = "오전" if dt.hour < 12 else "오후"
    hour12 = dt.hour % 12 or 12
    return f"{dt.year}.{dt.month:02d}.{dt.day:02d}. {ampm} {hour12}:{dt.minute:02d}"

def build_message(article: dict) -> str:
    title = html.escape(article["title"])
    desc = html.escape(article["description"])
    if len(desc) > 300:
        desc = desc[:300] + "..."
    return (
        f"<b>{title}</b>\n\n"
        f"{desc}\n\n"
        f"📅 {format_kst(article['published'])}\n"
        f"🔗 <a href=\"{article['link']}\">뉴스 전문 보기</a>"
    )

def send_telegram(text: str) -> bool:
    for attempt in range(3):
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": CHAT_ID,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                },
                timeout=30,
            )
            if resp.ok:
                return True
            if resp.status_code == 429:  # rate limit — 안내된 시간만큼 대기 후 재시도
                wait = resp.json().get("parameters", {}).get("retry_after", 5)
                time.sleep(wait + 1)
                continue
            print(f"  [!] 텔레그램 전송 실패: {resp.status_code} {resp.text}")
            return False
        except requests.RequestException as e:
            print(f"  [!] 텔레그램 전송 오류 (시도 {attempt + 1}/3): {e}")
            time.sleep(3)
    return False

# ── 메인 루프 ─────────────────────────────────────────────────────
def check_once(seen: dict, first_run: bool) -> None:
    now = datetime.now(KST).strftime("%H:%M:%S")
    for kw in KEYWORDS:
        try:
            articles = search_news(kw["query"])
        except Exception as e:
            print(f"[{now}] '{kw['query']}' 검색 실패: {e}")
            continue

        fresh = [a for a in articles if a["link"] and a["link"] not in seen]
        matched = [a for a in fresh if passes_filter(a, kw)]
        cutoff = datetime.now(KST) - timedelta(hours=MAX_AGE_HOURS)
        recent = [a for a in matched if a["published"] >= cutoff]
        if first_run:
            limit = FIRST_RUN_SEND
        else:
            limit = MAX_PER_KEYWORD if MAX_PER_KEYWORD > 0 else None  # None = 무제한
        to_send = recent[:limit]

        # 전송하지 않는 기사(필터 탈락·오래된 기사 포함)도 '본 것'으로 기록
        stamp = time.time()
        for a in fresh:
            seen[a["link"]] = stamp

        filtered_out = len(fresh) - len(matched)
        too_old = len(matched) - len(recent)
        print(
            f"[{now}] '{kw['query']}': 새 기사 {len(fresh)}건"
            f" (필터 제외 {filtered_out}건, 오래된 기사 제외 {too_old}건), {len(to_send)}건 전송"
        )
        save_seen(seen)  # 전송 도중 중단돼도 중복 전송을 막기 위해 미리 저장
        for a in reversed(to_send):  # 오래된 것부터 전송
            if a["title"].endswith(("...", "…")):  # 잘린 제목이면 원제목 시도
                full_title = fetch_full_title(a)
                if full_title:
                    a["title"] = full_title
            if send_telegram(build_message(a)):
                log_to_csv(kw["query"], a)
            time.sleep(3)  # 텔레그램은 같은 채널에 분당 ~20건 제한 — 3초 간격이면 안전

def main() -> None:
    if not BOT_TOKEN or not CHAT_ID:
        sys.exit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID를 .env에 설정하세요. (README 참고)")
    if not KEYWORDS:
        sys.exit("KEYWORDS를 .env(또는 keywords.txt)에 설정하세요. 예: KEYWORDS=동료지원,장애인복지")

    once = "--once" in sys.argv  # GitHub Actions 등 외부 스케줄러용: 1회 검색 후 종료

    source = "네이버 뉴스 API" if USE_NAVER else "구글 뉴스 RSS"
    mode = "1회 실행" if once else f"{INTERVAL_MINUTES}분 주기"
    kw_names = [k["query"] for k in KEYWORDS]
    print(f"뉴스봇 시작 — 소스: {source}, 키워드: {kw_names}, 모드: {mode}")

    seen = load_seen()
    first_run = not seen

    if once:
        check_once(seen, first_run)
        return

    while True:
        try:
            check_once(seen, first_run)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"[!] 오류 발생, 다음 주기에 재시도: {e}")
        first_run = False
        time.sleep(INTERVAL_MINUTES * 60)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n뉴스봇 종료")
