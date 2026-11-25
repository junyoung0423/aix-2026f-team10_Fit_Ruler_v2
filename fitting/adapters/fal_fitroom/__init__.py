"""피팅룸 실사화 어댑터 (#10 #11): 렌더를 핏 참고로 써서 매장 피팅룸에서 실제 사람이 한 바퀴 도는
영상을 만든다. `Realizer` 포트 구현은 `realizer.FitRoomRealizer`, 팩토리는
`factory.make_fitroom_realizer`.

흐름(모두 fal)
1. 렌더 정지 프레임 → 실사 사진(Nano Banana Pro 편집). 렌더 핏을 그대로 두라고 지시하고, 이 사진이
   핏 확인 프레임이다
2. (옵션, 기본 끔) 팔을 내린 정면 사진(Nano Banana Pro 편집)
3. 시작·끝을 1의 사진(2를 켜면 2의 사진)으로 고정한 8초 회전 영상(Veo 3.1 Fast first-last-frame,
   표준판은 옵션)
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
