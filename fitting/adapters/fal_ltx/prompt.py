"""실사화 프롬프트 템플릿 (D5, #10).

LTX-2.3 render-to-real에 보내는 영어 프롬프트를 의류 정보로 만든다. 템플릿 판(`PROMPT_VERSION`)을
도구 버전과 기록에 남겨 어떤 문장으로 만든 영상인지 나중에 알 수 있게 한다.
문장을 바꾸면 판을 올린다. 근거와 실측은 docs/realize-pipeline.md 1절.

문장 구성 (트리거 `3DREAL.`은 `render_to_real.with_trigger`가 맨 앞에 붙인다)
1. 대상과 옷: "A realistic photo of {대상} wearing a {옷}, standing still with arms slightly away
   from the body"
   - 옷 = 색·원단 + 의류군 + 핏. 예: "plain navy cotton jersey short-sleeve crew-neck T-shirt with a
     regular fit"
   - 자세 문구는 렌더(C5)의 몸 자세(A자세 45°, 정지 클립)와 맞춘 것이다.
2. 얼굴 처리 (decisions.md 2절): "front view from the neck down, cropped below the chin".
   렌더가 턱 아래로 자른 구도를 그대로 적는다. "no face" 같은 부정문은 넣지 않는다. 생성 모델은
   부정을 잘 따르지 않아 그 낱말이 오히려 얼굴을 부를 수 있다([제안], 비교 호출은 안 함).
3. 배경·조명: 렌더 배경(`config/render/render_v1.json` 중간 회색 #808080)과 같은
   "plain medium gray studio background".
4. 보존: "Keep the garment color, shape, length and fit exactly as in the render."

`negative_prompt`는 보내지 않는다. 보내면 fal 기본값(색 왜곡·흐림·자막·손가락 수 같은 품질·해부
오류 목록, fal 모델 페이지 2026-11-23 확인)을 통째로 덮어쓴다.

대상(`subject`)은 렌더 몸 재질(중립 회색 마네킹, render.md "결정 필요")과 함께 정할 항목이다.
기본은 D1·D4 실제 호출과 같은 "a person"이고, "mannequin"은 렌더의 회색 마네킹을 그대로 두는 문구다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from fitting.domain import FAMILY_BASIC_SHORT_SLEEVE_CREW, GarmentVariant

PROMPT_VERSION = "realize_prompt_v1"  # 스튜디오 문장(기본)

Subject = Literal["person", "mannequin"]
Style = Literal["studio", "fitting_room"]

# 장면 → 문장 판. `studio`는 v1(렌더 그대로의 회색 스튜디오), `fitting_room`은 v3(매장 피팅룸의 실제
# 사람, 팔을 내리고 숨쉬기만). 강한 실사화 강도(`strong`·`strong-v2`)와 함께 쓰려고 만든 문장이다.
# v2(실험 q1·q3)는 "밑단 당기기·앞판 펴기·어깨 돌리기"를 시켜 손이 밑단을 잡은 채 끝났다.
STYLE_VERSIONS: dict[str, str] = {
    "studio": PROMPT_VERSION,
    "fitting_room": "realize_prompt_v3",
}

SUBJECTS: dict[str, str] = {
    "person": "a person",
    "mannequin": "a matte light gray mannequin",
}

PROMPT_TEMPLATE = (
    "A realistic photo of {subject} wearing a {garment}, "
    "standing still with arms slightly away from the body, "
    "front view from the neck down, cropped below the chin, "
    "plain medium gray studio background, soft even lighting, natural fabric folds. "
    "Keep the garment color, shape, length and fit exactly as in the render."
)

# v3: 매장형 피팅룸에서 실제 사람이 옷을 입고 선 장면. 얼굴은 v1처럼 턱 아래로 자른 구도를 적고,
# 얼굴·머리는 부정 프롬프트로 막는다(본문에 부정문을 넣지 않는다). 거울은 화면에 넣지 않고 "카메라가
# 거울인 것처럼" 적는다. 거울을 그리면 비친 얼굴이 생길 수 있어서다.
FITTING_ROOM_TEMPLATE = (
    "A realistic video of a real person wearing a {garment} in a bright retail clothing store "
    "fitting room, checking the fit as if looking into a full-length mirror at the camera. "
    "Front view from the neck down, cropped below the chin. "
    "Natural skin with realistic texture on the neck and arms. "
    "The person stands still and relaxed facing the camera, with both arms hanging naturally at "
    "the sides and the hands not touching the shirt; the only movement is gentle breathing. "
    "Clean modern fitting room with a light beige curtain, white walls and clothes hooks, "
    "bright warm retail lighting from above with soft natural shadows. The camera stays fixed. "
    "Realistic cotton fabric texture with natural folds and creases. "
    "Keep the T-shirt color, shape, length and fit exactly as in the render."
)

# fal 기본 부정 프롬프트(2026-11-25 API 스키마에서 확인). 부정 프롬프트를 보내면 기본값이 통째로
# 바뀌어서 기본값을 앞에 두고 v3용 낱말을 덧붙인다. 다만 움직임을 부추기는 "static·still·motionless
# frame"은 v3(가만히 서서 숨쉬기만)와 맞지 않아 뺀다(`FITTING_ROOM_BASE_NEGATIVE`).
FAL_DEFAULT_NEGATIVE_PROMPT = (
    "color distortion, overexposure, static, blurry details, subtitles, style, artwork, painting, "
    "frame, still, dim overall tone, worst quality, low quality, JPEG compression artifacts, ugly, "
    "mutilated, extra fingers, poorly drawn hands, poorly drawn face, deformed, disfigured, "
    "malformed limbs, fused fingers, motionless frame, cluttered background, three legs, crowded "
    "background, walking backwards"
)
FITTING_ROOM_NEGATIVE_ADDITIONS = (
    "face, head, mouth, eyes, mannequin, plastic skin, gray skin, CGI, 3D render, video game, "
    "mirror reflection, text, logo, changing the shirt color, taking off the shirt, hand gestures, "
    "touching the shirt, pulling the hem, hands on hips, raised arms"
)
_MOTION_TERMS = {"static", "still", "motionless frame"}
FITTING_ROOM_BASE_NEGATIVE = ", ".join(
    term for term in FAL_DEFAULT_NEGATIVE_PROMPT.split(", ") if term not in _MOTION_TERMS
)

# 의류군 → 프롬프트의 옷 이름
FAMILY_TEXT: dict[str, str] = {
    FAMILY_BASIC_SHORT_SLEEVE_CREW: "short-sleeve crew-neck T-shirt",
}
DEFAULT_FAMILY_TEXT = "short-sleeve T-shirt"

# 핏형(`style`) → 프롬프트의 핏. 표에 없는 값(unknown 등)은 넣지 않는다.
# 키는 화면 표(`web/src/labels.ts`)·decisions.md 3절과 같은 네 가지다.
FIT_TEXT: dict[str, str] = {
    "slim": "slim fit",
    "regular": "regular fit",
    "relaxed": "relaxed fit",
    "oversized": "oversized fit",
}

_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class RealizePrompt:
    """만든 프롬프트와 그 재료. `text`에는 트리거가 없다."""

    text: str
    version: str
    subject: str
    garment: str  # 옷 설명 부분
    negative_prompt: str | None = None  # v1은 보내지 않는다(fal 기본값 유지). v3는 기본값 + 덧붙임

    def to_dict(self) -> dict[str, str | None]:
        return {
            "version": self.version,
            "subject": self.subject,
            "garment": self.garment,
            "text": self.text,
            "negative_prompt": self.negative_prompt,
        }


def _clean(text: str) -> str:
    return _SPACES.sub(" ", text).strip().rstrip(".").strip()


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text, flags=re.IGNORECASE) is not None


def material_description(garment: GarmentVariant) -> str:
    """색과 원단. 예: "plain navy cotton jersey".

    - 원단 설명(`fabric.description_en`)이 있으면 쓴다. 색 낱말이 없으면 앞에 붙인다.
    - 없고 섬유 목록이 있으면 "plain {색} {섬유} jersey"(AI Hub 변환과 같은 모양).
    - 둘 다 없으면 "plain {색} cotton jersey"(D4와 같은 가정).
    - 색이 `unknown`이면 색 낱말을 넣지 않는다.
    """
    color = "" if garment.color == "unknown" else _clean(garment.color.replace("_", " "))
    fabric = garment.fabric
    described = _clean(fabric.description_en) if fabric and fabric.description_en else ""
    if described:
        if color and not _has_word(described, color):
            return f"{color} {described}"
        return described
    fibers = [f.replace("_", " ") for f in (fabric.fibers if fabric else [])]
    if not fibers:
        material = "cotton"
    elif len(fibers) == 1:
        material = fibers[0]
    else:
        material = f"{'-'.join(fibers)} blend"
    return _clean(f"plain {color} {material} jersey")


def garment_description(garment: GarmentVariant) -> str:
    """프롬프트에 넣을 옷 설명(영어): 색·원단 + 의류군 + 핏."""
    family = FAMILY_TEXT.get(garment.garment_family, DEFAULT_FAMILY_TEXT)
    fit = FIT_TEXT.get(garment.style)
    text = f"{material_description(garment)} {family}"
    return f"{text} with a {fit}" if fit else text


def build_prompt(
    garment: GarmentVariant,
    *,
    subject: str = "person",
    garment_text: str | None = None,
    style: str = "studio",
) -> RealizePrompt:
    """의류로 실사화 프롬프트를 만든다. `garment_text`가 있으면 옷 설명 대신 쓴다(시험용).

    `style`이 `fitting_room`이면 v3 문장(매장 피팅룸·실제 사람·팔 내리고 숨쉬기만)과 부정 프롬프트를
    쓴다. 이때 대상은 `person`만 된다.

    Raises:
        ValueError: 모르는 `subject`·`style`이거나 `garment_text`가 비었거나, 피팅룸에
            마네킹을 골랐다.
    """
    if subject not in SUBJECTS:
        raise ValueError(f"모르는 대상입니다: {subject} (가능: {', '.join(SUBJECTS)})")
    if style not in STYLE_VERSIONS:
        raise ValueError(f"모르는 장면입니다: {style} (가능: {', '.join(STYLE_VERSIONS)})")
    if style == "fitting_room" and subject != "person":
        raise ValueError("피팅룸 장면(fitting_room)은 대상이 person일 때만 씁니다")
    if garment_text is not None:
        described = _clean(garment_text)
        if not described:
            raise ValueError("garment_text가 비었습니다")
    else:
        described = garment_description(garment)
    if style == "fitting_room":
        return RealizePrompt(
            text=FITTING_ROOM_TEMPLATE.format(garment=described),
            version=STYLE_VERSIONS[style],
            subject=subject,
            garment=described,
            negative_prompt=f"{FITTING_ROOM_BASE_NEGATIVE}, {FITTING_ROOM_NEGATIVE_ADDITIONS}",
        )
    text = PROMPT_TEMPLATE.format(subject=SUBJECTS[subject], garment=described)
    return RealizePrompt(text=text, version=PROMPT_VERSION, subject=subject, garment=described)
