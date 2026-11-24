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
from .frame_match import (
    MATCH_MODES,
    ClipTiming,
    FrameMatch,
    MatchMode,
    coverage,
    match_render_frame,
)
from .visual_check import CHECK_VERSION as VISUAL_CHECK_VERSION
from .visual_check import (
    FrameMeasure,
    FrameResult,
    VisualCheckResult,
    VisualCriteria,
    delta_e_2000,
    judge_frame,
    judge_video,
    mask_iou,
    srgb_to_lab,
)

__all__ = [
    "CHECK_VERSION",
    "DRAPE_INVALID",
    "FAIL",
    "MATCH_MODES",
    "PASS",
    "UNVERIFIABLE",
    "VISUAL_CHECK_VERSION",
    "ClipTiming",
    "DrapeCheckResult",
    "DrapeCheckThresholds",
    "FrameMatch",
    "FrameMeasure",
    "FrameResult",
    "MatchMode",
    "PenetrationSummary",
    "VisualCheckResult",
    "VisualCriteria",
    "coverage",
    "delta_e_2000",
    "judge_drape",
    "judge_frame",
    "judge_video",
    "mask_iou",
    "match_render_frame",
    "srgb_to_lab",
    "summarize_penetration",
]
