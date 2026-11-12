"""테스트용 스텁 어댑터 (#12 #13). 실제 도구(Blender·SMPL·fal) 없이 "실제 어댑터 경로"를 시험한다.

설정 파일에 `tests.stub_adapters:make_drape`처럼 적으면 `server/wiring.py`가 실제 어댑터와 똑같이
이 모듈을 불러 팩토리를 부른다. 스텁은 `settings.artifacts_dir`에 `{artifact_id}.{확장자}`로 작은
**실제 파일**을 쓰고 `PortResult`를 돌려준다(가짜 어댑터는 파일을 만들지 않는다).

호출 기록은 `CALLS`에 `(포트, 메서드)`로 남는다. 테스트가 포트가 불렸는지 확인한다.
실패·설정 오류·엉뚱한 반환을 내는 팩토리도 있다.
"""

from pathlib import Path

from fitting.domain import SCHEMA_VERSION, ErrorCode, Stage
from fitting.ports import (
    BODY_OUTPUTS,
    DRAPE_OUTPUTS,
    REALIZE_OUTPUTS,
    RENDER_OUTPUTS,
    VALIDATE_OUTPUTS,
    PortError,
    PortFailure,
    PortRequest,
    PortResult,
)
from server.placeholders import solid_png
from server.wiring import AdapterConfigError, AdapterSettings

CALLS: list[tuple[str, str]] = []  # (포트 이름, 메서드 이름)
SEEN_SETTINGS: list[AdapterSettings] = []  # 팩토리가 받은 설정 (가장 최근 것이 뒤)

# 산출물 역할 → 파일 확장자
_EXTENSIONS = {
    "body_mesh": "obj",
    "drape_result": "json",
    "drape_preview": "png",
    "render_clip": "mp4",
    "video": "mp4",
    "frame": "png",
    "render_frame": "png",
}
_PNG = solid_png(2, 2, (200, 100, 50))


def reset() -> None:
    CALLS.clear()
    SEEN_SETTINGS.clear()


def _content(role: str) -> bytes:
    return _PNG if _EXTENSIONS[role] == "png" else f"stub {role}".encode()


class _Stub:
    port: str
    method: str
    stage: Stage
    outputs: tuple[str, ...]
    metrics: dict

    def __init__(self, settings: AdapterSettings, *, fail: ErrorCode | None = None) -> None:
        self.settings = settings
        self.fail = fail

    def _run(self, request: PortRequest) -> PortResult:
        CALLS.append((self.port, self.method))
        if self.fail is not None:
            raise PortFailure(
                PortError(
                    code=self.fail,
                    stage=self.stage,
                    retryable=False,
                    safe_message="스텁이 실패를 흉내 냈습니다.",
                )
            )
        directory = Path(self.settings.artifacts_dir)
        directory.mkdir(parents=True, exist_ok=True)
        ids = []
        for role in self.outputs:
            artifact_id = f"{request.job_id}_{role}"
            (directory / f"{artifact_id}.{_EXTENSIONS[role]}").write_bytes(_content(role))
            ids.append(artifact_id)
        return PortResult(
            schema_version=SCHEMA_VERSION,
            output_artifact_ids=ids,
            metrics=dict(self.metrics),
            tool_versions={"stub": "0"},
        )


class StubBody(_Stub):
    port, method, stage, outputs = "body", "build_body", Stage.BODY_FIT, BODY_OUTPUTS
    metrics = {"max_abs_error_cm": 0.1}

    def build_body(self, request, body):
        return self._run(request)


class StubDraper(_Stub):
    port, method, stage, outputs = "drape", "drape", Stage.DRAPE, DRAPE_OUTPUTS
    metrics = {"penetration_ratio": 0.0}

    def drape(self, request, garment):
        return self._run(request)


class StubRenderer(_Stub):
    port, method, stage, outputs = "render", "render", Stage.RENDER, RENDER_OUTPUTS
    metrics = {"frames": 4}

    def render(self, request, garment):
        return self._run(request)


class StubRealizer(_Stub):
    port, method, stage, outputs = "realize", "realize", Stage.REALIZE, REALIZE_OUTPUTS
    metrics = {"cost_usd": 0.0}

    def realize(self, request, garment):
        return self._run(request)


class StubValidator(_Stub):
    port, method, stage, outputs = "validate", "validate", Stage.VALIDATE, VALIDATE_OUTPUTS
    metrics = {"quality_status": "PASS", "color_delta_e": 1.0, "silhouette_iou": 0.9}

    def validate(self, request, garment):
        return self._run(request)


class ExplodingRenderer(StubRenderer):
    """`PortFailure`가 아닌 예상 밖 예외를 던진다."""

    def render(self, request, garment):
        CALLS.append((self.port, self.method))
        raise RuntimeError("스텁 렌더가 터졌다")


def _remember(settings: AdapterSettings) -> AdapterSettings:
    SEEN_SETTINGS.append(settings)
    return settings


# --- 3-2절 모양의 팩토리 ---


def make_body(settings: AdapterSettings) -> StubBody:
    return StubBody(_remember(settings))


def make_drape(settings: AdapterSettings) -> StubDraper:
    return StubDraper(_remember(settings))


def make_render(settings: AdapterSettings) -> StubRenderer:
    return StubRenderer(_remember(settings))


def make_realize(settings: AdapterSettings) -> StubRealizer:
    return StubRealizer(_remember(settings))


def make_validate(settings: AdapterSettings) -> StubValidator:
    return StubValidator(_remember(settings))


# --- 틀린 팩토리 ---


def make_failing_drape(settings: AdapterSettings) -> StubDraper:
    """착장 단계가 `PortFailure(DRAPE_INVALID)`로 실패한다."""
    return StubDraper(_remember(settings), fail=ErrorCode.DRAPE_INVALID)


def make_failing_body(settings: AdapterSettings) -> StubBody:
    return StubBody(_remember(settings), fail=ErrorCode.BODY_FIT_NOT_CONVERGED)


def make_exploding_render(settings: AdapterSettings) -> ExplodingRenderer:
    return ExplodingRenderer(_remember(settings))


def make_config_error(settings: AdapterSettings):
    """필요한 것이 없다고 알리는 팩토리 (예: Blender 경로 없음)."""
    raise AdapterConfigError("Blender 경로가 없습니다. BLENDER_PATH를 정해 주세요.")


def make_raises(settings: AdapterSettings):
    raise RuntimeError("팩토리가 터졌다")


def make_wrong_object(settings: AdapterSettings):
    """필요한 메서드가 없는 객체를 돌려준다."""
    return object()


NOT_A_FUNCTION = 5  # 팩토리 자리에 함수가 아닌 값이 있다
