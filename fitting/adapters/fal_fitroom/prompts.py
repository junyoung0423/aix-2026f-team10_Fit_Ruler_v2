"""피팅룸 실사화 프롬프트 (`fitroom_prompt_v1`).

단계마다 문장이 다르다. 문장을 바꾸면 판을 올린다. 문장은 2026-11-25 실험에서 결과를 보고 고른
것이다(docs/realize-fitroom.md 2절).

- ① 사진(`photo`): 렌더 정지 프레임을 매장 피팅룸의 실제 사람 사진으로 바꾼다. 구도·체형·자세와 옷의
  핏(색·기장·가슴·허리 여유·어깨선·소매)은 렌더 그대로 둔다.
- ② 핏 옮기기: D5 프롬프트의 피팅룸 판(`fal_ltx.prompt`의 `realize_prompt_v3`, 팔을 내리고
  숨쉬기만)을 그대로 쓴다.
- ③ 자세(`pose`, 옵션): 팔을 자연스럽게 내리고 나머지는 그대로 둔다.
- ④ 회전(`turn`): 고정 카메라, 숨쉬기만, 제자리에서 앞 → 옆 → 뒤 → 반대쪽 옆 → 앞, 정면에서 끝남.

얼굴 처리(decisions.md 2절): 모든 단계에서 "목 아래, 머리와 얼굴은 화면 밖"을 적고, ④는 부정
프롬프트로도
막는다.

바지 색(`pants_for`): 옷과 대비되는 색을 고른다. 짙은 청바지는 네이비 티셔츠와 색이 비슷해 검증의 옷
영역 찾기가 둘을 가르지 못했다(실험 2회차). 후보 중 옷 색과 CIEDE2000이 가장 먼 것을 고른다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from fitting.adapters.aihub.tables import COLOR_HEX
from fitting.adapters.fal_ltx.prompt import garment_description
from fitting.domain import GarmentVariant
from fitting.verify.visual_check import delta_e_2000, srgb_to_lab

FITROOM_PROMPT_VERSION = "fitroom_prompt_v1"

# 바지 후보: (프롬프트 문구, sRGB hex). 옷과 대비되는 쪽을 고른다
PANTS: tuple[tuple[str, str], ...] = (
    ("light beige chino trousers", "#d8c8a8"),
    ("dark charcoal trousers", "#33333a"),
)

PHOTO_TEMPLATE = (
    "Convert this 3D render into a realistic photograph. Keep the exact same camera framing, crop "
    "and composition: front view, the image starts at the neck and the head and face are outside "
    "the frame. Keep the exact same body shape, body proportions and pose. Keep the {garment} "
    "exactly as it fits in the render: the same color, the same length and hem position, the same "
    "looseness at the chest and waist, the same shoulder seam position and the same sleeve length "
    "and width. Make it a real person with natural skin on the neck and arms, wearing plain "
    "{pants}, "
    "standing in a bright modern retail clothing store fitting room with a light beige curtain, "
    "white walls and clothes hooks, lit by warm overhead store lighting with soft natural shadows. "
    "Photorealistic, natural fabric texture, high detail."
)

POSE_TEMPLATE = (
    "Edit this photo: lower both arms so they hang naturally and relaxed at the sides of the body, "
    "with the hands open and resting near the thighs, and release the hem of the shirt so it falls "
    "naturally. Keep everything else exactly the same: the same person and body shape, the same "
    "{garment} with exactly the same fit, length, sleeve shape and color, the same {pants}, the "
    "same "
    "fitting room background and lighting, and the exact same camera framing from the neck down "
    "with the head and face outside the frame. Photorealistic."
)

TURN_TEMPLATE = (
    "Static locked-off camera that never moves, pans or zooms; the framing stays exactly the same "
    "for the whole shot: from the neck down, and the head and face always stay outside the frame. "
    "A real person stands on the same spot at the same distance from the camera in a bright retail "
    "clothing store fitting room, wearing the {garment} and {pants}, arms relaxed at the sides. "
    "Right "
    "from the first second, the person slowly and continuously turns in place through exactly one "
    "full 360-degree rotation at a steady, even speed: front, then one side profile, then the "
    "back, "
    "then the other side profile, and back to the front. The turn ends exactly at the last frame, "
    "facing the camera again just like the first frame. The only other movement is natural, gentle "
    "breathing. No hand gestures: the hands stay relaxed at the sides and do not touch the "
    "clothes. "
    "The person does not step toward or away from the camera. The shirt keeps the same color, fit "
    "and length from every angle, with realistic fabric that drapes and moves naturally with the "
    "turn."
)

TURN_NEGATIVE = (
    "face, head, hair, chin, camera movement, zoom in, zoom out, pan, close-up, cut, scene change, "
    "stepping forward, walking, touching the shirt, hand gestures, waving, turning back and forth, "
    "text, logo, extra limbs, extra fingers, changing clothes, blurry, distorted"
)


@dataclass(frozen=True)
class FitRoomPrompts:
    """한 의류에 대한 단계별 문장."""

    version: str
    garment: str  # 옷 설명(영어)
    pants: str
    photo: str
    pose: str
    turn: str
    turn_negative: str

    def to_dict(self) -> dict[str, str]:
        return {
            "version": self.version,
            "garment": self.garment,
            "pants": self.pants,
            "photo": self.photo,
            "pose": self.pose,
            "turn": self.turn,
            "turn_negative": self.turn_negative,
        }


def _garment_hex(garment: GarmentVariant) -> str | None:
    if garment.color_hex:
        return garment.color_hex
    return COLOR_HEX.get(garment.color)


def _lab(hex_color: str) -> np.ndarray:
    value = hex_color.lstrip("#")
    rgb = [int(value[i : i + 2], 16) for i in (0, 2, 4)]
    return srgb_to_lab(np.array(rgb, dtype=np.uint8))


def pants_for(garment: GarmentVariant) -> str:
    """옷과 색이 가장 먼 바지 문구. 옷 색을 모르면 첫 후보(밝은 베이지)."""
    hex_color = _garment_hex(garment)
    if not hex_color:
        return PANTS[0][0]
    garment_lab = _lab(hex_color)
    return max(PANTS, key=lambda p: float(delta_e_2000(garment_lab, _lab(p[1]))))[0]


def build_fitroom_prompts(
    garment: GarmentVariant, *, garment_text: str | None = None
) -> FitRoomPrompts:
    """의류로 단계별 문장을 만든다. `garment_text`가 있으면 옷 설명 대신 쓴다(시험용).

    Raises:
        ValueError: `garment_text`가 비었다.
    """
    if garment_text is not None and not garment_text.strip():
        raise ValueError("garment_text가 비었습니다")
    described = garment_text.strip() if garment_text else garment_description(garment)
    pants = pants_for(garment)
    return FitRoomPrompts(
        version=FITROOM_PROMPT_VERSION,
        garment=described,
        pants=pants,
        photo=PHOTO_TEMPLATE.format(garment=described, pants=pants),
        pose=POSE_TEMPLATE.format(garment=described, pants=pants),
        turn=TURN_TEMPLATE.format(garment=described, pants=pants),
        turn_negative=TURN_NEGATIVE,
    )
