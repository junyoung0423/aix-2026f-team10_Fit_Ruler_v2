"""AI Hub 의류 통합 데이터(dataSetSn 71501) 라벨 1개 -> `GarmentVariant` (#3).

라벨 구조와 필드 뜻은 docs/spikes/aihub-71501.md에 있다. 변환 규칙:

- 반팔(`short sleeves`) 라운드넥(`round neck`) 티셔츠(`02top_02t-shirt`)만 받는다. 색이 하나가
  아니면 무지로 볼 수 없어 받지 않는다([제안]).
- 가슴·허리(`상의가슴둘레`·`상의허리둘레`)는 둘레다. 2배 하지 않는다([제안]: 설명서에 측정 방법이
  없어 필드 이름과 값의 범위로 판단).
- 허리 위치가 신체 자연 허리와 맞는지 근거가 없어 `waist_position_matched=false`로 둔다.
- 앞길이 기준점이 HPS인지 알 수 없어 `body_length_cm`는 null로 두고 원래 값은 notes에 적는다.
- 0이나 "null"은 값 없음(null)이다. 음수·숫자가 아닌 값·범위 밖 값은 거절한다.
- 가슴·어깨가 있으면 READY, 하나라도 없으면 DRAFT다.
- 라벨의 `image_path`·`label_path`는 한글이 깨져 있어 쓰지 않는다. 상품 번호는
  `dataset.name`에서 읽는다.
"""

import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fitting.adapters.aihub.tables import (
    AIHUB_COLOR,
    AIHUB_FIBER,
    AIHUB_STRETCH,
    COLOR_HEX,
    DEFAULT_FABRIC_PRESET,
    MEASUREMENT_RANGES_CM,
    ROUND_NECK,
    SHORT_SLEEVES,
    TSHIRT_TYPE,
)
from fitting.domain import (
    FAMILY_BASIC_SHORT_SLEEVE_CREW,
    FabricInfo,
    GarmentMeasurements,
    GarmentStatus,
    GarmentVariant,
    MeasurementMeta,
)

SOURCE = "AI Hub 의류 통합 데이터(dataSetSn 71501)"
ATTRIBUTION = (
    "한국지능정보사회진흥원(NIA) AI 허브 사업 결과물인 "
    "'의류 통합 데이터(착용 이미지, 치수 및 원단 정보)'에서 변환했다."
)
MEASUREMENT_PROTOCOL_VERSION = "measure_v1"
ASSET_VERSION = "aihub71501_v1"
# 데이터에 의류 자체의 핏형·사이즈가 없다. 근거 없는 값을 만들지 않는다 ([제안], decisions.md)
UNKNOWN = "unknown"

# dataset.name 예: 01_sou_000123_004567_front_02top_02t-shirt_man
#   세 번째 토막(000123)이 상품 번호다. 같은 상품의 front·back·wear 라벨은 치수·색·소재가 같다.
_NAME_PATTERN = re.compile(r"^\d+_[a-z]+_(?P<product>\d+)_(?P<dataset_id>\d+)_(?P<view>[a-z]+)_")

_CLOTHES = "metadata.clothes"
_MODEL = "metadata.model"
_EMPTY = (None, "", "null")
# 단면 너비일 수 있는 치수 (둘레로 바꿀 때 2배 한다)
_FLAT_FIELDS = ("chest_size", "waist_size")
# 데이터에 대소문자만 다른 표기(gray, Regenerated fiber Others 등)가 섞여 있어 대소문자를 무시한다
# ([제안]). 오타(Orenge 등)는 받지 않는다.
_COLOR_BY_LOWER = {name.lower(): key for name, key in AIHUB_COLOR.items()}
_FIBER_BY_LOWER = {name.lower(): value for name, value in AIHUB_FIBER.items()}


class AihubLabelRejected(ValueError):
    """변환할 수 없는 라벨. `reasons`에 이유를 모두 담는다."""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


def read_label(path: Path) -> dict[str, Any]:
    """라벨 JSON 파일을 읽는다 (UTF-8, BOM 허용)."""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise AihubLabelRejected(["라벨 최상위가 JSON 객체가 아닙니다"])
    return data


def garment_id(color: str, product_id: str) -> str:
    """`tee_{style}_{size}_{color}_aihub_{상품 번호}`. 영문 소문자·숫자·밑줄만 쓴다."""
    return f"tee_{UNKNOWN}_{UNKNOWN}_{color}_aihub_{product_id}"


def label_to_garment(
    label: Mapping[str, Any], *, chest_waist_flat_width: bool = False
) -> GarmentVariant:
    """라벨 1개를 등록 의류 1벌로 바꾼다. 받을 수 없으면 `AihubLabelRejected`.

    `chest_waist_flat_width`: 가슴·허리 값이 단면 너비라면 True로 준다. 2배 해서 둘레로 넣고
    `chest_from_flat_width`·`waist_from_flat_width`를 true로 적는다. 기본값 False는 값의 범위로
    정한 [제안]이며, 데이터 설명서에서 단면이라고 확인되면 바꾼다.
    """
    reasons: list[str] = []
    flat = chest_waist_flat_width
    factor = 2.0 if flat else 1.0

    dataset = label.get("dataset")
    clothes = label.get(_CLOTHES)
    if not isinstance(dataset, Mapping) or not isinstance(clothes, Mapping):
        raise AihubLabelRejected([f"라벨에 dataset 또는 {_CLOTHES} 객체가 없습니다"])
    model = label.get(_MODEL)
    model = model if isinstance(model, Mapping) else {}

    name = dataset.get("dataset.name")
    match = _NAME_PATTERN.match(name) if isinstance(name, str) else None
    if match is None:
        reasons.append(f"dataset.name에서 상품 번호를 읽을 수 없습니다: {name!r}")

    reasons += _family_reasons(clothes)
    color, color_reason = _color(clothes.get(f"{_CLOTHES}.color"))
    if color_reason:
        reasons.append(color_reason)
    fibers, fiber_words, fiber_reasons = _fibers(clothes.get(f"{_CLOTHES}.fiber_composition"))
    reasons += fiber_reasons
    stretch = clothes.get(f"{_CLOTHES}.elasticity")
    if stretch in _EMPTY:
        stretch = None
    elif stretch not in AIHUB_STRETCH:
        reasons.append(f"모르는 신축성 값: {stretch!r}")

    values: dict[str, float | None] = {}
    for field, (low, high) in MEASUREMENT_RANGES_CM.items():
        # 범위는 둘레 기준이다. 단면이면 원래 값은 그 절반 범위여야 한다.
        scale = factor if field in _FLAT_FIELDS else 1.0
        value, reason = _measurement(
            clothes.get(f"metadata.top.{field}"), field, low / scale, high / scale
        )
        values[field] = None if value is None else value * scale
        if reason:
            reasons.append(reason)

    if reasons:
        raise AihubLabelRejected(reasons)
    assert match is not None and color is not None  # 위에서 거절하지 않았다

    measurements = GarmentMeasurements(
        chest_girth_cm=values["chest_size"],
        waist_girth_cm=values["waist_size"],
        shoulder_width_cm=values["shoulder_width"],
        sleeve_length_cm=values["sleeve_length"],
        body_length_cm=None,  # 앞길이 기준점이 HPS인지 알 수 없다
    )
    ready = measurements.chest_girth_cm is not None and measurements.shoulder_width_cm is not None
    product_id = match["product"]
    return GarmentVariant(
        id=garment_id(color, product_id),
        style=UNKNOWN,
        size=UNKNOWN,
        color=color,
        color_hex=COLOR_HEX[color],
        fabric_preset=DEFAULT_FABRIC_PRESET,
        fabric=FabricInfo(
            fibers=fibers,
            composition=None,  # 데이터에 섬유 비율이 없다
            stretch=stretch,
            description_en=_description_en(color, fiber_words),
        ),
        garment_family=FAMILY_BASIC_SHORT_SLEEVE_CREW,
        status=GarmentStatus.READY if ready else GarmentStatus.DRAFT,
        measurements=measurements,
        measurement_meta=MeasurementMeta(
            measurement_protocol_version=MEASUREMENT_PROTOCOL_VERSION,
            source=SOURCE,
            is_measured=True,
            chest_from_flat_width=flat and measurements.chest_girth_cm is not None,
            waist_from_flat_width=flat and measurements.waist_girth_cm is not None,
            waist_position_matched=False,
            notes=_notes(match, model, values, fibers, flat),
        ),
        asset_version=ASSET_VERSION,
        source_ref=product_id,
    )


def _family_reasons(clothes: Mapping[str, Any]) -> list[str]:
    expected = {
        "type": TSHIRT_TYPE,
        "sleeve_length_type": SHORT_SLEEVES,
        "topneck_color_design": ROUND_NECK,
    }
    reasons = []
    for key, want in expected.items():
        got = clothes.get(f"{_CLOTHES}.{key}")
        if got != want:
            reasons.append(f"반팔 라운드넥 티셔츠가 아닙니다: {key}={got!r} (필요: {want!r})")
    return reasons


def _color(raw: Any) -> tuple[str | None, str | None]:
    if raw in _EMPTY:
        return None, "색 값이 없습니다"
    if not isinstance(raw, str):
        return None, f"색 값이 문자열이 아닙니다: {raw!r}"
    if "," in raw or raw.strip().lower() == "mixed":
        return None, f"한 가지 색이 아니라서 무지로 볼 수 없습니다: {raw!r}"
    color = _COLOR_BY_LOWER.get(raw.strip().lower())
    if color is None:
        return None, f"모르는 색 이름: {raw!r}"
    return color, None


def _fibers(raw: Any) -> tuple[list[str], list[str], list[str]]:
    """섬유 키, 프롬프트용 영어, 거절 이유. 섬유 정보가 없으면 빈 목록이다."""
    if raw in _EMPTY:
        return [], [], []
    if not isinstance(raw, str):
        return [], [], [f"섬유 조성 값이 문자열이 아닙니다: {raw!r}"]
    keys, words, reasons = [], [], []
    for name in (part.strip() for part in raw.split(",")):
        known = _FIBER_BY_LOWER.get(name.lower())
        if known is None:
            reasons.append(f"모르는 섬유 이름: {name!r}")
        elif known[0] not in keys:
            keys.append(known[0])
            words.append(known[1])
    return keys, words, reasons


def _measurement(raw: Any, field: str, low: float, high: float) -> tuple[float | None, str | None]:
    """cm 값 하나. 값 없음(0, "null")은 None, 받을 수 없는 값은 이유를 돌려준다."""
    if raw in _EMPTY:
        return None, None
    if isinstance(raw, bool):
        return None, f"{field}: 숫자가 아닙니다: {raw!r}"
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, f"{field}: 숫자가 아닙니다: {raw!r}"
    if not math.isfinite(value):
        return None, f"{field}: 유한한 숫자가 아닙니다: {raw!r}"
    if value < 0:
        return None, f"{field}: 음수입니다: {value}"
    if value == 0:
        return None, None
    if not low <= value <= high:
        hint = " (mm로 적었을 수 있습니다)" if value > high else ""
        return None, f"{field}: {value} cm는 받는 범위 {low}~{high} cm 밖입니다{hint}"
    return value, None


def _description_en(color: str, fiber_words: list[str]) -> str:
    """D5 실사화 프롬프트용. 예: "plain black cotton jersey"."""
    if not fiber_words:
        material = ""
    elif len(fiber_words) == 1:
        material = f"{fiber_words[0]} "
    else:
        material = f"{'-'.join(fiber_words)} blend "
    return f"plain {color} {material}jersey"


def _notes(
    match: re.Match[str],
    model: Mapping[str, Any],
    values: Mapping[str, float | None],
    fibers: list[str],
    flat_width: bool,
) -> str:
    notes = [
        ATTRIBUTION,
        f"라벨 dataset.id {int(match['dataset_id'])} ({match['view']}).",
    ]
    usual = model.get(f"{_MODEL}.usually_size")
    usual_text = ""
    if usual not in _EMPTY:
        usual_text = f"착용 모델 평소 사이즈 {usual}(의류 표기 사이즈 아님). "
    notes.append(f"{usual_text}데이터에 의류 사이즈·핏형이 없어 style·size는 unknown.")
    if values["front_length"] is not None:
        notes.append(
            f"앞길이 원래 값 {values['front_length']} cm: 기준점이 데이터 설명에 없어 HPS 기준인지 "
            "알 수 없으므로 body_length_cm는 null."
        )
    if flat_width:
        notes.append("가슴·허리 원래 값을 단면 너비로 보고 2배 해서 둘레로 넣었다.")
    else:
        notes.append("가슴·허리는 필드 이름(둘레)과 값 범위로 둘레로 판단해 2배 하지 않았다.")
    notes.append("허리 측정 위치가 신체 자연 허리와 맞는지 근거가 없다.")
    notes.append("소매길이 시작점(어깨 봉제선)은 확인하지 못했다.")
    fabric = "섬유 비율과 니트(저지) 여부는 데이터에 없다."
    if fibers != ["cotton"]:
        fabric += f" 면 단독이 아니다(섬유: {', '.join(fibers) or '없음'})."
    notes.append(f"{fabric} 원단 프리셋 {DEFAULT_FABRIC_PRESET}은 임시이며 C4에서 매핑한다.")
    return " ".join(notes)
