# 썸네일 일괄 렌더링

CSV 한 줄 = 썸네일 PNG 한 장. 디자인은 전부 `_template.html`에 있고,
이 스크립트는 값을 채워 넣고 글자 크기를 맞춘 뒤 1200×630으로 캡처만 합니다.

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

## 실행

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

## CSV 형식

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

## 폰트

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

## 옵션

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
