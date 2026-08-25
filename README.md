# Korea Rundown — assets & content tooling

[korearundown.blogspot.com](https://korearundown.blogspot.com) (영문) 과 네이버 블로그(국내)
두 채널의 썸네일 에셋과 콘텐츠 작업 도구를 담은 저장소입니다.

## 두 시장

같은 조사에서 두 편이 나오지만, **플랫폼 조건이 반대라 글쓰기 방식은 다릅니다.**

| | 국내 (`kr`) | 영문 (`us`) |
|---|---|---|
| 플랫폼 | 네이버 블로그 | Blogger |
| 검색엔진 | 네이버 | 구글 |
| 수익화 | 애드포스트 (애드센스 불가) | 애드센스 + 제휴 |
| 유튜브 임베드 | 검색상 이점 없음 | 이점 있음 |
| 썸네일 | 1080×1080 → `kr/` | 1200×630 → 루트 |
| 데이터 | `thumbnails.kr.csv` | `thumbnails.us.csv` |
| 가이드 | [`docs/content-guide-kr.md`](docs/content-guide-kr.md) | [`docs/content-guide-us.md`](docs/content-guide-us.md) |
| 글 템플릿 | [`docs/post-template-kr.md`](docs/post-template-kr.md) | [`docs/post-template-us.md`](docs/post-template-us.md) |
| 뉴스 대응 | — | [`docs/newsjacking-playbook.md`](docs/newsjacking-playbook.md) |
| 촬영 목록 | — | [`docs/photo-shot-list.md`](docs/photo-shot-list.md) |

## 이미지 만들기

CSV 한 줄 = PNG 한 장.

```bash
npm install --prefix tools && npx playwright install chromium   # 최초 1회

# 글 대표 썸네일
node tools/render-thumbnails.mjs                  # 영문 (기본)
node tools/render-thumbnails.mjs --profile kr     # 국내
node tools/render-thumbnails.mjs --profile us,kr  # 둘 다

# 본문용 비교 차트 · 추이 차트 · 숫자 카드
node tools/render-figures.mjs
```

옵션과 CSV 형식은 [`tools/README.md`](tools/README.md).

## 구성

```
_template.html          썸네일 템플릿 (CSS 변수로 두 포맷 공용)
thumbnails.us.csv       영문 썸네일 데이터
thumbnails.kr.csv       국내 썸네일 데이터
figures.csv             본문용 이미지 데이터
figures.example.csv     본문용 이미지 예시 (숫자는 전부 예시)
part1~4-*.png           영문 썸네일 (Why Korea Is So Convenient)
tools/                  렌더링 스크립트 · 템플릿 · 프로파일 설정
docs/                   콘텐츠 가이드 · 글 템플릿 · 뉴스 대응 플레이북
```

## 기본 문구 바꾸기

시리즈명·브랜드·블로그 주소는 [`tools/profiles.json`](tools/profiles.json)의
프로파일별 `defaults`에 있습니다. 한 곳만 고치면 이후 렌더되는 모든 썸네일에 반영됩니다.
