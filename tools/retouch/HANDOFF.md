# 인계 메모 — 사진 보정 프로그램 (2026-09-09, 클라우드 세션 → PC 세션)

> PC의 Claude가 이 파일을 먼저 읽는다. 이 파일은 클라우드 세션에서 하루 동안 만든 것의
> 상태·결정·다음 할 일을 담는다. 코드 사용법은 [`README.md`](README.md), 저장소 규칙은
> 루트 [`CLAUDE.md`](../../CLAUDE.md).

## 사용자와 작업

- 사용자는 **사진관 리터칭**을 한다. 주 작업: **아이 학사모 사진**, 그다음 성인 프로필.
- 학사모 사진 기준 (사용자가 정함): **모자 수평 · 고개 수직 · 어깨선 좌우 일치 · 옷매무새.**
- 배경 흐림은 사용자가 맨 마지막에 필터로 따로 넣는 것 → **기본 순서에서 뺐다.**
- 사용자는 개발자가 아니다. 깃·터미널 명령을 대신 해 주고, 결과는 사진으로 보여 주는 게 맞다.
- PC: 3080 Ti. 야간 근무자, 혼자 여러 채널 운영 → 자동화·재사용 제안이 맞다.
- **사람 사진은 절대 이 public 저장소에 커밋하지 않는다.** 모델 파일(`models/`)도 gitignore.

## 만들어진 것 (브랜치 `claude/photo-correction-program-o8ghsm`, 전부 푸시됨)

| 파일 | 역할 | 상태 |
|---|---|---|
| `tools/photo-fix/index.html` | 브라우저 색 보정 앱 (설치 불필요) | 검증됨 |
| `tools/learn-preset.mjs` | before/after 쌍 → 색 LUT 프리셋 | 합성 쌍으로 검증 (오차 0.7/255) |
| `tools/retouch/studio.py` + `studio.html` | **한 장 작업 스튜디오**: 좌 원본/우 결과 + Claude 대화창. 지시를 도구 호출로 실행 | 가짜 클라이언트로 UI 왕복 검증. **실제 API 호출은 미검증** (클라우드에 키 없음) |
| `tools/retouch/common.py` | 랜드마크(MediaPipe 478점), 얼굴 크롭, 마스크, piecewise-affine 워핑, LUT | 실사진 검증 |
| `tools/retouch/color.py` | photo-fix 색 파이프라인의 numpy 판 (JS와 ≤1/255) | 검증 |
| `tools/retouch/prepare.py` → `train_geom.py` → `train_tex.py` → `retouch.py` | 원본/보정본 쌍 학습(얼굴형 회귀 + 질감 U-Net) → 일괄 적용 | 합성 얼굴로 배관만 검증. **실제 쌍 학습 전** |
| `tools/retouch/review.html` | 원본/결과 폴더 검수, 손볼 목록 추출 | 검증 |

### 스튜디오 도구 목록 (`studio.py` 의 `TOOLS`)

adjust · auto_levels · preset · zoom · heal · smooth_skin · face_models · crop · undo · save ·
straighten · head_tilt · eyes_from · mouth_from · expression · face_shape(포토샵 얼굴 인식
리퀴파이 16개 슬라이더) · curves · hsl · dodge_burn(only=background 로 배경 글로우) · clone ·
vignette · denoise · background(흐림/단색/투명) · liquify · perspective ·
**pose_check · level_hat · level_shoulders · extend_backdrop**(학사모·프로필용)

### 실사진 검증 결과 (사용자가 준 학사모 원본/보정본 1쌍, 프로필 원본/보정본 1쌍)

- 학사모 원본: 모자 +2.7° → 수평(측정 오차 ±0.8°), 고개 +0.6° → 0.0°, 어깨선 −4.9° → −0.5°.
  **보정사 완성본을 같은 도구로 재면 모자 −0.3°, 어깨 +0.1°** → 측정 기준이 실제 작업 기준과 맞음.
- 프로필 원본: 고개 0.0°, 어깨 −5.8° → −0.4°, `extend_backdrop` 으로 스탠드·바닥을 배경지 질감으로 채움.
- 눈·입 이식(`eyes_from`/`mouth_from`): 같은 촬영 다른 컷에서 이음새 없음.
- 배경 분리: 사람 전용 모델이 모자·소품을 잘라내서 GrabCut 색 재판정을 붙였음(단색 배경에서 OK).

### 알려진 약점 (솔직하게)

1. `extend_backdrop` 은 **채울 곳을 자동으로 못 찾는다.** 배경지의 어두운 테두리와 검은 스탠드가
   색·밝기로 안 갈린다. 대화창에서 Claude가 사진을 보고 `regions` 다각형을 지정하는 게 기본.
   채운 곳 경계가 옅게 보일 수 있다 → 비네팅으로 묻힌다.
2. 모자 각도 측정은 판 중앙 60% 윗선 직선 맞춤, 오차 ±0.8°. 한 번만 회전한다(재측정 반복은 발산).
3. 모자 검출은 세그멘테이션 'others' 클래스 → 학사모 1장에서만 확인. **다른 아이·다른 모자에서
   깨질 수 있다.** 어깨선은 옷 마스크 윗선(얼굴 중심에서 좌우 0.95 얼굴폭 위치).
4. 얼굴 리퀴파이(`face_shape`)는 실사진에서 작은 값(≤30)만 확인. 큰 값은 미확인.
5. 이 보이는 웃음·감은 눈 뜨기는 **생성 없이 안 됨** → 같은 사람 다른 컷에서 이식하는 방법만 제공.
   생성 모델(LivePortrait/LaMa/Real-ESRGAN)은 붙이지 않았다 — 원하면 다음 단계.

## PC 세션에서 끝낸 것 (2026-09-10)

환경은 `D:\korea-rundown-assets` 에 있고 전부 실물로 확인했다.

- **전용 가상환경** `tools/retouch/.venv` (전역 파이썬은 안 건드림).
  numpy 2.2.6 · opencv 5.0 · mediapipe 1.0.1 · pillow · anthropic 1.4.0 ·
  **torch 2.14.0+cu126 (`cuda True`, RTX 3080 Ti 인식)**.
- **대화창이 실제로 동작한다.** 사진 열기 → 지시 입력 → 도구 호출 → 결과 반영 →
  보고까지 브라우저에서 왕복 확인. 도구가 실패했을 때(`dodge_burn only=person` 에서
  인물 인식 실패) Claude 가 오류를 읽고 마스크 없이 재시도해 성공하는 것까지 확인됨.
- **API 키 없이 돌아간다** (`cli_backend.py`). 아래 별도 절 참고.
- **`보정스튜디오.cmd`** — 더블클릭 실행기. 폴더 선택창 → 기억 → 빈 포트 탐색 → 기동.
  폴더를 아이콘에 끌어다 놓아도 된다.
- 기본 포트를 8765 → **8792** 로 바꿨다. 8765 는 refhub 수집기가 이미 쓴다.
  윈도의 `allow_reuse_address` 때문에 이 충돌은 **오류 없이 통과하고 요청만 남의 서버로
  간다.** 그래서 기동 전에 연결을 시도해 점유를 판정하고(`studio.port_taken`) 죽는다.
- 한글 IME 로 조합 중인 Enter 가 지시를 잘라 보내던 것을 고쳤다 (`studio.html`, `isComposing`).

### 대화창 백엔드 두 갈래 (`--backend auto|api|cli`)

`auto` 는 `ANTHROPIC_API_KEY` 가 있으면 `api`, 없으면 설치된 Claude Code 로 우회한다.

| | api | cli (`claude -p`) |
|---|---|---|
| 준비물 | API 키 + 결제수단 | Claude Code 설치만 (이미 있음) |
| 한 턴 | 5~8초 | **16~28초** (실측: 첫 턴 27.6초, 이어지는 턴 16.1초) |
| 비용 | 실제 청구 (턴당 대략 $0.01~0.02) | 현금 청구 없음, **구독 사용량**을 씀 (턴당 $0.035 상당) |

cli 경로는 턴마다 Claude Code 자체 컨텍스트 약 70k 를 캐시에서 다시 읽는데 이걸 없앨 수
없다 — `--bare` / `CLAUDE_CODE_SIMPLE=1` 로 걷어내면 OAuth 가 끊겨 오히려 API 키가
필요해진다(실측: "Not logged in"). 즉 **키가 생기면 api 가 항상 낫다.** cli 는
키 없이 오늘 당장 쓰기 위한 길이다.

## 다음에 할 일 (순서)

1. **학사모 원본 5~10장에 `pose_report()` 일괄** → 검출 깨지는 사진 찾기
   (`_hat_mask`, `shoulder_points`). **사용자에게 사진 폴더 경로를 받아야 시작된다.**
   사람 사진은 저장소 밖 원래 자리에서만 읽고, 커밋도 업로드도 하지 않는다.
2. 알려진 약점 1(`extend_backdrop` 자동 영역 탐색)·3(모자 검출 일반화) 손보기.
   둘 다 실사진 여러 장이 있어야 판정된다 → 1번 결과를 보고 정한다.
3. 원본/보정본 쌍 20개가 모이면 `learn-preset.mjs`(색 LUT) + `prepare.py`(정렬 수치) 부터.

## 세션에서 있었던 결정·교훈

- 처음 만든 photo-fix(색만) → 인물 일괄(학습) → 스튜디오(대화창) → 포토샵 도구 → 학사모 전용 순으로 확장됨.
- 만화 얼굴 합성 데이터는 배관 검증에만 쓸 것. 랜드마크가 흔들려 수치 비교가 안 된다.
- 모자 판 자리 채우기는 단순 인페인팅이면 몸통 색이 번져 잔상이 남는다 → 배경 추정색 + 몸통 겹침만 벨벳.
- 정규화 블러로 외삽할 때 분모가 0에 가까워지면 검게 무너진다 → 넓은 시그마로 단계적 대체.
- OpenCV xphoto 패치 인페인팅은 인물·의자 조각을 배경에 복사한다 → 미채택.
- (PC 세션) 포트가 이미 점유돼 있어도 윈도에서는 바인드가 성공한다. "떴다 + 200 이 온다"
  두 신호가 모두 참인데 응답한 쪽은 다른 프로그램이었다. 검증의 첫 질문은 "정상인가"가
  아니라 "내가 재는 게 맞는 대상인가"다.
- (PC 세션) `requirements.txt` 의 `torch` 는 이름만으로 CPU 판이 깔린다. 설치는 성공하고
  임포트도 되고 학습도 돌아간다 — GPU 만 안 쓸 뿐이다. 설치 뒤
  `torch.cuda.is_available()` 을 반드시 찍어 볼 것.
