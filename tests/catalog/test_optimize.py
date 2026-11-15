"""견본 최적화 판정·기록 (#6). 순수 계산이라 GarmentCode 없이 돈다."""

import pytest

from fitting.catalog import optimize as opt

TOL = 1.0
TARGETS = {"chest_girth_cm": 108.0, "sleeve_length_cm": 22.0, "body_length_cm": 70.0}


def _measured(chest=108.0, sleeve=22.0, length=70.0, shoulder=34.457):
    return {
        "chest_girth_cm": chest,
        "sleeve_length_cm": sleeve,
        "body_length_cm": length,
        "shoulder_width_cm": shoulder,
    }


def test_exact_fit_is_ready():
    assert opt.judge(TARGETS, _measured(), TOL) == (opt.READY, None, None)


@pytest.mark.parametrize("field", ["chest_girth_cm", "sleeve_length_cm", "body_length_cm"])
@pytest.mark.parametrize("delta", [1.0, -1.0, 0.999])
def test_residual_up_to_tolerance_is_ready(field, delta):
    measured = _measured()
    measured[field] = TARGETS[field] + delta
    assert opt.judge(TARGETS, measured, TOL)[0] == opt.READY


@pytest.mark.parametrize("field", ["chest_girth_cm", "sleeve_length_cm", "body_length_cm"])
@pytest.mark.parametrize("delta", [1.001, -1.001, 5.0])
def test_residual_over_tolerance_fails(field, delta):
    measured = _measured()
    measured[field] = TARGETS[field] + delta
    status, reason, detail = opt.judge(TARGETS, measured, TOL)
    assert status == opt.FAILED
    assert reason == opt.FailureReason.OUT_OF_TOLERANCE
    assert field in detail


def test_shoulder_is_never_judged():
    measured = _measured(shoulder=10.0)  # 어깨가 아무리 달라도
    assert opt.judge(TARGETS, measured, TOL)[0] == opt.READY


def test_missing_length_target_is_not_judged_and_noted():
    targets = {**TARGETS, "body_length_cm": None}
    measured = _measured(length=50.0)
    assert opt.judge(targets, measured, TOL)[0] == opt.READY
    assert opt.residuals(targets, measured)["body_length_cm"] is None
    assert any("기장 목표 없음" in n for n in opt.notes_for(targets))


def test_self_intersecting_pattern_fails():
    status, reason, _ = opt.judge(TARGETS, _measured(), TOL, self_intersecting=True)
    assert (status, reason) == (opt.FAILED, opt.FailureReason.SELF_INTERSECTING)


def test_nan_measurement_fails():
    assert opt.judge(TARGETS, _measured(chest=float("nan")), TOL)[0] == opt.FAILED


@pytest.mark.parametrize("tol", [0.0, -1.0, float("nan")])
def test_tolerance_must_be_positive(tol):
    with pytest.raises(ValueError):
        opt.judge(TARGETS, _measured(), tol)


def test_residuals_are_measured_minus_target_rounded():
    r = opt.residuals(TARGETS, _measured(chest=108.0004, sleeve=21.5, length=70.2))
    assert r == {"chest_girth_cm": 0.0, "sleeve_length_cm": -0.5, "body_length_cm": 0.2}


def test_shoulder_record_is_garment_minus_sample():
    assert opt.shoulder_record(46.0, 34.457) == {
        "judged": False,
        "garment_cm": 46.0,
        "sample_cm": 34.457,
        "diff_cm": 11.543,
    }
    assert opt.shoulder_record(None, 34.457)["diff_cm"] is None


def test_fit_targets_keep_null_as_none():
    t = opt.fit_targets({"chest_girth_cm": 98, "sleeve_length_cm": None, "body_length_cm": None})
    assert t == {"chest_girth_cm": 98.0, "sleeve_length_cm": None, "body_length_cm": None}


def test_inputs_key_changes_with_any_input():
    a = {"garment_sha256": "a", "sample_id": "s", "configs": {"x": "1"}}
    assert opt.inputs_key(a) == opt.inputs_key(dict(a))
    assert opt.inputs_key(a) != opt.inputs_key({**a, "garment_sha256": "b"})
    assert opt.is_up_to_date({"inputs_key": opt.inputs_key(a)}, opt.inputs_key(a))
    assert not opt.is_up_to_date(None, opt.inputs_key(a))


def test_prepared_dir_env_then_default(tmp_path):
    assert opt.prepared_dir({"FIT_PREPARED_DIR": str(tmp_path)}) == tmp_path
    assert opt.prepared_dir({"FIT_PREPARED_DIR": "  "}) == opt.DEFAULT_PREPARED_DIR
    assert opt.prepared_dir({}) == opt.DEFAULT_PREPARED_DIR
