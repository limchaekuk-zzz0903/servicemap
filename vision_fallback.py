import base64
import logging
from typing import Optional

from config import ANTHROPIC_API_KEY, VISION_FALLBACK_ENABLED, VISION_MODEL

logger = logging.getLogger("tmap_crawler.vision")

_client = None
if VISION_FALLBACK_ENABLED:
    import anthropic

    _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
else:
    logger.info("ANTHROPIC_API_KEY 미설정: vision fallback 비활성화 상태로 진행")


def is_available() -> bool:
    return _client is not None


def describe_screen(screenshot_path: str) -> Optional[str]:
    """UI 계층 분석만으로 화면을 특정하기 어려울 때(WebView, 커스텀 렌더링 등)
    비전 모델로 화면의 이름/목적을 설명한다.

    중요: 이 함수는 화면에 대한 설명 텍스트만 반환한다. 클릭 좌표를 반환하거나
    클릭을 유도하지 않는다 - 실제 탭 대상 선정은 항상 screen_analyzer의
    UI 계층 기반 로직(및 DRIVING_BLACKLIST_KEYWORDS 필터)을 통해서만 이뤄진다.
    """
    if not is_available():
        return None

    try:
        with open(screenshot_path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")

        response = _client.messages.create(
            model=VISION_MODEL,
            max_tokens=300,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_data,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "이 화면은 내비게이션 앱의 한 화면입니다. "
                                "화면의 이름/목적을 한국어 한 문장으로 간단히 요약해주세요. "
                                "좌표나 클릭 지시는 하지 마세요."
                            ),
                        },
                    ],
                }
            ],
        )
        return response.content[0].text.strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("vision fallback 호출 실패: %s", exc)
        return None
