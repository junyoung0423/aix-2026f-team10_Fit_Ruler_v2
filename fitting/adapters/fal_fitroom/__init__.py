"""피팅룸 실사화 어댑터 (#10 #11): 렌더를 핏 참고로 써서 매장 피팅룸에서 실제 사람이 한 바퀴 도는
영상을 만든다. `Realizer` 포트 구현은 `realizer.FitRoomRealizer`, 팩토리는
`factory.make_fitroom_realizer`.

흐름(모두 fal)
1. 렌더 정지 프레임 → 실사 사진(Nano Banana Pro 편집)
2. 렌더 클립 + 1의 사진을 첫 프레임으로 실사화해 렌더 핏을 입힌 프레임(LTX-2.3 render-to-real)
3. 팔을 내린 정면 사진(Nano Banana Pro 편집)
4. 시작·끝을 3의 사진으로 고정한 8초 회전 영상(Veo 3.1 Fast first-last-frame, 표준판은 옵션)
근거·실험 결과는 docs/realize-fitroom.md.
"""

from .prompts import (
    FITROOM_PROMPT_VERSION,
    FitRoomPrompts,
    build_fitroom_prompts,
    pants_for,
)

__all__ = [
    "FITROOM_PROMPT_VERSION",
    "FitRoomPrompts",
    "build_fitroom_prompts",
    "pants_for",
]
