"""검증 로직: 착장 점검(D4), 실사 영상 검증(D5·D6). 외부 SDK·어댑터를 import하지 않는다."""

from .drape_check import (
    CHECK_VERSION,
    DRAPE_INVALID,
    FAIL,
    PASS,
    UNVERIFIABLE,
    DrapeCheckResult,
    DrapeCheckThresholds,
    PenetrationSummary,
    judge_drape,
    summarize_penetration,
)
from .silhouette import SHAPE_VERSION, EmptyMaskError, compare_masks, mask_shape

__all__ = [
    "CHECK_VERSION",
    "DRAPE_INVALID",
    "FAIL",
    "PASS",
    "UNVERIFIABLE",
    "DrapeCheckResult",
    "DrapeCheckThresholds",
    "PenetrationSummary",
    "SHAPE_VERSION",
    "EmptyMaskError",
    "compare_masks",
    "judge_drape",
    "mask_shape",
    "summarize_penetration",
]
