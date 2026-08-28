# 이미지 렌더링 도구

CSV 한 줄 = PNG 한 장. 도구는 두 개입니다.

| 스크립트 | 만드는 것 | 템플릿 |
|---|---|---|
| `render-thumbnails.mjs` | **글 대표 썸네일** (시장별 규격) | `_template.html` |
| `render-figures.mjs` | **본문용 차트·숫자 카드** | `figure-template.html` |
| `build-post.mjs` | **초고 → 발행 형식 변환** (`post.html` + `meta.json`) | — |
| `grab-frames.mjs` | **편집 영상에서 본문 이미지 추출** | — |

디자인은 전부 템플릿에 있고, 스크립트는 값을 채워 넣고 크기를 맞춘 뒤 캡처만 합니다.
아래 **폰트** 절은 두 도구에 공통으로 적용됩니다.

## 준비 (최초 1회)

```bash
npm install --prefix tools
npx playwright install chromium
```

## 프로파일 — 시장별로 분리돼 있습니다

영문 블로그와 국내 블로그는 규격도 문구도 다르므로 **프로파일**로 나눠 뒀습니다.
각 프로파일은 자기 CSV · 출력 폴더 · 이미지 크기 · 기본 문구를 가집니다.

| 프로파일 | 대상 | 크기 | CSV | 출력 |
|---|---|---|---|---|
| `us` (기본) | 영문 블로그 (Blogger) | 1200×630 | `thumbnails.us.csv` | 저장소 루트 |
| `kr` | 국내 블로그 (네이버) | 1080×1080 | `thumbnails.kr.csv` | `kr/` |

```bash
node tools/render-thumbnails.mjs --list-profiles
```

`us`는 OG 미리보기 규격, `kr`은 네이버 목록·검색의 정사각 대표이미지에 맞춘
크기입니다. 정의는 [`profiles.json`](profiles.json)에 있고, 크기·여백·기본 문구를
바꾸려면 그 파일만 고치면 됩니다.

## 썸네일 렌더링

```bash
node tools/render-thumbnails.mjs                  # us (기본)
node tools/render-thumbnails.mjs --profile kr     # 국내용
node tools/render-thumbnails.mjs --profile us,kr  # 둘 다
```

4장 기준 약 1.5초.

```bash
# 일부만 다시 그리기
node tools/render-thumbnails.mjs --only part3-subway,part4-healthcare

# 이미 있는 파일은 건너뛰기 (새로 추가한 행만 렌더)
node tools/render-thumbnails.mjs --profile kr --skip-existing

# 다른 폴더에 2배 해상도로
node tools/render-thumbnails.mjs --out drafts --scale 2

# 뭐가 써질지만 확인
node tools/render-thumbnails.mjs --profile us,kr --dry-run
```

`--csv` / `--out`은 프로파일 설정을 덮어씁니다. 단 프로파일을 하나만 지정했을 때만 쓸 수 있습니다.

## 썸네일 CSV 형식

| 컬럼 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `slug` | ✅ | — | 출력 파일 이름 (`<slug>.png`). 영숫자 · `.` `_` `-`만 |
| `figure` | ✅ | — | 큰 숫자 |
| `kicker` | ✅ | — | 숫자 아래 문장 |
| `unit` | | 없음 | 숫자 뒤 작은 단위 |
| `part` | | 없음 | 상단 우측 파트 표기. 비우면 표시 안 함 |
| `series` | | 프로파일 기본값 | 상단 시리즈명 |
| `brand` | | 프로파일 기본값 | 좌하단 |
| `sub` | | 프로파일 기본값 | 우하단 |

컬럼 순서는 상관없고, 없는 컬럼은 **해당 프로파일의 기본값**이 쓰입니다.
`series` · `brand` · `sub`를 시장마다 다시 적을 필요가 없다는 뜻입니다.

| | `us` | `kr` |
|---|---|---|
| `series` | `Why Korea Is So Convenient` | `한국이 편한 이유` |
| `brand` | `KOREA RUNDOWN` | `코리아 런다운` |
| `sub` | `korearundown.blogspot.com` | `blog.naver.com/clark315` |

### 카피 표기법

- `**강조**` → 골드 컬러 굵은 글씨
- `|` → 줄바꿈
- 쉼표(`,`)가 들어가는 값은 `"1,234"`처럼 큰따옴표로 감싸기

```csv
slug,part,figure,unit,kicker
part3-subway,Part 3,$1.10,a ride,Seoul charges for **distance**.|New York charges for the turnstile.
```

### 자동 크기 조정

숫자가 가로로 넘치거나 문장이 세로로 넘치면 자동으로 줄어듭니다
(`us` 기준 숫자 150→56px, 문장 37→22px. 범위는 프로파일의 `type`에서 조정).
줄어든 경우 실행 로그에 `auto-fit`으로 표시됩니다.
최소 크기로도 안 들어가면 경고와 함께 종료 코드 1을 반환하니, 그 행은 카피를 줄이면 됩니다.

한글은 `word-break: keep-all`이 적용돼 어절 중간에서 끊기지 않습니다.

## 본문용 이미지 (`render-figures.mjs`)

썸네일과 별개로, **글 본문에 넣을 비교 차트·추이 차트·숫자 카드**를 CSV에서 뽑습니다.
디자인은 [`figure-template.html`](figure-template.html)에 있습니다.

```bash
node tools/render-figures.mjs                          # figures.csv -> figures/
node tools/render-figures.mjs --csv figures.example.csv --out /tmp/demo   # 예시 4장
node tools/render-figures.mjs --theme dark             # 썸네일과 같은 딥틸 배경
```

기본 출력은 **640 CSS px 폭을 2배 해상도(1280px)** 로 렌더합니다. 블로그 본문 폭에 맞춰
`--width`를 조정하세요. 높이는 내용에 따라 자동으로 정해집니다.

### 타입 3가지

| `type` | 쓸 곳 | 형태 |
|---|---|---|
| `bar` | **한국 vs 미국 비교** (핵심 용도) | 가로 막대, 최대 6개 |
| `column` | 연도별 추이 | 세로 막대 + y축 눈금 |
| `stat` | 숫자 카드 | 큰 숫자 타일, 최대 3개 |

### CSV 컬럼

| 컬럼 | 필수 | 설명 |
|---|---|---|
| `slug` | ✅ | 출력 파일명 |
| `type` | ✅ | `bar` · `column` · `stat` |
| `title` | ✅ | 제목. **결론을 문장으로** 쓰세요 (`Seoul costs a third of New York's`) |
| `series` | ✅ | `이름=값\|이름=값` — 값은 숫자 |
| `subtitle` | | 제목 아래 한 줄. 단위·기준을 여기에 |
| `highlight` | | 강조할 항목 이름 (기본값: 첫 번째) |
| `prefix` / `suffix` | | 값 앞뒤에 붙일 문자 (`$`, `원` 등) |
| `note` | | 좌하단 출처 줄 |
| `theme` | | `light`(기본) · `dark` — 행마다 지정 가능 |

> ⚠️ **쉼표가 든 값은 큰따옴표로 감싸세요.** `"서울=1,400\|뉴욕=4,000"` — 안 그러면
> CSV 컬럼이 쪼개집니다.

**숫자는 쓴 그대로 표시됩니다.** `1.10`이라 쓰면 `1.10`, `1,550`이라 쓰면 `1,550`.
소수점 자릿수와 천 단위 쉼표를 직접 통제하시라는 뜻입니다. 막대 길이는 숫자값으로 계산합니다.

### 설계 규칙 (지켜져 있습니다)

- **강조 형식** — 한국은 골드, 비교 대상은 회색. 값 크기에 따라 색을 바꾸지 않습니다
  (막대 길이가 이미 보여주는 걸 색이 중복으로 표현하면 안 됨)
- 막대는 22px 이하, 데이터 끝만 4px 둥글게, **0에서 시작**
- 값은 각 막대 끝에 붙고, 도형 밖으로 넘치지 않게 막대 길이를 자동으로 줄입니다
- 세로 차트는 y축 눈금이 있어서, 직접 라벨이 안 붙은 막대도 값을 읽을 수 있습니다
- 격자선은 1px 실선, 배경에서 한 단계만 떨어진 색
- **텍스트에는 데이터 색을 쓰지 않습니다** — 색은 막대가, 글자는 잉크 색이 담당

팔레트는 밝은/어두운 두 배경 모두에서 대비·색각이상 분리도 검사를 통과한 값입니다
(검증 내역은 `figure-template.html` 상단 주석 참고). 색을 바꾸실 거면 재검증이 필요합니다.

### 표는 이미지로 만들지 마세요

이미지 속 숫자는 검색에 색인되지 않습니다. **표는 HTML 텍스트로, 차트는 이미지로** —
둘은 역할이 다릅니다. 자세한 내용은
[`docs/content-guide-us.md` §5](../docs/content-guide-us.md).

### 예시 파일

`figures.example.csv`에 4가지 타입 예시가 있습니다. **거기 숫자는 전부 예시이고
검증되지 않았습니다** — 이미지 하단에도 그렇게 찍히니, 실제 발행에는
`figures.csv`에 검증된 값을 직접 채워 쓰세요.

---

## 폰트 (두 도구 공통)

템플릿은 Google Fonts에서 **Inter**(영문)와 **Noto Sans KR**(한글)을 불러옵니다.
따라서 렌더링에는 네트워크가 필요합니다.

폰트를 못 받아오면 스크립트가 **에러로 중단**합니다. 대체 폰트로 그려진 PNG는
기존 썸네일과 미묘하게 어긋나는데, 그게 조용히 섞이는 걸 막기 위한 장치입니다.

- 그래도 그냥 뽑고 싶으면 → `--allow-fallback-fonts`
- 오프라인 / CI에서 결정적으로 렌더하려면 → `--font-css <경로>`

### 오프라인용 폰트 준비 (선택)

Inter는 가변 폰트 파일 하나(약 48KB)로 모든 굵기를 커버합니다.

```bash
mkdir -p tools/fonts
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
curl -sS -A "$UA" "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" \
  | grep -B1 'unicode-range: U+0000-00FF' | grep -oE 'https://[^)]*woff2' | head -1 \
  | xargs curl -sS -A "$UA" -o tools/fonts/inter.woff2

cat > tools/fonts/local.css <<'CSS'
@font-face{font-family:'Inter';font-style:normal;font-weight:100 900;
  font-display:block;src:url('inter.woff2') format('woff2');}
CSS

node tools/render-thumbnails.mjs --profile us,kr --font-css tools/fonts/local.css
```

`tools/fonts/`는 `.gitignore`에 있습니다. 한글(Noto Sans KR)은 유니코드 구간별로
124개 파일로 쪼개져 있어(약 3.7MB) 내장하지 않았습니다. 한글 썸네일은 네트워크가 필요합니다.

## 옵션 — `render-thumbnails.mjs`

`render-figures.mjs`의 옵션은 `node tools/render-figures.mjs --help`로 보세요.

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--profile <name,...>` | `us` | 렌더할 시장. 쉼표로 여러 개 |
| `--list-profiles` | — | 설정된 프로파일 보기 |
| `--csv <path>` | 프로파일 설정 | 입력 CSV (단일 프로파일에서만) |
| `--out <dir>` | 프로파일 설정 | 출력 폴더 (단일 프로파일에서만) |
| `--template <path>` | `_template.html` | 템플릿 |
| `--only <slug,...>` | — | 지정한 행만 |
| `--scale <n>` | `1` | 2면 2400×1260 |
| `--concurrency <n>` | `4` | 동시 렌더 수 |
| `--skip-existing` | off | 기존 PNG 유지 |
| `--font-css <path>` | — | 로컬 폰트 사용 |
| `--allow-fallback-fonts` | off | 폰트 실패해도 진행 |
| `--dry-run` | off | 쓰지 않고 확인만 |

## 기존 4장에 대한 참고

저장소에 이미 있는 `part1`~`part4` PNG는 지금과 다른 Inter 빌드로 만들어졌습니다.
지금 스크립트로 다시 그리면 숫자 위치가 10px 남짓 달라집니다(디자인은 동일).
새로 뽑는 썸네일끼리는 당연히 일관되며, 기존 4장까지 통일하고 싶으면
`node tools/render-thumbnails.mjs`를 한 번 돌려 전부 덮어쓰면 됩니다.

---

## 초고를 발행 형식으로 (`build-post.mjs`)

`drafts/*.md`는 마크다운이라 `autoworker-script` 파이프라인이 바로 못 먹습니다.
이 도구가 **`post.html` + `meta.json`** 으로 바꿔줍니다.

```bash
node tools/build-post.mjs drafts/2026-08-25-seoul-lunch-prices.md --labels "Food,Prices,Seoul"
```

`blog/posts/<slug>/`에 생성됩니다. 그 폴더를 `autoworker-script`의
`channels/<채널>/blog/posts/` 아래로 옮기면 업로더가 인식합니다.

| 옵션 | 설명 |
|---|---|
| `--labels <a,b>` | Blogger 라벨 |
| `--description <text>` | 검색 설명 (기본: 첫 문단 앞부분) |
| `--slug <name>` | 폴더 이름 (기본: 파일명에서 날짜 제거) |
| `--out <dir>` | 출력 위치 (기본: `blog/posts`) |

**자동으로 처리되는 것**

- 초고 맨 위 **한국어 경고 블록**과 맨 아래 **발행 체크리스트**는 제거됩니다 (작성자용 메모라서)
- `# 제목`은 본문에서 빠지고 `meta.json`의 `title`로 갑니다
- 표·인용·목록에 인라인 style이 붙습니다 (Blogger가 `<style>` 태그를 지웁니다)
- 이미지는 **raw.githubusercontent.com 주소로 바뀝니다.** 이 저장소가 public이라 그대로 호스팅됩니다.
  따라서 **이미지를 먼저 푸시한 뒤** 변환해야 합니다

**수동으로 해야 하는 것**

- 아직 발행 안 된 글끼리의 **내부 링크는 링크가 아니라 굵은 글씨로 렌더**됩니다.
  URL이 없으니까요. 변환 시 목록으로 알려주니, 발행 후 `post.html`에서 `<a>`로 바꾸세요
- `meta.json`의 `search_description`은 첫 문단을 자른 것이라 다듬는 편이 낫습니다

---

## 영상에서 본문 이미지 뽑기 (`grab-frames.mjs`)

**편집이 끝난 영상에는 이미 자막과 그래픽이 들어가 있습니다.** 거기서 프레임을 뽑으면
새로 디자인할 필요 없이 브랜드가 맞는 본문 이미지가 나옵니다. 촬영하러 나갈 일도 없고요.

```bash
# 1. 어느 장면을 쓸지 고르기 — 컨택트 시트
node tools/grab-frames.mjs "C:\\영상\\편의점편.mp4" --sheet

# 2. 고른 시점만 추출
node tools/grab-frames.mjs "C:\\영상\\편의점편.mp4" --at 0:12,1:47,3:05
```

`figures/`에 저장되고, 커밋·푸시하면 raw.githubusercontent 주소로 본문에 바로 쓸 수 있습니다.

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--sheet` | — | 균등 간격 프레임을 격자로 만들어 시점 고르기용 |
| `--tiles <n>` | `12` | 시트에 넣을 프레임 수 (3의 배수가 보기 좋음) |
| `--at <t,...>` | — | 추출할 시점. `0:12` · `1:47.5` · `95` 형식 |
| `--width <px>` | `1280` | 출력 가로 폭 |
| `--out <dir>` | `figures` | 저장 위치 |
| `--ffmpeg <path>` | 자동 탐색 | ffmpeg 경로 |

**ffmpeg가 필요합니다.** PATH에 있으면 그걸 쓰고, 없으면 Playwright 번들을 찾습니다.
다만 번들 빌드는 기능이 축소돼 있어서 **격자 조립은 ffmpeg가 아니라 Chromium이 합니다** —
어느 ffmpeg를 쓰든 시트가 나옵니다.

### 영상 임베드

`build-post.mjs`에 `--youtube`를 주면 본문 끝에 임베드가 붙고 `meta.json`에도 기록됩니다.

```bash
node tools/build-post.mjs drafts/<파일>.md --youtube "https://www.youtube.com/watch?v=..."
```

형식은 채널 공용 규칙(`autoworker-script`의 `블로그작성.md` 4절)을 따릅니다:

- **반응형 래퍼를 쓰지 않습니다.** Blogger가 그 CSS를 걷어내 영상이 사라집니다
- **대체 링크를 함께 넣습니다.** 광고차단 확장이 iframe을 막으면 자리가 통째로 비어서요
- 영상이 실제로 올라온 뒤에 붙이세요. 없는 영상을 임베드하면 나중에 고치는 품이 더 듭니다

특정 장면부터 재생시키려면 임베드 주소 끝에 `?start=107`(초)을 붙이면 됩니다.

---

## 레퍼런스 수집 (`collect-refs.mjs`)

**사이언스 썰 영상 중 "제 채널 크기보다 훨씬 크게 터진 것"만 모읍니다.**
기준은 한 줄입니다 — **조회수 ≥ 구독자 × 100** (`--ratio`, 기본 100).
구독자 대비 배수가 큰 영상은 구독자층 밖으로 나간 영상이고, 레퍼런스로 볼 값어치가
있는 건 그런 영상뿐입니다.

**최근 30일이 최우선입니다** (`--recent`, 기본 30). 그 창에 든 영상은 별도 검색을
한 번 더 돌려서 찾고, CSV·표에서 맨 위에 옵니다.

```bash
# 최초 1회: YouTube Data API v3 키 발급 (Google Cloud Console)
export YOUTUBE_API_KEY=...        # Windows PowerShell: $env:YOUTUBE_API_KEY="..."

# 무엇을 몇 유닛 써서 훑을지 먼저 확인
node tools/collect-refs.mjs --seeds refs/seed-queries.txt --dry-run

# 수집 — 쇼츠 제외, 200 구독자 미만 채널의 우연한 대박 제외
node tools/collect-refs.mjs --seeds refs/seed-queries.txt --no-shorts --min-subs 1000

# 쓸 만한 채널을 찾았으면 그 채널만 훑기 (쿼터가 거의 안 듭니다)
node tools/collect-refs.mjs --channel @핸들 --since 2025-01-01
```

`refs/refs-<날짜>.csv`(스프레드시트용)와 `refs/refs-<날짜>.md`(바로 읽는 표)가 나옵니다.
배수 내림차순 정렬입니다.

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--seeds <file>` | — | 검색어·채널을 한 줄에 하나씩 (`refs/seed-queries.txt`) |
| `--query <text>` | — | 검색어 하나. 여러 번 쓸 수 있음 |
| `--channel <id\|@핸들\|url>` | — | 그 채널 업로드를 훑음. 여러 번 쓸 수 있음 |
| `--ratio <n>` | `100` | 조회수 ÷ 구독자가 이 값 이상이면 채택 |
| `--recent <days>` | `30` | 우선 창. 별도 검색 + 상단 정렬. `0`이면 끔 |
| `--recent-ratio <n>` | `--ratio`와 동일 | 그 창 안에서만 쓰는 문턱 |
| `--since <YYYY-MM-DD>` | — | 이 날짜 이전 공개분 제외 |
| `--pages <n>` | `2` | 검색어당 결과 페이지 수 (한 페이지 50편·100유닛) |
| `--recent-pages <n>` | `1` | 최근 창 검색에 쓸 페이지 수 |
| `--uploads <n>` | `200` | 채널당 최신 업로드 몇 편까지 볼지 |
| `--min-subs <n>` | `0` | 구독자 하한 |
| `--min-views <n>` | `0` | 조회수 하한 |
| `--no-shorts` | — | 60초 이하 제외 |
| `--exclude <패턴>` | — | 제목·채널명이 이 정규식에 걸리면 제외. 여러 번 가능 |
| `--region` / `--lang` | `KR` / `ko` | 검색 지역·언어 |
| `--out` / `--name` | `refs` / `refs-<오늘>` | 저장 위치·파일명 |
| `--dry-run` | — | 호출 없이 계획과 예상 쿼터만 출력 |

### 배수만으로는 소재가 안 걸러집니다

**100배 기준은 "얼마나 퍼졌나"만 봅니다. 무엇에 대한 영상인지는 안 봅니다.**
2026-08-27 첫 수집에서 채택 22편 중 절반 가까이가 **유아용 만화·동요·게임 실황**이었습니다.
공룡 키즈 채널과 게임 스트리머가 이 기준을 아주 쉽게 넘습니다.

```bash
node tools/collect-refs.mjs --seeds refs/seed-queries.txt --no-shorts --min-subs 1000 \
  --exclude "키즈|kids|kidz|어린이|동요|만화|cartoon|toy|장난감" \
  --exclude "게임|gameplay|로블록스|roblox|마인크래프트|minecraft|서브노티카|subnautica" \
  --exclude "- Topic"
```

`- Topic`은 유튜브가 자동 생성하는 음원 채널입니다. 실행 끝에 몇 편이 이 패턴으로
빠졌는지 찍히니, 너무 많이 빠지면 패턴을 좁히세요.

### 숫자를 읽을 때 주의할 것

- **구독자 수는 API가 유효숫자 3자리로 반올림해서 줍니다.** 12,345명은 12,300명으로
  옵니다. 배수는 근사치로 읽으세요
- **배수는 "오늘의 구독자 수" 기준입니다.** 2년 전 영상이 터진 뒤 채널이 컸다면
  실제로 터졌던 배수는 여기 적힌 것보다 큽니다. 반대로 최근에 급성장한 채널은
  과거 영상이 실제보다 작게 잡힙니다
- **구독자 수를 숨긴 채널은 통째로 빠집니다.** 나눗셈이 성립하지 않아서요. 몇 편이
  그렇게 빠졌는지 실행 끝에 찍힙니다
- **구독자가 아주 적은 채널은 100배를 우연히 넘깁니다.** 200명 채널의 3만 조회는
  150배지만 레퍼런스는 아닙니다. `--min-subs`로 바닥을 깔아 두세요
- **쿼터**: 검색 1페이지 100유닛, 나머지 호출 1유닛, 무료 한도 하루 10,000.
  기본값에서 검색어 하나가 300유닛(일반 2페이지 + 최근 창 1페이지)이라 하루 30개쯤
  돌립니다. 검색은 아껴 쓰고 채널 훑기로 옮겨 가는 편이 낫습니다

### 최근 30일이 왜 따로 도는가

**조회수순 검색은 몇 년 묵은 대박이 결과를 다 차지합니다.** 최근 것만 골라내려고
전체 검색 결과를 나중에 거르면 애초에 목록에 오르질 못합니다. 그래서 날짜를 건
검색을 한 번 더 돌립니다. 그 대신 검색어당 비용이 200 → 300유닛으로 늘어납니다.

**그리고 최근 영상은 배수에서 손해를 봅니다.** 조회수는 시간이 지나야 쌓이니까요.
사흘 된 영상의 30배가 2년 된 영상의 300배보다 셀 수 있습니다. 그래서:

- 최근 창이 얇게 나오면 `--recent-ratio 30` 처럼 그 창에만 문턱을 낮추세요.
  100배 기준은 그대로 두고 최근 것만 느슨하게 봅니다
- 실행 끝에 `building` 줄이 뜨면, 문턱의 1/4 ~ 문턱 사이에 있는 최근 영상이
  몇 편인지 알려주는 것입니다. 아직 오르는 중일 수 있다는 신호입니다
- 결과의 `window` 열이 `recent`/`older`, `age_days` 열이 공개 후 지난 날수입니다.
  스프레드시트에서 다시 정렬할 때 쓰세요

날짜를 좁힐 때 `--since`가 최근 창보다 뒤면 검색이 겹치므로 한 번만 돕니다.

---

## 밤에 알아서 돌리기 (`refs-nightly.mjs`)

**수집 → 다이제스트 → 커밋 → 푸시를 한 번에 합니다.** 결과가 저장소에 올라가므로,
다음 세션에서 CSV를 붙여넣을 필요 없이 클로드가 `refs/`를 바로 읽습니다.

```bash
node tools/refs-nightly.mjs                    # 전체 한 번
node tools/refs-nightly.mjs --no-push          # 커밋만
node tools/refs-nightly.mjs -- --recent-ratio 30   # -- 뒤는 collect-refs로 전달
```

윈도우 작업 스케줄러 등록 — **키 넣고 한 줄, 두 번만 실행하면 끝입니다:**

```bat
setx YOUTUBE_API_KEY "발급받은키"
schtasks /create /tn "korea-refs" /tr "C:\...\korea-rundown-assets\tools\refs-nightly.cmd" /sc daily /st 05:00
```

- 새 결과가 없으면 커밋하지 않습니다. 빈 커밋으로 로그가 지저분해지지 않습니다
- 커밋 메시지에 채택 편수와 최근 창 편수가 들어갑니다 (`refs: 2026-08-26 - 12 kept, 4 recent`)
- 실패하면 종료 코드가 0이 아니라 스케줄러 기록에 남습니다

### 다이제스트 (`analyze-refs.mjs`)

`refs-nightly`가 자동으로 부르지만, 따로 돌릴 수도 있습니다.

```bash
node tools/analyze-refs.mjs                    # 가장 최근 refs-*.csv
node tools/analyze-refs.mjs refs/refs-2026-08-26.csv --top 20
```

`refs/digest-<날짜>.md`에 이런 것들이 정리됩니다.

- **최근 창 상위** — 중앙 배수와 최고 배수 포함
- **두 번 이상 터진 채널** — 우연이 아니라 포맷인 쪽. `--channel @핸들`로 파고들 대상
- **길이 구간별 중앙 배수** — 어느 길이가 실제로 터지는지
- **제목이 쓰는 수** — 충격·소름 / 실화 / 왜 / 최상급 / 만약 / 정체 / 크기 숫자 / 물음표 빈도
- **반복되는 소재어** — 제목에 두 번 이상 나온 단어. 다음 회차 시드 조정용

소재어 추출은 조사·어미를 떼는 정도의 단순한 방식입니다. 경향을 보는 용도지
정밀한 형태소 분석이 아닙니다.
