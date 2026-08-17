# tmap-crawler

티맵(Tmap) 앱을 Appium으로 탐색해서 화면 지도(sitemap)를 자동 생성하는 크롤러.

## ⚠️ 절대 제약사항

**주행/경로안내 관련 화면과 기능은 절대 자동으로 탭하지 않는다.**
"경로안내 시작", "주행 시작", "출발" 등 `config.py`의 `DRIVING_BLACKLIST_KEYWORDS`에 매칭되는
버튼은 발견 즉시 기록만 하고 건너뛴다. 새로운 주행 관련 문구를 발견하면 반드시
`DRIVING_BLACKLIST_KEYWORDS`에 추가한 뒤 재실행할 것.

## 사전 준비

1. Android SDK / adb / emulator, Node.js, Java(JDK 17+) 설치
2. Appium + uiautomator2 드라이버 설치:
   ```bash
   npm install -g appium
   appium driver install uiautomator2
   ```
3. Python 가상환경:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
4. Google Play가 포함된 에뮬레이터(AVD)를 만들고, **직접** Google 계정으로 로그인 후
   Play Store에서 티맵을 설치·로그인해둔다 (계정/비밀번호 자동 입력은 하지 않음).
5. (선택) 비전 모델 폴백을 쓰려면 `ANTHROPIC_API_KEY` 환경변수 설정.

## 실행

```bash
# 1) 에뮬레이터 부팅 (별도 터미널)
emulator -avd tmap_crawler

# 2) Appium 서버 (별도 터미널)
appium

# 3) 크롤러 실행
source venv/bin/activate
python3 crawler.py
```

## 결과물

- `output/sitemap.json` - 화면(node)과 전이(edge) 데이터
- `output/sitemap.graphml` - Gephi 등에서 열 수 있는 그래프 포맷
- `output/sitemap.png` - 시각화 이미지
- `output/screenshots/` - 화면별 스크린샷 (파일명 = 화면 fingerprint)

`sitemap.json`의 각 화면(screen)은 `blocked_actions` 필드에 해당 화면에서 발견했지만
탭하지 않은 주행 관련 액션 목록을 갖는다.

## 구성 파일

| 파일 | 역할 |
|---|---|
| `config.py` | Appium capability, 주행 블랙리스트 키워드, 크롤링 한도 |
| `screen_analyzer.py` | UI 계층(XML) 파싱, 화면 지문 생성, 클릭 가능 요소 추출/분류 |
| `vision_fallback.py` | UI 계층만으로 화면을 특정하기 어려울 때(WebView 등) 비전 모델로 화면 설명 생성. 클릭 좌표는 반환하지 않음 |
| `graph_builder.py` | networkx 기반 그래프 빌드 및 json/graphml/png 내보내기 |
| `crawler.py` | DFS 탐색 메인 루프 |

## 알려진 한계

- DFS + 뒤로가기 기반 탐색이라, 뒤로가기로 원래 화면에 복귀하지 못하는 화면(딥링크, 강제 종료 등)을 만나면 해당 분기 탐색이 조기 종료된다.
- 화면 지문은 clickable 요소의 resource-id/class 구조로만 계산되므로, 구조는 같고 내용만 다른 화면(예: 검색 결과 리스트)은 같은 화면으로 병합될 수 있다.
- 로그인/결제 등 민감 플로우는 자동 진행하지 않으므로 커버리지에서 제외된다.
