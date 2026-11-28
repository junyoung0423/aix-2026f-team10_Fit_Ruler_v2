"""피팅룸 실사화 프롬프트 (`fitroom_prompt_v5`).

단계마다 문장이 다르다. 문장을 바꾸면 판을 올린다. 문장은 2026-11-25~26 실험에서 결과를 보고 고른
것이다(docs/realize-fitroom.md 2절). v5는 v4에 사진 기준 그림 문장(`photo_guide`)만 더했다(나머지
문장은 v4와 글자까지 같다).

- 사진(`photo`): 렌더 정지 프레임을 매장 피팅룸의 실제 사람 사진으로 바꾼다. 재질·피부·배경·조명만
  바꾸고 구도(화면 위치)·체형·자세(A자세)와 옷의 핏(박스형 몸판·넓은 소매·기장)은 렌더 그대로 둔다.
  옷 설명에는 핏 이름을 넣지 않는다(정돈된 핏으로 끌 수 있어서). 이 사진이 핏 확인 프레임이다.
  v2는 같은 뜻을 짧게 적었고, 신체 1 렌더에서 팔을 내리고 확대했다(v3에서 바꾼 이유).
- 사진 기준 그림(`photo_guide`, 실사화 옵션 `photo_mask_reference`를 켰을 때만): 렌더 옷 영역을 검은
  바탕의 흰 실루엣으로 둘째 그림에 같이 보내고, 사진의 옷이 그 흰 영역을 그대로 채우게(가슴·허리·
  밑단 폭과 소매) 하는 문장을 사진 문장 뒤에 붙인다. 문장만으로는 사진 모델이 옷을 더 붙게 만든 일이
  있어서다(2026-11-28 시연 작업, 핏 확인 가슴 폭 −9.7 %).
- 자세(`pose`, 기본 켬): 사진에서 팔을 자연스럽게 내리고 나머지는 그대로 둔다.
- 회전(`turn`): 고정 카메라, 숨쉬기만, 앞 → 옆 → 뒤 → 반대쪽 옆 → 앞 한 바퀴(8초). 턱·입·수염·
  얼굴은 옆모습을 포함해 도는 내내 화면에 들어오지 않는다고 적는다. v3의 4초 1/4 바퀴 왕복은 문장과
  반대 방향으로 돌거나 옆모습에서 뒤집혀(q18~q20) v4에서 한 바퀴로 되돌렸다(q23).

얼굴 처리(decisions.md 2절): 모든 단계에서 "목 아래, 머리와 얼굴은 화면 밖"을 적고, 회전은 부정
프롬프트로도 막는다. 그래도 옆모습에서 수염 끝이 화면 맨 위에 걸칠 수 있어, 실사화 어댑터가 회전
영상의 맨 위를 잘라 낸다(`encode.py`).

바지 색(`pants_for`): 옷과 대비되는 색을 고른다. 짙은 청바지는 네이비 티셔츠와 색이 비슷해 검증의 옷
영역 찾기가 둘을 가르지 못했다(실험 2회차). 후보 중 옷 색과 CIEDE2000이 가장 먼 것을 고른다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from fitting.adapters.aihub.tables import COLOR_HEX
from fitting.adapters.fal_ltx.prompt import (
    DEFAULT_FAMILY_TEXT,
    FAMILY_TEXT,
    garment_description,
    material_description,
)
from fitting.domain import GarmentVariant
from fitting.verify.visual_check import delta_e_2000, srgb_to_lab

FITROOM_PROMPT_VERSION = "fitroom_prompt_v5"

# 바지 후보: (프롬프트 문구, sRGB hex). 옷과 대비되는 쪽을 고른다
PANTS: tuple[tuple[str, str], ...] = (
    ("light beige chino trousers", "#d8c8a8"),
    ("dark charcoal trousers", "#33333a"),
)

# 사진: 형상은 그대로 두고 재질·피부·배경·조명만 바꾼다(v3). v2 문장으로 신체 1 렌더를 넣으니 사진
# 모델이 팔을 내리고 약 1.3배 확대하고 박스형 핏을 다듬었다(핏 확인 IoU 0.577). 화면 위치·A자세·
# 박스형 핏을 적고 옷 설명에서 핏 이름을 빼자 IoU 0.989로 렌더를 지켰다(q17, docs/realize-fitroom.md
# 2.8절)
PHOTO_TEMPLATE = (
    "Convert this 3D render into a realistic photograph by changing only the materials, the "
    "skin, the background and the lighting. The geometry must stay identical to the render: "
    "do not move, zoom, re-crop, re-pose or re-shape anything. Camera: keep the exact same "
    "framing, crop and scale: front view, the image starts at the neck and the head and face "
    "are outside the frame. The collar, the shoulders, the sleeve hems and the bottom hem of "
    "the shirt stay at exactly the same positions in the image as in the render, and the "
    "same length of the thighs stays visible below the hem. Do not zoom in or out. Pose: "
    "keep the exact same A-pose: both arms stay raised away from the body at the same angle "
    "as in the render, reaching toward the sides of the image. Do not lower the arms and do "
    "not bring them closer to the body. Shirt: keep the {garment} exactly as it is draped in "
    "the render: the same color, the same boxy, loose silhouette that hangs straight down "
    "from the chest, the same length and hem position, the same looseness at the chest and "
    "waist, the same shoulder seam position, and the same short, wide sleeves that stand "
    "away from the upper arms. Do not tailor, slim down or tidy the shirt into a neater "
    "regular fit. Make it a real person with natural skin on the neck and arms, wearing "
    "plain {pants}, standing in a bright modern retail clothing store fitting room with a "
    "light beige curtain, white walls and clothes hooks, lit by warm overhead store lighting "
    "with soft natural shadows. Photorealistic, natural fabric texture, high detail."
)

# 사진 기준 그림(둘째 그림 = 렌더 옷 영역의 흰 실루엣)을 같이 보낼 때 사진 문장 뒤에 붙인다(v5)
PHOTO_GUIDE = (
    "The second image is only a guide and must not appear in the photo: it is a black-and-white "
    "silhouette of the shirt in the first image, at exactly the same framing (white = shirt). "
    "The shirt in the photo must fill exactly this white area: the same width at the chest, at "
    "the waist and at the hem, and the same sleeves. Never make the shirt narrower, tighter or "
    "more fitted than this silhouette."
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

# 회전: 8초 한 바퀴(앞 → 옆 → 뒤 → 반대쪽 옆 → 앞). 예전 한 바퀴 문장(v1, 커밋 34abf08)에서 구도
# 문장만 바꿨다: 턱·입·수염·얼굴은 옆모습에서도 화면에 들어오지 않고 머리는 늘 화면 위 끝 위에 있다
# (q19에서 턱을 없앤 문장). 4초 1/4 바퀴 왕복(v3)은 문장과 반대 방향으로 돌거나 옆모습에서 뒤집혔고
# (q18~q20), 한 바퀴는 뒤집힌 적이 없다(q5·q6·q8·q22·q23). 도는 방향은 적지 않는다
# (docs/realize-fitroom.md 2.8절)
TURN_TEMPLATE = (
    "Static locked-off camera that never moves, pans or zooms; the framing stays exactly the same "
    "for the whole shot: from the neck down. The chin, mouth, beard and face never enter the frame "
    "at any moment of the turn, not even at the side profiles: the head always stays above the top "
    "edge of the image. "
    "A real person stands on the same spot at the same distance from the camera in a bright retail "
    "clothing store fitting room, wearing the {garment} and {pants}, arms relaxed at the sides. "
    "Right from the first second, the person slowly and continuously turns in place through "
    "exactly one full 360-degree rotation at a steady, even speed: front, then one side profile, "
    "then the back, then the other side profile, and back to the front. The turn ends exactly at "
    "the last frame, facing the camera again just like the first frame. The only other movement is "
    "natural, gentle breathing. No hand gestures: the hands stay relaxed at the sides and do not "
    "touch the clothes. The person does not step toward or away from the camera. The shirt keeps "
    "the same color, fit and length from every angle, with realistic fabric that drapes and moves "
    "naturally with the turn."
)

TURN_NEGATIVE = (
    "face, head, hair, chin, camera movement, zoom in, zoom out, pan, close-up, cut, scene change, "
    "stepping forward, walking, touching the shirt, hand gestures, waving, turning back and forth, "
    "text, logo, extra limbs, extra fingers, changing clothes, blurry, distorted, "
    "beard, mouth, lips, jaw, stubble"
)


@dataclass(frozen=True)
class FitRoomPrompts:
    """한 의류에 대한 단계별 문장."""

    version: str
    garment: str  # 옷 설명(영어), 자세·회전 문장
    photo_garment: str  # 사진 문장의 옷 설명(핏 이름 없이)
    pants: str
    photo: str
    pose: str
    turn: str
    turn_negative: str
    photo_guide: str = PHOTO_GUIDE  # 기준 그림을 같이 보낼 때 사진 문장 뒤에 붙인다

    def to_dict(self) -> dict[str, str]:
        return {
            "version": self.version,
            "garment": self.garment,
            "photo_garment": self.photo_garment,
            "pants": self.pants,
            "photo": self.photo,
            "pose": self.pose,
            "turn": self.turn,
            "turn_negative": self.turn_negative,
            "photo_guide": self.photo_guide,
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


def plain_garment_description(garment: GarmentVariant) -> str:
    """핏 이름 없는 옷 설명(색·원단 + 의류군). 사진 문장에 쓴다."""
    family = FAMILY_TEXT.get(garment.garment_family, DEFAULT_FAMILY_TEXT)
    return f"{material_description(garment)} {family}"


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
    photo_described = garment_text.strip() if garment_text else plain_garment_description(garment)
    pants = pants_for(garment)
    return FitRoomPrompts(
        version=FITROOM_PROMPT_VERSION,
        garment=described,
        photo_garment=photo_described,
        pants=pants,
        photo=PHOTO_TEMPLATE.format(garment=photo_described, pants=pants),
        pose=POSE_TEMPLATE.format(garment=described, pants=pants),
        turn=TURN_TEMPLATE.format(garment=described, pants=pants),
        turn_negative=TURN_NEGATIVE,
    )
