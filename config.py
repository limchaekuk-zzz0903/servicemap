import os

APPIUM_SERVER_URL = "http://127.0.0.1:4723"

# ── 대상 디바이스/빌드 선택 ────────────────────────────────────────────────────
# 환경변수로 실물 단말/에뮬레이터를 전환한다. 기본은 기존 에뮬레이터(운영 빌드).
#   TMAP_TARGET=emulator (기본) → com.skt.tmap.ku
#   TMAP_TARGET=device            → 물리 단말의 스테이지 빌드 com.skt.tmap.ku.stage
#   TMAP_UDID=<adb serial>        → 특정 단말 지정 (여러 대 연결 시 필수)
TMAP_TARGET = os.environ.get("TMAP_TARGET", "emulator")
TMAP_UDID = os.environ.get("TMAP_UDID", "")

_TARGETS = {
    "emulator": {
        "deviceName": "tmap_crawler",
        "appPackage": "com.skt.tmap.ku",
    },
    "device": {
        # 물리 단말에는 스테이지 빌드만 설치되어 있음(2026-07-13 확인).
        "deviceName": "physical",
        "appPackage": "com.skt.tmap.ku.stage",
    },
}
_target = _TARGETS.get(TMAP_TARGET, _TARGETS["emulator"])

APPIUM_CAPABILITIES = {
    "platformName": "Android",
    "automationName": "UiAutomator2",
    "deviceName": _target["deviceName"],
    "appPackage": _target["appPackage"],
    "appActivity": "com.skt.tmap.activity.TmapIntroActivity",
    # noReset=True: 로그인 세션을 유지한다. 앱 데이터를 초기화하면 로그인 화면부터
    # 다시 시작하게 되는데, 로그인 자동화(비밀번호 입력)는 정책상 하지 않는다.
    "noReset": True,
    # noReset=True 상태에서 앱이 이미 실행 중이면 Appium이 포그라운드 전환을 건너뛰는 경우가 있어
    # 매 세션 시작 시 확실히 앱을 포그라운드로 가져오도록 강제한다 (로그인 세션 자체는 유지됨).
    "forceAppLaunch": True,
    "autoGrantPermissions": True,
    "newCommandTimeout": 300,
}
if TMAP_UDID:
    APPIUM_CAPABILITIES["udid"] = TMAP_UDID

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APK_PATH = os.path.join(BASE_DIR, "apk", "tmap_base.apk")

OUTPUT_DIR = os.path.join(BASE_DIR, "output")
SCREENSHOT_DIR = os.path.join(OUTPUT_DIR, "screenshots")
# 반복 실행 간 "이미 시도한 요소" 상태를 영속화하는 파일. 매 실행마다 이어서 새 요소만 시도한다.
STATE_PATH = os.path.join(OUTPUT_DIR, "crawl_state.json")

MAX_DEPTH = 16
MAX_SCREENS = 600
MAX_ACTIONS_PER_SCREEN = 20
CLICK_WAIT_SECONDS = 1.5

# ── 지문(fingerprint) 튜닝 ─────────────────────────────────────────────────────
# 화면을 얼마나 세밀하게 구분할지 조절한다. compute_fingerprint는 항상
# activity + resource-id 골격을 반영한다(구조 병합 완화). 아래를 켜면 헤더/툴바의
# 짧은 "제목" 텍스트까지 반영해 설정 하위 페이지처럼 구조는 같고 제목만 다른 화면을
# 더 잘 구분한다. 단, 검색 결과/상세처럼 데이터마다 제목이 바뀌는 화면은 폭증할 수
# 있으니 커버리지가 부족할 때만 켠다.
FINGERPRINT_INCLUDE_TITLE = os.environ.get("TMAP_FP_TITLE", "0") == "1"

# ── 깊은 화면 재진입(DFS 한계 보완) ────────────────────────────────────────────
# 뒤로가기로 복귀 실패 시 앱을 재시작하는데, 그다음 이미 알아낸 그래프의 최단 경로를
# 재생(replay)해서 미탐색 요소가 남은 가장 얕은 화면으로 다시 들어간다. 매 재시작마다
# depth=0부터 DFS를 반복하느라 깊은 분기를 놓치던 문제를 완화한다. 재생 중에도
# 주행/결제 안전 필터(categorize_elements)를 그대로 통과한 요소만 탭한다.
ENABLE_FRONTIER_REPLAY = os.environ.get("TMAP_FRONTIER", "1") == "1"
MAX_REPLAY_STEPS = 16

# 절대 자동으로 탭하지 않는 키워드 목록 (주행/경로안내 관련).
# 대소문자 무시, 부분 일치. 매칭되면 발견/기록만 하고 건너뛴다.
# crawler.py 실행 중 새로운 주행 관련 문구를 발견하면 반드시 이 목록에 추가한 뒤 재실행할 것.
DRIVING_BLACKLIST_KEYWORDS = [
    "경로안내", "안내시작", "안내 시작", "주행시작", "주행 시작", "출발",
    "길안내", "네비게이션 시작", "운전 시작", "실시간 경로", "경로 시작",
    "도착", "정차", "경로 탐색", "안내 재시작", "안내 종료",
    "start navigation", "start driving", "start guide", "guidance start",
    "drive", "navigate",
    # 2026-07-12 실제 크롤링 중 '안전운행' 버튼이 필터를 통과해 라이브 내비게이션
    # 화면(TmapNaviActivity, "주행종료" 버튼 노출)까지 들어간 사고 이후 보강:
    "운행", "주행", "네비", "turn-by-turn", "turn by turn",
    # 2026-07-12 홈 화면 바로가기 탭('편리한 이동'/'혜택·전체')을 찾기 위해
    # clickable=false인 요소까지 추출 범위를 넓히면서, 홈 화면에 바로 노출된
    # "내비"(표준 표기, 기존 "네비"와 다른 철자) 아이콘이 걸러지지 않는 것을 확인해 추가.
    # "운전"도 "대리운전" 등을 잡기 위해 함께 추가 (기존엔 "운전 시작"만 있었음).
    "내비", "운전",
    # 주의: "navi", "guide"는 일부러 뺌 - resource-id까지 검사하는 지금 구조에서는
    # 안드로이드 표준 컴포넌트명(BottomNavigationView 등)과 충돌해 하단 탭바 전체가
    # 차단되는 심각한 과차단이 발생했다(2026-07-12 확인). 대신 구체적인 구문만 사용.
]

# 결제/구매/예약 등 실거래로 이어질 수 있는 액션 차단 키워드.
# 2026-07-12: clickable=false 요소 및 하위 트리 라벨까지 탐색 범위를 넓히면서,
# 주행 관련 필터만으로는 주차 결제/전기차 충전 시작/대중교통 발권/대리운전
# 예약·결제처럼 실제 금전 거래를 유발할 수 있는 버튼을 전혀 걸러내지 못한다는
# 지적을 받고 추가했다. DRIVING_BLACKLIST_KEYWORDS와 동일한 방식(발견만 하고
# 탭하지 않음)으로 처리된다.
PAYMENT_BLACKLIST_KEYWORDS = [
    "결제", "구매", "주문하기", "예약하기", "예약", "신청하기", "신청",
    "충전 시작", "충전하기", "발권", "결제수단", "카드 등록", "카드등록",
    "계좌 등록", "계좌등록", "송금", "바로결제", "바로구매", "정기결제", "구독",
    "checkout", "payment", "pay now", "purchase", "buy now", "subscribe",
    "book now", "reserve", "add card", "add payment",
]

# 광고 배너류 resource-id 패턴 (탭하면 100% 앱 밖(Chrome/Play Store)으로 나가고
# 서비스 화면 지도에 아무 가치가 없어, 탭 후보에서 아예 제외해 재시작 낭비를 줄인다.
# 안전 차단(DRIVING_BLACKLIST_KEYWORDS)과는 성격이 다름 - 이건 순수 효율화용.
AD_ID_HINTS = ("_ad_view", "ad_banner", "adfit", "admob", "native_ad", "top_ad")

# 화면 크롤링 결과 보고 시 참고할 예상 카테고리 (화이트리스트가 아니라 리포트용 참고 목록)
EXPECTED_CATEGORIES = [
    "홈", "검색", "즐겨찾기", "설정", "내정보", "대중교통", "주차", "전기차",
    "테마", "알림", "리뷰", "약관", "고객센터",
]

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
VISION_FALLBACK_ENABLED = bool(ANTHROPIC_API_KEY)
VISION_MODEL = "claude-sonnet-5"
