"""실제 SMPL 중성 모델로 기준 신체를 만들어 본다(D3). SMPL_MODEL_DIR이 있을 때만 돈다.

SMPL_MODEL_DIR=models/smpl python -m pytest -m smpl

한 명에 15~30초쯤 걸린다. 결과 수치는 docs/body-fit.md에 기록한다.
"""

import json
import os

import pytest

pytest.importorskip("numpy")  # 선택 의존성 body가 없으면 건너뛴다
pytest.importorskip("scipy")

from fitting.adapters.smpl import BODY_FIT_RECORD  # noqa: E402
from fitting.adapters.smpl.factory import make_body_builder  # noqa: E402
from fitting.body.fit import BODY_TOLERANCE_CM, FIELDS  # noqa: E402
from fitting.domain import BodyInput  # noqa: E402
from fitting.ports import PortRequest  # noqa: E402
from server.wiring import AdapterSettings  # noqa: E402

MODEL_DIR = (os.environ.get("SMPL_MODEL_DIR") or "").strip()

pytestmark = [
    pytest.mark.smpl,
    pytest.mark.skipif(not MODEL_DIR, reason="SMPL_MODEL_DIR가 없어 실제 모델 확인을 건너뜀"),
]

# 기준 신체 (docs/decisions.md "1주차 기준 [제안]" 1절)
REFERENCE_BODIES = {
    "body1": (175.0, 100.0, 80.0, 40.0, 56.0),
    "body2": (166.0, 97.5, 81.0, 36.0, 52.0),
}


@pytest.fixture(scope="module")
def builder(tmp_path_factory):
    root = tmp_path_factory.mktemp("smpl_body")
    settings = AdapterSettings.from_environ(
        artifacts_dir=root / "artifacts",
        environ={"SMPL_MODEL_DIR": MODEL_DIR, "FIT_WORK_DIR": str(root / "work")},
    )
    return make_body_builder(settings)


@pytest.mark.parametrize("name", sorted(REFERENCE_BODIES))
def test_reference_body_is_built_within_tolerance(builder, name):
    values = REFERENCE_BODIES[name]
    body = BodyInput(
        **dict(zip(FIELDS, values, strict=True)), measurement_protocol_version="measure_v1"
    )
    request = PortRequest(
        job_id=name,
        attempt_id=f"{name}_body_fit_1",
        config_version="test",
        timeout_seconds=600,
        trace_id=name,
    )

    result = builder.build_body(request, body)

    mesh = builder.artifacts_dir / f"{name}_body_mesh.obj"
    assert result.output_artifact_ids == [f"{name}_body_mesh"]
    assert mesh.read_text(encoding="utf-8").count("\nv ") >= 6889  # SMPL 정점 6890개
    for field, target in zip(FIELDS, values, strict=True):
        assert abs(result.metrics[f"fitted_{field}"] - target) <= BODY_TOLERANCE_CM[field] + 1e-9
    # 명세서 5장 한도(시작점 3개 × 200회) 안
    assert result.metrics["fit_evaluations"] <= 1 + 3 * 200 + 1

    record = json.loads((builder.work_dir / name / BODY_FIT_RECORD).read_text(encoding="utf-8"))
    assert record["passed"] is True
    assert max(abs(b) for b in record["betas"]) <= 3.0
