"""피팅룸 실사화 문장(`fitroom_prompt_v1`)과 D5 프롬프트의 피팅룸 판(`realize_prompt_v2`) 테스트."""

from pathlib import Path

import pytest

pytest.importorskip("numpy")

from fitting.adapters.fal_fitroom import (  # noqa: E402
    FITROOM_PROMPT_VERSION,
    build_fitroom_prompts,
    pants_for,
)
from fitting.adapters.fal_ltx.prompt import (  # noqa: E402
    FAL_DEFAULT_NEGATIVE_PROMPT,
    PROMPT_VERSION,
    STYLE_VERSIONS,
    build_prompt,
)
from fitting.domain import GarmentVariant  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def garment(name: str, **update) -> GarmentVariant:
    data = GarmentVariant.model_validate_json(
        (ROOT / "data" / "garments" / f"{name}.json").read_text(encoding="utf-8")
    )
    return data.model_copy(update=update)


NAVY = garment("tee_regular_m_navy_v1")
BLACK = garment("tee_relaxed_l_black_v1")


def test_pants_contrast_with_the_garment():
    assert pants_for(NAVY) == "light beige chino trousers"  # 짙은 청바지는 네이비와 구분되지 않았다
    assert pants_for(BLACK) == "light beige chino trousers"
    for light in ("#f2f2f2", "#e1c699"):  # 흰색·베이지 옷에는 짙은 바지
        assert pants_for(garment("tee_regular_m_navy_v1", color_hex=light)) == (
            "dark charcoal trousers"
        )


def test_prompts_keep_the_fit_framing_and_face_rule():
    p = build_fitroom_prompts(NAVY)
    assert p.version == FITROOM_PROMPT_VERSION == "fitroom_prompt_v1"
    assert p.garment == "plain navy cotton jersey short-sleeve crew-neck T-shirt with a regular fit"
    for text in (p.photo, p.pose, p.turn):
        assert "neck" in text and "face" in text  # 목 아래, 얼굴은 화면 밖
        assert p.pants in text
    assert "same looseness at the chest and waist" in p.photo
    assert "same shoulder seam position" in p.photo
    assert "fitting room" in p.photo
    assert "lower both arms" in p.pose
    assert "360-degree rotation" in p.turn and "breathing" in p.turn
    assert "No hand gestures" in p.turn
    assert p.turn.index("side profile") < p.turn.index("the back")
    assert {"face", "head", "zoom in", "walking"} <= set(p.turn_negative.split(", "))
    assert set(p.to_dict()) == {"version", "garment", "pants", "photo", "pose", "turn",
                                "turn_negative"}


def test_garment_text_overrides_and_must_not_be_blank():
    custom = build_fitroom_prompts(NAVY, garment_text="plain red T-shirt")
    assert custom.garment == "plain red T-shirt"
    with pytest.raises(ValueError):
        build_fitroom_prompts(NAVY, garment_text="  ")


def test_d5_fitting_room_style_is_version_3_still_with_arms_down():
    studio = build_prompt(NAVY)
    room = build_prompt(NAVY, style="fitting_room")
    assert studio.version == PROMPT_VERSION == STYLE_VERSIONS["studio"]
    assert studio.negative_prompt is None  # v1은 fal 기본값을 그대로 둔다
    assert room.version == STYLE_VERSIONS["fitting_room"] == "realize_prompt_v3"
    assert "fitting room" in room.text and "from the neck down" in room.text
    assert "arms hanging naturally" in room.text
    assert "only movement is gentle breathing" in room.text
    assert "tugging" not in room.text and "smoothing" not in room.text  # v2의 손동작을 뺐다
    terms = room.negative_prompt.split(", ")
    # fal 기본값을 앞에 두되 움직임을 부추기는 낱말만 뺀다
    assert {"static", "still", "motionless frame"}.isdisjoint(terms)
    motion = {"static", "still", "motionless frame"}
    base = [t for t in FAL_DEFAULT_NEGATIVE_PROMPT.split(", ") if t not in motion]
    assert terms[: len(base)] == base  # 나머지 기본값은 순서 그대로 앞에 둔다
    assert {"mannequin", "face", "hand gestures", "hands on hips"} <= set(terms)


def test_d5_fitting_room_style_rejects_unknown_style_and_mannequin():
    with pytest.raises(ValueError, match="모르는 장면"):
        build_prompt(NAVY, style="beach")
    with pytest.raises(ValueError, match="person"):
        build_prompt(NAVY, style="fitting_room", subject="mannequin")
