# TMAP 서비스맵 프로젝트 — 다른 PC 셋업/작업 지침서

TMAP 앱을 Appium으로 자동 크롤링해 화면 구조(서비스맵)를 수집하고,
자기완결형 HTML로 시각화하는 프로젝트를 **다른 PC에서 동일하게** 진행하기 위한 문서.

---

## 0. 파이프라인 개요

```
[안드로이드 기기]  --Appium-->  crawler.py  -->  output/sitemap.json
   (에뮬 or 실물)                (DFS 화면 탐색)      (화면/전이/차단액션)
                                                           |
                                    scripts/build_service_map.py
                                                           v
                                    output/service_map.html  (단일 파일, 이미지 임베드)
```

- **crawler.py**: 화면을 DFS로 눌러보며 새 화면/전이를 기록. 주행·결제 버튼은 절대 탭하지 않음.
- **screen_analyzer.py**: UI XML 파싱, 화면 지문(fingerprint) 계산, 요소 분류/안전필터.
- **graph_builder.py**: networkx 그래프 → json/graphml/png 내보내기.
- **build_service_map.py + service_map_template.html**: sitemap.json → 서비스맵 HTML.
- **config.py**: 대상 기기/패키지, 탐색 한도, **주행·결제 차단 키워드**.

---

## 1. 프로젝트 폴더 옮기기

`tmap-crawler`는 **git 저장소가 아님** → 폴더를 통째로 복사(zip 전송)한다.

**반드시 포함**: `*.py`, `scripts/`, `requirements.txt`, `run_stage.sh`, `run_repeated.sh`,
`README.md`, `HANDOFF.md`, (에뮬레이터로 돌릴 경우) `apk/tmap_base.apk`

**제외(재생성/재수집됨)**: `venv/`, `output/screenshots/`, `output/*.json`, `output/*.html`

---

## 2. 사전 설치물

| 항목 | 용도 | 비고 |
|---|---|---|
| Python 3.9+ | 크롤러/생성기 | |
| Node.js + Appium 2/3 | UI 자동화 서버 | `npm i -g appium` |
| Appium uiautomator2 드라이버 | 안드로이드 제어 | `appium driver install uiautomator2` |
| Java JDK 17 | Appium/adb 요구 | `JAVA_HOME` 설정 |
| Android SDK (adb, emulator) | 기기 연결 | **`ANDROID_HOME` 설정 필수** |
| Pillow (macOS 외) | 썸네일 생성 | 윈도우/리눅스는 `pip install pillow`. macOS는 내장 `sips` 자동 사용 |
| 크롤 대상 기기 | 화면 수집 | 에뮬(APK 설치) 또는 실물(TMAP 로그인 상태) |

> **핵심 함정**: Appium 서버는 `ANDROID_HOME`을 export한 셸에서 띄워야 한다.
> 안 그러면 `Neither ANDROID_HOME nor ANDROID_SDK_ROOT ...` 에러로 세션이 안 열린다.

---

## 3. 환경 구성 (명령)

```bash
cd tmap-crawler
python3 -m venv venv
source venv/bin/activate               # 윈도우: venv\Scripts\activate
pip install -r requirements.txt
pip install pillow                     # macOS가 아니면 실행 (썸네일용)

# Appium 드라이버 (최초 1회)
npm install -g appium
appium driver install uiautomator2

# 환경변수 (셸 프로필에 넣어두면 편함)
export ANDROID_HOME="$HOME/Library/Android/sdk"     # 각자 SDK 경로로
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export JAVA_HOME="/opt/homebrew/opt/openjdk@17"     # 각자 JDK17 경로로
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$PATH"
```

---

## 4. 대상 기기 선택 (환경변수)

`config.py`가 환경변수로 대상을 전환한다.

```bash
adb devices           # 연결된 기기의 serial 확인 (예: R3CX405R5FM)
```

| 시나리오 | 설정 | 패키지 | 준비 |
|---|---|---|---|
| 에뮬레이터 | `TMAP_TARGET=emulator` (기본) | `com.skt.tmap.ku` | `adb install apk/tmap_base.apk` 후 앱에서 로그인 |
| 실물 단말 | `TMAP_TARGET=device` `TMAP_UDID=<serial>` | `com.skt.tmap.ku.stage` | TMAP 스테이지 앱 설치+로그인 상태여야 함 |

> 기기가 여러 대 연결돼 있으면 **`TMAP_UDID`로 대상 serial을 반드시 지정**한다.
> 패키지명이 다른 빌드를 쓰면 `config.py`의 `_TARGETS`에서 `appPackage`를 맞춰준다.

---

## 5. 실행 절차

### 5-1. Appium 서버 기동 (ANDROID_HOME 있는 셸에서!)
```bash
appium            # http://127.0.0.1:4723 에서 대기. 별도 터미널 유지
```
(에뮬레이터로 돌린다면 먼저 `emulator -avd <이름>`으로 부팅)

### 5-2. 크롤 실행
단발(검증용):
```bash
source venv/bin/activate
export TMAP_TARGET=device TMAP_UDID=<serial>     # 에뮬이면 이 줄 생략
python3 crawler.py
```
반복 + 라운드마다 맵 자동 갱신(권장, 커버리지 극대화):
```bash
export TMAP_TARGET=device TMAP_UDID=<serial> TMAP_TIME_BUDGET_MIN=20
./run_stage.sh 12          # 20분짜리 12라운드. 각 라운드 끝에 service_map.html 갱신
```
- 상태는 `output/crawl_state.json`에 저장되어 **재실행하면 이어서** 탐색한다.
- 화면 증가가 멈추면(예: 3~4라운드 연속 무증가) 커버리지 소진으로 보고 종료.

### 5-3. 서비스맵 HTML 생성 (수동으로도)
```bash
python3 scripts/build_service_map.py
# → output/service_map.html  (더블클릭하면 브라우저에서 열림)
```

---

## 6. 안전 규칙 — 반드시 유지 ⚠️

`config.py`의 두 블랙리스트는 **삭제·완화 금지**. 크롤러는 여기 매칭되는 버튼을
**발견만 하고 절대 탭하지 않는다.**

- `DRIVING_BLACKLIST_KEYWORDS` — 주행·경로안내 (안전 필수: 실물에서 실제 내비 시작 방지)
- `PAYMENT_BLACKLIST_KEYWORDS` — 결제·구매·예약·충전 등 금전 거래

크롤 로그에 새로운 주행/결제 문구가 그대로 탭되는 게 보이면, 해당 키워드를 목록에
**추가한 뒤 재실행**한다. (실물 단말은 이 필터가 안전의 핵심이다.)

---

## 7. 튜닝 파라미터

| 항목 | 위치/환경변수 | 기본 | 설명 |
|---|---|---|---|
| 최대 화면 수 | `config.MAX_SCREENS` | 600 | 한 실행에서 수집할 상한 |
| 최대 깊이 | `config.MAX_DEPTH` | 16 | DFS 깊이 |
| 라운드 길이 | `TMAP_TIME_BUDGET_MIN` | 45 | run_stage.sh 라운드당 분 |
| 화면 구분 강도 | `TMAP_FP_TITLE=1` | off | 헤더 제목까지 지문에 반영(설정 하위페이지 구분↑, 리스트 폭증 위험) |
| 경로 재생 복구 | `TMAP_FRONTIER=1` | on | 뒤로가기 복구 실패 시 그래프 최단경로 재생 |

지문 로직(`screen_analyzer.compute_fingerprint`)은 **activity + resource-id 골격**을
기본 반영해, 구조는 같지만 다른 화면이 하나로 병합되는 문제를 줄인다.

---

## 8. 트러블슈팅 / 알려진 한계

- **`ANDROID_HOME ... not exported` (세션 안 열림)**: Appium을 `ANDROID_HOME` export된
  셸에서 재기동. (`pkill -f appium` 후 위 4번 export 하고 `appium` 다시 실행)
- **여러 기기 → 엉뚱한 기기 잡음**: `TMAP_UDID`로 serial 지정.
- **경로 재생이 자주 실패**: TMAP 홈은 광고/동적 콘텐츠로 지문이 매번 바뀌어 재시작 후
  경로를 못 찾는 경우가 많다. **정상이며 depth=0 재시작으로 안전 폴백**한다. 크롤은
  tried_elements 누적으로 계속 전진한다.
- **썸네일이 비어 나옴**: macOS 외 환경에서 `pip install pillow`.
- **화면 수가 기대보다 적음**: 위치 기반 화면(주변/주차/충전 등)은 GPS가 필요.
  실물 단말이 유리하고, 에뮬은 mock location 주입을 고려.

---

## 9. 산출물

- `output/sitemap.json` — 화면(node)/전이(edge)/차단액션 원본 데이터
- `output/service_map.html` — **최종 시각화 (단일 파일, 공유용)**
- `output/screenshots/<fingerprint>.png` — 화면별 스크린샷
- `output/sitemap.graphml`, `sitemap.png` — Gephi/미리보기용

---

## 부록 A. Claude Code(또는 AI 에이전트)에 붙여넣을 프롬프트

> 아래를 그대로 복사해 다른 PC의 Claude Code에 붙여넣으면 된다.

```text
tmap-crawler 폴더에서 TMAP 앱 서비스맵 작업을 이어서 진행해줘.

목표: TMAP 앱을 Appium으로 자동 크롤링해 화면 구조(sitemap)를 수집하고,
scripts/build_service_map.py로 자기완결형 서비스맵 HTML을 생성한다.

지켜야 할 것:
1) config.py의 DRIVING_BLACKLIST_KEYWORDS / PAYMENT_BLACKLIST_KEYWORDS(주행·결제 차단)는
   절대 삭제·완화하지 말 것. 크롤러는 이 버튼들을 발견만 하고 절대 탭하지 않는다.
2) Appium 서버는 반드시 ANDROID_HOME이 export된 셸에서 기동한다.
3) 기기가 여러 대면 TMAP_UDID로 대상 serial을 지정한다.

진행 순서:
1) HANDOFF.md 3번대로 venv + requirements + (macOS 아니면) pillow 설치.
2) `adb devices`로 대상 serial 확인. 에뮬은 TMAP_TARGET=emulator(기본),
   실물은 TMAP_TARGET=device TMAP_UDID=<serial> (패키지 com.skt.tmap.ku.stage).
3) Appium 기동 후, 반복 크롤:
   export TMAP_TARGET=... TMAP_UDID=... TMAP_TIME_BUDGET_MIN=20 && ./run_stage.sh 12
4) 화면 증가가 3~4라운드 연속 멈추면 커버리지 소진으로 보고 종료.
5) python3 scripts/build_service_map.py 로 output/service_map.html 생성.

크롤러 구조/한계는 HANDOFF.md와 README.md에 정리돼 있으니 먼저 읽고 시작해줘.
지문 병합·DFS 복구(navigate_to 경로재생) 개선이 이미 적용돼 있다.
```

---

## 부록 B. 한 줄 요약 실행 (에뮬레이터 기준, 셋업 완료 후)

```bash
cd tmap-crawler && source venv/bin/activate
# (별도 터미널) ANDROID_HOME export 후 appium
./run_stage.sh 12 && open output/service_map.html   # 윈도우: start output\service_map.html
```
