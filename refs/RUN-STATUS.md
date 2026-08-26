# 레퍼런스 수집 실행 상태 (2차 시도)

- **실행 시각**: 2026-08-27 06:03 KST (2026-08-26 21:03 UTC)
- **브랜치**: `claude/science-reference-depth-j47fg4`
- **결론**: 이번에도 수집 **실행 못 함**. `YOUTUBE_API_KEY`가 **여전히 이 세션 환경에 없음**. 1차 시도와 동일한 원인이며, 네트워크·프록시는 정상이다.

---

## 1. 진단 명령 실제 출력

### 1-1. 키 존재 여부

```
$ echo "KEY:${YOUTUBE_API_KEY:+set}${YOUTUBE_API_KEY:-missing}"
KEY:missing
```

### 1-2. API 호출 상태코드

```
$ curl -sS -o /tmp/yt.json -w "%{http_code}\n" \
    "https://www.googleapis.com/youtube/v3/videos?part=id&id=dQw4w9WgXcQ&key=$YOUTUBE_API_KEY"
403
curl_exit:0
```

### 1-3. 403 응답 본문

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

**`reason` = `forbidden`** — 지시받은 판정표대로면 "키가 요청에 아예 안 실림"이다. `accessNotConfigured`(API 미활성화)도, `ipRefererBlocked`(키 제한)도, `quotaExceeded`(쿼터 소진)도 아니다. `$YOUTUBE_API_KEY`가 빈 문자열이라 `key=` 뒤가 비어서 나갔다.

`curl_exit:0`이므로 연결·응답 수신은 정상 — 프록시 문제가 아니라는 1차 시도의 결론이 그대로 유지된다.

---

## 2. 이번 시도에서 새로 확인된 것

**이 세션 컨테이너는 2026-08-26 21:00:47 UTC(06:00 KST)에 새로 뜬 것이고, 그 시점 환경변수 목록에 `YOUTUBE_API_KEY`가 없다.** 즉 컨테이너가 오래돼서 설정을 못 받은 게 아니라, **이 환경의 환경변수 목록에 키가 저장되어 있지 않다.**

- 이 세션이 쓰는 환경 ID: **`env_01ApTTBGFHba6yRJzEhDn3mh`** (kind: `anthropic_cloud`)
- 컨테이너에 주입된 환경변수 133개를 이름 기준으로 확인했으나 `YOUTUBE_API_KEY` 없음
- 저장소 안에도 `.env` 등 대체 경로 없음 (저장소 루트에 dotfile은 `.gitignore`뿐)

가능성은 둘 중 하나다:

1. 설정 저장이 실제로 반영되지 않았다 (저장 버튼/입력 확정 누락 등)
2. **키를 다른 환경에 넣었다** — 계정에 환경이 여러 개면, 이 세션이 쓰는 `env_01ApTTBGFHba6yRJzEhDn3mh`가 아닌 쪽에 들어갔을 수 있다

`tools/collect-refs.mjs`는 키를 `process.env.YOUTUBE_API_KEY` 또는 `--key` 인자에서만 읽는다(387~388행). 우회 경로는 없다.

---

## 3. 수집 실행 결과

**채택 편수 0, 최근 30일 편수 0, 소모 쿼터 0 유닛.** API를 한 번도 호출하지 못하고 스크립트 진입 단계에서 종료됐으므로 쿼터는 그대로 남아 있다. (`--recent-ratio 30` 재실행 단계까지 가지 못했다.)

```
$ node tools/collect-refs.mjs --seeds refs/seed-queries.txt --no-shorts --min-subs 1000
error: no API key - set YOUTUBE_API_KEY or pass --key
exit:1

$ node tools/analyze-refs.mjs
error: no refs-*.csv in refs/ - run collect-refs.mjs first
exit:1
```

생성된 결과 파일 없음 (`refs/refs-*.csv`, `refs/refs-*.md`, `refs/digest-*.md` 모두 미생성).

---

## 4. 사용자가 고쳐야 할 것 (한 줄)

**claude.ai/code → 환경 설정에서 이 세션이 쓰는 환경(`env_01ApTTBGFHba6yRJzEhDn3mh`)의 환경변수에 `YOUTUBE_API_KEY`가 실제로 저장돼 있는지 확인하고(다른 환경에 넣었을 가능성 포함) 저장한 뒤 새 세션을 띄우면 된다** — 네트워크·프록시는 손댈 필요 없다.

### 설정 후 재확인 방법

새 세션에서 아래 한 줄이 `set`으로 나오는지 먼저 보는 게 가장 빠르다.

```
echo "KEY:${YOUTUBE_API_KEY:+set}${YOUTUBE_API_KEY:-missing}"
```

`set`이면 그다음 이게 `200`이어야 수집이 돈다.

```
curl -sS -o /dev/null -w "%{http_code}\n" \
  "https://www.googleapis.com/youtube/v3/videos?part=id&id=dQw4w9WgXcQ&key=$YOUTUBE_API_KEY"
```

`set`인데도 403이면 원인이 이번 건과 다르며, 응답 본문의 `reason`으로 갈린다:

| `reason` | 의미 | 조치 |
|---|---|---|
| `forbidden` (1·2차 실행) | 키가 요청에 안 실림 | 환경변수 설정 |
| `accessNotConfigured` | 프로젝트에 YouTube Data API v3 미활성화 | GCP 콘솔에서 API 사용 설정 |
| `ipRefererBlocked` | 키에 IP/리퍼러 제한 걸림 | 키 제한 해제 또는 API 제한만 남기기 |
| `quotaExceeded` | 일일 쿼터 소진 (기본 10,000유닛) | 다음 날 재시도 또는 쿼터 증설 |

### 임시 대안

환경변수 설정이 계속 안 먹으면, 세션에서 아래처럼 키를 직접 넘겨도 동일하게 돈다 (키가 대화 로그에 남는 점은 감수해야 한다).

```
node tools/collect-refs.mjs --seeds refs/seed-queries.txt --no-shorts --min-subs 1000 --key <발급받은키>
node tools/analyze-refs.mjs
```
