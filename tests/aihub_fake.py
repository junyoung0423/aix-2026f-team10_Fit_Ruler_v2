"""AI Hub 의류 통합 데이터(71501) 라벨과 구조만 같은 가짜 라벨 (#3).

값은 모두 테스트용으로 지어낸 것이다. AI Hub 원본 파일을 복사하지 않는다.
CI에는 AI Hub 데이터가 없으므로 어댑터 테스트는 이 가짜 라벨만 쓴다.
"""

import copy
from typing import Any

FAKE_PRODUCT_ID = "900001"

_BASE: dict[str, Any] = {
    "dataset": {
        "dataset.id": 990001,
        "dataset.name": f"01_sou_{FAKE_PRODUCT_ID}_990001_front_02top_02t-shirt_man",
        # 원본처럼 경로 문자열이 있지만 어댑터는 쓰지 않는다
        "dataset.image_path": "./fake/broken-encoding/path.jpg",
        "dataset.label_path": "./fake/broken-encoding/path.json",
        "dataset.category": 6,
        "dataset.type": 1,
        "dataset.width": 100,
        "dataset.height": 100,
        "dataset.file_size": 1,
    },
    "annotation": [
        {
            "annotation_type": "Polygon",
            "annotation_id": 1,
            "annotation_point_count": 3.0,
            "annotation_point": [0.0, 0.0, 10.0, 0.0, 10.0, 10.0],
        }
    ],
    "metadata.model": {
        "metadata.model.gender": "MALE",
        "metadata.model.type": "front",
        "metadata.model.usually_size": "L",
        "metadata.model.body_height": "175",
        "metadata.model.chest_size_male": "99.0",
        "metadata.model.breast_size_female": "null",
        "metadata.model.waist_size": "80",
        "metadata.model.shoulders_width": "44",
        "metadata.model.arm_length": "58",
    },
    "metadata.clothes": {
        "metadata.clothes.type": "02top_02t-shirt",
        "metadata.clothes.season": "summer",
        "metadata.clothes.fiber_composition": "Cotton",
        "metadata.clothes.elasticity": "contain little",
        "metadata.clothes.transparency": "none",
        "metadata.clothes.isfleece": "fleece_none",
        "metadata.clothes.color": "White",
        "metadata.clothes.top_length_type": "normal",
        "metadata.clothes.sleeve_length_type": "short sleeves",
        "metadata.clothes.topneck_color_design": "round neck",
        "metadata.clothes.topsleeve_design": "basic sleeve",
        "metadata.top.front_length": "70.0",
        "metadata.top.chest_size": "110.0",
        "metadata.top.waist_size": "104.0",
        "metadata.top.hem_size": "104.0",
        "metadata.top.shoulder_width": "47.0",
        "metadata.top.sleeve_length": "21.0",
        "metadata.top.sleeve_cuff_size": "36.0",
        "metadata.pants.pants_length": "null",
    },
}


def fake_label(**clothes: Any) -> dict[str, Any]:
    """가짜 라벨. `chest_size="54.0"`처럼 `metadata.top.*`·`metadata.clothes.*`의 끝 이름으로
    값을 바꾼다.

    `None`을 주면 그 키를 지운다.
    """
    label = copy.deepcopy(_BASE)
    body = label["metadata.clothes"]
    for short, value in clothes.items():
        key = next((k for k in body if k.rsplit(".", 1)[-1] == short), None)
        if key is None:
            raise KeyError(short)
        if value is None:
            del body[key]
        else:
            body[key] = value
    return label
