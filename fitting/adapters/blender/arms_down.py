"""A자세 착장 뒤 팔 내리기의 설정·프레임 계획·판정 (Blender 안팎 공용, 표준 라이브러리만).

착장(blender_drape.py)은 A자세로 착장이 안정되면, D3가 내보낸 팔 내리는 길(몸 정점 자세 K개)을 몸의
shape key로 넣어 `move_frames` 동안 팔을 내리고 `settle_frames` 더 돌린다. 끝 상태를 A자세와 같은
검사(metrics.evaluate)로 재서 통과하면 그 상태로 저장하고, 넘으면 A자세 상태로 저장한다.
착장 판정(`drape_ok`)과 산출물(옷 OBJ·좌표)은 늘 A자세 결과다. 팔 내린 상태는 렌더(C5)에만 쓴다.

장면에 남기는 값(렌더가 읽는다)
- `fit_arms_down`: 저장한 상태가 팔 내린 상태면 True
- `fit_a_pose_frame`: A자세 착장이 끝난 프레임(렌더는 이 프레임에서 구도를 잡는다)
"""

from __future__ import annotations

from typing import Any

# 팔 내리는 길(D3 `{job_id}_body_arms_path.npy`)을 명세서 좌표로 바꿔 착장 폴더에 쓰는 파일 이름
ARMS_PATH_SPEC_FILE = "body_arms_path_spec.npy"
SCENE_ARMS_DOWN = "fit_arms_down"
SCENE_A_POSE_FRAME = "fit_a_pose_frame"
# 끝 상태를 이 검사로 판정한다(metrics.evaluate 키). 하나라도 False면 A자세로 돌아간다
CHECKS = ("stable_ok", "penetration_ok", "stretch_ok", "seam_closed_ok")


def settings(cfg: dict[str, Any]) -> dict[str, int] | None:
    """설정의 `arms_down` 블록. 없거나 꺼져 있으면 None.

    Raises:
        ValueError: 켜져 있는데 프레임 수가 1 이상의 정수가 아니다.
    """
    block = cfg.get("arms_down")
    if not isinstance(block, dict) or not block.get("enabled"):
        return None
    values = {}
    for key in ("move_frames", "settle_frames"):
        value = block.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"arms_down.{key}는 1 이상의 정수여야 합니다: {value!r}")
        values[key] = value
    return values


def plan_end_frame(
    start: int, move: int, settle: int, max_frames: int
) -> tuple[int | None, str | None]:
    """(끝 프레임, 못 하는 이유). 천 캐시가 `max_frames`까지라 그 안에 끝나야 한다."""
    end = start + move + settle
    if end > max_frames:
        return None, f"not_enough_frames: {start} + {move} + {settle} > {max_frames}"
    return end, None


def decide(result: dict[str, Any]) -> tuple[bool, str | None]:
    """팔 내린 끝 상태를 쓸지. (쓴다, 안 쓰는 이유)."""
    if result.get("nan"):
        return False, "failed: nan"
    failed = [k for k in CHECKS if result.get(k) is False]
    if failed:
        return False, "failed: " + ", ".join(failed)
    return True, None
