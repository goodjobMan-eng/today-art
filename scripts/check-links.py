#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
「오늘 미술 뭐 하지?」 도안·관련 링크 점검 (하루 1번 실행)

발행된 data/detail/*.json 의 도안 링크(templateLinks)·관련 링크(relatedLinks)를 열어 보고
결과를 data/link-status.json 에 쌓는다.  {링크: {"ok": true/false/null, "checkedAt": "YYYY-MM-DD", "fails": 연속 죽은 횟수, "why": 이유}}
  ok=true  살아 있음 (fails 0)
  ok=false 죽음 (fails 는 서로 다른 날 연속으로 죽은 횟수. 같은 날 다시 돌려도 늘지 않음)
  ok=null  판단 못 함(막힘·시간 초과 등). 이전 기록을 그대로 둔다
build-art-data.py 는 fails 가 2 이상인 링크를 발행에서 빼고, 도안 링크가 모두 빠지면 '도안 없음'으로 바꾼다.
이미 발행에서 빠진 죽은 링크도 계속 다시 확인하므로, 글이 되살아나면 다음 발행 때 돌아온다.

죽음 판정
  - HTTP 404·410 등 4xx(401·403·407·429 제외: 로봇 차단일 수 있어 '판단 못 함'), 5xx, 주소(도메인) 없음
  - 네이버 블로그: 200을 주면서 "게시물이 삭제되었거나…" 알림만 띄우는 경우가 많아,
    PostView.naver?blogId=X&logNo=Y 를 받아 본문이 없고 알림·짧은 본문에 '존재하지 않'·'삭제'·'비공개' 등이 있으면 죽음
  - 티스토리 등: 404, 또는 제목·짧은 본문에 '삭제된 글'·'존재하지 않는'·'비공개 글' 등
  - 그 밖의 사이트: 제목이 'Not Found'·'페이지를 찾을 수 없' 같은 소프트 404

사용
  python scripts/check-links.py                 # 점검하고 data/link-status.json 저장
  python scripts/check-links.py --url 주소 ...   # 몇 개만 판정해 보기(저장 안 함)
  python scripts/check-links.py --force         # 오늘 이미 확인한 링크도 다시 (fails 는 하루 1번만 늘어남)
"""
import argparse
import collections
import concurrent.futures
import datetime as dt
import glob
import json
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data"
STATUS = OUT / "link-status.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
MAX_BYTES = 1_500_000
TIMEOUT = 20

DEAD_WORDS = ["존재하지 않", "삭제되었", "삭제된", "비공개", "찾을 수 없"]
NAVER_DEAD_WORDS = DEAD_WORDS + ["권한이 없", "접근할 수 없", "볼 수 없"]  # 네이버는 비공개·이웃 공개 글도 일반 방문자에게는 죽은 링크
SOFT404_TITLE = re.compile(r"(\b404\b|not found|page not found|페이지를 찾을 수 없|존재하지 않는|삭제된 (글|게시물|페이지)|비공개 (글|게시물)|"
                           r"ページが見つかりません|お探しのページ|seite nicht gefunden|page introuvable|pagina non trovata|página no encontrada)", re.I)
NAVER_BODY = re.compile(r"se-main-container|postViewArea|se_component_wrap|postListBody|id=\"post-view|class=\"post-view|_postView")
UNKNOWN_CODES = {401, 403, 407, 429, 999}

_host_locks = collections.defaultdict(lambda: threading.BoundedSemaphore(2))
_host_guard = threading.Lock()


def host_sem(host):
    with _host_guard:
        return _host_locks[host]


def fetch(url):
    """(상태 코드, 본문 텍스트, 최종 주소). 연결 실패는 예외"""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                                               "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read(MAX_BYTES)
            cs = r.headers.get_content_charset() or "utf-8"
            return r.status, body.decode(cs, "replace"), r.geturl()
    except urllib.error.HTTPError as e:
        try:
            body = e.read(200_000).decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body, url


def naver_postview(url):
    """네이버 블로그 글 주소 → PostView.naver 주소 (글 번호가 없으면 블로그 목록 주소). 네이버 블로그가 아니면 None"""
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    if host not in ("blog.naver.com", "m.blog.naver.com"):
        return None
    q = urllib.parse.parse_qs(p.query)
    blog = (q.get("blogId") or [""])[0]
    log = (q.get("logNo") or [""])[0]
    parts = [x for x in p.path.split("/") if x]
    if not blog and parts and not parts[0].lower().startswith(("postview", "postlist", "prologue")):
        blog = parts[0]
        if len(parts) > 1 and parts[1].isdigit():
            log = parts[1]
    if not blog:
        return None
    if log:
        return f"https://blog.naver.com/PostView.naver?blogId={urllib.parse.quote(blog)}&logNo={urllib.parse.quote(log)}"
    return f"https://blog.naver.com/PostList.naver?blogId={urllib.parse.quote(blog)}"


def page_title(body):
    m = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def alerts(body):
    return " ".join(re.findall(r"alert\(\s*[\"']([^\"']{0,300})[\"']", body))


def visible_text(body):
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.I | re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)).strip()


def has_dead_word(t, words=DEAD_WORDS):
    return next((w for w in words if w in t), "")


def check(url):
    """('ok'|'dead'|'unknown', 이유)"""
    target = naver_postview(url) or url
    host = (urllib.parse.urlsplit(target).hostname or "").lower()
    sem = host_sem(host)
    last = None
    for attempt in range(2):
        with sem:
            try:
                code, body, final = fetch(target)
                last = None
            except urllib.error.URLError as e:
                reason = getattr(e, "reason", e)
                if isinstance(reason, socket.gaierror):
                    return "dead", "주소(도메인) 없음"
                last = f"연결 실패: {reason}"
            except (socket.timeout, TimeoutError):
                last = "시간 초과"
            except Exception as e:
                last = f"오류: {e}"
        if last is None:
            break
        time.sleep(2)
    if last is not None:
        return "unknown", last

    if code in UNKNOWN_CODES:
        return "unknown", f"HTTP {code} (막힘)"
    if code >= 400:
        return "dead", f"HTTP {code}"

    title = page_title(body)
    if naver_postview(url):
        if NAVER_BODY.search(body):
            return "ok", "HTTP 200"
        w = has_dead_word(alerts(body), NAVER_DEAD_WORDS) or (has_dead_word(visible_text(body), NAVER_DEAD_WORDS) if len(body) < 8000 else "")
        if w:
            return "dead", f"네이버: '{w}' ({(alerts(body) or title)[:40]})"
        if "PostList.naver" in target:  # 블로그 첫 화면 주소: 블로그가 있으면 200
            return "ok", "HTTP 200"
        return "unknown", "네이버: 본문을 찾지 못함"

    if SOFT404_TITLE.search(title):
        return "dead", f"제목: {title[:40]}"
    if "로그인" in alerts(body):  # 회원 전용 게시판: 글은 있다
        return "ok", "로그인 필요"
    if "tistory.com" in host or len(body) < 8000:
        w = has_dead_word(alerts(body)) or (has_dead_word(visible_text(body)) if len(body) < 8000 else "")
        if w and ("tistory.com" in host or alerts(body)):
            return "dead", f"'{w}'"
    return "ok", f"HTTP {code}"


def published_links():
    links = set()
    for f in sorted(glob.glob(str(OUT / "detail" / "*.json"))):
        try:
            for d in json.loads(Path(f).read_text(encoding="utf-8")).values():
                for k in ("templateLinks", "relatedLinks"):
                    links.update(u for u in (d.get(k) or []) if isinstance(u, str) and u.startswith("http"))
        except Exception as e:
            print("읽기 실패:", f, e)
    return links


def main():
    ap = argparse.ArgumentParser(description="도안·관련 링크 점검")
    ap.add_argument("--url", nargs="*", help="이 주소들만 판정해 보기(저장하지 않음)")
    ap.add_argument("--workers", type=int, default=6, help="동시에 여는 요청 수 (기본 6, 한 사이트에는 2개까지)")
    ap.add_argument("--force", action="store_true", help="오늘 이미 확인한 링크도 다시 확인")
    a = ap.parse_args()

    if a.url:
        with concurrent.futures.ThreadPoolExecutor(max(1, a.workers)) as ex:
            for u, (st, why) in zip(a.url, ex.map(check, a.url)):
                print(f"{st:7s} {u}  ({why})")
        return

    try:
        status = json.loads(STATUS.read_text(encoding="utf-8"))
    except Exception:
        status = {}
    today = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=9)).date().isoformat()
    pub = published_links()
    # 발행 중인 링크 + 이미 빠진 죽은 링크(되살아났는지 확인)
    todo = sorted(pub | {u for u, v in status.items() if isinstance(v, dict) and v.get("ok") is False})
    if not a.force:
        todo = [u for u in todo if (status.get(u) or {}).get("checkedAt") != today]
    print(f"점검할 링크 {len(todo)}개 (발행 중 {len(pub)}개)")

    t0 = time.time()
    counts = collections.Counter()
    newly_dead = []
    with concurrent.futures.ThreadPoolExecutor(max(1, a.workers)) as ex:
        for u, (st, why) in zip(todo, ex.map(check, todo)):
            prev = status.get(u) or {}
            counts[st] += 1
            if st == "ok":
                status[u] = {"ok": True, "checkedAt": today, "fails": 0}
            elif st == "dead":
                same_day = prev.get("checkedAt") == today
                fails = int(prev.get("fails") or 0) if prev.get("ok") is False else 0
                status[u] = {"ok": False, "checkedAt": today, "fails": fails if same_day and fails else fails + 1, "why": why}
                newly_dead.append((u, why, status[u]["fails"]))
            else:
                status[u] = {"ok": prev.get("ok"), "checkedAt": prev.get("checkedAt", today), "fails": int(prev.get("fails") or 0),
                             "why": why}
    # 발행에도 없고 살아 있는 기록은 지운다(파일이 커지지 않게)
    status = {u: v for u, v in status.items() if u in pub or (isinstance(v, dict) and v.get("ok") is False)}
    OUT.mkdir(parents=True, exist_ok=True)
    STATUS.write_text("{\n" + ",\n".join(json.dumps(k, ensure_ascii=False) + ":" + json.dumps(status[k], ensure_ascii=False)
                                         for k in sorted(status)) + "\n}\n", encoding="utf-8")
    gone = sum(1 for v in status.values() if v.get("ok") is False and int(v.get("fails") or 0) >= 2)
    print(f"살아 있음 {counts['ok']}개, 죽음 {counts['dead']}개, 판단 못 함 {counts['unknown']}개 ({time.time() - t0:.0f}초)")
    print(f"발행에서 빠질 링크(연속 2번 이상 죽음): {gone}개")
    for u, why, f in newly_dead:
        print(f"  죽음 {f}회  {u}  ({why})")
    for u, v in sorted(status.items()):
        if v.get("ok") is None:
            print(f"  판단 못 함  {u}  ({v.get('why')})")


if __name__ == "__main__":
    sys.exit(main())
