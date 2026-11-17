"""착장 점검 메시 기하·실행(fitting.adapters.meshcheck)과 tools.check_drape 테스트.

정육면체처럼 답을 손으로 셀 수 있는 메시로 시험한다. Blender는 쓰지 않는다.
"""

import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from fitting.adapters.meshcheck import (  # noqa: E402
    MeshInput,
    check_drape,
    closest_points_on_triangles,
    is_closed,
    load_c2_run,
    read_obj,
    signed_distance,
)
from fitting.verify import FAIL, PASS, UNVERIFIABLE, DrapeCheckThresholds  # noqa: E402

THRESHOLDS = DrapeCheckThresholds(penetration_depth_m=0.003, penetration_area_ratio_max=0.01)

# 바깥을 향하게 감은 정육면체 삼각형 12개
CUBE_FACES = np.array(
    [
        [0, 2, 1], [0, 3, 2],  # z = -h
        [4, 5, 6], [4, 6, 7],  # z = +h
        [0, 1, 5], [0, 5, 4],  # y = -h
        [3, 7, 6], [3, 6, 2],  # y = +h
        [0, 4, 7], [0, 7, 3],  # x = -h
        [1, 2, 6], [1, 6, 5],  # x = +h
    ]
)  # fmt: skip


def cube(half: float, center=(0.0, 0.0, 0.0)) -> np.ndarray:
    signs = np.array(
        [[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
         [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]], dtype=float
    )  # fmt: skip
    return signs * half + np.asarray(center)


def subdivided_cube(half: float, n: int = 8) -> tuple[np.ndarray, np.ndarray]:
    """면마다 n×n 격자로 나눈 닫힌 정육면체 겉면(옷처럼 면이 많은 메시). 면은 바깥을 향한다."""
    points: list[np.ndarray] = []
    faces: list[tuple[int, int, int]] = []
    grid = np.linspace(-half, half, n + 1)
    for axis in range(3):
        for sign in (-1.0, 1.0):
            a, b = [i for i in range(3) if i != axis]
            start = len(points)
            for u in grid:
                for v in grid:
                    p = np.zeros(3)
                    p[axis], p[a], p[b] = sign * half, u, v
                    points.append(p)
            for i in range(n):
                for j in range(n):
                    q = start + i * (n + 1) + j
                    faces += [(q, q + n + 1, q + n + 2), (q, q + n + 2, q + 1)]
    # 모서리에서 겹치는 정점을 합친다
    unique, inverse = np.unique(np.round(points, 9), axis=0, return_inverse=True)
    tris = inverse.reshape(-1)[np.array(faces)]
    # 바깥을 향하게: 면 법선이 중심(원점)에서 멀어지는 쪽이 되도록 뒤집는다
    a, b, c = (unique[tris[:, i]] for i in range(3))
    inward = np.einsum("ij,ij->i", np.cross(b - a, c - a), (a + b + c) / 3) < 0
    tris[inward] = tris[inward][:, [0, 2, 1]]
    return unique, tris


def test_read_obj_handles_polygons_slashes_and_negative_indices(tmp_path):
    path = tmp_path / "quad.obj"
    path.write_text(
        "# 사각형 하나\nv 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nvn 0 0 1\nf 1//1 2//1 3//1 -1//1\n",
        encoding="utf-8",
    )
    vertices, faces = read_obj(path)
    assert vertices.shape == (4, 3)
    assert faces.tolist() == [[0, 1, 2], [0, 2, 3]]

    (tmp_path / "empty.obj").write_text("v 0 0 0\n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_obj(tmp_path / "empty.obj")


def test_closed_and_open_meshes():
    assert is_closed(CUBE_FACES)
    assert not is_closed(CUBE_FACES[:-1])  # 면 하나를 빼면 구멍


def test_closest_point_matches_brute_force_sampling():
    rng = np.random.default_rng(3)
    a, b, c = rng.normal(size=(3, 50, 3))
    p = rng.normal(size=(50, 3)) * 2
    q, bary = closest_points_on_triangles(p, a, b, c)
    assert np.allclose(bary.sum(axis=1), 1.0)
    assert np.all(bary >= -1e-12)
    # 삼각형 위 점을 촘촘히 뽑아 잰 최소 거리보다 크면 안 된다
    s = np.linspace(0, 1, 41)
    uu, vv = np.meshgrid(s, s)
    keep = uu + vv <= 1
    u, v = uu[keep], vv[keep]
    samples = a[:, None] + u[None, :, None] * (b - a)[:, None] + v[None, :, None] * (c - a)[:, None]
    sampled = np.linalg.norm(samples - p[:, None], axis=2).min(axis=1)
    ours = np.linalg.norm(q - p, axis=1)
    assert np.all(ours <= sampled + 1e-9)
    assert np.allclose(ours, sampled, atol=0.05)


def test_signed_distance_is_exact_on_a_cube():
    vertices, faces = subdivided_cube(0.2)
    points = np.array(
        [
            [0.0, 0.0, 0.25],  # 위로 5 cm 바깥
            [0.0, 0.0, 0.19],  # 위면에서 1 cm 안
            [0.195, 0.0, 0.0],  # 옆면에서 5 mm 안
            [0.3, 0.3, 0.0],  # 모서리 바깥 (거리 √2 × 0.1)
            [np.nan, 0.0, 0.0],  # 잴 수 없음
        ]
    )
    signed, closest = signed_distance(points, vertices, faces)
    assert signed[:4] == pytest.approx([0.05, -0.01, -0.005, np.sqrt(2) * 0.1], abs=1e-9)
    assert np.isnan(signed[4])
    assert closest[0] == pytest.approx([0.0, 0.0, 0.2])


def drape_inputs(offset: float, *, open_body: bool = False):
    body_v, body_f = subdivided_cube(0.2)
    if open_body:
        body_f = body_f[:-1]
    garment_v, garment_f = subdivided_cube(0.2 + offset, n=10)
    return MeshInput(body_v, body_f, "body"), MeshInput(garment_v, garment_f, "garment")


def test_garment_outside_the_body_passes():
    body, garment = drape_inputs(0.01)
    result, signed = check_drape(body, garment, THRESHOLDS)

    assert result.status == PASS
    assert result.penetration.area_ratio == 0.0
    assert result.penetration.clearance_median_m == pytest.approx(0.01, abs=1e-6)
    assert np.all(signed > 0)
    assert result.details["body"]["faces"] == len(body.faces)


def test_garment_inside_the_body_fails_with_the_deepest_points():
    body, garment = drape_inputs(-0.005)  # 5 mm 안쪽: 관통 면적 100 %
    result, _ = check_drape(body, garment, THRESHOLDS)

    assert result.status == FAIL
    assert result.error_code == "DRAPE_INVALID"
    assert result.penetration.area_ratio == pytest.approx(1.0)
    assert result.penetration.max_depth_m == pytest.approx(0.005, abs=1e-6)
    assert result.details["deepest"][0]["depth_mm"] == pytest.approx(5.0, abs=0.01)


def test_shallow_contact_under_the_depth_is_not_penetration():
    body, garment = drape_inputs(-0.002)  # 2 mm 안쪽: 3 mm 기준보다 얕다
    result, _ = check_drape(body, garment, THRESHOLDS)
    assert result.status == PASS
    assert result.penetration.inside_area_ratio == pytest.approx(1.0)


def test_open_body_is_unverifiable_and_nan_garment_fails():
    body, garment = drape_inputs(0.01, open_body=True)
    assert check_drape(body, garment, THRESHOLDS)[0].status == UNVERIFIABLE

    body, garment = drape_inputs(0.01)
    broken = garment.vertices.copy()
    broken[0] = np.nan
    result, _ = check_drape(body, MeshInput(broken, garment.faces), THRESHOLDS)
    assert result.status == FAIL
    assert result.penetration.unknown_faces > 0


def write_obj(path: Path, mesh: MeshInput) -> Path:
    lines = [f"v {x} {y} {z}" for x, y, z in mesh.vertices]
    lines += [f"f {a + 1} {b + 1} {c + 1}" for a, b, c in mesh.faces]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_check_drape_tool_writes_the_record(tmp_path, capsys):
    pytest.importorskip("cv2")
    from tools import check_drape as tool

    body, garment = drape_inputs(-0.005)
    out = tmp_path / "out"
    code = tool.main(
        [
            "--body", str(write_obj(tmp_path / "body.obj", body)),
            "--garment", str(write_obj(tmp_path / "garment.obj", garment)),
            "--out-dir", str(out),
        ]
    )  # fmt: skip

    assert code == 3
    record = json.loads((out / "drape_check.json").read_text(encoding="utf-8"))
    assert record["status"] == FAIL
    assert record["config"]["file"] == "drape_v1.json"
    assert record["thresholds"]["source"].startswith("drape_v1")
    assert (out / "debug_front.png").stat().st_size > 0
    assert "DRAPE_INVALID" in capsys.readouterr().out

    body, garment = drape_inputs(0.01)
    write_obj(tmp_path / "garment_ok.obj", garment)
    assert (
        tool.main(
            [
                "--body",
                str(tmp_path / "body.obj"),
                "--garment",
                str(tmp_path / "garment_ok.obj"),
                "--out-dir",
                str(tmp_path / "ok"),
                "--no-image",
            ]
        )  # fmt: skip
        == 0
    )
    assert tool.main(["--body", str(tmp_path / "missing.obj"), "--garment", "x.obj"]) == 1


def test_c2_run_folder_is_read_and_compared(tmp_path, capsys):
    from tools import check_drape as tool

    body, garment = drape_inputs(0.01)
    run = tmp_path / "run"
    run.mkdir()
    (run / "drape_input.json").write_text(
        json.dumps(
            {
                "body": {
                    "vertices": body.vertices.tolist(),
                    "faces": body.faces.tolist(),
                    "is_watertight": True,
                },
                "garment": {"vertices": garment.vertices.tolist(), "faces": garment.faces.tolist()},
            }
        ),
        encoding="utf-8",
    )
    np.save(run / "garment_draped_co.npy", garment.vertices)
    (run / "drape_metrics.json").write_text(
        json.dumps({"penetration_area_ratio": 0.0, "penetration_ok": True}), encoding="utf-8"
    )

    loaded_body, loaded_garment, c2 = load_c2_run(run)
    assert len(loaded_body.faces) == len(body.faces)
    assert c2["penetration_ok"] is True

    code = tool.main(["--c2-run", str(run), "--out-dir", str(tmp_path / "out"), "--no-image"])
    assert code == 0
    assert "C2(Blender) 결과" in capsys.readouterr().out
    record = json.loads((tmp_path / "out" / "drape_check.json").read_text(encoding="utf-8"))
    assert record["c2_metrics"]["penetration_area_ratio"] == 0.0
