"""AI Hub 의류 통합 데이터(dataSetSn 71501) 변환 어댑터 (#3, C1).

라벨 1개를 등록 의류(`GarmentVariant`) 1벌로 바꾼다. 등록 명령은 `tools/register_aihub_garment.py`.
AI Hub 원본(라벨·이미지·설명서)은 저장소에 넣지 않는다.
"""

from fitting.adapters.aihub.convert import (
    SOURCE,
    AihubLabelRejected,
    garment_id,
    label_to_garment,
    read_label,
)

__all__ = ["SOURCE", "AihubLabelRejected", "garment_id", "label_to_garment", "read_label"]
