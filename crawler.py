import datetime
import json
import logging
import os
import time

import networkx as nx
from appium import webdriver
from appium.options.android import UiAutomator2Options
from selenium.common.exceptions import WebDriverException

import config
import vision_fallback
from graph_builder import SitemapGraph
from screen_analyzer import (
    categorize_elements,
    compute_fingerprint,
    extract_clickable_elements,
    is_likely_ad,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("tmap_crawler")


class NavigationLost(Exception):
    """뒤로가기로 예상 화면에 복귀하지 못해 앱을 강제 재시작한 경우 발생한다.
    run()까지 전파되어 안전한 지점(depth=0)에서 탐색을 이어간다."""


class TmapCrawler:
    def __init__(self):
        self.driver = None
        self.graph = SitemapGraph()
        self.screens_discovered = 0
        self.blocked_actions = []
        self.left_app_events = []
        self.restart_count = 0
        # 화면별로 이미 시도한 요소의 signature 집합. 앱 재시작/재실행 후 이어서
        # 탐색할 때 같은 버튼을 중복으로 다시 누르지 않기 위함. output/crawl_state.json에
        # 영속화되어 프로세스를 껐다 켜도(반복 실행) 이어서 새 요소만 시도한다.
        self.tried_elements: dict[str, set[str]] = {}
        # 가로/세로 스크롤로 숨은 요소를 이미 확인한 화면 fingerprint 집합 (화면당 1회만 수행)
        self.scroll_checked: set[str] = set()
        os.makedirs(config.SCREENSHOT_DIR, exist_ok=True)

    def load_state(self):
        if not os.path.exists(config.STATE_PATH):
            logger.info("이전 상태 없음 - 처음부터 탐색 시작")
            return
        with open(config.STATE_PATH, encoding="utf-8") as f:
            state = json.load(f)

        for s in state.get("screens", []):
            self.graph.add_screen(
                fingerprint=s["fingerprint"],
                activity=s["activity"],
                screenshot_path=s.get("screenshot_path"),
                depth=s.get("depth", 0),
                discovered_at=s.get("discovered_at"),
                label=s.get("label"),
            )
            for ba in s.get("blocked_actions", []):
                self.graph.add_blocked_action(s["fingerprint"], ba["action_label"], ba["block_reason"])
                self.blocked_actions.append(
                    {"from_screen": s["fingerprint"], "action_label": ba["action_label"], "block_reason": ba["block_reason"]}
                )
        for t in state.get("transitions", []):
            self.graph.add_transition(
                t["from"], t["to"], t["action_label"], t.get("blocked", False),
                t.get("block_reason"), t.get("signature", ""),
            )

        self.left_app_events = state.get("left_app_events", [])
        self.tried_elements = {fp: set(sigs) for fp, sigs in state.get("tried_elements", {}).items()}
        self.scroll_checked = set(state.get("scroll_checked", []))
        self.screens_discovered = len(state.get("screens", []))
        logger.info(
            "이전 상태 로드: 화면 %d개, 시도 기록 있는 화면 %d개, 스크롤 확인된 화면 %d개",
            self.screens_discovered,
            len(self.tried_elements),
            len(self.scroll_checked),
        )

    def save_state(self):
        screens = [{"fingerprint": n, **attrs} for n, attrs in self.graph.graph.nodes(data=True)]
        transitions = [{"from": u, "to": v, **attrs} for u, v, attrs in self.graph.graph.edges(data=True)]
        state = {
            "screens": screens,
            "transitions": transitions,
            "left_app_events": self.left_app_events,
            "tried_elements": {fp: sorted(sigs) for fp, sigs in self.tried_elements.items()},
            "scroll_checked": sorted(self.scroll_checked),
        }
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        with open(config.STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def start_session(self):
        options = UiAutomator2Options().load_capabilities(config.APPIUM_CAPABILITIES)
        self.driver = webdriver.Remote(config.APPIUM_SERVER_URL, options=options)
        self.driver.implicitly_wait(5)
        logger.info("Appium 세션 시작")

    def stop_session(self):
        if self.driver:
            self.driver.quit()
            logger.info("Appium 세션 종료")

    def screenshot(self, fingerprint: str) -> str:
        path = os.path.join(config.SCREENSHOT_DIR, f"{fingerprint}.png")
        self.driver.save_screenshot(path)
        return path

    def current_activity(self) -> str:
        try:
            return self.driver.current_activity
        except Exception:  # noqa: BLE001
            return "unknown"

    def current_package(self) -> str:
        try:
            return self.driver.current_package
        except Exception:  # noqa: BLE001
            return "unknown"

    def fingerprint(self, xml_source: str = None) -> str:
        """현재(또는 주어진) 화면의 지문을 activity와 함께 계산한다. compute_fingerprint가
        activity를 함께 반영하도록 바뀌면서, 지문을 만드는 모든 지점이 activity를 넘기도록
        이 헬퍼로 통일한다."""
        if xml_source is None:
            xml_source = self.driver.page_source
        return compute_fingerprint(xml_source, self.current_activity())

    def register_screen(self, fingerprint: str, xml_source: str, depth: int):
        if fingerprint in self.graph.graph:
            return
        path = self.screenshot(fingerprint)
        activity = self.current_activity()
        label = activity.split(".")[-1]

        if not extract_clickable_elements(xml_source) and vision_fallback.is_available():
            desc = vision_fallback.describe_screen(path)
            if desc:
                label = desc

        self.graph.add_screen(
            fingerprint=fingerprint,
            activity=activity,
            screenshot_path=path,
            depth=depth,
            discovered_at=datetime.datetime.now().isoformat(),
            label=label,
        )
        self.screens_discovered += 1
        logger.info("[depth=%d] 신규 화면 발견: %s (%s)", depth, fingerprint, label)

    def go_back_safely(self):
        try:
            self.driver.back()
        except WebDriverException:
            pass

    def return_to_screen(self, target_fp: str, max_attempts: int = 4) -> str:
        """뒤로가기를 최대 max_attempts회 시도하며 target_fp로 복귀를 확인한다.
        팝업/오버레이가 뒤로가기 한 번을 소비하는 경우가 있어 재시도가 필요하다.
        그래도 복귀하지 못하면 앱을 강제 재시작해 depth=0 루트로 복구하고
        NavigationLost를 던져 run()에서 이어서 탐색하도록 한다."""
        current_fp = target_fp
        for attempt in range(max_attempts):
            self.go_back_safely()
            time.sleep(config.CLICK_WAIT_SECONDS)
            current_fp = self.fingerprint()
            if current_fp == target_fp:
                return current_fp
            logger.debug(
                "복귀 재시도 %d/%d: target=%s, 현재=%s", attempt + 1, max_attempts, target_fp, current_fp
            )

        logger.warning(
            "뒤로가기 %d회 시도했지만 원래 화면(%s)으로 복귀하지 못함 (현재: %s). 앱 재시작으로 복구.",
            max_attempts,
            target_fp,
            current_fp,
        )
        root_fp = self.recover_app()
        # 앱을 재시작했으니 이미 알아낸 그래프의 최단 경로를 재생해 원래 탐색하던
        # 화면으로 되돌아간다. 성공하면 depth=0부터 다시 내려오지 않고 그 자리에서
        # 이어서 탐색할 수 있어 깊은 분기 손실을 크게 줄인다.
        if getattr(config, "ENABLE_FRONTIER_REPLAY", True) and target_fp in self.graph.graph:
            if self.navigate_to(target_fp):
                logger.info("경로 재생으로 원래 화면(%s) 복귀 성공 - 이어서 탐색", target_fp)
                return target_fp
            logger.info("경로 재생 실패 - depth=0에서 탐색 재개")
        raise NavigationLost(root_fp)

    def navigate_to(self, target_fp: str, max_steps: int = None) -> bool:
        """앱 재시작 직후 루트에서, 이미 구축한 그래프의 최단 경로를 따라 요소를 다시
        탭하며 target_fp로 되돌아간다. 각 단계에서 저장된 action_label과 일치하고
        주행/결제 안전 필터를 통과한 요소만 탭한다(재생 중에도 안전 정책 유지).
        경로가 없거나 한 단계라도 재현에 실패하면 False를 반환한다."""
        max_steps = max_steps or getattr(config, "MAX_REPLAY_STEPS", 16)
        g = self.graph.graph
        try:
            cur = self.fingerprint()
        except WebDriverException:
            return False
        if cur == target_fp:
            return True
        if cur not in g or target_fp not in g:
            return False
        try:
            path = nx.shortest_path(g, cur, target_fp)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return False
        if len(path) - 1 > max_steps:
            return False

        for u, v in zip(path, path[1:]):
            edge = g.edges[u, v]
            sig = edge.get("signature", "")
            label = edge.get("action_label", "")
            safe, _blocked = categorize_elements(extract_clickable_elements(self.driver.page_source))
            # signature 우선 매칭(재시작에 안정적), 없으면 label로 폴백.
            target_el = None
            if sig:
                target_el = next((e for e in safe if e.signature == sig), None)
            if target_el is None and label:
                target_el = next((e for e in safe if e.label == label), None)
            if target_el is None:
                return False
            center = target_el.center()
            if not center:
                return False
            try:
                self.driver.tap([center])
            except WebDriverException:
                return False
            time.sleep(config.CLICK_WAIT_SECONDS)
            if self.current_package() != config.APPIUM_CAPABILITIES["appPackage"]:
                return False
            if self.fingerprint() != v:
                return False
        return True

    def recover_app(self) -> str:
        """앱을 강제 종료 후 재시작해 depth=0 루트 화면으로 복구한다."""
        self.restart_count += 1
        pkg = config.APPIUM_CAPABILITIES["appPackage"]
        try:
            self.driver.terminate_app(pkg)
        except WebDriverException as exc:
            logger.debug("terminate_app 실패: %s", exc)
        time.sleep(1.0)
        try:
            self.driver.activate_app(pkg)
        except WebDriverException as exc:
            logger.debug("activate_app 실패: %s", exc)

        self.wait_for_app_foreground()
        self.wait_for_splash_to_clear()
        time.sleep(config.CLICK_WAIT_SECONDS)
        return self.fingerprint()

    def explore_and_tap(self, elements, from_fp: str, depth: int, tried: set):
        """주어진 요소 목록(기본 화면이든 스크롤로 드러난 화면이든)을 분류해서
        주행/결제 관련은 차단 기록만 하고, 안전한 요소는 아직 안 시도한 것만 탭해본다."""
        safe_elements, blocked_elements = categorize_elements(elements)

        for el, reason in blocked_elements:
            if el.signature in tried:
                continue
            tried.add(el.signature)
            logger.warning("주행/결제 관련 액션 차단(탭하지 않음): '%s' (키워드: %s)", el.label, reason)
            self.blocked_actions.append(
                {"from_screen": from_fp, "action_label": el.label, "block_reason": reason}
            )
            self.graph.add_blocked_action(from_fp, el.label, reason)

        for el in safe_elements:
            if el.signature in tried:
                continue
            if is_likely_ad(el):
                tried.add(el.signature)
                logger.debug("광고 배너 패턴이라 탭 건너뜀: %s", el.resource_id)

        untried_safe = [
            el for el in safe_elements if el.signature not in tried and not is_likely_ad(el)
        ]

        for el in untried_safe[: config.MAX_ACTIONS_PER_SCREEN]:
            if self.screens_discovered >= config.MAX_SCREENS:
                break

            center = el.center()
            if not center:
                continue

            tried.add(el.signature)

            try:
                self.driver.tap([center])
            except WebDriverException as exc:
                logger.debug("탭 실패: %s (%s)", el.label, exc)
                continue

            time.sleep(config.CLICK_WAIT_SECONDS)

            current_package = self.current_package()
            if current_package != config.APPIUM_CAPABILITIES["appPackage"]:
                logger.warning(
                    "'%s' 탭으로 티맵 밖(%s)으로 이동함. 즉시 복귀하고 이 요소는 건너뜀.",
                    el.label,
                    current_package,
                )
                self.left_app_events.append({"from_screen": from_fp, "action_label": el.label, "landed_package": current_package})
                self.return_to_screen(from_fp, max_attempts=4)
                continue

            new_xml = self.driver.page_source
            to_fp = self.fingerprint(new_xml)

            if to_fp == from_fp:
                continue

            already_explored = to_fp in self.tried_elements
            self.register_screen(to_fp, new_xml, depth + 1)
            self.graph.add_transition(from_fp, to_fp, el.label, signature=el.signature)
            logger.info("[depth=%d] '%s' 탭 -> %s", depth, el.label, to_fp)

            if not already_explored:
                self.crawl(depth + 1)

            self.return_to_screen(from_fp, max_attempts=4)

    def reveal_scrolled_elements(self, expected_fp: str, direction: str) -> list:
        """가로/세로로 살짝 스와이프해 화면 밖에 숨은 요소(홈 화면 바로가기 아이콘,
        긴 목록 등)를 찾는다. 지도를 스와이프해도 지도가 이동할 뿐이라 위험하지 않다.
        스크롤로 드러난 요소도 categorize_elements의 동일한 주행 블랙리스트 검사를
        그대로 거치므로 안전 정책은 동일하게 적용된다. 탐색 후 원래 위치로 복원한다."""
        try:
            size = self.driver.get_window_size()
        except WebDriverException as exc:
            logger.debug("window size 조회 실패: %s", exc)
            return []

        if direction == "horizontal":
            y = int(size["height"] * 0.35)
            start = (int(size["width"] * 0.85), y)
            end = (int(size["width"] * 0.15), y)
        else:
            x = int(size["width"] * 0.5)
            start = (x, int(size["height"] * 0.75))
            end = (x, int(size["height"] * 0.35))

        revealed: list = []
        try:
            self.driver.swipe(start[0], start[1], end[0], end[1], 300)
            time.sleep(config.CLICK_WAIT_SECONDS)
            xml = self.driver.page_source
            if self.fingerprint(xml) != expected_fp:
                revealed = extract_clickable_elements(xml)
        except WebDriverException as exc:
            logger.debug("스크롤 탐색(%s) 실패: %s", direction, exc)
        finally:
            try:
                self.driver.swipe(end[0], end[1], start[0], start[1], 300)
                time.sleep(config.CLICK_WAIT_SECONDS)
            except WebDriverException:
                pass

        return revealed

    def crawl(self, depth: int = 0):
        """depth=0에서 시작해 재귀적으로 탐색한다. 화면별로 이미 시도한 요소는
        tried_elements에 기록되므로, NavigationLost로 중단된 뒤 run()이 다시
        crawl(0)을 호출해도(혹은 프로세스를 재실행해도) 새 요소만 이어서 시도한다."""
        if depth > config.MAX_DEPTH or self.screens_discovered >= config.MAX_SCREENS:
            return

        time.sleep(config.CLICK_WAIT_SECONDS)
        xml_source = self.driver.page_source
        from_fp = self.fingerprint(xml_source)
        self.register_screen(from_fp, xml_source, depth)

        tried = self.tried_elements.setdefault(from_fp, set())

        elements = extract_clickable_elements(xml_source)
        self.explore_and_tap(elements, from_fp, depth, tried)

        if from_fp not in self.scroll_checked:
            self.scroll_checked.add(from_fp)
            for direction in ("horizontal", "vertical"):
                revealed = self.reveal_scrolled_elements(from_fp, direction)
                if revealed:
                    logger.info(
                        "[depth=%d] %s 스크롤로 %d개 추가 요소 발견", depth, direction, len(revealed)
                    )
                    self.explore_and_tap(revealed, from_fp, depth, tried)

    def wait_for_app_foreground(self, timeout: float = 15.0) -> bool:
        """세션 시작 직후 앱이 실제로 포그라운드에 뜰 때까지 대기한다."""
        target = config.APPIUM_CAPABILITIES["appPackage"]
        start = time.time()
        while time.time() - start < timeout:
            if self.current_package() == target:
                return True
            time.sleep(0.5)
        return False

    def wait_for_splash_to_clear(self, timeout: float = 15.0):
        """TmapIntroActivity 스플래시가 타이머로 자동 전환될 때까지 대기한다."""
        intro_activity = config.APPIUM_CAPABILITIES["appActivity"].rsplit(".", 1)[-1]
        start = time.time()
        while time.time() - start < timeout:
            activity = self.current_activity().rsplit(".", 1)[-1]
            if activity != intro_activity:
                return
            time.sleep(0.5)
        logger.warning("스플래시(%s)가 %.0f초 내에 전환되지 않음", intro_activity, timeout)

    def run(self, max_restarts: int = 40, time_budget_seconds: float = 45 * 60):
        self.load_state()
        screens_at_start = self.screens_discovered
        self.start_session()
        deadline = time.time() + time_budget_seconds
        try:
            if not self.wait_for_app_foreground():
                logger.error("티맵이 포그라운드로 전환되지 않음. 크롤링 중단.")
                return
            self.wait_for_splash_to_clear()

            while self.restart_count <= max_restarts and time.time() < deadline:
                if self.screens_discovered >= config.MAX_SCREENS:
                    break
                try:
                    self.crawl(depth=0)
                    logger.info("depth=0에서 더 이상 새로 시도할 요소가 없어 탐색을 마칩니다.")
                    break
                except NavigationLost:
                    logger.info(
                        "탐색 재개 (재시작 %d/%d, 지금까지 화면 %d개)",
                        self.restart_count,
                        max_restarts,
                        self.screens_discovered,
                    )
                    continue
            else:
                if time.time() >= deadline:
                    logger.warning("시간 제한(%.0f분)에 도달해 탐색을 종료합니다.", time_budget_seconds / 60)
                else:
                    logger.warning("최대 재시작 횟수(%d)에 도달해 탐색을 종료합니다.", max_restarts)
        finally:
            self.save_state()
            self.save_outputs(screens_at_start)
            self.stop_session()

    def save_outputs(self, screens_at_start: int = 0):
        json_path = self.graph.to_json(left_app_events=self.left_app_events)
        graphml_path = self.graph.to_graphml()
        png_path = self.graph.render_png()

        logger.info("=" * 60)
        logger.info("크롤링 완료")
        logger.info("이번 실행에서 새로 발견한 화면 수: %d", self.screens_discovered - screens_at_start)
        logger.info("누적 발견된 화면 수: %d", self.screens_discovered)
        logger.info("앱 강제 재시작 횟수: %d", self.restart_count)
        logger.info("차단된(주행/결제 관련) 액션 수: %d", len(self.blocked_actions))
        for b in self.blocked_actions:
            logger.info("  - [%s] %s (키워드: %s)", b["from_screen"], b["action_label"], b["block_reason"])
        logger.info("티맵 밖으로 이탈했다가 복귀한 횟수: %d", len(self.left_app_events))
        for e in self.left_app_events:
            logger.info("  - [%s] '%s' -> %s", e["from_screen"], e["action_label"], e["landed_package"])
        logger.info("sitemap.json: %s", json_path)
        logger.info("sitemap.graphml: %s", graphml_path)
        logger.info("sitemap.png: %s", png_path)


if __name__ == "__main__":
    # 라운드 길이/재시작 한도를 환경변수로 조절(반복 실행 시 체크포인트 주기 제어).
    budget_min = float(os.environ.get("TMAP_TIME_BUDGET_MIN", "45"))
    max_restarts = int(os.environ.get("TMAP_MAX_RESTARTS", "40"))
    crawler = TmapCrawler()
    crawler.run(max_restarts=max_restarts, time_budget_seconds=budget_min * 60)
