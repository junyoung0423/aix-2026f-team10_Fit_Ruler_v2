"""실패 처리 설정 `config/pipeline_v0.yaml` (B5, #12): 읽기, 검증, 서버·실행 명령 연결."""

import copy

import pytest
import yaml

import tools.run_fitting_once as tool
from fitting.domain import Stage
from fitting.services.fitting import (
    DEFAULT_STAGE_TIMEOUTS_S,
    DEFAULT_WATCHDOG_GRACE_S,
    VISUAL_STAGES,
    RunSettings,
)
from fitting.services.pipeline import (
    DEFAULT_PIPELINE_PATH,
    PIPELINE_CONFIG_ENV,
    PipelineConfigError,
    load_run_settings,
    resolve_pipeline_path,
)
from server.app import create_app

GOOD = {
    "version": "pipeline_v0",
    "stage_timeouts_s": {
        "BODY_FIT": 180,
        "DRAPE": 600,
        "RENDER": 300,
        "REALIZE": 600,
        "VALIDATE": 120,
    },
    "watchdog_grace_s": {
        "BODY_FIT": 60,
        "DRAPE": 660,
        "RENDER": 60,
        "REALIZE": 120,
        "VALIDATE": 60,
    },
    "requery": {"max_attempts": 2, "delay_s": 5},
}


def write(tmp_path, data, name="pipeline.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def changed(**edits):
    data = copy.deepcopy(GOOD)
    for dotted, value in edits.items():
        keys = dotted.split("__")
        target = data
        for key in keys[:-1]:
            target = target[key]
        if value is KeyError:
            del target[keys[-1]]
        else:
            target[keys[-1]] = value
    return data


def test_the_committed_file_loads_and_matches_the_defaults_in_code():
    settings = load_run_settings(DEFAULT_PIPELINE_PATH)

    assert {s: settings.stage_timeouts_s[s] for s in VISUAL_STAGES} == dict(
        DEFAULT_STAGE_TIMEOUTS_S
    )
    assert settings.watchdog_grace_s == dict(DEFAULT_WATCHDOG_GRACE_S)  # 숫자 60 → 단계별
    assert (settings.requery_max_attempts, settings.requery_delay_s) == (2, 5)


def test_values_are_read_per_stage(tmp_path):
    path = write(tmp_path, changed(stage_timeouts_s__RENDER=12.5, requery__max_attempts=0))

    settings = load_run_settings(path)

    assert settings.timeout_for(Stage.RENDER) == 12.5
    assert settings.requery_max_attempts == 0


BAD = {
    "버전이 다름": (changed(version="pipeline_v9"), "version"),
    "단계 누락": (changed(stage_timeouts_s__RENDER=KeyError), "RENDER"),
    "모르는 단계": (changed(stage_timeouts_s__FIT_CALCULATE=10), "FIT_CALCULATE"),
    "모르는 최상위 키": (dict(GOOD, extra=1), "extra"),
    "최상위 키 누락": (changed(watchdog_grace_s=KeyError), "watchdog_grace_s"),
    "시간 0": (changed(stage_timeouts_s__DRAPE=0), "stage_timeouts_s.DRAPE"),
    "시간 음수": (changed(stage_timeouts_s__DRAPE=-5), "stage_timeouts_s.DRAPE"),
    "시간이 문자열": (changed(stage_timeouts_s__DRAPE="600"), "stage_timeouts_s.DRAPE"),
    "시간이 불리언": (changed(stage_timeouts_s__DRAPE=True), "stage_timeouts_s.DRAPE"),
    "감시 여유 0": (changed(watchdog_grace_s=0), "watchdog_grace_s"),
    "재조회 횟수 음수": (changed(requery__max_attempts=-1), "max_attempts"),
    "재조회 횟수가 소수": (changed(requery__max_attempts=1.5), "max_attempts"),
    "재조회 간격 음수": (changed(requery__delay_s=-1), "delay_s"),
    "재조회 키 누락": (changed(requery__delay_s=KeyError), "delay_s"),
    "재조회 모르는 키": (changed(requery__retry=3), "retry"),
    "묶음이 아님": (["a"], "키-값 묶음"),
}


@pytest.mark.parametrize("case", BAD)
def test_a_wrong_file_is_rejected_in_korean(tmp_path, case):
    data, expected = BAD[case]

    with pytest.raises(PipelineConfigError) as caught:
        load_run_settings(write(tmp_path, data))

    assert expected in str(caught.value)
    assert "맞지 않습니다" in str(caught.value)


def test_problems_are_reported_together(tmp_path):
    data = changed(stage_timeouts_s__DRAPE=0, watchdog_grace_s=-1)

    with pytest.raises(PipelineConfigError) as caught:
        load_run_settings(write(tmp_path, data))

    assert "stage_timeouts_s.DRAPE" in str(caught.value)
    assert "watchdog_grace_s" in str(caught.value)


def test_a_missing_or_broken_file_is_rejected(tmp_path):
    with pytest.raises(PipelineConfigError, match="읽을 수 없습니다"):
        load_run_settings(tmp_path / "없는파일.yaml")
    broken = tmp_path / "broken.yaml"
    broken.write_text("a: [1, 2", encoding="utf-8")
    with pytest.raises(PipelineConfigError, match="YAML"):
        load_run_settings(broken)


def test_the_environment_variable_picks_the_file(tmp_path):
    path = write(tmp_path, changed(watchdog_grace_s=9))

    assert resolve_pipeline_path(environ={}) == DEFAULT_PIPELINE_PATH
    assert resolve_pipeline_path(environ={PIPELINE_CONFIG_ENV: str(path)}) == path
    assert load_run_settings(environ={PIPELINE_CONFIG_ENV: str(path)}).watchdog_grace_s == 9
    assert (
        resolve_pipeline_path("x.yaml", environ={PIPELINE_CONFIG_ENV: str(path)}).name == "x.yaml"
    )


def test_run_settings_without_a_file_keep_the_old_single_timeout_behavior():
    assert RunSettings(timeout_seconds=7).timeout_for(Stage.DRAPE) == 7
    assert RunSettings().timeout_for(Stage.DRAPE) == 600


def test_the_server_does_not_start_with_a_wrong_pipeline_file(tmp_path, monkeypatch):
    monkeypatch.setenv(PIPELINE_CONFIG_ENV, str(tmp_path / "없는파일.yaml"))

    with pytest.raises(PipelineConfigError, match="실패 처리 설정"):
        create_app(garments_dir=tmp_path, artifacts_dir=tmp_path)


def test_the_server_uses_the_pipeline_file(tmp_path, monkeypatch):
    path = write(tmp_path, changed(watchdog_grace_s=9))
    monkeypatch.setenv(PIPELINE_CONFIG_ENV, str(path))

    app = create_app(garments_dir=tmp_path, artifacts_dir=tmp_path)

    assert app.state.runner._settings.watchdog_grace_s == 9


def test_run_fitting_once_stops_with_exit_code_1_on_a_wrong_pipeline_file(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv(PIPELINE_CONFIG_ENV, str(tmp_path / "없는파일.yaml"))

    code = tool.main(["--adapters", "fake", "--out", str(tmp_path / "run")])

    captured = capsys.readouterr()
    assert code == 1
    assert "실패 처리 설정 파일을 읽을 수 없습니다" in captured.err
    assert "피팅 시작" not in captured.out


# --- 단계별 감시 여유 ---------------------------------------------------------------------------


def test_the_committed_file_has_the_per_stage_grace_from_the_work_order():
    settings = load_run_settings(DEFAULT_PIPELINE_PATH)

    assert {s.value: settings.grace_for(s) for s in VISUAL_STAGES} == {
        "BODY_FIT": 60,
        "DRAPE": 660,
        "RENDER": 60,
        "REALIZE": 120,
        "VALIDATE": 60,
    }


def test_run_settings_defaults_and_the_loaded_file_give_the_same_grace():
    loaded = load_run_settings()

    assert {s: RunSettings().grace_for(s) for s in VISUAL_STAGES} == {
        s: loaded.grace_for(s) for s in VISUAL_STAGES
    }


def test_a_single_number_applies_to_every_stage(tmp_path):
    settings = load_run_settings(write(tmp_path, changed(watchdog_grace_s=45)))

    assert {settings.grace_for(s) for s in VISUAL_STAGES} == {45.0}


def test_a_mapping_may_use_zero_and_missing_stages_fall_back_to_the_default():
    settings = RunSettings(watchdog_grace_s={Stage.RENDER: 0.0})

    assert settings.grace_for(Stage.RENDER) == 0
    assert settings.grace_for(Stage.DRAPE) == DEFAULT_WATCHDOG_GRACE_S[Stage.DRAPE]


BAD_GRACE = {
    "단계 누락": (changed(watchdog_grace_s__RENDER=KeyError), "RENDER"),
    "모르는 단계": (changed(watchdog_grace_s__FIT_REPORT=5), "FIT_REPORT"),
    "음수": (changed(watchdog_grace_s__DRAPE=-1), "watchdog_grace_s.DRAPE"),
    "불리언": (changed(watchdog_grace_s__DRAPE=True), "watchdog_grace_s.DRAPE"),
    "문자열": (changed(watchdog_grace_s__REALIZE="120"), "watchdog_grace_s.REALIZE"),
}


@pytest.mark.parametrize("case", BAD_GRACE)
def test_a_wrong_per_stage_grace_is_rejected_with_the_stage_name(tmp_path, case):
    data, expected = BAD_GRACE[case]

    with pytest.raises(PipelineConfigError) as caught:
        load_run_settings(write(tmp_path, data))

    assert expected in str(caught.value)


def test_a_zero_in_the_mapping_is_accepted(tmp_path):
    settings = load_run_settings(write(tmp_path, changed(watchdog_grace_s__RENDER=0)))

    assert settings.grace_for(Stage.RENDER) == 0
