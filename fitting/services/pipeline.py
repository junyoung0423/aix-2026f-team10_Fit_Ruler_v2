"""영상 경로 실패 처리 설정 `config/pipeline_v0.yaml`을 읽어 `RunSettings`로 만든다 (B5, #12).

단계별 시간 제한, 감시 시간 여유, 같은 요청 ID 재조회 횟수·간격을 정한다. 값의 뜻은
`fitting.services.fitting.RunSettings`에 있다. 설정 파일은 환경변수 `FIT_PIPELINE_CONFIG`로 다른
파일을 줄 수 있다. 파일이 틀리면 `PipelineConfigError`로 알리고, 호출하는 쪽(서버·실행 명령)은
어댑터 설정처럼 시작을 멈춘다(기본값으로 조용히 대신하지 않는다).
"""

import math
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from fitting.domain import Stage
from fitting.services.fitting import VISUAL_STAGES, RunSettings

DEFAULT_PIPELINE_PATH = Path(__file__).resolve().parents[2] / "config" / "pipeline_v0.yaml"
PIPELINE_CONFIG_ENV = "FIT_PIPELINE_CONFIG"
SUPPORTED_VERSION = "pipeline_v0"

_TOP_KEYS = {"version", "stage_timeouts_s", "watchdog_grace_s", "requery"}
_REQUERY_KEYS = {"max_attempts", "delay_s"}


class PipelineConfigError(Exception):
    """실패 처리 설정 파일이 틀렸다. 메시지는 사람이 읽는 한국어다."""


def resolve_pipeline_path(
    path: Path | str | None = None, *, environ: Mapping[str, str] | None = None
) -> Path:
    """인자 > 환경변수 `FIT_PIPELINE_CONFIG` > `config/pipeline_v0.yaml` 순서로 파일을 고른다."""
    if path is not None:
        return Path(path)
    env = (environ if environ is not None else os.environ).get(PIPELINE_CONFIG_ENV, "").strip()
    return Path(env) if env else DEFAULT_PIPELINE_PATH


def load_run_settings(
    path: Path | str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    config_version: str = "cfg_v1",
) -> RunSettings:
    """설정 파일을 읽고 형식을 검증해 `RunSettings`를 만든다.

    Raises:
        PipelineConfigError: 파일이 없거나, YAML이 아니거나, 형식이 맞지 않는다. 형식 문제는
            모아서 한 번에 알린다.
    """
    source = resolve_pipeline_path(path, environ=environ)
    try:
        data = yaml.safe_load(source.read_text(encoding="utf-8"))
    except OSError as error:
        raise PipelineConfigError(
            f"실패 처리 설정 파일을 읽을 수 없습니다: {source} ({error.strerror or error}) "
            f"(다른 파일은 {PIPELINE_CONFIG_ENV}로 고릅니다)"
        ) from error
    except yaml.YAMLError as error:
        raise PipelineConfigError(
            f"실패 처리 설정 파일이 YAML 형식이 아닙니다: {source} ({error})"
        ) from error

    problems: list[str] = []
    settings = _parse(data, problems, config_version)
    if problems or settings is None:
        raise PipelineConfigError(
            f"실패 처리 설정 파일이 맞지 않습니다({source}):\n"
            + "\n".join(f"  - {item}" for item in problems)
        )
    return settings


def _parse(data: Any, problems: list[str], config_version: str) -> RunSettings | None:
    if not isinstance(data, Mapping):
        problems.append(
            "키-값 묶음이어야 합니다 (version, stage_timeouts_s, watchdog_grace_s, requery)"
        )
        return None
    unknown = sorted(str(key) for key in set(data) - _TOP_KEYS)
    if unknown:
        problems.append(f"모르는 키가 있습니다: {', '.join(unknown)}")
    missing = sorted(_TOP_KEYS - set(data))
    if missing:
        problems.append(f"필요한 키가 없습니다: {', '.join(missing)}")
    if data.get("version") != SUPPORTED_VERSION:
        problems.append(f"version은 {SUPPORTED_VERSION}이어야 합니다: {data.get('version')!r}")

    timeouts = _stage_timeouts(data.get("stage_timeouts_s"), problems)
    grace = _positive_number(data.get("watchdog_grace_s"), "watchdog_grace_s", problems)
    attempts, delay = _requery(data.get("requery"), problems)
    if problems:
        return None
    return RunSettings(
        config_version=config_version,
        stage_timeouts_s=timeouts,
        watchdog_grace_s=grace,
        requery_max_attempts=attempts,
        requery_delay_s=delay,
    )


def _stage_timeouts(raw: Any, problems: list[str]) -> dict[Stage, float]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        problems.append("stage_timeouts_s는 단계 이름 → 초의 묶음이어야 합니다")
        return {}
    names = [stage.value for stage in VISUAL_STAGES]
    missing = [name for name in names if name not in raw]
    if missing:
        problems.append(f"stage_timeouts_s에 필요한 단계가 없습니다: {', '.join(missing)}")
    unknown = sorted(str(key) for key in set(raw) - set(names))
    if unknown:
        problems.append(
            f"stage_timeouts_s에 모르는 단계가 있습니다: {', '.join(unknown)} "
            f"(가능: {', '.join(names)})"
        )
    result: dict[Stage, float] = {}
    for stage in VISUAL_STAGES:
        if stage.value in raw:
            value = _positive_number(raw[stage.value], f"stage_timeouts_s.{stage.value}", problems)
            result[stage] = value
    return result


def _requery(raw: Any, problems: list[str]) -> tuple[int, float]:
    if raw is None:
        return 0, 0.0
    if not isinstance(raw, Mapping):
        problems.append("requery는 키-값 묶음이어야 합니다 (max_attempts, delay_s)")
        return 0, 0.0
    unknown = sorted(str(key) for key in set(raw) - _REQUERY_KEYS)
    if unknown:
        problems.append(f"requery에 모르는 키가 있습니다: {', '.join(unknown)}")
    missing = sorted(_REQUERY_KEYS - set(raw))
    if missing:
        problems.append(f"requery에 필요한 키가 없습니다: {', '.join(missing)}")
    attempts = raw.get("max_attempts")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        if "max_attempts" in raw:
            problems.append(f"requery.max_attempts는 0 이상의 정수여야 합니다: {attempts!r}")
        attempts = 0
    delay = raw.get("delay_s")
    if "delay_s" in raw:
        delay = _non_negative_number(delay, "requery.delay_s", problems)
    else:
        delay = 0.0
    return attempts, delay


def _positive_number(value: Any, where: str, problems: list[str]) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        problems.append(f"{where}는 0보다 큰 숫자여야 합니다: {value!r}")
        return 1.0
    if value <= 0:
        problems.append(f"{where}는 0보다 큰 숫자여야 합니다: {value!r}")
        return 1.0
    return float(value)


def _non_negative_number(value: Any, where: str, problems: list[str]) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        problems.append(f"{where}는 0 이상의 숫자여야 합니다: {value!r}")
        return 0.0
    if value < 0:
        problems.append(f"{where}는 0 이상의 숫자여야 합니다: {value!r}")
        return 0.0
    return float(value)
