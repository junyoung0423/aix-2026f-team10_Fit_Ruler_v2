"""tools.smpl_body를 합성 모델(.npz)로 끝까지 돌려 본다. 실제 SMPL 모델은 쓰지 않는다."""

import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")  # 선택 의존성 body가 없으면 건너뛴다
pytest.importorskip("cv2")

from fitting.body.measure import measure_body  # noqa: E402
from tools import smpl_body  # noqa: E402

from .synthetic import make_body  # noqa: E402

# SMPL 24관절의 부모 관절 번호
SMPL_PARENTS = [-1, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9, 12, 13, 14, 16, 17, 18, 19, 20, 21]


def synthetic_model_npz(path: Path) -> Path:
    """합성 몸을 SMPL 모델 파일 모양으로 저장한다. 관절 위치는 따로 붙인 정점에서 뽑는다."""
    body = make_body()
    n = len(body.vertices)
    vertices = np.vstack(
        [body.vertices, body.joints]
    )  # 관절 자리 정점 24개를 끝에 붙인다(면에는 안 씀)
    weights = np.vstack([body.weights, np.eye(24)])
    j_regressor = np.zeros((24, n + 24))
    j_regressor[np.arange(24), n + np.arange(24)] = 1.0
    kintree = np.array([[2**32 - 1 if p < 0 else p for p in SMPL_PARENTS], list(range(24))])
    np.savez(
        path,
        v_template=vertices,
        shapedirs=np.zeros((n + 24, 3, 10)),
        posedirs=np.zeros((n + 24, 3, 207)),
        J_regressor=j_regressor,
        kintree_table=kintree,
        weights=weights,
        f=body.faces,
    )
    return path


def test_tool_writes_obj_measurements_and_debug_image(tmp_path: Path) -> None:
    model_path = synthetic_model_npz(tmp_path / "synthetic.npz")
    out_dir = tmp_path / "바탕 화면" / "out"  # 한글·공백 경로에서도 저장되는지 확인

    # 합성 몸은 이미 A자세라 팔을 더 내리지 않는다(각도 0)
    body = make_body()
    left, right = body.acromion_vertices
    code = smpl_body.main(
        [
            "--model",
            str(model_path),
            "--arm-angle",
            "0",
            "--acromion-vertices",
            str(left),
            str(right),
            "--out-dir",
            str(out_dir),
        ]
    )

    assert code == 0
    record = json.loads((out_dir / "measurements.json").read_text(encoding="utf-8"))
    assert record["acromion_vertices"] == [left, right]
    expected, _ = measure_body(
        body.vertices, body.faces, body.joints, body.weights, body.acromion_vertices
    )
    assert record["measurements_cm"] == pytest.approx(expected.to_dict())
    obj_lines = (out_dir / "body_apose.obj").read_text(encoding="utf-8").splitlines()
    assert sum(line.startswith("v ") for line in obj_lines) == len(body.vertices) + 24
    assert sum(line.startswith("f ") for line in obj_lines) == len(body.faces)
    assert (out_dir / "debug_front.png").stat().st_size > 0


def test_tool_reports_missing_model(tmp_path: Path) -> None:
    assert smpl_body.main(["--model", str(tmp_path / "none.pkl")]) == 2
