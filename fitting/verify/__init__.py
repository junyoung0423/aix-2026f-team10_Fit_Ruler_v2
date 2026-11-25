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

__all__ = [
    "CHECK_VERSION",
    "DRAPE_INVALID",
    "FAIL",
    "MATCH_MODES",
    "PASS",
    "UNVERIFIABLE",
    "ClipTiming",
    "DrapeCheckResult",
    "DrapeCheckThresholds",
    "FrameMatch",
    "MatchMode",
    "PenetrationSummary",
    "coverage",
    "judge_drape",
    "match_render_frame",
    "summarize_penetration",
]
