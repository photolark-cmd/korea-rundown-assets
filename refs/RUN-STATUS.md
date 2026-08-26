# 레퍼런스 수집 실행 상태 (4차 시도)

- **실행 시각**: 2026-08-27 08:30 KST (2026-08-26 23:30 UTC)
- **브랜치**: `claude/science-reference-depth-j47fg4`
- **컨테이너 기동**: 2026-08-26 23:29:25 UTC (08:29 KST) — 3차 시도(21:18 UTC) 이후 **약 2시간 만에 새로 뜬 컨테이너**
- **결론**: **수집 실행 못 함.** `YOUTUBE_API_KEY`가 이 세션 환경에 **없다.** 4차 연속 동일.
- **소모 쿼터 0유닛 · 채택 편수 0 · 최근 30일 편수 0**
- **네트워크는 이번에도 정상**이다. `www.googleapis.com` 통과 확인. 막힌 건 키 하나뿐이다.
- **권고가 바뀐다**: 환경변수 입력이 네 번 연속 반영되지 않았다. 다섯 번째로 같은 방법을 다시 시도할 게 아니라, **`--key`로 직접 넘기는 쪽으로 갈아타는 것을 권한다.** (4장)

---

## 1. 수집 실행 결과

```
$ node tools/collect-refs.mjs --seeds refs/seed-queries.txt --no-shorts --min-subs 1000
error: no API key - set YOUTUBE_API_KEY or pass --key

$ node tools/analyze-refs.mjs
error: no refs-*.csv in refs/ - run collect-refs.mjs first
exit:1
```

지시대로 `error: no API key`를 확인한 즉시 이 기록으로 넘어왔다.

| 항목 | 값 |
|---|---|
| 소모 쿼터 | **0유닛** (API 호출 0회 — 일일 10,000유닛 전량 미사용) |
| 채택 편수 | **0** |
| 최근 30일 편수 | **0** |
| 최근 30일 상위 5편 | **없음** — 수집 자체가 없었다 |
| 그 이전 상위 5편 | **없음** |
| 실패한 채널 핸들 | **없음** — 핸들 조회를 한 번도 시도하지 못했다. 시드의 채널 6줄은 성공·실패 판정 자체가 미정 |
| `--recent-ratio 30` 재실행 | **안 돌렸다.** 1차 수집이 없으니 "최근 30일 3편 미만" 판정 조건에 도달하지 못했다. 쿼터는 남아 있었으나 돌릴 대상이 없었다 |

생성된 결과 파일 없음:

```
$ ls refs/refs-*.csv refs/refs-*.md refs/digest-*.md
ls: cannot access 'refs/refs-*.csv': No such file or directory
ls: cannot access 'refs/refs-*.md': No such file or directory
ls: cannot access 'refs/digest-*.md': No such file or directory
```

따라서 이번 커밋에 포함되는 결과물은 이 문서 하나다. **소재 제안(3단계)도 근거가 될 `digest-*.md`가 없어 하지 않았다.** 숫자를 지어내지 않기 위해서다.

---

## 2. 이번 세션에서 실제로 확인한 것

### 2-1. 키 부재 — 이름·값 양쪽으로 확인

```
$ echo "KEY:${YOUTUBE_API_KEY:+set}${YOUTUBE_API_KEY:-missing}"
KEY:missing

$ env | grep -oE "^[A-Z_]*(YOUTUBE|YT|GOOGLE|API|KEY)[A-Z_]*=" | sort
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
PYTHONUNBUFFERED=

$ env | grep -c "<구글 키 접두사 4글자>"   # 실제로는 접두사를 그대로 넣어 돌렸다
0

$ env | sed -E 's/=.*//' | sort | wc -l
133
```

이름으로도, 값 형식(구글 키 접두사)으로도 잡히는 게 없다. 걸린 셋은 컨테이너 기본 변수다. 파일 경로 대체 수단도 없다 — 저장소에 `.env`가 없고(`.gitignore`는 `node_modules/`·`tools/fonts/` 두 줄뿐), 시스템 전체 `.env` 검색도 0건이다.

### 2-2. 403의 `reason` — 지시받은 판정표 대로

```
$ curl -sS -o /tmp/yt.json -w "HTTP:%{http_code} exit:%{exitcode}\n" \
    "https://www.googleapis.com/youtube/v3/videos?part=id&id=dQw4w9WgXcQ&key="
HTTP:403 exit:0
```

```json
{ "error": { "code": 403,
  "message": "Method doesn't allow unregistered callers (callers without established identity). ...",
  "errors": [ { "domain": "global", "reason": "forbidden" } ],
  "status": "PERMISSION_DENIED" } }
```

**`reason: forbidden`.** 지시받은 표의 세 항목 중 어느 것도 아니다:

| `reason` | 의미 | 이번 판정 |
|---|---|---|
| `accessNotConfigured` | GCP에서 API 미활성화 | **아님** |
| `ipRefererBlocked` | 키 제한 | **아님** |
| `quotaExceeded` | 쿼터 소진 | **아님** — 0유닛 썼다 |
| `forbidden` | **요청에 키가 아예 실리지 않음** | **← 이것** |

`Method doesn't allow unregistered callers`는 `key=` 뒤가 빈 문자열일 때 나오는 응답이다. GCP 설정 문제도, 키 값 문제도 아니고 **키가 컨테이너에 도달하지 않은 것**이다. curl 종료코드 0 — 연결·응답 수신은 정상.

### 2-3. 네트워크는 이번에도 열려 있다

`www.googleapis.com`은 프록시를 통과했고, 위 403은 프록시가 아니라 **구글 서버 본체가 준 JSON 응답**이다. `www.youtube.com`은 여전히 CONNECT 403으로 막혀 있지만 **문제되지 않는다** — `tools/collect-refs.mjs`가 실제로 호출하는 호스트는 `www.googleapis.com` 하나뿐이고, 코드에 나오는 `www.youtube.com`은 CSV·마크다운에 적어 넣는 링크 문자열일 뿐 네트워크 호출이 아니다.

```
$ curl -sS -o /dev/null -w "HTTP:%{http_code}\n" "https://www.youtube.com/"
curl: (56) CONNECT tunnel failed, response 403
```

**Network access 설정은 건드릴 필요 없다. 지금 상태 그대로면 수집은 온전히 돈다.**

### 2-4. "설정이 반영될 시간이 없었다"도 아니다

이 컨테이너는 3차 시도보다 **2시간 뒤인 23:29:25 UTC에 새로** 떴다. 옛 컨테이너가 새 변수를 못 받은 상황이 아니다.

환경 목록도 다시 조회했다 — 계정에 환경은 여전히 **하나뿐**이다:

```
environment_id: env_01ApTTBGFHba6yRJzEhDn3mh   name: "기본값"   state: active
has_more: false
```

키를 넣을 수 있는 다른 환경이 없으므로 "다른 환경에 넣었을 것"이라는 설명은 성립하지 않는다.

---

## 3. 4차까지의 누적 판정

| 단계 | 내용 | 상태 |
|---|---|---|
| ⓪ | 네트워크 허용 도메인에 `www.googleapis.com` | **완료** (3차·4차 연속 확인) |
| ① | GCP에서 YouTube Data API v3 사용 설정 | 판정 불가. **원인 아님** — 미활성화면 `accessNotConfigured`가 떠야 한다 |
| ② | API 키 발급 | 판정 불가. **원인 아님** — 키 값이 틀렸다면 `badRequest`가 떠야 한다 |
| ③ | 클로드 환경변수에 `YOUTUBE_API_KEY=` 입력 | **← 4차 연속 여기서 막힘** |

같은 설정 창의 Network access는 반영됐는데 Environment variables만 네 번 연속 반영되지 않았다. 창을 열고 저장한 것 자체는 맞다는 뜻이다.

---

## 4. 사용자가 고칠 지점 — 한 줄

**환경변수 칸을 다섯 번째로 다시 시도하지 말고, 다음 세션에서 키를 명령에 직접 실어 보내라:**

```
node tools/collect-refs.mjs --seeds refs/seed-queries.txt --no-shorts --min-subs 1000 --key <발급받은_키>
```

(`<발급받은_키>` 자리에 실제 키를 넣어 이 한 줄을 그대로 주면 된다. 이후는 `node tools/analyze-refs.mjs`까지 자동으로 이어간다.)

- 키가 **대화 로그에 남는다.** 수집이 끝난 뒤 GCP 콘솔에서 그 키를 폐기하고 새로 발급하면 된다. 네 번 실패한 경로를 또 밟는 것보다 이쪽이 확실하다.
- 커밋되는 파일에는 키를 쓰지 않는다. 이 문서에도 키 문자열은 없다.
- **이 문서는 구글 키 접두사 네 글자조차 일부러 적지 않았다.** 커밋 직전 유출 점검이
  `git diff --cached | grep -c <접두사>`로 0인지 보는 방식이라, 문서에 예시로라도 그 문자열을
  써 두면 점검이 매번 걸려 쓸모가 없어진다. 그래서 자리표시자는 `<발급받은_키>`로 쓴다.

### 그래도 환경변수로 하겠다면

claude.ai/code 입력창 위 **구름 아이콘** → 환경 `기본값`의 **톱니** → 창을 아래로 내려 **Environment variables** → 이름 `YOUTUBE_API_KEY`, 값 `<발급받은_키>` → 저장 → **새 세션**을 띄운다. Network access는 이미 되어 있으니 건드리지 말 것.

네 번 실패했으므로 다음을 같이 확인할 만하다:
- 이름 칸에 공백·등호가 섞였는지 (`YOUTUBE_API_KEY =`)
- 이름·값을 넣고 **행 추가/확정을 누르지 않은 채** 창을 닫았는지
- 저장 후 **기존 세션을 이어서** 열었는지 (기존 컨테이너는 새 변수를 못 받는다)

새 세션에서 이게 `set`으로 나오면 끝이다:

```
echo "KEY:${YOUTUBE_API_KEY:+set}${YOUTUBE_API_KEY:-missing}"
```

---

## 5. 대기 중인 것 — 키만 들어오면 그대로 돈다

- `refs/seed-queries.txt` — 검색어 16줄(공룡 5 · 고생물 4 · 현생 거대생물 4 · 멸종/괴물 3) + 채널 6줄. 검색어 한 줄 300유닛으로 **약 4,800유닛**, 채널 훑기는 줄당 6유닛 남짓. 일일 10,000 안이라 하루 두 번 돌릴 수 있다.
- 시드의 채널 6줄은 웹 검색으로 주운 **미확인 핸들**이라 일부가 실패할 수 있다. 도구는 경고만 남기고 넘어가게 되어 있고, 어느 핸들이 실패했는지는 다음 실행에서 기록한다. 근거·제외 목록은 `refs/candidate-channels.md`.
- `tools/collect-refs.mjs` · `tools/analyze-refs.mjs` — 정상 (Node v22.22.2에서 인자 파싱까지 진입, 키 검사에서만 멈춤)
- 최근 30일 창이 3편 미만이면 `--recent-ratio 30`으로 한 번 더 돌려 그 결과를 채택하는 규칙도 그대로 대기 중
- **소재 제안 3개**는 `refs/digest-*.md`가 나온 다음이다. 없는 숫자로 쓰지 않는다.

세 단계 중 두 단계는 서 있고, 마지막 한 칸만 비어 있다.
