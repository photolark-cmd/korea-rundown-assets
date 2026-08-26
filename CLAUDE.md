# korea-rundown-assets

Korea Rundown 두 블로그의 **콘텐츠 자산과 이미지 생성 도구**. 원고·썸네일·본문 도형을 여기서 만들고, 실제 발행은 별도 저장소가 한다.

> **새 세션이면 이 파일부터 읽고 시작할 것.** 세션 간 기억이 이어지지 않으므로, 사용자에게 배경을 다시 묻기 전에 여기 적힌 내용을 먼저 확인한다.

## 사용자 상황 (먼저 알 것)

- **야간 근무.** 밤에 일하고 낮에 잔다. 낮 시간대 외출·촬영·전화가 필요한 작업은 제안하지 말 것. 편의점 등 24시간 매장은 가능.
- 채널 여러 개를 혼자 운영한다. 작업량을 늘리는 제안보다 **자동화·재사용** 제안이 맞다.

## 두 개의 블로그

| | 영문 (`us`) | 국내 (`kr`) |
|---|---|---|
| 플랫폼 | Blogger — `korearundown.blogspot.com` | 네이버 — `blog.naver.com/clark315` |
| 검색엔진 | 구글 | 네이버 |
| 수익화 | 애드센스 + 제휴 | 애드포스트 (**애드센스 불가**) |
| 유튜브 임베드 | 효과 있음 | **검색상 이점 없음** |
| 썸네일 | 1200×630 → 루트 | 1080×1080 → `kr/` |

두 블로그는 **번역 관계가 아니다.** 독자 검색 의도가 다르므로 같은 조사에서 각각 재구성한다.
자세한 지침: [`docs/content-guide-us.md`](docs/content-guide-us.md) · [`docs/content-guide-kr.md`](docs/content-guide-kr.md)

## 발행 파이프라인은 다른 저장소에 있다

**`photolark-cmd/autoworker-script`** — 채널 자동화 시스템. 이 저장소에는 발행 코드가 없다.

- `channels/_shared/blog/blogger_upload.py` — Blogger API v3 업로더
- 발행 단위: `channels/<채널>/blog/posts/<프로젝트>/` 안에 **`post.html` + `meta.json`**
- `post.html`은 인라인 style을 쓴 HTML 조각(`<h2>`, `<table>`, `<p>` 등). 마크다운이 아니다
- `meta.json`: `title` · `labels` · `search_description` · `youtube_url` · `uploaded`
- **예약 발행 기능 없음.** 호출되는 즉시 올라간다. 시각 지정은 외부 스케줄러 또는 Blogger 예약 기능이 담당

**이 저장소의 `drafts/*.md`는 마크다운이라 파이프라인이 바로 먹지 못한다.**
`node tools/build-post.mjs drafts/<파일>.md` 로 변환하면 `blog/posts/<slug>/`에
`post.html` + `meta.json`이 생성된다. 그 폴더를 `autoworker-script`의
`channels/<채널>/blog/posts/` 아래로 옮기면 업로더가 인식한다.

이미지는 이 저장소가 public이라 **raw.githubusercontent.com 주소로 호스팅**된다.
변환 전에 이미지를 먼저 푸시할 것.

## 자동화 현황

- 야간 자동 발행 스케줄이 **사용자 PC 쪽에 존재한다** (사용자가 직접 확인함)
- 클라우드 Routine 목록·이 저장소·`autoworker-script` 저장소에는 스케줄 파일이 **없다**. PC 로컬 또는 Blogger 예약이다
- **자동화 유무를 단정하지 말 것.** 확인했다면 *어디까지* 확인했는지 반드시 밝힌다 (과거에 이걸 안 밝혀 "자동화 없다"고 잘못 답한 적 있음)

### 댓글 운영 규칙 (사용자가 정함)

Blogger 댓글 승인제 ON, 알림 메일 `photolark@gmail.com`.
밤 점검은 **분류 → 초안 → 아침 보고까지만**. 답글을 직접 게시하지 않는다.

| 유형 | 처리 |
|---|---|
| 스팸·광고 | 초안 없음. **삭제도 승인도 하지 않음** — 개수만 보고 |
| 인사·감사 | 짧은 답글 초안 |
| 사실 질문 | 검색 확인 후 초안. 확인 안 되면 초안 없이 보고 |
| 사실 지적 | 검증 → 맞으면 본문 수정안까지 |
| 의료·법률·개인 상담 | **초안 없음.** 질문이 왔다는 사실만 보고 |
| 시비·논쟁조 | 초안 없음. 요약만 |

초안은 영어, 아침 보고에는 한국어 요약 병기.

## 이미지 만들기

```bash
npm install --prefix tools && npx playwright install chromium   # 최초 1회
node tools/render-thumbnails.mjs --profile us|kr|us,kr          # 글 대표 썸네일
node tools/render-figures.mjs                                   # 본문 차트·숫자 카드
node tools/grab-frames.mjs <영상> --sheet | --at 0:12,1:47      # 편집 영상에서 본문 이미지
node tools/build-post.mjs drafts/<파일>.md --youtube <url>      # 발행 형식 변환 + 영상 임베드
```

**본문이 밋밋할 때는 편집 영상에서 프레임을 뽑는다.** 이미 자막·그래픽이 들어가 있어
새로 만들 필요가 없고, 사용자가 촬영하러 나갈 필요도 없다. 채널 영상 소스는 사용자 PC에 있다
(`autoworker-script`의 `.gitignore`가 `*.png/jpg/mp4`를 전부 막으므로 git에는 없다).

사용법: [`tools/README.md`](tools/README.md)

## 유튜브 '사이언스 썰' 레퍼런스

블로그 두 개 말고 **유튜브 사이언스 썰 채널**도 운영한다.
**소재는 생물 · 공룡 · 거대 괴물 위주다** (사용자가 밝힘). 시드 검색어가 그쪽으로 맞춰져 있다.

- **레퍼런스 채택 기준은 사용자가 정했다: 조회수 ≥ 구독자 × 100.** 그 아래는 모으지 않는다
- **최근 30일이 최우선이다** (사용자가 정함). 도구가 그 창을 따로 검색해서 맨 위에 놓는다
- `node tools/refs-nightly.mjs` 하나면 수집 → 다이제스트 → 커밋 → 푸시까지 끝난다.
  PC 작업 스케줄러에 `tools/refs-nightly.cmd`를 걸어 두는 것이 정상 운영 형태다
- 결과는 `refs/refs-<날짜>.csv` + `.md` (최근 30일 먼저, 그 안에서 배수 내림차순),
  그리고 `refs/digest-<날짜>.md` (채널·길이·제목 훅 요약). 사용법: [`tools/README.md`](tools/README.md)
- **다음 세션은 사용자에게 CSV를 요구하지 말 것.** `refs/`를 직접 읽으면 된다
- 최근 영상은 조회수가 덜 쌓여서 배수가 낮게 잡힌다. 최근 창이 얇으면 `--recent-ratio`로
  그 창의 문턱만 낮춘다 — 100배 기준 자체를 건드리지 말 것
- **클라우드 세션의 차단은 환경 설정 문제다.** 2026-08-26 세션 확인: `www.youtube.com`·
  `www.googleapis.com` 모두 egress 프록시가 403 (curl·WebFetch 둘 다). 환경 "기본값"의
  네트워크 접근이 **Trusted**라서 그렇다. 해제 방법(문서 확인함):
  claude.ai/code 입력창 위 **구름 아이콘** → 환경에 마우스 올리면 나오는 **톱니** →
  Network access를 **Custom**으로 → Allowed domains에 `www.googleapis.com` +
  "Also include default list of common package managers" 체크 → 같은 창의
  Environment variables에 `YOUTUBE_API_KEY=...`. **설정 페이지 URL은 없다**
- 그 설정이 되어 있으면 세션에서 직접 수집한다. 안 되어 있으면 사용자 PC 몫이다.
  야간 루틴(`trig_01PoRtjWkv5XenL2HZLWJgU3`)이 매일 07:00 KST에 둘 다 시도한다
- 환경변수는 비밀 저장소가 아니다(문서 경고). 키는 Google Cloud에서
  YouTube Data API 전용으로 제한해 두는 편이 안전하다
- 배수는 API가 주는 반올림된 현재 구독자 수 기준이라 근사치다. 단정해서 쓰지 말 것

## 지켜야 할 것

1. **숫자는 검증 전까지 발행하지 않는다.** `drafts/`의 초고 맨 위에 경고 블록이 있고, 그 안의 수치는 검색 결과 교차 대조일 뿐 1차 출처 확인이 아니다. 경고 블록은 발행 전에 지운다.
2. **`part1~4-*.png`를 다시 렌더하지 않는다.** 지금과 다른 Inter 빌드로 만들어져서 재렌더하면 숫자 위치가 10px쯤 달라진다. `--profile us`로 전체 렌더 시 덮어써지므로 주의.
3. **CSV에 쉼표가 든 값은 큰따옴표로 감싼다.** 안 그러면 컬럼이 밀리는데 **에러 없이 잘못 렌더된다.** (도구가 경고는 하지만 중단하지는 않음)
4. **표는 이미지가 아니라 HTML 텍스트로.** 이미지 속 숫자는 색인되지 않는다.
5. 이미지는 **직접 제작하거나 라이선스 확인된 것만.** 언론사 사진 금지 — 국내 통신사는 실제로 적극 대응한다.

## 현재 상태

- `drafts/` 초고 3편 (외식비 / 라면 가격 / 편의점 한 끼) — **전부 검증 전, 미발행**
- 세 편은 상호 링크돼 있고 같은 조사에서 나왔다
- 사진 미확보. 촬영 목록: [`docs/photo-shot-list.md`](docs/photo-shot-list.md) (야간 기준으로 작성됨)
- 뉴스 대응 절차: [`docs/newsjacking-playbook.md`](docs/newsjacking-playbook.md)
