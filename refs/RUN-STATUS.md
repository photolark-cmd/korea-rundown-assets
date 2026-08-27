# 레퍼런스 수집 실행 상태 (6차 시도)

- **실행 시각**: 2026-08-27 09:19 KST (2026-08-27 00:19 UTC)
- **브랜치**: `claude/science-reference-depth-j47fg4`
- **결론**: **성공.** 다섯 번 막혀 있던 수집이 이번에 끝까지 돌았다.
- **소모 쿼터 4,792유닛** (하루 10,000 중) · **채택 22편** · **최근 30일 3편**

| 항목 | 값 |
|---|---:|
| 소모 쿼터 | **4,792유닛** (계획 4,836과 거의 일치, 채널 1줄 실패분만큼 덜 씀) |
| 남은 쿼터 | 약 5,208유닛 |
| 훑어본 영상 | 2,396편 / 888개 채널 |
| 채택 편수 | **22편** (100배 이상) |
| 최근 30일 편수 | **3편** |
| 걸러낸 것 | 기준 미달 1,502 · 하한 미달 97 · 쇼츠 770 · 구독자 비공개 5 |
| 산출물 | `refs/refs-2026-08-27.csv` · `refs/refs-2026-08-27.md` · `refs/digest-2026-08-27.md` |

## 0. 키 형식 확인 — 통과

5차 실패 원인이었던 값 형식부터 봤다 (값 자체는 출력하지 않음).

```
$ node -e '...console.log("len:"+k.length,"prefix:"+k.slice(0,6),"hangul:"+/[가-힣]/.test(k))'
len:39 prefix:(구글 정상 접두사 6글자) hangul:false
```

정상 형식이라 그대로 수집에 들어갔다. 이번엔 안내 문구가 아니라 **실제 키**가 들어와 있었다.

## 1. 수집 실행 결과

```
$ node tools/collect-refs.mjs --seeds refs/seed-queries.txt --no-shorts --min-subs 1000
  looked at   2,396 videos on 888 channels
  kept        22 at 100x or better, 3 of them from the last 30 days
  quota used  4,792 units
exit:0

$ node tools/analyze-refs.mjs
  read        refs/refs-2026-08-27.csv - 22 rows
  wrote       refs/digest-2026-08-27.md
exit:0
```

**403은 한 건도 없었다.** `accessNotConfigured` · `ipRefererBlocked` · `quotaExceeded` 모두 발생하지 않음.

### 실패한 채널 핸들

시드의 채널 6줄 중 **1줄 실패**, 나머지 5줄은 정상 수집.

| 시드 줄 | 결과 |
|---|---|
| `https://www.youtube.com/channel/UCYEokR7TzrZnrIefPM_nZnw` | **실패** — `playlistItems 404: The playlist identified with the request's playlistId parameter cannot be found.` 존재하지 않거나 삭제된 채널 ID로 보인다. 경고만 남고 나머지는 계속 진행됐다. |
| `https://www.youtube.com/@Jabsik_dinosaur` | 성공 — 14편 |
| `https://www.youtube.com/channel/UCVKvxjaxcKgFm7BEUw__2gg` | 성공 — 79편 |
| `https://www.youtube.com/@우마UMA` | 성공 — 156편 |
| `https://www.youtube.com/@TV생물도감` | 성공 — 200편 |
| `https://www.youtube.com/@kodaiseibutuch` | 성공 — 200편 |

→ 다음 회차에는 `UCYEokR7TzrZnrIefPM_nZnw` 줄을 시드에서 빼거나 올바른 ID로 고치는 게 좋다.

### `--recent-ratio 30` 재실행 여부 — **돌리지 않았다**

지시 조건은 "최근 30일 창이 **3편 미만**이면"인데 **정확히 3편**이 나왔다. 조건 미달이라 재실행하지 않았다.

다만 도구가 이런 힌트를 남겼으니 기록해 둔다:

```
building    13 more inside the window sit between 25x and 100x
            - views are still piling up there (best 99x); --recent-ratio lowers the bar
```

**최근 창 안에 25~100배 구간이 13편 더 있고 그 중 최고가 99배**다. 기준을 조금만 낮추면 최근 창이
3편에서 크게 늘어난다는 뜻이다. 남은 쿼터 약 5,208유닛으로 재실행(약 4,836유닛)이 **가능은 하다.**
조건을 "3편 이하"로 볼지 "3편 미만"으로 볼지는 판단이 필요해 이번엔 지시문 그대로 미만으로 처리했다.

---

## 2. 최근 30일 상위 5편

**채택된 것이 3편뿐이라 3편 전부다.** 없는 항목을 채우지 않았다.

| # | 배수 | 조회수 | 구독자 | 길이 | 지난날 | 채널 | 제목 |
|---:|---:|---:|---:|---:|---:|---|---|
| 1 | **161배** | 1,168,515 | 7,240 | 1:52 | 15일 | 쇼츠다이노 | [복수에 성공한 알로사우루스에게 닥친 또 다른 위협 #공룡](https://www.youtube.com/watch?v=U723VEBG788) |
| 2 | **157배** | 169,007 | 1,080 | 66:50 | 29일 | Dino and Friends | [🦖 T-Rex Dinosaur Adventures 🦖 Dino & Friends Episodes 1–5](https://www.youtube.com/watch?v=smctrtIcFk8) |
| 3 | **102배** | 345,378 | 3,400 | 1:33 | 11일 | Hamaru | [심해에 떠 있는 거대 석유 시추선이 버티는 놀라운 원리](https://www.youtube.com/watch?v=esrpB0wDXyU) |

## 3. 그 이전 상위 5편

| # | 배수 | 조회수 | 구독자 | 길이 | 지난날 | 채널 | 제목 |
|---:|---:|---:|---:|---:|---:|---|---|
| 1 | **921배** | 2,330,776 | 2,530 | 8:51 | 1874일 | 디팔 DIPAL | [서브노티카 빌로우제로 가장 거대한 생물 TOP5](https://www.youtube.com/watch?v=phrU_rwD7W4) |
| 2 | **263배** | 410,751 | 1,560 | 3:06 | 2712일 | 공룡갤러리 dinosaur gallery | [herbivorous dinosaur size comparison](https://www.youtube.com/watch?v=F38qed59UWg) |
| 3 | **255배** | 272,655 | 1,070 | 1:35 | 783일 | 톰토미 (TOMTOMI) - Topic | [스피노 사우루스 송](https://www.youtube.com/watch?v=loHcUx3Vuas) |
| 4 | **240배** | 4,590,093 | 19,100 | 20:21 | 1220일 | AlecRaptor1 | [Evolution of T-Rex in Movies and T.V Size Comparison(Edited)](https://www.youtube.com/watch?v=O5XwDGotwdg) |
| 5 | **234배** | 35,998,561 | 154,000 | 10:06 | 2069일 | Mr. Fishing | [Modern Fast Squid Fishing Technology on Big Boat...](https://www.youtube.com/watch?v=cbqGEHxG2Ps) |

---

## 4. 소재 제안 3개

`refs/digest-2026-08-27.md`를 읽고 **생물 · 공룡 · 거대 괴물** 채널에서 만들 만한 것만 골랐다.
숫자는 전부 이번 회차 채택본에서 그대로 가져온 값이다.

### 제안 1 — 공룡 크기 비교 (size comparison)

> **근거 영상**
> - [herbivorous dinosaur size comparison](https://www.youtube.com/watch?v=F38qed59UWg) — 263배 · 410,751회 · 1,560구독 · 3:06
> - [carnivorous dinosaur size comparison](https://www.youtube.com/watch?v=bX9m-2ynyG8) — 165배 · 257,723회 · 1,560구독 · 1:41
> - [ichthyosaur size comparison](https://www.youtube.com/watch?v=nIsxVllk2z8) — 159배 · 247,991회 · 1,560구독 · 2:31
> - [Evolution of T-Rex in Movies and T.V Size Comparison](https://www.youtube.com/watch?v=O5XwDGotwdg) — 240배 · 4,590,093회 · 19,100구독 · 20:21

**왜 먹혔나**: 채택 22편 중 4편이 `공룡갤러리`(구독자 1,560) 한 채널의 **같은 포맷**이고 중앙값이 162배다.
우연히 하나 터진 게 아니라 포맷이 반복해서 통한다는 뜻이고, 제목 반복어 집계에서도 `size 4 · comparison 4`로
`Rex 4`와 나란히 1위다. 말이 거의 필요 없어 언어 장벽 없이 조회수가 밖으로 나간다.

### 제안 2 — 심해 하강: 수심별로 무엇이 사는가

> **근거 영상**
> - [마리아나 해구로의 하강 | 그 곳에서 발견된 공포와 생명의 기록](https://www.youtube.com/watch?v=437OK121Nxc) — 145배 · 566,603회 · 3,910구독 · 13:01 (심해도감)
> - [심해에 떠 있는 거대 석유 시추선이 버티는 놀라운 원리](https://www.youtube.com/watch?v=esrpB0wDXyU) — 102배 · 345,378회 · 3,400구독 · 1:33 · **11일 전** (Hamaru)

**왜 먹혔나**: 심해가 **최근 30일 창 안에 살아 있는 유일한 소재**다. 채택된 최근 3편 중 1편이 심해고,
나머지 둘은 공룡이다. 길이 구간 통계에서도 10~20분이 중앙 204배로 가장 높은데 심해도감 13:01이 정확히 그 구간이다.
구독자 3~4천대 채널이 낸 기록이라 규모가 작아도 넘을 수 있는 벽임을 보여준다.

### 제안 3 — "가장 거대한 생물 TOP5" 랭킹

> **근거 영상**
> - [서브노티카 빌로우제로 가장 거대한 생물 TOP5](https://www.youtube.com/watch?v=phrU_rwD7W4) — **921배** · 2,330,776회 · 2,530구독 · 8:51 (디팔 DIPAL)
> - [버그로 맵 탐험중 마주친 거대 생명체 ㄷㄷ #붉은사막](https://www.youtube.com/watch?v=TTy8Br8-lgo) — 220배 · 2,062,188회 · 9,360구독 · 1:03 (퍽하)

**왜 먹혔나**: 921배는 **이번 회차 최고 배수**이고, 2위(263배)와 3.5배 차이로 압도적이다.
"거대"는 제목 반복어 3회로 한국어 단어 중 1위. 다만 **두 편 모두 게임 화면**이라는 점은 그대로 적어 둔다 —
실사·복원 CG 채널로 그대로 옮겨도 같은 배수가 나온다는 근거는 이번 데이터에 **없다.**
가져올 만한 건 소재가 아니라 **"TOP5 랭킹 + 갈수록 커지는 순서"라는 구성**이다.

---

배수는 **오늘의 구독자 수** 기준이며 API가 유효숫자 3자리로 반올림한다.
최근 창 영상은 조회수가 덜 쌓여 배수가 낮게 잡히므로, 같은 배수라도 최근 것이 더 세다.
