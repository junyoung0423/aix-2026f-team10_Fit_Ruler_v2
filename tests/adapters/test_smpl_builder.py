"""SMPL 몸 생성 어댑터(D3)와 팩토리 테스트. SMPL 모델 없이 작은 가짜 몸 모델로 시험한다.

실제 SMPL 모델로 기준 신체를 만드는 확인은 `test_smpl_real.py`(`-m smpl`)에 있다.
"""

import json
import re
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")  # 선택 의존성 body가 없으면 건너뛴다
pytest.importorskip("scipy")

from fitting.adapters.smpl import BODY_FIT_RECORD, SmplBodyBuilder  # noqa: E402
from fitting.adapters.smpl.builder import ARMS_DOWN_DEG, arms_path_angles  # noqa: E402
from fitting.adapters.smpl.factory import make_body_builder  # noqa: E402
from fitting.body.fit import FitSettings  # noqa: E402
from fitting.body.measure import BodyMeasurementsCm, MeasureDebug  # noqa: E402
from fitting.domain import BodyInput, ErrorCode, Stage  # noqa: E402
from fitting.fit import RuleFitCalculator  # noqa: E402
from fitting.ports import BodyBuilder, PortFailure, PortRequest, PortResult  # noqa: E402
from server.wiring import (  # noqa: E402
    AdapterConfigError,
    AdapterSettings,
    build_ports,
    load_adapter_config,
)

BASE = np.array([170.0, 95.0, 85.0, 38.0, 54.0])
J = np.array(
    [
        [4.0, 1.0, 0.0, 0.0],
        [1.0, 5.0, 1.0, 0.0],
        [0.0, 2.0, 5.0, 0.0],
        [0.5, 0.5, 0.0, 1.0],
        [1.0, 0.0, 0.0, 0.5],
    ]
)
# 사면체 하나. 메시 저장을 확인하는 데만 쓴다.
TETRA = np.array([[0.0, 0.0, 0.0], [0.1, 0.0, 0.0], [0.0, 0.1, 0.0], [0.0, 0.0, 0.1]])
TETRA_FACES = np.array([[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]])


class FakePosedBody:
    """치수 = 크기 × (평균 + J·β)인 가짜 몸 모델 (`PosedBodyModel`)."""

    def __init__(
        self, jacobian=J, num_betas: int = 4, nan_mesh: bool = False, floor: float = 0.0
    ) -> None:
        self.jacobian = jacobian
        self._num_betas = num_betas
        self.nan_mesh = nan_mesh
        self.floor = floor  # 메시 발바닥 높이(SMPL처럼 원점이 골반 근처면 음수)

    @property
    def num_betas(self) -> int:
        return self._num_betas

    @property
    def faces(self):
        return TETRA_FACES

    def mesh(self, betas, scale):
        betas = np.asarray(betas)
        if betas.shape[0] > self._num_betas:
            raise ValueError("형상 계수가 너무 많다")
        vertices = TETRA * scale + np.array([0.0, self.floor, 0.0])
        if self.nan_mesh:
            vertices = vertices.copy()
            vertices[0, 0] = np.nan
        return vertices, np.zeros((24, 3))

    def measure(self, betas, scale, *, round_cm=True):
        self.mesh(betas, scale)
        values = scale * (BASE + self.jacobian[:, : len(betas)] @ np.asarray(betas))
        values = np.round(values, 1) if round_cm else values
        debug = MeasureDebug(
            chest_level_m=1.3,
            waist_level_m=1.05,
            left_shoulder_point=np.array([0.2, 1.45, 0.0]),
            right_shoulder_point=np.array([-0.2, 1.45, 0.0]),
            chest_loop=np.zeros((0, 2)),
            waist_loop=np.zeros((0, 2)),
        )
        return BodyMeasurementsCm(*(float(v) for v in values)), debug

    def describe(self):
        return {"kind": "fake"}


class FakeArmsBody(FakePosedBody):
    """팔 각도를 바꿀 수 있는 가짜 몸. 팔을 내릴수록 정점이 x로 조금씩 옮겨 간다."""

    def __init__(self, nan_arms: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self.nan_arms = nan_arms

    def vertices_at(self, betas, scale, arm_deg):
        vertices, _ = self.mesh(betas, scale)
        moved = vertices + np.array([arm_deg / 1000.0, 0.0, 0.0])
        if self.nan_arms:
            moved[0, 0] = np.nan
        return moved


def body_input(**overrides) -> BodyInput:
    values = dict(height_cm=175, chest_cm=100, waist_cm=80, shoulder_cm=40, arm_length_cm=56)
    values.update(overrides)
    return BodyInput(**values, measurement_protocol_version="measure_v1")


def request(job_id: str = "job_ok", timeout: int = 600) -> PortRequest:
    return PortRequest(
        job_id=job_id,
        attempt_id=f"{job_id}_body_fit_1",
        config_version="test",
        timeout_seconds=timeout,
        trace_id=job_id,
    )


def builder(tmp_path: Path, model=None, **kwargs) -> SmplBodyBuilder:
    return SmplBodyBuilder(
        body_model=model or FakePosedBody(),
        artifacts_dir=tmp_path / "artifacts",
        work_dir=tmp_path / "work",
        model_name="fake_neutral.npz",
        **kwargs,
    )


def unreachable_arm() -> FakePosedBody:
    jacobian = J.copy()
    jacobian[4] = 0.0  # 팔이 형상 계수와 상관없다
    return FakePosedBody(jacobian=jacobian)


def read_record(tmp_path: Path, job_id: str) -> dict:
    text = (tmp_path / "work" / job_id / BODY_FIT_RECORD).read_text(encoding="utf-8")
    return json.loads(text, parse_constant=lambda name: pytest.fail(f"JSON에 {name}"))


# --- 성공 --------------------------------------------------------------------------------------


def test_success_writes_the_mesh_and_returns_contract_metrics(tmp_path):
    result = builder(tmp_path).build_body(request(), body_input())

    assert isinstance(result, PortResult)
    assert result.output_artifact_ids == ["job_ok_body_mesh"]
    mesh = (tmp_path / "artifacts" / "job_ok_body_mesh.obj").read_text(encoding="utf-8")
    assert mesh.count("\nv ") + mesh.startswith("v ") == 4
    assert "\nf 1 2 3" in mesh

    # B1 계약(BodyBuilder 설명)의 지표 키를 모두 쓴다
    contract = re.findall(r"`(fitted_\w+_cm|max_abs_error_cm)`", BodyBuilder.__doc__)
    assert len(contract) == 6
    assert set(contract) <= set(result.metrics)
    assert result.metrics["max_abs_error_cm"] <= 2.0
    assert result.metrics["fit_evaluations"] >= 2
    assert result.tool_versions["body_model"] == "smpl/fake_neutral.npz"
    assert result.tool_versions["measurement_protocol"] == "measure_v1"


def test_success_record_has_inputs_outputs_errors_and_parameters(tmp_path):
    builder(tmp_path).build_body(request(), body_input())
    record = read_record(tmp_path, "job_ok")

    assert record["passed"] is True
    assert record["failure"] is None
    assert record["artifact_id"] == "job_ok_body_mesh"
    assert record["input_cm"] == {
        "height_cm": 175.0,
        "chest_cm": 100.0,
        "waist_cm": 80.0,
        "shoulder_cm": 40.0,
        "arm_length_cm": 56.0,
    }
    assert set(record["fitted_cm"]) == set(record["input_cm"]) == set(record["error_cm"])
    assert record["tolerance_cm"]["arm_length_cm"] == 1.5
    assert len(record["betas"]) == 4
    assert record["landmarks"]["left_shoulder_point_m"] == [0.2, 1.45, 0.0]
    assert record["body_model"] == {"model_file": "fake_neutral.npz", "kind": "fake"}
    assert record["optimization"]["stopped_by"] == "converged"
    assert record["measurement_protocol_version"] == "measure_v1"


def test_mesh_is_moved_so_the_feet_are_at_zero(tmp_path):
    """착장(C4) 패널이 바닥 0 기준이라 발바닥(y 최솟값)을 0으로 옮겨 저장하고 옮긴 양을 남긴다."""
    builder(tmp_path, FakePosedBody(floor=-1.2)).build_body(request(), body_input())

    obj = (tmp_path / "artifacts" / "job_ok_body_mesh.obj").read_text(encoding="utf-8")
    ys = [float(line.split()[2]) for line in obj.splitlines() if line.startswith("v ")]
    assert min(ys) == pytest.approx(0.0, abs=1e-6)
    record = read_record(tmp_path, "job_ok")
    assert record["translation_m"] == pytest.approx([0.0, 1.2, 0.0])
    # 기준점도 OBJ 좌표(옮긴 뒤)로 적는다
    assert record["landmarks"]["chest_level_m"] == pytest.approx(1.3 + 1.2)
    assert record["landmarks"]["left_shoulder_point_m"] == pytest.approx([0.2, 1.45 + 1.2, 0.0])
    assert record["betas_at_bound"] == 0


def test_arms_path_is_written_next_to_the_mesh_in_obj_coordinates(tmp_path):
    """착장(C4)이 따라갈 팔 내리는 길: 45°에서 58°까지 6단계, OBJ와 같은 이동(발바닥 y = 0)."""
    builder(tmp_path, FakeArmsBody(floor=-0.9)).build_body(request(), body_input())

    path = np.load(tmp_path / "artifacts" / "job_ok_body_arms_path.npy")
    assert path.shape == (6, 4, 3) and path.dtype == np.float32
    angles = arms_path_angles()
    assert angles[-1] == ARMS_DOWN_DEG == 58.0 and len(angles) == 6
    assert angles[0] == pytest.approx(45.0 + 13.0 / 6)  # 측정 자세(45°) 다음부터
    assert path[:, :, 1].min() == pytest.approx(0.0, abs=1e-6)  # OBJ처럼 발바닥이 0
    assert path[-1, 0, 0] == pytest.approx(0.058, abs=1e-6)
    record = read_record(tmp_path, "job_ok")["arms_path"]
    assert record["artifact"] == "job_ok_body_arms_path.npy"
    assert record["shape"] == [6, 4, 3] and record["angles_deg"][-1] == 58.0


def test_drape_adapter_looks_for_the_same_arms_path_name():
    from fitting.adapters.blender.draper import ARMS_PATH_ROLE as DRAPER_ROLE
    from fitting.adapters.smpl.builder import ARMS_PATH_ROLE

    assert DRAPER_ROLE == ARMS_PATH_ROLE == "body_arms_path"


def test_a_body_model_without_arm_poses_writes_no_arms_path(tmp_path):
    builder(tmp_path).build_body(request(), body_input())

    assert not (tmp_path / "artifacts" / "job_ok_body_arms_path.npy").exists()
    assert read_record(tmp_path, "job_ok")["arms_path"] is None


def test_a_non_finite_arms_path_is_not_written_and_the_body_still_succeeds(tmp_path):
    result = builder(tmp_path, FakeArmsBody(nan_arms=True)).build_body(request(), body_input())

    assert result.output_artifact_ids == ["job_ok_body_mesh"]
    assert not (tmp_path / "artifacts" / "job_ok_body_arms_path.npy").exists()
    assert read_record(tmp_path, "job_ok")["arms_path"]["skipped"] == "non_finite"


def test_betas_are_capped_to_what_the_model_has(tmp_path):
    model = FakePosedBody(num_betas=3)
    result = builder(tmp_path, model, settings=FitSettings(num_betas=10)).build_body(
        request(), body_input()
    )
    assert result.output_artifact_ids == ["job_ok_body_mesh"]
    assert len(read_record(tmp_path, "job_ok")["betas"]) == 3


# --- 실패 --------------------------------------------------------------------------------------


def test_out_of_tolerance_fails_without_a_mesh(tmp_path):
    stale = tmp_path / "artifacts" / "job_arm_body_mesh.obj"  # 같은 작업의 예전 성공 결과
    stale.parent.mkdir(parents=True)
    stale.write_text("v 0 0 0", encoding="utf-8")
    stale_arms = tmp_path / "artifacts" / "job_arm_body_arms_path.npy"
    np.save(stale_arms, np.zeros((6, 4, 3), np.float32))
    with pytest.raises(PortFailure) as caught:
        builder(tmp_path, unreachable_arm()).build_body(
            request("job_arm"), body_input(arm_length_cm=70)
        )

    error = caught.value.error
    assert error.code == ErrorCode.BODY_FIT_NOT_CONVERGED
    assert error.stage == Stage.BODY_FIT
    assert error.retryable is False
    assert "팔길이" in error.safe_message
    assert "핏 리포트" in error.safe_message
    assert str(tmp_path) not in error.safe_message  # 화면에 내부 경로를 보이지 않는다
    assert error.internal_details_ref == "job_arm/body_fit.json"
    assert not (tmp_path / "artifacts" / "job_arm_body_mesh.obj").exists()
    assert not stale_arms.exists()  # 예전 팔 내리는 길도 남기지 않는다

    record = read_record(tmp_path, "job_arm")
    assert record["passed"] is False
    assert record["artifact_id"] is None
    assert "arm_length_cm" in record["failure"]["failed_fields"]
    assert len(record["optimization"]["starts"]) == 3  # 시작점 3개를 다 썼다
    assert record["input_cm"]["arm_length_cm"] == 70.0  # 입력을 고치지 않는다
    assert record["removed_stale_mesh"] is True  # 예전 메시를 지웠다
    assert isinstance(record["betas_at_bound"], int)


def test_mesh_that_is_not_finite_fails(tmp_path):
    with pytest.raises(PortFailure) as caught:
        builder(tmp_path, FakePosedBody(nan_mesh=True)).build_body(request("job_nan"), body_input())

    assert caught.value.error.code == ErrorCode.BODY_FIT_NOT_CONVERGED
    assert "메시" in caught.value.error.safe_message
    assert read_record(tmp_path, "job_nan")["failure"]["mesh_finite"] is False


def test_request_timeout_limits_the_search(tmp_path):
    ticks = iter(range(100_000))
    slow = builder(tmp_path, unreachable_arm(), clock=lambda: float(next(ticks)))

    with pytest.raises(PortFailure) as caught:
        slow.build_body(request("job_slow", timeout=30), body_input(arm_length_cm=70))

    # 시간 초과는 "몸이 안 맞는다"가 아니라 시간 한도로 안내한다
    assert "시간 한도" in caught.value.error.safe_message
    assert "넘은 치수" not in caught.value.error.safe_message

    optimization = read_record(tmp_path, "job_slow")["optimization"]
    assert optimization["stopped_by"] == "time_limit"
    assert optimization["settings"]["time_limit_s"] == 30.0


def test_job_id_that_cannot_be_an_artifact_id_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        builder(tmp_path).build_body(request("../escape"), body_input())


# --- 팩토리와 설정 --------------------------------------------------------------------------------


def settings_for(tmp_path: Path, smpl_dir: str | None) -> AdapterSettings:
    environ = {"FIT_WORK_DIR": str(tmp_path / "work")}
    if smpl_dir is not None:
        environ["SMPL_MODEL_DIR"] = smpl_dir
    return AdapterSettings.from_environ(artifacts_dir=tmp_path / "artifacts", environ=environ)


def save_fake_model(path: Path, vertices: int = 6890, betas: int = 10) -> Path:
    """모양만 SMPL과 같은 0 배열 모델(.npz). 읽기·검사만 확인한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    kintree = np.vstack([np.r_[0, np.arange(23)], np.arange(24)])
    np.savez_compressed(
        path,
        v_template=np.zeros((vertices, 3)),
        shapedirs=np.zeros((vertices, 3, betas)),
        posedirs=np.zeros((vertices, 3, 207)),
        J_regressor=np.zeros((24, vertices)),
        kintree_table=kintree,
        weights=np.zeros((vertices, 24)),
        f=np.zeros((10, 3), dtype=np.int64),
    )
    return path


def test_factory_builds_from_the_neutral_model_in_smpl_model_dir(tmp_path):
    save_fake_model(tmp_path / "smpl" / "basicmodel_neutral_test.npz")
    (tmp_path / "smpl" / "basicmodel_m_test.npz").write_bytes(b"not used")

    adapter = make_body_builder(settings_for(tmp_path, str(tmp_path / "smpl")))

    assert isinstance(adapter, SmplBodyBuilder)
    assert adapter.model_name == "basicmodel_neutral_test.npz"
    assert adapter.artifacts_dir == tmp_path / "artifacts"
    assert adapter.work_dir == tmp_path / "work"


@pytest.mark.parametrize(
    ("prepare", "message"),
    [
        (lambda d: None, "SMPL_MODEL_DIR이 비어"),
        (lambda d: str(d / "missing"), "폴더가 없습니다"),
        (lambda d: (d / "smpl").mkdir() or str(d / "smpl"), "중성 모델 파일"),
    ],
)
def test_factory_explains_missing_models(tmp_path, prepare, message):
    with pytest.raises(AdapterConfigError, match=message) as caught:
        make_body_builder(settings_for(tmp_path, prepare(tmp_path)))
    assert "models/README.md" in str(caught.value)


def test_factory_rejects_unreadable_or_mismatched_models(tmp_path):
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "neutral.pkl").write_bytes(b"not a pickle")
    with pytest.raises(AdapterConfigError, match="읽을 수 없습니다: neutral.pkl"):
        make_body_builder(settings_for(tmp_path, str(broken)))

    save_fake_model(tmp_path / "small" / "neutral.npz", vertices=100)
    with pytest.raises(AdapterConfigError, match="measure_v1과 맞지 않습니다"):
        make_body_builder(settings_for(tmp_path, str(tmp_path / "small")))


def test_smpl_body_config_wires_the_real_body_adapter_and_fakes_the_rest(tmp_path):
    config = load_adapter_config("smpl_body", environ={})
    assert config.ports == {
        "body": "fitting.adapters.smpl.factory:make_body_builder",
        "drape": "fake",
        "render": "fake",
        "realize": "fake",
        "validate": "fake",
    }

    save_fake_model(tmp_path / "smpl" / "neutral.npz")
    ports = build_ports(
        config, settings_for(tmp_path, str(tmp_path / "smpl")), fit=RuleFitCalculator()
    )
    assert isinstance(ports.body, SmplBodyBuilder)


def test_smpl_body_config_stops_when_the_model_is_missing(tmp_path):
    config = load_adapter_config("smpl_body", environ={})
    with pytest.raises(AdapterConfigError, match="body: .*SMPL_MODEL_DIR이 비어"):
        build_ports(config, settings_for(tmp_path, None), fit=RuleFitCalculator())


def test_default_config_still_uses_the_fake_body():
    assert load_adapter_config("default", environ={}).ports["body"] == "fake"
