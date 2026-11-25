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

PROMPT_VERSION = "realize_prompt_v1"

Subject = Literal["person", "mannequin"]

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
    negative_prompt: str | None = None  # 보내지 않는다(fal 기본값 유지)

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
    garment: GarmentVariant, *, subject: str = "person", garment_text: str | None = None
) -> RealizePrompt:
    """의류로 실사화 프롬프트를 만든다. `garment_text`가 있으면 옷 설명 대신 쓴다(시험용).

    Raises:
        ValueError: 모르는 `subject`이거나 `garment_text`가 비었다.
    """
    if subject not in SUBJECTS:
        raise ValueError(f"모르는 대상입니다: {subject} (가능: {', '.join(SUBJECTS)})")
    if garment_text is not None:
        described = _clean(garment_text)
        if not described:
            raise ValueError("garment_text가 비었습니다")
    else:
        described = garment_description(garment)
    text = PROMPT_TEMPLATE.format(subject=SUBJECTS[subject], garment=described)
    return RealizePrompt(text=text, version=PROMPT_VERSION, subject=subject, garment=described)
