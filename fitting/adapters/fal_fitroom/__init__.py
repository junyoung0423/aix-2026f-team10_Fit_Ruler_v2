"""피팅룸 실사화 어댑터 (#10 #11): 렌더를 핏 참고로 써서 매장 피팅룸에서 실제 사람이 1/4 바퀴 돌아
옆모습을 보이고 돌아오는 영상을 만든다. `Realizer` 포트 구현은 `realizer.FitRoomRealizer`, 팩토리는
`factory.make_fitroom_realizer`.

흐름(모두 fal)
1. 렌더 정지 프레임 → 실사 사진(Nano Banana Pro 편집). 렌더 핏을 그대로 두라고 지시하고, 이 사진이
   핏 확인 프레임이다
2. 팔을 내린 정면 사진(Nano Banana Pro 편집, 기본 켬. 1의 사진은 렌더의 A자세라 팔이 벌어져 있다)
3. 시작·끝을 2의 사진(끄면 1의 사진)으로 고정한 4초 1/4 바퀴 왕복 영상(Veo 3.1 Fast
   first-last-frame, 표준판은 옵션)
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
