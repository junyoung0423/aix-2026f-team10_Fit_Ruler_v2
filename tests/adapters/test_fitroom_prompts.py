"""피팅룸 실사화 문장(`fitroom_prompt_v5`)과 D5 프롬프트의 피팅룸 판(`realize_prompt_v2`) 테스트."""

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
    assert p.version == FITROOM_PROMPT_VERSION == "fitroom_prompt_v5"
    # v5: 기준 그림 문장은 따로 두고, 사진 문장에는 붙지 않는다(실사화 옵션을 켤 때만 붙인다)
    assert "black-and-white silhouette" in p.photo_guide and p.photo_guide not in p.photo
    assert p.to_dict()["photo_guide"] == p.photo_guide
    assert p.garment == "plain navy cotton jersey short-sleeve crew-neck T-shirt with a regular fit"
    # 사진 문장에는 핏 이름을 넣지 않는다(정돈된 레귤러 핏으로 끌 수 있어서, q17)
    assert p.photo_garment == "plain navy cotton jersey short-sleeve crew-neck T-shirt"
    assert p.photo_garment in p.photo and "with a regular fit" not in p.photo
    assert p.garment in p.pose and p.garment in p.turn
    for text in (p.photo, p.pose, p.turn):
        assert "neck" in text and "face" in text  # 목 아래, 얼굴은 화면 밖
        assert p.pants in text
    assert "same looseness at the chest and waist" in p.photo
    # v3: 형상은 그대로, 화면 위치·A자세·박스형 핏을 지킨다
    assert "changing only the materials, the skin, the background and the lighting" in p.photo
    assert "Do not zoom in or out" in p.photo and "Do not lower the arms" in p.photo
    assert "boxy" in p.photo and "Do not tailor, slim down or tidy the shirt" in p.photo
    assert "same shoulder seam position" in p.photo
    assert "fitting room" in p.photo
    assert "lower both arms" in p.pose
    assert "one full 360-degree rotation" in p.turn and "breathing" in p.turn
    # v4: 8초 한 바퀴(앞 → 옆 → 뒤 → 반대쪽 옆 → 앞). 도는 방향은 적지 않는다. 방향을 적은 4초
    # 1/4 바퀴 왕복(v3)은 문장과 반대로 돌거나 옆모습에서 뒤집혔다(q18~q20)
    assert "then the back, then the other side profile" in p.turn
    assert "person's own left" not in p.turn and "quarter turn" not in p.turn
    # 옆모습에서도 턱·수염이 화면에 들어오지 않게(q19·q23)
    assert "The chin, mouth, beard and face never enter the frame" in p.turn
    assert "No hand gestures" in p.turn
    negative = set(p.turn_negative.split(", "))
    assert {"face", "head", "zoom in", "walking", "turning back and forth"} <= negative
    assert {"beard", "mouth", "lips", "jaw", "stubble"} <= negative
    assert "back view" not in negative  # 한 바퀴라 뒷모습을 보인다
    assert set(p.to_dict()) == {"version", "garment", "photo_garment", "pants", "photo", "pose",
                                "turn", "turn_negative", "photo_guide"}


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
