import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional, Tuple

import config
from config import AD_ID_HINTS, DRIVING_BLACKLIST_KEYWORDS, PAYMENT_BLACKLIST_KEYWORDS

UNSAFE_ACTION_KEYWORDS = list(DRIVING_BLACKLIST_KEYWORDS) + list(PAYMENT_BLACKLIST_KEYWORDS)

BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


@dataclass
class UIElement:
    resource_id: str
    text: str
    content_desc: str
    class_name: str
    bounds: str
    # 요소 자신은 text/content-desc/resource-id가 모두 비어있지만(흔한 익명 클릭
    # 컨테이너), 하위 트리(아이콘+라벨 등)에 사람이 읽는 문구가 있는 경우 여기 채워진다.
    # 2026-07-12: "내비"/"대중교통" 같은 홈 화면 바로가기의 실제 탭 대상은 라벨이 없는
    # 부모 컨테이너였고, 라벨은 형제/자식 TextView에만 있었다. 이 필드 없이는
    # is_driving_related가 해당 라벨을 전혀 보지 못해 주행 관련 버튼이 필터를
    # 완전히 우회하는 심각한 안전 문제가 있었다.
    descendant_text: str = ""

    @property
    def label(self) -> str:
        if self.text or self.content_desc or self.resource_id:
            return self.text or self.content_desc or self.resource_id
        if self.descendant_text:
            return self.descendant_text
        center = self.center()
        pos = f"@{center}" if center else ""
        return f"{self.class_name}{pos}"

    @property
    def signature(self) -> str:
        """앱 재시작 등으로 같은 화면을 다시 방문했을 때 '같은 버튼'인지 식별하기 위한 안정적인 키.
        좌표(bounds)는 기본적으로 제외한다 - 동적 레이아웃에서 살짝 흔들릴 수 있어 불안정하다.

        예외: resource-id/text/content-desc가 전부 비어있는 "익명 컨테이너"는 좌표를
        섞어서 구분한다. 홈 화면 바로가기 아이콘들처럼 클릭 가능한 wrapper뷰에
        고유 식별자가 전혀 없고 라벨은 옆의 형제 노드에만 있는 레이아웃에서, 식별
        정보 없이는 서로 다른 아이콘(대중교통/티맵랭킹/...)이 전부 동일한 시그니처로
        묶여 하나만 탭하고 나머지는 "이미 시도함"으로 건너뛰는 버그가 있었다(2026-07-12 확인).
        """
        base = f"{self.resource_id}|{self.class_name}|{self.text}|{self.content_desc}"
        if not (self.resource_id or self.text or self.content_desc):
            center = self.center()
            if center:
                grid = (round(center[0] / 40) * 40, round(center[1] / 40) * 40)
                base += f"|pos:{grid}"
        return base

    def center(self) -> Optional[Tuple[int, int]]:
        m = BOUNDS_RE.match(self.bounds)
        if not m:
            return None
        x1, y1, x2, y2 = map(int, m.groups())
        return (x1 + x2) // 2, (y1 + y2) // 2


def is_likely_ad(element: "UIElement") -> bool:
    """탭하면 거의 항상 앱 밖(Chrome/Play Store)으로 나가는 광고 배너 패턴인지 확인한다.
    안전 차단이 아니라 순수 효율화 목적 - 서비스 지도에 가치가 없는 탭을 미리 걸러낸다."""
    resource_id = element.resource_id.lower()
    return any(hint in resource_id for hint in AD_ID_HINTS)


def is_driving_related(element: "UIElement") -> Optional[str]:
    """text/content-desc/resource-id/descendant_text를 모두 검사한다. 화면에 보이는
    문구(text)만 검사하면 'resource-id: btn_start_navi'처럼 우호적인 표시 문구 뒤에
    숨은 주행 관련 버튼을 놓칠 수 있고, 라벨이 하위 트리(형제 아이콘/텍스트)에만
    있는 익명 컨테이너도 놓칠 수 있어 descendant_text까지 함께 확인한다.

    이름은 그대로지만 실제로는 주행(DRIVING_BLACKLIST_KEYWORDS)과 결제/구매/예약
    (PAYMENT_BLACKLIST_KEYWORDS)을 함께 검사한다 - 둘 다 "발견만 하고 탭하지 않음"
    정책이 동일해서 하나의 체크 함수로 통합했다."""
    haystack = " ".join(
        [element.text, element.content_desc, element.resource_id, element.descendant_text]
    ).strip().lower()
    if not haystack:
        return None
    for kw in UNSAFE_ACTION_KEYWORDS:
        if kw.lower() in haystack:
            return kw
    return None


_TITLE_ID_HINTS = ("title", "toolbar", "appbar", "app_bar", "header", "actionbar", "action_bar")


def _extract_title_band(root) -> str:
    """헤더/툴바/타이틀 영역의 짧은 텍스트만 모은다. 화면마다 안정적으로 다른
    "제목"(예: 설정 하위 페이지명)으로 화면을 구분하기 위한 신호다. 리스트 아이템의
    긴 본문 텍스트는 제외해(길이 제한) 검색 결과처럼 내용만 다른 화면이 무한히
    쪼개지는 것을 막는다."""
    titles = []
    for node in root.iter():
        rid = node.attrib.get("resource-id", "").lower()
        if not any(h in rid for h in _TITLE_ID_HINTS):
            continue
        txt = node.attrib.get("text", "").strip()
        if txt and len(txt) <= 24:
            titles.append(txt)
    # 순서 흔들림/중복 제거 후 정렬해 안정적인 키를 만든다.
    return "|".join(sorted(set(titles)))


def compute_fingerprint(xml_source: str, activity: Optional[str] = None) -> str:
    """화면 지문을 생성한다.

    기존에는 clickable 요소의 class:resource-id 구조만 사용해서, 서로 다른
    액티비티(화면)라도 클릭 요소 골격이 우연히 같으면 하나로 병합되는 문제가 있었다
    (README 알려진 한계 참고). 이를 완화하기 위해 다음을 함께 반영한다.

    1) activity 이름 - 다른 액티비티는 확실히 다른 화면으로 구분한다.
    2) resource-id 골격 - clickable 여부와 무관하게 화면에 존재하는 전체 resource-id
       집합(정렬·중복제거). 같은 액티비티 안에서 메뉴/기능이 다른 하위 화면을 구분한다.
    3) (config.FINGERPRINT_INCLUDE_TITLE=True일 때) 헤더/툴바의 짧은 제목 텍스트.

    좌표나 실시간 텍스트 같은 동적 값은 여전히 제외해, 같은 화면을 다른 화면으로
    오인하지 않게 한다. 제목 반영은 리스트/상세 화면이 데이터마다 폭증하는 부작용이
    있어 기본 비활성이며 config에서 켤 수 있다."""
    try:
        root = ET.fromstring(xml_source)
    except ET.ParseError:
        return hashlib.sha1(xml_source.encode("utf-8")).hexdigest()[:16]

    clickable_parts = []
    resource_ids = set()
    for node in root.iter():
        rid = node.attrib.get("resource-id", "")
        cls = node.attrib.get("class", "")
        clickable = node.attrib.get("clickable", "false")
        if rid:
            resource_ids.add(rid)
        if rid or clickable == "true":
            clickable_parts.append(f"{cls}:{rid}:{clickable}")

    segments = [
        f"act:{activity or ''}",
        "clk:" + "|".join(clickable_parts),
        "ids:" + "|".join(sorted(resource_ids)),
    ]

    if getattr(config, "FINGERPRINT_INCLUDE_TITLE", False):
        segments.append("title:" + _extract_title_band(root))

    structure = "\n".join(segments)
    return hashlib.sha1(structure.encode("utf-8")).hexdigest()[:16]


def _collect_descendant_label(node) -> str:
    """이 노드(자신 포함) 하위 트리에 있는 모든 text/content-desc를 모아 하나의
    문자열로 합친다. 클릭 대상 컨테이너 자신은 라벨이 없고, 실제 라벨은 자식/형제
    아이콘·텍스트 뷰에 있는 커스텀 UI(홈 화면 바로가기 등)를 위한 안전망이다."""
    parts = []
    for sub in node.iter():
        t = sub.attrib.get("text", "").strip()
        cd = sub.attrib.get("content-desc", "").strip()
        if t:
            parts.append(t)
        if cd:
            parts.append(cd)
    return " ".join(parts)


def extract_clickable_elements(xml_source: str) -> List[UIElement]:
    try:
        root = ET.fromstring(xml_source)
    except ET.ParseError:
        return []

    # 클릭 가능해 보이지만 clickable="false"인 커스텀 탭/칩 UI를 위한 보조 신호.
    # (예: 티맵 홈의 "편리한 이동"/"혜택·전체" 탭은 clickable 속성이 false지만
    # 실제로는 탭 전환이 동작한다 - RecyclerView/커스텀 제스처 기반 UI에서 흔함)
    INTERACTIVE_ID_HINTS = ("tab", "chip", "btn", "button", "item", "cell", "service")

    elements = []
    for node in root.iter():
        attrib = node.attrib
        if attrib.get("enabled", "true") != "true":
            continue
        if attrib.get("displayed", "true") != "true":
            continue
        bounds = attrib.get("bounds", "")
        if not bounds or bounds == "[0,0][0,0]":
            continue

        resource_id = attrib.get("resource-id", "")
        text = attrib.get("text", "")
        content_desc = attrib.get("content-desc", "")
        is_clickable = attrib.get("clickable", "false") == "true"
        looks_interactive = any(kw in resource_id.lower() for kw in INTERACTIVE_ID_HINTS)
        has_label = bool(text.strip() or content_desc.strip())

        # 자신에게 라벨이 없는 클릭 가능 컨테이너는 하위 트리에서 라벨을 찾아본다.
        descendant_text = ""
        if is_clickable and not (text.strip() or content_desc.strip()):
            descendant_text = _collect_descendant_label(node)

        if not (is_clickable or (looks_interactive and has_label)):
            continue

        elements.append(
            UIElement(
                resource_id=resource_id,
                text=text,
                content_desc=content_desc,
                class_name=attrib.get("class", ""),
                bounds=bounds,
                descendant_text=descendant_text,
            )
        )
    return elements


def categorize_elements(
    elements: List[UIElement],
) -> Tuple[List[UIElement], List[Tuple[UIElement, str]]]:
    """주행 관련(블랙리스트) 요소와 탭해도 안전한 요소를 분리한다."""
    safe, blocked = [], []
    for el in elements:
        reason = is_driving_related(el)
        if reason:
            blocked.append((el, reason))
        else:
            safe.append(el)
    return safe, blocked
