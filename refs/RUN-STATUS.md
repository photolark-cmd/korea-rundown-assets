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

---

## 7차 — 최근 30일 재수집

**실행 2026-08-27 09:28 KST** · 결과 `refs/recent-2026-08-27.csv` · `.md` · `refs/digest-recent-2026-08-27.md`
1차(`refs-2026-08-27.*`, `digest-2026-08-27.md`)는 손대지 않았다.

### 쿼터

| | 유닛 |
|---|---:|
| 실행 전 남은 양(추정) | 5,208 |
| `--dry-run` 예상 | 3,230 |
| **실제 소모** | **2,485** |
| 실행 후 남은 양(추정) | **약 2,723** |

예상보다 745유닛 덜 썼다. 검색어 16개 중 **8개**가 최근 30일 안에서 결과가 50건 이하라
2페이지째를 부르지 않았기 때문이다(`공룡 실제 모습 복원`·`고생대 생물`·`고대 상어 메갈로돈`·
`거대 곤충 고생대`·`매머드 복원`·`지구에서 가장 큰 생물`·`미확인 생물체`·`거대 생물 전설`).
검색에서 800유닛을 덜 썼고, 대신 영상·채널 상세 조회가 예상치의 30유닛보다 55유닛쯤 많이 나가
차액이 745가 됐다. **dry-run 예상치는 상한으로 읽으면 된다.**

### 실행 전에 고쳐야 했던 것 두 가지

지시받은 명령을 그대로 `--dry-run` 하면 **4,830유닛**이 나왔다. 4,500 상한을 넘는다.
원인을 확인해 보니 실행을 막을 일이 아니라 **도구 쪽 문제 두 개**였다.

1. **`--dry-run` 추정식이 중복 패스 생략을 반영하지 않았다.**
   `main()`은 `needRecentPass`로 최근 창 패스를 건너뛰는데, 추정식은 그 판단을 보지 않고
   `pages + recentPages`를 무조건 곱했다. 즉 `--since`를 무엇으로 주든 항상 검색어당 100유닛을
   과다 계상했고, **그 숫자에 대고 예산을 확인하는 일 자체가 의미가 없었다.**
   추정식이 `needRecentPass`를 그대로 쓰도록 고쳤다(`tools/collect-refs.mjs`).
   생략이 걸리면 dry-run이 `no second pass — --since already starts inside the window`이라고 밝힌다.

2. **`--since <30일 전 날짜>`만으로는 생략이 걸리지 않는다.**
   창 경계 `recentAfter`는 **날짜가 아니라 시각**이다. 실행 시점 기준
   `2026-07-28T00:26:43Z`인데 `--since 2026-07-28`은 `2026-07-28T00:00:00Z`로 풀린다.
   경계가 26분 더 늦어서 `recentAfter > since`가 참이 되고, 생략 조건이 빗나간다.
   `--recent 31`로 경계를 `--since` **뒤로** 물려 생략을 걸었다.
   창을 좁히는 방향(`--since 2026-07-29`)이 아니라 넓히는 방향이라 30일이 온전히 남는다.
   실제로 돌아온 영상은 전부 07-28 이후이므로 13편 모두 최근 창으로 잡혔고 30배 기준을 받았다.

고친 뒤 dry-run이 지시서가 예상한 **3,230유닛** 그대로 나왔고, 그때 실행했다.

최종 명령:

```
node tools/collect-refs.mjs --seeds refs/seed-queries.txt --no-shorts --min-subs 1000 \
  --since 2026-07-28 --recent 31 --recent-ratio 30 --name recent-2026-08-27 \
  --exclude "키즈|kids|kidz|어린이|동요|만화|cartoon|toy|장난감" \
  --exclude "게임|gameplay|로블록스|roblox|마인크래프트|minecraft|서브노티카|subnautica|붉은사막" \
  --exclude "- Topic"
```

### 채택과 탈락

840편을 봤고 **13편 채택**, 전부 최근 30일 안이다. 1차의 최근 창 3편에서 13편으로 늘었다.

| 탈락 사유 | 편수 |
|---|---:|
| 배수 미달 | 212 |
| 쇼츠(60초 이하) | 398 |
| 구독자·조회수 하한 미달 | 138 |
| **제외 패턴에 걸림** | **66** |
| 구독자 비공개 | 13 |

기준선 바로 아래도 봐 둘 것: 최근 창 안에 **7.5~30배 구간이 19편**, 최고 28배다.

### 상위 10편

| # | 배수 | 조회수 | 구독자 | 길이 | 지난날 | 채널 | 제목 |
|---:|---:|---:|---:|---:|---:|---|---|
| 1 | 161배 | 1,168,644 | 7,240 | 1:52 | 15일 | 쇼츠다이노 | [복수에 성공한 알로사우루스에게 닥친 또 다른 위협 #공룡](https://www.youtube.com/watch?v=U723VEBG788) **↺1차** |
| 2 | 102배 | 345,503 | 3,400 | 1:33 | 11일 | Hamaru | [심해에 떠 있는 거대 석유 시추선이 버티는 놀라운 원리](https://www.youtube.com/watch?v=esrpB0wDXyU) **↺1차** |
| 3 | 99배 | 342,452 | 3,450 | 10:31 | 27일 | World Ai Ki Duniya | [🦖 गाँव में आया खतरनाक डायनासोर 😱 \| Dinosaur Attack in Village \| Hindi Story](https://www.youtube.com/watch?v=Ma0DCpKPqbs) |
| 4 | 94배 | 682,284 | 7,240 | 1:40 | 25일 | 쇼츠다이노 | [공룡 멸종의 날, 티라노사우루스와 트루돈이 맞이한 비극적 결말 #공룡](https://www.youtube.com/watch?v=nxy3NdjjOfs) |
| 5 | 90배 | 4,682,370 | 51,900 | 1:01 | 22일 | Satwa Files | [2050 Nanti, Hewan Ini Mungkin Sudah Tidak Ada!](https://www.youtube.com/watch?v=__ZcBSjhZIM) |
| 6 | 81배 | 356,638 | 4,420 | 2:37 | 22일 | 내손바닥 | [얼떨결에 안킬로사우루스의 아빠가 된 티라노사우루스](https://www.youtube.com/watch?v=coYDga4_GnU) |
| 7 | 67배 | 731,170 | 10,900 | 1:01 | 24일 | 바로 대한민국 | [무작정 돈을 쏟아붓는 대신 자연의 균형을 택한 레전드 대한민국의 위엄](https://www.youtube.com/watch?v=iZK-H4gOlvc) |
| 8 | 49배 | 285,590 | 5,780 | 1:58 | 25일 | 스라소니 | [전 세계가 포기한 괴물 물고기 한국 아재들이 한번에 해결하자](https://www.youtube.com/watch?v=jSR9T3zFBWY) |
| 9 | 48배 | 346,885 | 7,240 | 1:23 | 18일 | 쇼츠다이노 | [티라노사우루수의 가족을 위한 복수 #공룡](https://www.youtube.com/watch?v=s-ePAAfKM8w) |
| 10 | 40배 | 113,958 | 2,850 | 1:09 | 7일 | 초점너머 | [고래가 죽으면 심해에 마을이 생깁니다](https://www.youtube.com/watch?v=qH1f9sOC3SM) |

11~13위는 `refs/digest-recent-2026-08-27.md`에 있다(쇼츠다이노 2편 + King Tatum 낚시 1편).

### 1차 최근 3편과의 겹침

| 1차 최근 창 | 이번 회차 |
|---|---|
| 161배 쇼츠다이노 — 알로사우루스 | **1위로 그대로** (조회수 1,168,515 → 1,168,644) |
| 157배 Dino and Friends — T-Rex 유아 만화 | **제외 패턴이 걸러냄** (`kids`·`cartoon`) — 의도한 결과 |
| 102배 Hamaru — 석유 시추선 | **2위로 그대로** |

즉 새로 나온 건 11편이고, 지우려던 유아 만화 1편은 정확히 지워졌다.

### 솔직히 — 이 목록에도 안 맞는 게 섞여 있다

만화·게임은 빠졌지만 **배수 기준이 소재를 안 가린다는 문제 자체는 그대로다.**
13편 중 소재가 실제로 맞는 건 넉넉히 봐도 3편(초점너머 고래 사체, 스라소니 괴물 물고기,
내손바닥 공룡)뿐이다. 나머지는 이렇게 어긋난다.

1. **1~2분 세로 애니메이션이 절반이다.** 쇼츠다이노 5편이 그렇다. 소재는 공룡이 맞지만
   전부 77~112초(1:17~1:52)로, `--no-shorts`의 60초 문턱을 **간신히 넘겨서** 통과했다.
   포맷이 사이언스 썰 내레이션이 아니라 쇼츠 애니라서, 배수를 그대로 참고하기 어렵다.
   구독자 7,240 한 채널이 채택본의 38%를 차지하는 것도 표본으로서 좋지 않다.
2. **비한국어 2편.** 3위는 힌디어 창작 동화(마을에 공룡이 나타난다는 이야기 — 사실상 아동물이고
   `Hindi Story`라 제외어에도 안 걸렸다), 5위는 인도네시아어다. 검색이 `regionCode=KR`이어도
   한국에서 재생되는 외국어 영상은 그대로 올라온다.
3. **과학이 아닌 것 1편.** 7위 `바로 대한민국`은 국뽕 채널이다. `자연의 균형`이라는 말 때문에
   생물 검색어에 걸렸을 뿐이다.
4. **생물이 아닌 것 1편.** 2위 Hamaru는 석유 시추선 공학이다. 과학 채널이긴 하나
   생물·공룡·괴물이 아니다. (1차에서도 올라왔던 영상이다.)
5. **낚시 1편.** 13위 King Tatum.

### 다음 회차에 걸 것 — 제안

제외어를 세 줄 더 붙이면 위의 2·3·5번이 빠진다. **1번은 제외어로 못 잡는다.**

```
  --exclude "[ऀ-ॿ]|hindi|kahani|cerita|dongeng|satwa|nonton"
  --exclude "국뽕|대한민국의 위엄|레전드 대한민국|한국인만|외국인 반응"
  --exclude "낚시|fishing|손맛|조황"
```

- 첫 줄의 `[ऀ-ॿ]`는 데바나가리(힌디) 문자 범위다. 제목에 한 글자만 있어도 걸린다.
  인도네시아어는 문자가 로마자라 문자 범위로 못 잡고 단어로 잡아야 한다.
- **1~2분 애니메이션은 도구를 고쳐야 한다.** `--no-shorts`가 60초 고정이라
  61초짜리를 못 막는다. `--min-duration <초>` 같은 하한 옵션을 넣고 `--min-duration 180`으로
  주는 것이 맞다. 이번엔 지시 범위 밖이라 넣지 않았다 — **다음 회차의 첫 작업으로 권한다.**
- 한 채널이 채택본을 독점하는 것도 옵션 하나로 막힌다(`--max-per-channel 2` 류).
  같이 넣으면 쇼츠다이노 5편이 2편으로 줄고 그만큼 다른 채널이 표에 올라온다.

무엇을 걸든 **100배 기준 자체는 건드리지 않았다.** 최근 창만 30배로 낮춘 것은 지시대로다.

---

배수는 **오늘의 구독자 수** 기준이며 API가 유효숫자 3자리로 반올림한다.
최근 창 영상은 조회수가 덜 쌓여 배수가 낮게 잡히므로, 같은 배수라도 최근 것이 더 세다.
