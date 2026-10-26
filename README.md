# 오늘 미술 뭐 하지?

선생님이 미술 수업 영상을 **찾고 고르게** 돕는 소개 사이트입니다. 세계 여러 나라의 초등 미술 수업 영상을 학년, 차시, 준비·정리 난이도, 도안 여부로 찾을 수 있고, 원작자의 영상과 도안 페이지로 바로 연결합니다.
「오늘 체육 뭐 하지?」와 같은 구조로 만들었지만 별도 사이트로 운영합니다(공개 목표 2026-12-10).

## 운영 원칙 (저작권)

- 영상은 **유튜브 공식 퍼가기 플레이어**(youtube-nocookie)로만 보여 줍니다. 내려받거나 다시 올리지 않습니다.
- 도안 파일이나 화면 캡처는 올리지 않습니다. **원작자 페이지 링크만** 겁니다("원작자 페이지에서 도안 받기" 버튼).
- **조회수는 어디에도 표시하지 않습니다.** (수집 DB에 있는 views 값은 빌드할 때 아예 읽지 않습니다.)
- 모든 상세 화면과 바닥글에 3개 국어로 "영상과 도안의 저작권은 원작자에게 있으며, 이 사이트는 소개와 연결만 합니다"를 표시합니다.
- 바닥글에 삭제 요청 연락처(`{{CONTACT_EMAIL}}`)가 있습니다. 요청이 오면 `optout.txt`에 영상 ID 또는 채널 ID를 적고 다시 발행합니다.
- 수업별 공유 미리보기(`a/*.html`)는 검색엔진에 노출하지 않습니다(noindex). 검색 손님은 원작자에게 가야 하므로.

## 폴더 구성

| 경로 | 내용 |
|---|---|
| `index.html` | 사이트 전체(한 파일). 목록·필터·검색·상세·저장·공유·뽑기·3개 국어 |
| `scripts/build-art-data.py` | 데이터 발행 스크립트 (art.db 또는 구글 시트 → `data/`, `a/`, `sitemap.xml`, `robots.txt`) |
| `scripts/check-links.py` | 도안·관련 링크 점검 → `data/link-status.json` (하루 1번) |
| `data/meta.json` | 버전, 건수, 도안 있는 수업 수 |
| `data/list-{ko,en,ja}.json` | 목록·검색용 (제목·요약·필수 준비물 + 분류) |
| `data/seasonal.json` | 「이맘때 많이 한 수업」 미리 계산한 순위 (기능이 꺼져 있으면 사이트가 읽지 않음) |
| `data/detail/00~63.json` | 상세 화면용 64묶음 (학습목표·준비물·사전 준비·활동 흐름·팁·도안/관련 링크) |
| `a/art-NNNNN.html` | 카톡·메신저 공유 미리보기 (og:image는 유튜브 공식 썸네일 i.ytimg.com) |
| `apps-script/Code.gs` | 구글 시트 백엔드 (후기·추천·제안·관리자). 아직 배포 전 |
| `.github/workflows/build-data.yml` | 시트로 옮긴 뒤 쓰는 자동 발행 (지금은 수동 실행만, GAS_URL 없으면 건너뜀) |
| `_shots/` | 점검용 스크린샷 (배포에는 필요 없음) |

## 데이터 발행 (지금 방식: art.db에서)

수집기(`today-art/collect.py`)가 매일 `art.db`에 수업을 쌓습니다. `status='done'`인 행만 사이트에 나가며, `art_no`가 `art-00001` 같은 수업 번호가 됩니다.

```bash
# today-art 폴더와 today-art-site 폴더가 나란히 있으면 그냥 실행
python scripts/build-art-data.py

# DB 위치가 다르면
python scripts/build-art-data.py --db ../today-art/art.db

# 도메인이 정해지면 (공유 미리보기·sitemap·robots·index.html 대표 주소가 바뀜)
SITE_URL=https://새도메인/ python scripts/build-art-data.py
```

- 파이썬 3.9 이상, 추가 설치 없음(표준 라이브러리만).
- **여러 번 실행해도 됩니다.** 내용이 그대로면 버전을 바꾸지 않고 "변경 없음"으로 끝납니다.
- `art.db`는 읽기 전용으로 열고 메모리로 복사해서 읽습니다. 수집기가 쓰는 중이어도 DB를 건드리지 않습니다.
- 분류 값이 허용 목록에 없으면 안전한 기본값(학년·차시 "불명", 영역 "기타" 등)으로 바꿉니다. 링크는 `http(s)://` 주소만 남깁니다.
- 계절·행사(자유 문자열)는 필터용으로 봄·여름·가을·겨울·새 학기·학년말·명절·전통·기념일·행사로 묶어 `seasonTags`에 넣습니다. 상세 화면(한국어)에는 원문을 그대로 보여 줍니다.
- `optout.txt`(이 폴더 또는 art.db 옆)에 적힌 영상·채널 ID는 발행에서 빠집니다.
- `CHECK_VIDEOS=1`을 붙이면 유튜브 oEmbed로 영상이 살아 있는지 점검하고, 두 번 연속 안 나오는 영상은 목록에서 뺍니다.
- Apps Script 주소는 `GAS_URL` 환경변수, 없으면 `index.html`의 `GAS_DEFAULT_URL`을 씁니다(`GAS_URL=`로 끌 수 있음). 이때 관리자가 사이트에서 누른 조치(시트 「관리조치」 탭)를 받아 **숨긴 수업은 발행에서 빼고, 「도안 링크만 지우기」 한 수업은 도안·관련 링크를 비우고 '도안 없음'으로** 냅니다. 받지 못하면 경고만 하고 계속합니다.
- 죽은 도안 링크 점검: `python scripts/check-links.py`(하루 1번). 결과는 `data/link-status.json`. 서로 다른 날 **연속 2번** 죽은 링크는 다음 발행에서 빠지고, 남은 도안 링크가 없으면 '도안 없음'이 됩니다. 네이버 블로그는 삭제된 글도 200을 주므로 본문의 "게시물이 삭제되었거나…" 알림으로 판정합니다. 몇 개만 시험: `python scripts/check-links.py --url 주소 …`

발행 후 바뀐 파일을 커밋·푸시하면 GitHub Pages에 반영됩니다.

```bash
python scripts/build-art-data.py
git add -A data a sitemap.xml robots.txt index.html
git commit -m "데이터 발행 $(date +%F)"
git push
```

### 규모가 커질 때 (약 1만~3만 개)

목록 파일은 언어별로 1건당 약 1KB입니다. 1만 개면 언어당 약 10MB(전송 시 gzip으로 2~3MB), 상세 묶음은 묶음당 수백 KB입니다. 1만 개 전후에서 첫 화면이 느려지면 `list-*.json`을 목록용 최소 칸(요약 제외)과 검색용 파일로 나누는 것을 검토하세요.

## 「이맘때 많이 한 수업」 (지금은 꺼 둠 — 2027년 가을쯤 켜기)

데이터 발행 때마다 `data/seasonal.json`에 순위를 미리 계산해 둡니다. 사이트는 꺼져 있으면 이 파일을 읽지도, 보여 주지도 않습니다.

- 점수: A 작년 같은 시기(1년 전 ±14일 후기×3·저장×2·추천×1) 0.6 + B 최근 28일 0.25 + C 계절·행사(이번 달·다음 달) 0.15.
  작년 창의 후기+저장이 30건 미만이면 A를 빼고 B 0.6·C 0.4, 최근 28일도 10건 미만이면 C만 씁니다. 학년군(1-2·3-4·5-6, 전학년 포함)별 상위 목록도 함께.
- 날짜 기록: Code.gs가 추천·저장을 누르거나 취소할 때마다 시트 「반응로그」 탭에 날짜·수업·종류만 쌓습니다(기기 번호 없음, 지우지 않음). 발행 스크립트는 `action=seasonalStats`로 수업별·날짜별 건수만 받습니다. 후기는 「후기」 탭(+연도별 보관 파일)의 createdAt을 씁니다.
- 결과 미리 보기: 관리자 로그인 → 「이맘때 추천」 탭 → 계산 요약·학년군별 목록 확인, 「첫 화면에서 미리보기」(이 브라우저에서만, 새로고침하면 꺼짐).

**내년에 켜는 방법**
1. 관리자 「이맘때 추천」 탭에서 방식이 `ABC`(또는 `AC`)로 나오는지, 목록이 그럴듯한지 확인 (작년 창 후기+저장 30건 이상이어야 A가 들어감).
2. `index.html`의 `const SEASONAL_ENABLED = false;`를 `true`로 바꿔 커밋·푸시.
3. 순위는 발행할 때 계산되므로 데이터 발행을 매일 한 번 이상 돌리기(기준일이 `seasonal.json`의 `date`).
4. 끄려면 다시 `false`. 기준 수치(30건·10건·가중치·±14일·28일)는 `build-art-data.py`의 `SEASONAL_*` 상수, 달별 행사 낱말은 `SEASONAL_MONTHS`.

## 로컬에서 보기

```bash
python -m http.server 8000
# 브라우저에서 http://localhost:8000
```
`index.html`을 파일로 바로 열면(file://) 데이터를 못 읽으니 꼭 서버로 여세요.

## 배포: GitHub Pages + 새 도메인

1. GitHub에 새 저장소를 만들고 이 폴더를 푸시합니다.
   ```bash
   git remote add origin https://github.com/계정/today-art.git
   git push -u origin main
   ```
2. 저장소 **Settings → Pages → Build and deployment**: Source를 `Deploy from a branch`, 브랜치 `main` / 폴더 `/ (root)`로 저장.
3. 도메인 구입 후 DNS 설정
   - 루트 도메인(예: `todayart.kr`): A 레코드 4개 `185.199.108.153`, `185.199.109.153`, `185.199.110.153`, `185.199.111.153` (IPv6를 쓰면 AAAA `2606:50c0:8000::153` ~ `8003::153`)
   - `www` 하위 도메인: CNAME → `계정.github.io`
4. **Settings → Pages → Custom domain**에 도메인을 넣고 저장 → 확인되면 **Enforce HTTPS** 체크. (저장소 루트에 `CNAME` 파일이 생깁니다.)
5. 새 주소로 다시 발행: `SITE_URL=https://todayart.kr/ python scripts/build-art-data.py` 후 커밋·푸시.
6. 검색 등록(선택): 네이버 서치어드바이저, 구글 서치 콘솔에 `https://도메인/sitemap.xml` 제출.

## 남은 작업

- [ ] **연락 이메일**: `index.html`의 `{{CONTACT_EMAIL}}` 두 곳(바닥글 링크와 `CONTACT_EMAIL` 상수)을 실제 주소로 바꾸기
- [ ] **도메인**: 정해지면 `SITE_URL`로 다시 발행 (기본값 `https://today-art.example/`은 자리표시자)
- [ ] **구글 시트·Apps Script 연결** (후기·추천 수·새 수업 제안·설명 수정 제안·신고·관리자 기능)
  1. 새 구글 시트 → 확장 프로그램 → Apps Script → `apps-script/Code.gs` 내용 붙여 넣기
  2. 편집기에서 `setup` 함수를 한 번 실행해 권한 승인과 탭 생성
  3. 프로젝트 설정 → 스크립트 속성에 `ADMIN_PIN` 추가 (관리자 비밀번호)
  4. 수업 데이터 올리기: `python scripts/build-art-data.py --export-tsv 수업.tsv` → 「수업」 탭 A1에 붙여 넣기 (5,000줄이 넘으면 「수업2」 탭에 이어서)
  5. 배포 → 새 배포 → 유형 「웹 앱」, 실행 계정 「나」, 액세스 「모든 사용자」 → 웹 앱 주소 복사
  6. `index.html`의 `GAS_DEFAULT_URL`에 주소 넣기 → 후기·추천 등이 켜짐 (비어 있으면 조용히 꺼져 있음)
  7. 시트를 원본으로 삼을 때: 저장소 Secrets에 `GAS_URL`, `SITE_URL`을 넣고 `.github/workflows/build-data.yml`의 schedule 주석을 풀기. 그 전까지는 art.db 발행을 계속 쓰면 됩니다(두 방식을 섞으면 시트에서 고친 내용이 art.db 발행에 덮일 수 있으니 하나로 정하기).
- [ ] 관리자 화면은 서버 연결 전에는 숨겨져 있습니다. `?admin` 을 주소 뒤에 붙이면 버튼이 보여서 시트 주소를 이 브라우저에만 넣어 시험해 볼 수 있습니다.
- [ ] 영어·일본어 화면의 자유 태그(#…)는 한국어 원문뿐이라 숨겨 두었습니다. 다국어 태그가 생기면 보이게 바꾸기.

## 라이선스

- 소개된 영상·썸네일·도안 등 원저작물의 저작권은 각 원저작권자에게 있으며, 이 플랫폼은 검색하기 쉽게 정리해 원작자에게 연결할 뿐입니다.
- 소스 코드: MIT License (`LICENSE`)
- 이 사이트가 작성한 소개 글·분류·번역 데이터: CC BY 4.0
- 자세한 내용: `NOTICE.md`
