"""A자세 착장 뒤 팔 내리기의 설정·프레임 계획·판정(Blender 없이)."""

import json
from pathlib import Path

import pytest

from fitting.adapters.blender import arms_down
from fitting.adapters.blender.drape_config import load_config

REPO_ROOT = Path(__file__).resolve().parents[2]
DRAPE_V1 = REPO_ROOT / "config" / "drape" / "drape_v1.json"


def test_drape_v1_lowers_the_arms_over_96_frames_and_settles_24():
    cfg, record = load_config(DRAPE_V1)
    assert record["version"] == "1.1.0"
    assert arms_down.settings(cfg) == {"move_frames": 96, "settle_frames": 24}


def test_missing_or_disabled_block_turns_the_arms_down_step_off():
    assert arms_down.settings({}) is None
    assert arms_down.settings({"arms_down": {"enabled": False, "move_frames": 96}}) is None
    candidate = json.loads((REPO_ROOT / "config" / "drape" / "drape_v2_candidate.json").read_text(
        encoding="utf-8"))
    assert arms_down.settings(candidate) is None  # 후보 설정은 A자세만


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, None, "96"])
def test_frame_counts_must_be_positive_integers(bad):
    block = {"enabled": True, "move_frames": bad, "settle_frames": 24}
    with pytest.raises(ValueError, match="move_frames"):
        arms_down.settings({"arms_down": block})


def test_the_end_frame_must_fit_in_the_cloth_cache():
    assert arms_down.plan_end_frame(110, 96, 24, 300) == (230, None)
    assert arms_down.plan_end_frame(180, 96, 24, 300) == (300, None)  # 끝 프레임까지 포함
    end, reason = arms_down.plan_end_frame(181, 96, 24, 300)
    assert end is None and reason.startswith("not_enough_frames")


def test_the_arms_down_state_is_used_only_when_every_check_passes():
    ok = {"stable_ok": True, "penetration_ok": True, "stretch_ok": True, "seam_closed_ok": True,
          "nan": False}
    assert arms_down.decide(ok) == (True, None)
    assert arms_down.decide({**ok, "stretch_ok": False}) == (False, "failed: stretch_ok")
    used, reason = arms_down.decide({**ok, "stable_ok": False, "penetration_ok": False})
    assert used is False and reason == "failed: stable_ok, penetration_ok"
    assert arms_down.decide({**ok, "nan": True}) == (False, "failed: nan")
    no_weld = {k: v for k, v in ok.items() if k != "seam_closed_ok"}  # 용접 없는 설정
    assert arms_down.decide(no_weld) == (True, None)
