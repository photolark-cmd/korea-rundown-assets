# 레퍼런스 수집 실행 상태

- **실행 시각**: 2026-08-27 03:43 KST (2026-08-26 18:43 UTC)
- **브랜치**: `claude/science-reference-depth-j47fg4`
- **결론**: 수집 **실행 못 함**. 원인은 프록시 차단이 아니라 **`YOUTUBE_API_KEY` 환경변수가 세션에 아예 없음**.

---

## 1. 진단 명령 실제 출력

### 1-1. 키 존재 여부

```
$ echo "KEY:${YOUTUBE_API_KEY:+set}${YOUTUBE_API_KEY:-missing}"
KEY:missing
```

### 1-2. googleapis.com 도달 여부

```
$ curl -sS -o /dev/null -w "googleapis:%{http_code}\n" https://www.googleapis.com/youtube/v3/videos --max-time 20; echo "curl_exit:$?"
googleapis:403
curl_exit:0
```

### 1-3. 실제 API 호출

```
$ curl -sS "https://www.googleapis.com/youtube/v3/videos?part=id&id=dQw4w9WgXcQ&key=$YOUTUBE_API_KEY" --max-time 20 | head -20
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

## 2. 403의 정체 — 프록시 아님, 키 문제임

지시받은 두 갈래 판정 기준대로 확인했고, **키 문제 쪽으로 확정**된다. 근거 세 가지:

**(가) curl 종료코드가 0이다.** 프록시 차단이면 `CONNECT tunnel failed` + 종료코드 56이 나야 한다. 실제로는 `curl_exit:0`, 즉 연결·응답 수신 모두 정상이다.

**(나) 응답 헤더를 보면 CONNECT 터널이 정상 수립됐고, 403은 구글 서버가 직접 돌려준 것이다.**

```
$ curl -sS -D- -o /dev/null "https://www.googleapis.com/youtube/v3/videos?part=id&id=dQw4w9WgXcQ&key=$YOUTUBE_API_KEY" --max-time 20 | head -15
HTTP/1.1 200 Connection Established      <-- 프록시 터널 수립 성공

HTTP/2 403                                <-- 그 다음 구글이 직접 준 403
vary: X-Origin
vary: Referer
vary: Origin,Accept-Encoding
content-type: application/json; charset=UTF-8
date: Wed, 26 Aug 2026 18:42:32 GMT
server: scaffolding on HTTPServer2         <-- 구글 프론트엔드 서버 시그니처
x-xss-protection: 0
x-frame-options: SAMEORIGIN
x-content-type-options: nosniff
accept-ranges: none
```

`HTTP/1.1 200 Connection Established`는 프록시가 터널을 열어줬다는 뜻이다. 그 뒤의 403은 구글 본체(`server: scaffolding on HTTPServer2`)에서 온 정상 API 에러 응답이다.

**(다) 에이전트 프록시 상태에 차단 기록이 없다.**

```
$ curl -sS "$HTTPS_PROXY/__agentproxy/status" --max-time 20
{
  "enabled": true,
  "port": 40265,
  ...
  "selective": false,
  "toolScoped": false,
  "recentRelayFailures": []      <-- 최근 릴레이 실패 0건
}
```

**즉 `www.googleapis.com` 도메인은 뚫려 있다.** 403 메시지도 "키가 제한됐다"(`ipRefererBlocked` / `API not enabled`)가 아니라 **`Method doesn't allow unregistered callers`** — 호출에 키가 아예 안 실렸다는 뜻이다. `$YOUTUBE_API_KEY`가 빈 문자열이라 `key=` 뒤가 비어서 나간 결과다.

컨테이너 전체를 뒤져도 키가 없다:

```
$ env | grep -cE "AIza"
0
$ find /home /root /mnt -maxdepth 4 -name ".env*" -o -maxdepth 4 -name "*.env"
(출력 없음)
$ ls -la .env*
ls: cannot access '.env*': No such file or directory
```

`tools/collect-refs.mjs`는 키를 `process.env.YOUTUBE_API_KEY` 또는 `--key` 인자에서만 읽는다(387~388행). 대체 경로 없음.

---

## 3. 수집 실행 결과

**채택 편수 0, 최근 30일 편수 0, 소모 쿼터 0 유닛.** 쿼터는 한 유닛도 쓰지 않았다 — `quotaExceeded`가 아니라 API를 아예 호출하지 못하고 스크립트 진입 단계에서 종료됐다.

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

**claude.ai/code 환경 설정에서 이 환경의 환경변수에 `YOUTUBE_API_KEY`(Google Cloud 콘솔에서 발급하고 YouTube Data API v3를 활성화한 키)를 추가한 뒤 세션을 다시 실행하면 된다** — 네트워크·프록시는 손댈 필요 없다.

### 참고: 설정 후 재확인 방법

키를 넣고 나서 아래가 `200`이면 바로 수집이 돌아간다.

```
curl -sS -o /dev/null -w "%{http_code}\n" \
  "https://www.googleapis.com/youtube/v3/videos?part=id&id=dQw4w9WgXcQ&key=$YOUTUBE_API_KEY"
```

여전히 403이면 이번 건과 원인이 다르며, 응답 본문의 `reason` 필드로 갈린다:

| `reason` | 의미 | 조치 |
|---|---|---|
| `forbidden` (이번 실행) | 키가 요청에 안 실림 | 환경변수 설정 |
| `accessNotConfigured` | 프로젝트에 YouTube Data API v3 미활성화 | GCP 콘솔에서 API 사용 설정 |
| `ipRefererBlocked` | 키에 IP/리퍼러 제한 걸림 | 키 제한 해제 또는 API 제한만 남기기 |
| `quotaExceeded` | 일일 쿼터 소진 (기본 10,000유닛) | 다음 날 재시도 또는 쿼터 증설 |
