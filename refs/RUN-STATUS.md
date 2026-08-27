# 레퍼런스 수집 실행 상태 (5차 시도)

- **실행 시각**: 2026-08-27 09:05 KST (2026-08-27 00:05 UTC)
- **브랜치**: `claude/science-reference-depth-j47fg4`
- **결론**: **수집 실행 못 함.** 다만 **4차까지와 원인이 다르다.**
  `YOUTUBE_API_KEY`는 이번엔 **환경에 들어와 있다.** 그런데 그 **값이 키가 아니라 안내 문구**다.
- **소모 쿼터 0유닛 · 채택 편수 0 · 최근 30일 편수 0**
- **한 줄 원인**: 환경변수 값 칸에 실제 키 대신 **`<구글 키 접두사 6글자>로시작하는실제키`** 라는
  **설명 문구가 그대로 붙여넣어져 있다.** 구글이 `400 API key not valid`로 거절한다.
- **좋은 소식**: 네 번 막혀 있던 ③번 칸(환경변수 전달)은 **이번에 뚫렸다.** 값만 바꾸면 끝이다.

---

## 1. 수집 실행 결과

지시대로 두 명령을 순서대로 돌렸다.

```
$ node tools/collect-refs.mjs --seeds refs/seed-queries.txt --no-shorts --min-subs 1000
  ! last 30 d   공룡 이야기 - search 400: API key not valid. Please pass a valid API key.
  ! search      공룡 이야기 - search 400: API key not valid. Please pass a valid API key.
  ... (검색어 16줄 × 2패스 = 32건 모두 동일)
  ! channel     https://www.youtube.com/@Jabsik_dinosaur - channels 400: API key not valid. ...
  ... (채널 6줄 모두 동일)
error: nothing came back - every search and channel failed
exit:1

$ node tools/analyze-refs.mjs
error: no refs-*.csv in refs/ - run collect-refs.mjs first
exit:1
```

**이번엔 `error: no API key`가 아니다.** 도구는 키 검사를 통과해 실제로 구글에 요청을 보냈고,
구글이 **값을 보고** 거절했다. 이 차이가 4차와 5차를 가른다.

| 항목 | 값 |
|---|---|
| 소모 쿼터 | **0유닛** — 요청 38건 전부 400. 잘못된 키는 쿼터 계정에 붙지 못해 과금되지 않는다. 일일 10,000유닛 전량 미사용 |
| 계획된 쿼터 | **약 4,836유닛** (`--dry-run`으로 확인, 예상치와 일치) |
| 채택 편수 | **0** |
| 최근 30일 편수 | **0** |
| 최근 30일 상위 5편 | **없음** — 수집 자체가 없었다 |
| 그 이전 상위 5편 | **없음** |
| `--recent-ratio 30` 재실행 | **안 돌렸다.** 1차 수집이 0편이라 "최근 30일 3편 미만" 판정에 도달하지 못했다. 쿼터는 10,000 전량 남아 있었으나 돌릴 대상이 없었다 |

생성된 결과 파일 없음 — `refs/refs-*.csv` · `refs/refs-*.md` · `refs/digest-*.md` 모두 미생성.
따라서 이번 커밋의 결과물은 이 문서 하나다.
**소재 제안(3단계)도 하지 않았다.** 근거가 될 `digest-*.md`가 없고, 없는 숫자를 지어내지 않기 위해서다.

### 실패한 채널 핸들

시드의 채널 6줄이 **전부** 실패했다. 다만 **핸들이 틀려서가 아니라 키 때문**이므로,
이 6줄의 유효성은 **여전히 판정 불가**다 (4차와 동일하게 미정).

```
https://www.youtube.com/@Jabsik_dinosaur                        400 invalid key
https://www.youtube.com/channel/UCVKvxjaxcKgFm7BEUw__2gg        400 invalid key
https://www.youtube.com/channel/UCYEokR7TzrZnrIefPM_nZnw        400 invalid key
https://www.youtube.com/@%EC%9A%B0%EB%A7%88UMA        (우마UMA)  400 invalid key
https://www.youtube.com/@TV%EC%83%9D%EB%AC%BC%EB%8F%84%EA%B0%90  (TV생물도감)  400 invalid key
https://www.youtube.com/@kodaiseibutuch                         400 invalid key
```

핸들 오타로 인한 실패(`channel not found` 계열 경고)는 **한 건도 관측되지 않았다.**
어느 핸들이 진짜 죽었는지는 유효한 키로 한 번 돌려야 나온다.

---

## 2. 원인 — 값 칸에 안내 문구가 들어갔다

### 2-1. 이름은 도착했다

```
$ echo "KEY:${YOUTUBE_API_KEY:+set}${YOUTUBE_API_KEY:-missing}"
KEY:set
```

**4차까지 `missing`이던 것이 `set`으로 바뀌었다.** 환경변수 전달 경로 자체는 고쳐졌다.

### 2-2. 값이 키가 아니다

값을 그대로 노출하지 않고 형태만 검사했다.

```
$ printf '%s' "$YOUTUBE_API_KEY" | wc -c        # 바이트
30
$ python3 -c "import os;print(len(os.environ['YOUTUBE_API_KEY']))"   # 글자
14
$ printf '%s' "$YOUTUBE_API_KEY" | tr -d 'A-Za-z0-9_-' | wc -c       # 키에 못 쓰는 문자 수
24
```

- 실제 구글 API 키는 **39글자**이고 전부 `A–Z a–z 0–9 _ -` 안에서만 쓰인다.
- 이 값은 **14글자**인데 그중 **24바이트가 한글**이다.
- 앞 6글자는 구글 키 접두사가 맞지만, 그 뒤가 **`로시작하는실제키`** 라는 한글이다.

즉 값 칸에 들어간 것은 키가 아니라 **"…로 시작하는 실제 키"라는 안내 문구 자체**다.
어딘가의 설명문에서 자리표시자를 통째로 복사해 붙인 형태다.

### 2-3. 구글의 응답 — 지시받은 판정표 대로

```
$ curl -s -o /tmp/probe.json -w "HTTP %{http_code}\n" \
    "https://www.googleapis.com/youtube/v3/videos?part=snippet&id=<임의ID>&key=$YOUTUBE_API_KEY"
HTTP 400
```

**403이 아니라 400이다.** 지시받은 `reason` 판정표의 세 항목 중 **어느 것도 해당하지 않는다.**

| `reason` | 의미 | 이번 판정 |
|---|---|---|
| `accessNotConfigured` | GCP에서 API 미활성화 | **아님** |
| `ipRefererBlocked` | 키에 IP·리퍼러 제한 | **아님** |
| `quotaExceeded` | 쿼터 소진 | **아님** — 0유닛 썼다 |
| `forbidden` (4차) | 요청에 키가 안 실림 | **아님** — 이번엔 실렸다 |
| **`400 API key not valid`** | **값이 키 형식이 아님** | **← 이것** |

구글은 키가 GCP에 도달하기도 전에 **문자열 형식 단계에서** 잘랐다.
그래서 GCP 쪽 설정(API 활성화 여부, 키 제한)은 **이번에도 판정 불가**다 — 아직 시험대에 오르지 못했다.

### 2-4. 네트워크는 정상

`www.googleapis.com`은 프록시를 통과했고, 위 400은 프록시가 아니라 **구글 서버 본체의 응답**이다.
`tools/collect-refs.mjs`가 호출하는 호스트는 `www.googleapis.com` 하나뿐이다.
**Network access 설정은 건드릴 필요 없다.**

---

## 3. 5차까지의 누적 판정

| 단계 | 내용 | 상태 |
|---|---|---|
| ⓪ | 네트워크 허용 도메인에 `www.googleapis.com` | **완료** (3·4·5차 연속 확인) |
| ① | GCP에서 YouTube Data API v3 사용 설정 | **판정 불가** — 아직 값 형식에서 잘려 시험되지 않았다 |
| ② | API 키 발급 | **미완 의심** — 발급된 키가 있었다면 값 칸에 문구가 들어갈 이유가 없다 |
| ③ | 클로드 환경변수에 `YOUTUBE_API_KEY=` 입력 | **경로는 뚫렸다.** 값만 틀렸다 |

**4차까지 막혀 있던 ③이 이번에 열렸다.** 남은 건 ②다.

---

## 4. 사용자가 고칠 지점 — 한 줄

**환경변수 `YOUTUBE_API_KEY`의 값 칸을 열어, 지금 들어 있는 한글 문구를 지우고 실제 키를 붙여넣어라.**

지금 값 → `<구글 키 접두사 6글자>` + `로시작하는실제키` (한글 안내 문구)
넣어야 할 값 → `<구글 키 접두사 4글자>`로 시작하는 **39글자 영숫자 문자열**, 한글 없음

키가 아직 없다면 먼저 발급해야 한다 (②단계):

1. Google Cloud Console → 프로젝트 선택 (없으면 생성)
2. **API 및 서비스 → 라이브러리** → `YouTube Data API v3` → **사용 설정**
3. **API 및 서비스 → 사용자 인증 정보** → **사용자 인증 정보 만들기 → API 키**
4. 나온 39글자 문자열을 복사

그다음 claude.ai/code 입력창 위 **구름 아이콘** → 환경 `기본값`의 **톱니** →
**Environment variables** → `YOUTUBE_API_KEY`의 값 교체 → 저장 → **새 세션**.

붙여넣은 뒤 새 세션에서 이 한 줄이 `ok`면 끝이다:

```
python3 -c "import os,re;k=os.environ.get('YOUTUBE_API_KEY','');print('ok' if re.fullmatch(r'[A-Za-z0-9_-]{39}',k) else 'still wrong: %d chars' % len(k))"
```

- 커밋되는 파일에는 키를 쓰지 않는다. **이 문서에도 키 문자열은 없다.**
- **이 문서는 구글 키 접두사 네 글자를 일부러 적지 않았다.** 커밋 직전 유출 점검이
  `git diff --cached | grep -c <접두사>`로 0인지 보는 방식이라, 문서에 예시로라도 그 문자열을
  써 두면 점검이 매번 걸려 쓸모가 없어진다. 그래서 자리표시자로만 쓴다.

---

## 5. 대기 중인 것 — 유효한 키만 들어오면 그대로 돈다

- `refs/seed-queries.txt` — 검색어 16줄(공룡 5 · 고생물 4 · 현생 거대생물 4 · 멸종/괴물 3) + 채널 6줄.
  `--dry-run` 실측 **약 4,836유닛**. 일일 10,000 안이라 하루 두 번 돌릴 수 있다.
- 시드의 채널 6줄은 웹 검색으로 주운 **미확인 핸들**이라 일부가 실패할 수 있다.
  이번에도 판정하지 못했다. 근거·제외 목록은 `refs/candidate-channels.md`.
- `tools/collect-refs.mjs` · `tools/analyze-refs.mjs` — **정상 동작 확인.**
  Node v22.22.2에서 인자 파싱 → 시드 해석 → HTTP 요청 발신까지 전부 통과했고,
  구글의 400을 받아 **줄마다 경고를 남기고 끝까지 진행한 뒤** 종료했다.
  도구 쪽에 고칠 것은 없다.
- 최근 30일 창이 3편 미만이면 `--recent-ratio 30`으로 한 번 더 돌려 그 결과를 채택하는 규칙도 그대로 대기 중.
- **소재 제안 3개**는 `refs/digest-*.md`가 나온 다음이다. 없는 숫자로 쓰지 않는다.

네 칸 중 세 칸이 섰다. 마지막 한 칸은 **값 교체 한 번**이다.
