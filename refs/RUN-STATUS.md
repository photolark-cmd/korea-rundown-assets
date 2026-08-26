# 레퍼런스 수집 실행 상태 (3차 시도)

- **실행 시각**: 2026-08-27 06:20 KST (2026-08-26 21:20 UTC)
- **브랜치**: `claude/science-reference-depth-j47fg4`
- **컨테이너 기동**: 2026-08-26 21:18:36 UTC (06:18 KST) — **이번 시도용으로 새로 뜬 컨테이너**
- **결론**: 이번에도 수집 **실행 못 함**. `YOUTUBE_API_KEY`가 이 세션 환경에 **없다**. 다른 이름으로도 없다.
- **그러나 이번엔 진전이 있다**: **네트워크 차단이 풀렸다.** `www.googleapis.com`이 이제 프록시를 통과한다 (2026-08-26 기록에서는 프록시가 403으로 막고 있었다). 사용자가 말한 "API 설정되어 있다"는 **이 부분이 맞다.**
- **남은 것은 딱 하나**: 같은 설정 창의 **Environment variables 칸**. Allowed domains는 들어갔는데 **환경변수는 안 들어갔다.**
- **새로 확정된 것**: 이 계정의 **클라우드 환경은 단 하나뿐**이다. 2차 시도에서 남겨둔 "다른 환경에 키를 넣었을 가능성"은 **제거됐다.**

---

## 1. 진단 명령 실제 출력 (전부)

### 1-1. 키 존재 여부

```
$ echo "KEY:${YOUTUBE_API_KEY:+set}${YOUTUBE_API_KEY:-missing}"
KEY:missing
```

### 1-2. 다른 이름으로 들어왔는지 — 이름 패턴 검색

```
$ env | grep -oE "^[A-Z_]*(YOUTUBE|YT|GOOGLE|API|KEY)[A-Z_]*=" | sort
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
PYTHONUNBUFFERED=
```

걸린 셋은 전부 컨테이너 기본 변수다. `YT_API_KEY` · `GOOGLE_API_KEY` · `YOUTUBE_KEY` 같은 대체 이름은 **하나도 없다.**

### 1-3. 값 기준 검색 — 구글 API 키 형식(`AIza…`)

```
$ env | grep -c "AIza"
0
```

이름을 아무렇게나 지었더라도 **값**이 구글 키 형식이면 잡혔을 텐데, 0건이다.

### 1-4. 전체 환경변수 이름 133개 육안 확인

```
$ env | sed -E 's/=.*//' | sort | wc -l
133
```

133개 전부를 확인했다. YouTube·Google API 관련 항목 없음. 값이 30~60자 영숫자(키처럼 생긴 것)인 변수도 `ANT_IMAGE_TAG`, `CLAUDE_CODE_ACCOUNT_UUID`, `CLAUDE_CODE_MESSAGING_TOKEN`, `CLAUDE_CODE_ORGANIZATION_UUID`, `CLAUDE_CODE_SESSION_ID`, `TRACEPARENT` — 전부 클로드 내부 변수다.

### 1-5. 파일 경로 대체 수단

```
$ find / -maxdepth 4 \( -name ".env" -o -name ".env.*" -o -name "*.env" \) 2>/dev/null
/usr/local/go1.25.1/go.env
/usr/local/go1.24.7/go.env
```

Go 툴체인 기본 파일 둘뿐. 저장소 루트 dotfile은 `.gitignore` 하나뿐이고 `.env`는 없다.

### 1-6. API 호출 상태코드와 응답 본문

```
$ curl -sS -o /tmp/yt.json -w "%{http_code}\n" \
    "https://www.googleapis.com/youtube/v3/videos?part=id&id=dQw4w9WgXcQ&key=$YOUTUBE_API_KEY"
403
```

```json
{
  "error": {
    "code": 403,
    "message": "Method doesn't allow unregistered callers (callers without established identity). Please use API Key or other form of API consumer identity to call this API.",
    "errors": [
      {
        "message": "Method doesn't allow unregistered callers (callers without established identity). Please use API Key or other form of API consumer identity to call this API.",
        "domain": "global",
        "reason": "forbidden"
      }
    ],
    "status": "PERMISSION_DENIED"
  }
}
```

---

### 1-7. 네트워크 — **여기가 지난번과 달라졌다**

```
$ curl -sS -D - -o /dev/null "https://www.googleapis.com/youtube/v3/videos?part=id&id=dQw4w9WgXcQ&key="
HTTP/1.1 200 Connection Established        ← 프록시 터널 성공
HTTP/2 403                                  ← 구글 API 본체가 준 응답
server: scaffolding on HTTPServer2
content-type: application/json; charset=UTF-8
```

**`200 Connection Established`가 핵심이다.** 프록시 터널이 뚫렸고, 뒤이은 403은 프록시가 아니라 **구글 서버가 직접 준 응답**이다(`server: scaffolding on HTTPServer2`, JSON 에러 본문). 2026-08-26 기록의 "`www.googleapis.com`이 egress 프록시에서 403"은 **해소됐다.**

대조군으로 `www.youtube.com`은 아직 막혀 있다:

```
$ curl -sS -o /dev/null -w "HTTP:%{http_code}\n" "https://www.youtube.com/"
curl: (56) CONNECT tunnel failed, response 403
HTTP:000

$ curl -sS "$HTTPS_PROXY/__agentproxy/status"
  "recentRelayFailures": [
    { "kind": "connect_rejected",
      "detail": "gateway answered 403 to CONNECT (policy denial or upstream failure)",
      "host": "www.youtube.com:443" } ]
```

**이건 문제가 되지 않는다.** `tools/collect-refs.mjs`가 실제로 호출하는 호스트는 `www.googleapis.com` 하나뿐이고(18행), 코드에 나오는 `www.youtube.com`(309·310·324행)은 **CSV·마크다운에 적어 넣는 링크 문자열일 뿐 네트워크 호출이 아니다.** 즉 Allowed domains에 `www.googleapis.com`만 들어가 있으면 수집은 온전히 돈다.

---

## 2. 판정 — 세 단계 중 어디가 빠졌나

| 단계 | 내용 | 이번 판정 |
|---|---|---|
| ⓪ | (선행) 환경 **네트워크 허용 도메인**에 `www.googleapis.com` | **완료됨** — 이번에 프록시 터널 통과 확인 |
| ① | GCP에서 YouTube Data API v3 **사용 설정** | 판정 불가(여기까지 가지도 못함). 다만 **원인 아님** — 미활성화면 `accessNotConfigured`가 떠야 한다 |
| ② | **API 키 발급** | 판정 불가. 원인 아님 — 잘못된 키를 보냈다면 `API key not valid`(`badRequest`)가 떠야 한다 |
| ③ | 클로드 환경변수에 **`YOUTUBE_API_KEY=` 입력** | **← 여기가 빠졌다** |

**`reason: forbidden`** + 메시지 `Method doesn't allow unregistered callers`는 **`key=` 뒤에 아무 값도 실리지 않았을 때만** 나오는 응답이다. 즉 요청에 키가 아예 없었다는 뜻이고, 지시받은 판정표대로 **③번 단계**다.

`curl` 종료코드는 0 — 연결·응답 수신 정상. 네트워크·프록시 문제가 아니라는 1·2차 결론이 그대로 유지된다.

### 2-1. 2차 시도의 남은 가설이 이번에 제거됐다

2차 시도 기록에는 가능성을 둘 남겨뒀다: (1) 저장이 반영되지 않았다, (2) **다른 환경**에 키를 넣었다.

이번에 계정의 환경 목록을 직접 조회했다:

```
environments:
  - environment_id: env_01ApTTBGFHba6yRJzEhDn3mh
    name: "기본값"  (온보딩 중에 생성된 기본 환경)
    kind: anthropic_cloud
    state: active
  has_more: false
```

**환경이 하나뿐이다.** 그리고 이 세션이 도는 환경이 바로 그 `env_01ApTTBGFHba6yRJzEhDn3mh`다. 넣을 수 있는 다른 환경 자체가 없으므로 가설 (2)는 성립하지 않는다. **남은 건 가설 (1) — 저장이 실제로 반영되지 않았다.**

그리고 1-7에서 본 대로, **같은 환경의 Allowed domains 변경은 반영됐다.** 같은 설정 창에서 네트워크 쪽은 저장됐는데 환경변수 쪽만 안 됐다는 뜻이다 — 창을 연 것도, 저장한 것도 맞으니 **환경변수 칸만 놓친 것**으로 보는 게 자연스럽다.

또한 이 컨테이너는 2026-08-26 21:18:36 UTC에 **새로** 떴다. "설정은 했는데 옛날 컨테이너라 못 받았다"도 아니다.

---

## 3. 수집 실행 결과

**채택 편수 0 · 최근 30일 편수 0 · 소모 쿼터 0 유닛.**

API를 한 번도 호출하지 못하고 스크립트 진입 단계에서 종료됐으므로 **일일 쿼터 10,000유닛은 그대로 남아 있다.** `--recent-ratio 30` 재실행 단계까지 가지 못했고, 최근 30일 상위 5편도 당연히 없다.

```
$ node --version
v22.22.2

$ node tools/collect-refs.mjs --seeds refs/seed-queries.txt --no-shorts --min-subs 1000
error: no API key - set YOUTUBE_API_KEY or pass --key
exit:1

$ node tools/analyze-refs.mjs
error: no refs-*.csv in refs/ - run collect-refs.mjs first
exit:1
```

생성된 결과 파일 없음 — `refs/refs-*.csv`, `refs/refs-*.md`, `refs/digest-*.md` 모두 미생성이라 커밋할 것이 없다.

`tools/collect-refs.mjs`는 키를 `process.env.YOUTUBE_API_KEY` 또는 `--key` 인자에서만 읽는다(387~388행). 우회 경로는 없다.

---

## 4. 지금 해야 할 일

환경변수 설정이 세 번 연속 반영되지 않았다. **가장 확실한 길은 환경변수를 건너뛰고 키를 직접 넘기는 것이다.**

### 방법 A — 키를 명령에 직접 넘긴다 (권장, 즉시 됨)

다음 세션에서 이 한 줄을 그대로 주면 된다. `<발급받은키>` 자리에 `AIza…`로 시작하는 키를 넣는다.

```
node tools/collect-refs.mjs --seeds refs/seed-queries.txt --no-shorts --min-subs 1000 --key AIza…
node tools/analyze-refs.mjs
```

키가 대화 로그에 남는 점은 감수해야 한다. 걸리면 수집이 끝난 뒤 GCP 콘솔에서 그 키를 폐기하고 새로 발급하면 된다.

### 방법 B — 환경변수를 다시 시도한다

**Allowed domains를 넣었던 바로 그 창을 다시 열면 된다.** 거기 아래쪽에 Environment variables 칸이 따로 있다.

claude.ai/code 입력창 위 **구름 아이콘** → 환경 `기본값`에 마우스를 올리면 나오는 **톱니** → 창을 아래로 내려 **Environment variables** → 이름 `YOUTUBE_API_KEY`, 값 `AIza…` → 저장 → **새 세션**을 띄운다. (계정에 환경은 `env_01ApTTBGFHba6yRJzEhDn3mh` 하나뿐이라 고를 것도 없다.)

Network access 쪽은 **이미 되어 있으니 건드리지 말 것.** 지금 상태 그대로면 된다.

세 번 다 실패했으니 아래도 같이 확인할 만하다:
- 값만 넣고 **이름 칸을 비웠거나**, 이름에 `YOUTUBE_API_KEY =`처럼 공백·등호가 섞였는지
- 이름·값을 입력하고 **행 추가/확정을 누르지 않은 채** 창을 닫았는지 (Allowed domains는 반영됐으므로 저장 자체는 눌렀을 가능성이 높다)
- 저장 후 **새 세션이 아니라 기존 세션을 이어서** 열었는지 (기존 컨테이너는 새 변수를 못 받는다)

### 확인 한 줄

어느 쪽이든, 새 세션에서 이게 `set`으로 나오면 끝이다.

```
echo "KEY:${YOUTUBE_API_KEY:+set}${YOUTUBE_API_KEY:-missing}"
```

`set`인데도 403이면 이번 건과 원인이 다르며, 응답 본문의 `reason`으로 갈린다:

| `reason` | 의미 | 조치 |
|---|---|---|
| `forbidden` (1·2·3차 실행) | 키가 요청에 안 실림 | ③ 환경변수 설정 (또는 `--key`로 직접 전달) |
| `accessNotConfigured` | 프로젝트에 YouTube Data API v3 미활성화 | ① GCP 콘솔에서 API 사용 설정 |
| `badRequest` / `API key not valid` | 키 값이 틀림 | ② 키 재발급 |
| `ipRefererBlocked` | 키에 IP/리퍼러 제한 걸림 | 키 제한 해제 또는 API 제한만 남기기 |
| `quotaExceeded` | 일일 쿼터 소진 (기본 10,000유닛) | 다음 날 재시도 또는 쿼터 증설 |

---

## 5. 대기 중인 것 — 키만 들어오면 바로 끝난다

수집 쪽 준비는 전부 끝나 있다. 남은 건 키 하나다.

- `refs/seed-queries.txt` — 시드 16줄(공룡 5 · 고생물 4 · 현생 거대생물 4 · 멸종/괴물 3), 예상 소모 **4,800유닛**. 일일 한도 10,000 안이라 하루 두 번 돌릴 수 있다.
- `tools/collect-refs.mjs` · `tools/analyze-refs.mjs` — 정상 (Node v22.22.2에서 인자 파싱까지 진입 확인, 키 검사에서만 멈춤)
- 최근 30일 창이 3편 미만이면 `--recent-ratio 30`으로 한 번 더 돌려 그 결과를 쓰기로 한 규칙도 그대로 대기 중
- **네트워크** — `www.googleapis.com` 통과 확인 완료. 수집에 필요한 호스트는 이것 하나뿐이다

세 단계 중 두 단계는 서 있고, 마지막 한 칸만 비어 있다.
