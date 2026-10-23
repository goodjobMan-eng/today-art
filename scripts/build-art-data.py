#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
「오늘 미술 뭐 하지?」 사이트 데이터 만들기

수집 DB(art.db, 읽기 전용) 또는 구글 시트(Apps Script)에서 수업을 받아 사이트가 읽는 정적 파일로 나눠 저장한다.
  data/meta.json            : 버전(만든 시각)·건수·묶음 수
  data/list-{ko,en,ja}.json : 목록·검색용 (언어별 제목·요약·필수 준비물 + 분류)
  data/detail/NN.json       : 상세 화면용 (학습목표·준비물·사전 준비·활동 흐름·팁·도안 링크…), 수업 id로 64묶음
  data/social.json, data/reviews/NN.json : 추천·저장 수와 후기 (Apps Script 주소가 있을 때만)
  data/video-status.json    : 영상 점검 기록(유튜브 oEmbed, CHECK_VIDEOS=1 일 때). 두 번 연속 안 나오면 목록에서 뺀다
  a/{id}.html               : 수업별 공유 미리보기(카톡용). 검색엔진에는 안 나오게(noindex)
  sitemap.xml, robots.txt   : 첫 화면만

사용
  python scripts/build-art-data.py                       # 기본: ../today-art/art.db 에서
  python scripts/build-art-data.py --db 경로/art.db
  python scripts/build-art-data.py --source sheet        # 구글 시트(GAS_URL)에서
  python scripts/build-art-data.py --export-tsv 수업.tsv  # 시트 「수업」 탭에 붙여 넣을 표도 함께 저장

환경변수
  SITE_URL      사이트 주소 (기본: https://today-art.example/ 자리표시자)
  GAS_URL       Apps Script 웹 앱 주소. 비어 있으면 후기·추천 파일은 만들지 않음
  ART_DB        art.db 경로 (--db 와 같음)
  CHECK_VIDEOS  1 이면 유튜브 oEmbed로 영상이 살아 있는지 점검 (기본 0)
  CHECK_PER_RUN 한 번에 점검할 영상 수 (기본 1000)

art.db 는 수집기가 매일 쓰는 파일이므로 절대 쓰지 않는다: 읽기 전용으로 연 뒤 메모리로 통째 복사해서 읽는다.
"""
import argparse
import concurrent.futures
import csv
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data"
BUCKETS = 64
LANGS = ["ko", "en", "ja"]
SITE_URL = (os.environ.get("SITE_URL") or "https://today-art.example/").rstrip("/") + "/"
GAS_URL = (os.environ.get("GAS_URL") or "").strip()
CHECK_VIDEOS = os.environ.get("CHECK_VIDEOS", "0") == "1"
CHECK_PER_RUN = int(os.environ.get("CHECK_PER_RUN") or 1000)
SITE_NAME = "오늘 미술 뭐 하지?"

# ---- 시트 「수업」 탭과 같은 칸 이름(평평한 한 줄). DB에서 읽어도 이 모양으로 바꾼 뒤 발행한다 ----
TEXT_FIELDS = {  # 칸 앞부분 → DB result 키
    "title": "title", "summary": "summary", "objective": "objective",
    "matReq": "materials_required", "matOpt": "materials_optional", "prep": "preparation",
    "tips": "classroom_tips", "linkNote": "link_note",
}
SHEET_HEADERS = (["id", "status", "region", "videoId", "channel", "channelId", "videoTitle", "seconds",
                  "area", "activityType", "grade", "periods", "prepLevel", "cleanupLevel", "safety",
                  "screenNeeded", "season", "templateStatus", "templateFormat",
                  "templateLinks", "relatedLinks", "tags", "evidence"]
                 + [f"{f}_{l}" for f in list(TEXT_FIELDS) + ["outline"] for l in LANGS]
                 + ["source", "createdAt", "updatedAt"])

# 목록 파일에 들어가는 칸(언어 공통) + 언어별 칸
LIST_COMMON = ["id", "region", "area", "activityType", "grade", "periods", "prepLevel", "cleanupLevel",
               "templateStatus", "season", "seasonTags", "videoId", "channel", "channelUrl", "seconds", "tags", "title_ko"]
LIST_LANG = ["title", "summary", "matReq"]
# 상세 묶음 파일에 들어가는 칸
DETAIL_LANG = ["objective", "matOpt", "prep", "outline", "tips", "linkNote"]
DETAIL_COMMON = ["templateLinks", "relatedLinks", "templateFormat", "safety", "screenNeeded", "evidence", "videoTitle"]

ALLOWED = {
    "region": ["KR", "EN", "JP", "FR", "DE", "ES", "IT"],
    "area": ["미적 체험", "표현", "감상", "기타"],
    "grade": ["1-2", "3-4", "5-6", "전학년", "불명"],
    "periods": ["1", "2", "3+", "불명"],
    "level": ["쉬움", "보통", "어려움"],
    "templateStatus": ["도안 있음(무료)", "도안 있음(유료·회원가입)", "영상 속 도안만", "도안 없음", "불명"],
}

# 계절·행사(자유 문자열)를 필터용 묶음으로. 한 수업이 여러 묶음에 들어갈 수 있다
SEASON_RULES = [
    ("봄", r"봄|식목|3월|4월|5월|소풍|어린이날|어버이|스승의"),
    ("여름", r"여름|장마|바다|6월|7월|8월|물놀이|비\b|비,"),
    ("가을", r"가을|추석|한가위|단풍|9월|10월|11월|독서|핼러윈|할로윈"),
    ("겨울", r"겨울|크리스마스|성탄|연말|12월|1월|2월|눈사람|설날|새해"),
    ("새 학기", r"새\s?학기|학기\s?초|학년\s?초|3월|입학|첫"),
    ("학년말", r"학년\s?말|학기\s?말|졸업"),
    ("명절·전통", r"추석|한가위|설날|설\b|명절|전통|한복|민속"),
    ("기념일·행사", r"어린이날|어버이|스승의|한글날|현충일|호국|개천절|광복|삼일절|3\.1|독도|식목일|크리스마스|성탄|핼러윈|할로윈|"
                  r"가족|월드컵|올림픽|지구의 날|환경의 날|부활절|발렌타인|밸런타인|추수감사|어머니의 날|아버지의 날|날\(|의 날"),
]


def bucket_of(i):
    """FNV-1a + 섞기. index.html 의 bucketOf 와 반드시 같아야 한다(id는 ASCII)"""
    h = 2166136261
    for ch in str(i):
        h ^= ord(ch) & 0xFFFF
        h = (h * 16777619) & 0xFFFFFFFF
    h ^= h >> 15
    h = (h * 0x2C1B3C6D) & 0xFFFFFFFF
    h ^= h >> 12
    return h % BUCKETS


def simple_hash(s):
    h = 2166136261
    for ch in s:
        # 자바스크립트 charCodeAt 와 같게: UTF-16 단위로
        for u in ([ord(ch)] if ord(ch) < 0x10000 else [0xD800 + ((ord(ch) - 0x10000) >> 10), 0xDC00 + ((ord(ch) - 0x10000) & 0x3FF)]):
            h ^= u
            h = (h * 16777619) & 0xFFFFFFFF
    return format(h, "x")


def s(v):
    return "" if v is None else (v.strip() if isinstance(v, str) else str(v))


def as_list(v):
    """JSON 배열 문자열·줄바꿈·쉼표 문자열·배열 → 문자열 배열"""
    if isinstance(v, list):
        return [s(x) for x in v if s(x)]
    t = s(v)
    if not t:
        return []
    if t.startswith("["):
        try:
            a = json.loads(t)
            if isinstance(a, list):
                return [s(x) for x in a if s(x)]
        except Exception:
            pass
    parts = re.split(r"\n|\s*,\s*(?=https?://)|\s*[|]\s*", t) if "http" in t else re.split(r"\n|,", t)
    return [p.strip() for p in parts if p.strip()]


def safe_url(u):
    u = s(u)
    if not re.match(r"^https?://[^\s\"'<>]+$", u):
        return ""
    return u


def season_tags(raw):
    t = s(raw)
    if not t:
        return []
    return [name for name, pat in SEASON_RULES if re.search(pat, t)]


# ----------------------------- 원천 1: art.db -----------------------------
def read_db(path):
    if not Path(path).exists():
        sys.exit(f"art.db 를 찾을 수 없습니다: {path}")
    src = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=30)
    mem = sqlite3.connect(":memory:")
    for i in range(5):  # 수집기가 쓰는 중이면 잠깐 기다렸다 다시
        try:
            src.backup(mem)
            break
        except sqlite3.OperationalError as e:
            if i == 4:
                raise
            print("DB가 사용 중이라 잠시 기다립니다:", e)
            time.sleep(3 * (i + 1))
    src.close()
    rows = []
    q = ("select art_no, video_id, region, channel, channel_id, seconds, title, result, updated "
         "from videos where status='done' and art_no is not null order by art_no")
    for art_no, vid, region, channel, channel_id, seconds, vtitle, res, updated in mem.execute(q):
        try:
            j = json.loads(res or "{}")
        except Exception:
            print(f"result JSON 오류로 건너뜀: art_no={art_no}")
            continue
        if j.get("skip"):
            continue
        o = {
            "id": f"art-{int(art_no):05d}", "status": "", "region": s(region), "videoId": s(vid),
            "channel": s(channel), "channelId": s(channel_id), "seconds": seconds or "",
            "area": s(j.get("area")), "activityType": s(j.get("activity_type")), "grade": s(j.get("grade")),
            "periods": s(j.get("periods")), "prepLevel": s(j.get("prep_level")), "cleanupLevel": s(j.get("cleanup_level")),
            "safety": s(j.get("safety")), "screenNeeded": s(j.get("screen_needed")), "season": s(j.get("season_theme")),
            "templateStatus": s(j.get("template_status")), "templateFormat": s(j.get("template_format")),
            "templateLinks": j.get("template_links") or [], "relatedLinks": j.get("related_links") or [],
            "tags": j.get("free_tags") or [], "evidence": s(j.get("evidence")),
            "videoTitle": s(vtitle), "source": "수집", "createdAt": "", "updatedAt": s(updated),
        }
        for f, key in TEXT_FIELDS.items():
            v = j.get(key) or {}
            for l in LANGS:
                o[f"{f}_{l}"] = s(v.get(l)) if isinstance(v, dict) else (s(v) if l == "ko" else "")
        ol = j.get("outline") or {}
        for l in LANGS:
            o[f"outline_{l}"] = [s(x) for x in (ol.get(l) or []) if s(x)] if isinstance(ol, dict) else []
        rows.append(o)
    mem.close()
    return rows


# ----------------------------- 원천 2: 구글 시트(Apps Script) -----------------------------
def gas(q):
    return GAS_URL + ("&" if "?" in GAS_URL else "?") + q + "&t=" + str(int(time.time() * 1000))


def get_json(url, tries=3):
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"받지 못했습니다: {url.split('?')[-1]} ({last})")


def read_sheet():
    if not GAS_URL:
        sys.exit("--source sheet 는 GAS_URL 환경변수가 필요합니다.")
    tabs = get_json(gas("action=actTabs"))
    if not tabs.get("ok"):
        raise RuntimeError(tabs.get("error") or "탭 목록을 받지 못했습니다")
    rows = []
    for t in tabs["tabs"]:
        for off in range(0, t["rows"], 2000):
            page = get_json(gas(f"action=acts&tab={urllib.parse.quote(t['name'])}&offset={off}&limit=2000"))
            if not page.get("ok"):
                raise RuntimeError(page.get("error") or "탭 읽기 실패")
            h = [s(x) for x in page["h"]]
            for r in page["r"]:
                o = {k: r[i] for i, k in enumerate(h) if k}
                for l in LANGS:
                    o[f"outline_{l}"] = as_list(o.get(f"outline_{l}"))
                for k in ("templateLinks", "relatedLinks", "tags"):
                    o[k] = as_list(o.get(k))
                rows.append(o)
        print(f"탭 「{t['name']}」 {t['rows']}줄")
    return rows


# ----------------------------- 정리·검사 -----------------------------
def load_optout(db_path):
    ids = set()
    for p in [ROOT / "optout.txt", Path(db_path).parent / "optout.txt" if db_path else None]:
        if p and p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                t = line.split("#", 1)[0].strip()
                if t:
                    ids.add(t)
    return ids


def normalize(o):
    for k in ("region", "area", "activityType", "grade", "periods", "prepLevel", "cleanupLevel", "safety",
              "screenNeeded", "season", "templateStatus", "templateFormat", "channel", "channelId", "videoId", "evidence"):
        o[k] = s(o.get(k))
    if o["region"] not in ALLOWED["region"]:
        o["region"] = o["region"].upper() if o["region"].upper() in ALLOWED["region"] else "EN"
    if o["area"] not in ALLOWED["area"]:
        o["area"] = "기타"
    if o["grade"] not in ALLOWED["grade"]:
        o["grade"] = "불명"
    if o["periods"] not in ALLOWED["periods"]:
        o["periods"] = "불명"
    for k in ("prepLevel", "cleanupLevel"):
        if o[k] not in ALLOWED["level"]:
            o[k] = "보통"
    if o["templateStatus"] not in ALLOWED["templateStatus"]:
        o["templateStatus"] = "불명"
    o["templateLinks"] = [u for u in (safe_url(x) for x in as_list(o.get("templateLinks"))) if u]
    o["relatedLinks"] = [u for u in (safe_url(x) for x in as_list(o.get("relatedLinks"))) if u and u not in o["templateLinks"]]
    o["tags"] = list(dict.fromkeys(t.replace(",", " ") for t in as_list(o.get("tags"))))[:12]
    o["seasonTags"] = season_tags(o["season"])
    o["channelUrl"] = f"https://www.youtube.com/channel/{o['channelId']}" if re.match(r"^UC[A-Za-z0-9_-]{20,}$", o["channelId"]) else ""
    try:
        o["seconds"] = int(float(o.get("seconds") or 0))
    except Exception:
        o["seconds"] = 0
    for f in TEXT_FIELDS:
        ko = s(o.get(f"{f}_ko"))
        for l in LANGS:
            o[f"{f}_{l}"] = s(o.get(f"{f}_{l}")) or ko
    for l in LANGS:
        o[f"outline_{l}"] = [x for x in as_list(o.get(f"outline_{l}"))][:8] or as_list(o.get("outline_ko"))[:8]
    return o


def list_val(o, c):
    v = o.get(c, "")
    if isinstance(v, list):
        return ",".join(v)
    return v


# ----------------------------- 영상 점검(선택) -----------------------------
def check_video(v):
    url = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote("https://www.youtube.com/watch?v=" + v, safe="")
    for _ in range(2):
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                return "ok" if r.status == 200 else "err"
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return "noembed"
            if e.code in (400, 404):
                return "gone"
        except Exception:
            pass
        time.sleep(1.5)
    return "err"


def video_status(rows):
    path = OUT / "video-status.json"
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        status = {}
    if CHECK_VIDEOS:
        vids = sorted({o["videoId"] for o in rows if re.match(r"^[A-Za-z0-9_-]{11}$", o["videoId"])},
                      key=lambda v: (v in status, (status.get(v) or {}).get("t", "")))[:CHECK_PER_RUN]
        today = dt.date.today().isoformat()
        bad = 0
        with concurrent.futures.ThreadPoolExecutor(8) as ex:
            for v, st in zip(vids, ex.map(check_video, vids)):
                if st == "err":
                    continue
                prev = status.get(v) or {}
                status[v] = {"s": st, "c": 0 if st == "ok" else (prev.get("c", 0) if prev.get("s") == st else 0) + 1, "t": today}
                bad += st != "ok"
        print(f"영상 점검 {len(vids)}개 (안 나옴 {bad}개)")
        OUT.mkdir(parents=True, exist_ok=True)
        path.write_text("{\n" + ",\n".join(json.dumps(k) + ":" + json.dumps(status[k]) for k in sorted(status)) + "\n}\n", encoding="utf-8")
    return status


# ----------------------------- 후기·추천(Apps Script 있을 때만) -----------------------------
REVIEW_H = ["reviewId", "activityId", "teacher", "grade", "fitScore", "rating", "duration", "sessionType",
            "caution_ko", "caution_en", "caution_ja", "tip_ko", "tip_en", "tip_ja", "createdAt"]


def build_social(ids):
    if not GAS_URL:
        return None
    try:
        counts = get_json(gas("action=counts"), tries=2)
    except Exception as e:
        print("후기 요약: Apps Script에 연결하지 못해 건너뜀", e)
        return None
    if not counts.get("ok"):
        return None
    kst = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=9) - dt.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
    srcs = get_json(gas("action=reviewSources"))
    sources = srcs["sources"] if srcs.get("ok") else [{"src": "main", "rows": None}]
    allr = []
    for src in sources:
        total, off = (1 if src["rows"] is None else src["rows"]), 0
        while off < total:
            page = get_json(gas(f"action=reviewsPage&src={urllib.parse.quote(str(src['src']))}&offset={off}&limit=2000"))
            if not page.get("ok"):
                raise RuntimeError(page.get("error") or "후기 읽기 실패")
            total = page["total"]
            allr += [dict(zip(page["h"], r)) for r in page["r"]]
            off += 2000
    stats, by_act = {}, {}
    for r in allr:
        a = s(r.get("activityId"))
        if a not in ids:
            continue
        st = stats.setdefault(a, [0, 0, 0, 0, 0, 0, 0])
        st[0] += 1
        st[1] += float(r.get("rating") or 5)
        st[2] += float(r.get("fitScore") or 5)
        st[3] += float(r.get("duration") or 40)
        g = s(r.get("grade"))
        if g in ("1~2학년", "3~4학년", "5~6학년"):
            st[4 + ["1~2학년", "3~4학년", "5~6학년"].index(g)] += 1
        by_act.setdefault(a, []).append(["" if r.get(k) is None else r.get(k) for k in REVIEW_H])
    likes = {k: int(v) for k, v in (counts.get("likes") or {}).items() if k in ids and int(v or 0) > 0}
    saves = {k: int(v) for k, v in (counts.get("saves") or {}).items() if k in ids and int(v or 0) > 0}
    social_json = json.dumps({"likes": likes, "saves": saves, "stats": stats}, ensure_ascii=False, separators=(",", ":"))
    (OUT / "social.json").write_text(social_json, encoding="utf-8")
    shutil.rmtree(OUT / "reviews", ignore_errors=True)
    (OUT / "reviews").mkdir(parents=True)
    buckets = [{} for _ in range(BUCKETS)]
    for a, lst in by_act.items():
        buckets[bucket_of(a)][a] = lst[-200:]
    bsig = ""
    for i, b in enumerate(buckets):
        txt = json.dumps({"h": REVIEW_H, "acts": b}, ensure_ascii=False, separators=(",", ":"))
        bsig += simple_hash(txt)
        (OUT / "reviews" / f"{i:02d}.json").write_text(txt, encoding="utf-8")
    print(f"후기 {len(allr)}개, 추천 {len(likes)}·저장 {len(saves)}개 수업")
    return {"sig": simple_hash(social_json + bsig), "since": kst}


# ----------------------------- 공유 미리보기 -----------------------------
def short(t, n):
    t = re.sub(r"\s+", " ", s(t))
    return t[: n - 1] + "…" if len(t) > n else t


def write_share_pages(rows):
    d = ROOT / "a"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    esc = lambda x: html.escape(s(x), quote=True)
    for o in rows:
        i = o["id"]
        if not re.match(r"^[A-Za-z0-9_-]{1,40}$", i):
            continue
        title = o["title_ko"] or i
        desc = short(o["summary_ko"] + (" 준비물: " + o["matReq_ko"] if o["matReq_ko"] else ""), 160)
        page = SITE_URL + "a/" + i + ".html"
        vid = o["videoId"]
        qid = urllib.parse.quote(i)
        img = f'\n<meta property="og:image" content="https://i.ytimg.com/vi/{esc(vid)}/hqdefault.jpg">' if vid else ""
        src = (f' · <a href="https://www.youtube.com/watch?v={esc(vid)}" rel="noopener">원본 영상({esc(o["channel"] or "YouTube")})</a>') if vid else ""
        (d / f"{i}.html").write_text(f"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} | {SITE_NAME}</title>
<meta name="description" content="{esc(desc)}">
<meta name="robots" content="noindex, follow">
<meta property="og:type" content="article"><meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{esc(page)}">{img}
<meta name="twitter:card" content="summary_large_image">
<script>location.replace("../?id={qid}" + location.hash);</script>
<style>body{{font-family:system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;line-height:1.6;color:#2b2420;background:#fbf8f3}}a{{color:#c2410c}}small{{color:#7c6f64}}</style>
</head><body>
<h1>{esc(title)}</h1>
<p>{esc(short(o["summary_ko"], 400))}</p>
<p><a href="../?id={qid}">「{SITE_NAME}」에서 수업 보기</a>{src}</p>
<p><small>영상과 도안의 저작권은 원작자에게 있으며, 이 사이트는 소개와 연결만 합니다.</small></p>
</body></html>
""", encoding="utf-8")
    (ROOT / "sitemap.xml").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>{html.escape(SITE_URL)}</loc></url>
</urlset>
""", encoding="utf-8")
    # 첫 화면(index.html)의 대표 주소(canonical·og:url)도 SITE_URL 로 맞춘다
    idx = ROOT / "index.html"
    if idx.exists():
        page_html = idx.read_text(encoding="utf-8")
        fixed = re.sub(r'(<link rel="canonical" href=")[^"]*(")', lambda m: m.group(1) + html.escape(SITE_URL) + m.group(2), page_html)
        fixed = re.sub(r'(<meta property="og:url" content=")[^"]*(")', lambda m: m.group(1) + html.escape(SITE_URL) + m.group(2), fixed)
        if fixed != page_html:
            idx.write_text(fixed, encoding="utf-8")
    (ROOT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nDisallow: /a/\nSitemap: {SITE_URL}sitemap.xml\n", encoding="utf-8")


def export_tsv(rows, path):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(SHEET_HEADERS)
        for o in rows:
            out = []
            for h in SHEET_HEADERS:
                v = o.get(h, "")
                if isinstance(v, list):
                    v = json.dumps(v, ensure_ascii=False)
                out.append(s(v).replace("\t", " ").replace("\r", " ").replace("\n", " "))
            w.writerow(out)
    print(f"시트용 표 저장: {path} ({len(rows)}줄)")


def dump(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


# ----------------------------- 발행 -----------------------------
def main():
    ap = argparse.ArgumentParser(description="오늘 미술 뭐 하지? 데이터 발행")
    ap.add_argument("--source", choices=["db", "sheet"], default=os.environ.get("SOURCE", "db"))
    ap.add_argument("--db", default=os.environ.get("ART_DB") or str(ROOT.parent / "today-art" / "art.db"))
    ap.add_argument("--export-tsv", default="")
    a = ap.parse_args()

    raw = read_db(a.db) if a.source == "db" else read_sheet()
    optout = load_optout(a.db if a.source == "db" else None)
    seen, rows = set(), []
    for o in raw:
        i = s(o.get("id"))
        if not i or s(o.get("status")) == "숨김" or i in seen:
            continue
        if s(o.get("videoId")) in optout or s(o.get("channelId")) in optout:
            continue
        seen.add(i)
        rows.append(normalize(o))
    if len(rows) < 1:
        sys.exit("발행할 수업이 없습니다.")
    if a.export_tsv:
        export_tsv(rows, a.export_tsv)

    status = video_status(rows)
    is_hidden = lambda v: (status.get(v) or {}).get("s", "ok") != "ok" and (status.get(v) or {}).get("c", 0) >= 2
    hidden = [{"id": o["id"], "videoId": o["videoId"], "s": status[o["videoId"]]["s"]} for o in rows if is_hidden(o["videoId"])]
    visible = [o for o in rows if not is_hidden(o["videoId"])]
    if hidden:
        print(f"영상이 안 나와 숨긴 수업 {len(hidden)}건")

    OUT.mkdir(parents=True, exist_ok=True)
    for l in LANGS:
        cols = LIST_COMMON + [f"{f}_{l}" for f in LIST_LANG if f"{f}_{l}" not in LIST_COMMON]
        (OUT / f"list-{l}.json").write_text(dump({"h": cols, "r": [[list_val(o, c) for c in cols] for o in visible]}), encoding="utf-8")

    shutil.rmtree(OUT / "detail", ignore_errors=True)
    (OUT / "detail").mkdir(parents=True)
    buckets = [{} for _ in range(BUCKETS)]
    for o in visible:
        d = {}
        for c in DETAIL_COMMON:
            v = o.get(c)
            if v not in ("", None, []):
                d[c] = v
        for f in DETAIL_LANG:
            for l in LANGS:
                v = o.get(f"{f}_{l}")
                if v not in ("", None, []) and (l == "ko" or v != o.get(f"{f}_ko")):
                    d[f"{f}_{l}"] = v
        buckets[bucket_of(o["id"])][o["id"]] = d
    for i, b in enumerate(buckets):
        (OUT / "detail" / f"{i:02d}.json").write_text(dump(b), encoding="utf-8")

    write_share_pages(visible)
    social = build_social({o["id"] for o in visible})
    if not GAS_URL:
        for p in [OUT / "social.json"]:
            p.unlink(missing_ok=True)
        shutil.rmtree(OUT / "reviews", ignore_errors=True)

    body = dump(visible)
    sig = f"{len(body)}:{len(visible)}:{hashlib.sha1(body.encode('utf-8')).hexdigest()[:10]}"
    try:
        prev = json.loads((OUT / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        prev = None
    list_same = bool(prev and prev.get("sig") == sig)
    social_same = (social is None) or bool(prev and prev.get("ssig") == social["sig"])
    if list_same and social_same and prev.get("gas", "") == GAS_URL and prev.get("site", "") == SITE_URL:
        print(f"수업 {len(visible)}건, 변경 없음 (버전 {prev['v']})")
        return
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
    at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=9)).strftime("%Y-%m-%d %H:%M")
    meta = {
        "v": prev["v"] if list_same else stamp, "n": len(visible), "at": prev.get("at", at) if list_same else at,
        "buckets": BUCKETS, "sig": sig, "hidden": hidden, "site": SITE_URL, "gas": GAS_URL,
        "templates": sum(1 for o in visible if o["templateStatus"].startswith("도안 있음")),
        "social": bool(social), "sv": (prev.get("sv") if (social_same and prev) else stamp) if social else "",
        "sAt": (prev.get("sAt") if (social_same and prev) else social["since"]) if social else "",
        "ssig": social["sig"] if social else "",
    }
    (OUT / "meta.json").write_text(dump(meta), encoding="utf-8")
    print(f"수업 {len(visible)}건 (도안 있음 {meta['templates']}건), 목록 버전 {meta['v']}, 후기 {'있음' if social else '없음(GAS 주소 없음)'}")


if __name__ == "__main__":
    main()
