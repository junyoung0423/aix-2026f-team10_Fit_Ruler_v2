import pytest

np = pytest.importorskip("numpy")  # 선택 의존성 body가 없으면 건너뛴다

from fitting.body.measure import (  # noqa: E402 (numpy가 있을 때만 불러온다)
    convex_hull,
    convex_perimeter,
    measure_body,
    slice_loops,
    torso_faces,
    torso_section,
)
from fitting.domain import Measurements  # noqa: E402
from fitting.ports import BodyBuilder  # noqa: E402

from .synthetic import (  # noqa: E402
    BELLY_TOP,
    CHEST_RADIUS,
    FOREARM,
    LEFT_ACROMION,
    RIGHT_ACROMION,
    UPPER_ARM,
    WAIST_RADIUS,
    make_body,
    ngon_perimeter,
    tube,
)


def test_convex_perimeter_of_square_and_concave_shape() -> None:
    square = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=float)
    assert convex_perimeter(square) == pytest.approx(4.0)
    # 오목한 'ㄷ'자 모양도 줄자처럼 바깥 둘레(정사각형)로 잰다
    concave = np.array(
        [[0, 0], [1, 0], [1, 0.3], [0.3, 0.3], [0.3, 0.7], [1, 0.7], [1, 1], [0, 1]],
        dtype=float,
    )
    assert convex_perimeter(concave) == pytest.approx(4.0)
    assert len(convex_hull(concave)) == 4


def test_slice_of_cylinder_matches_polygon_perimeter() -> None:
    verts, faces = tube([0.0, 0.0, 0.0], [0.0, 1.0, 0.0], 1.0, 0.15)
    loops = slice_loops(verts, faces, 0.43)
    assert len(loops) == 1
    assert convex_perimeter(loops[0]) == pytest.approx(ngon_perimeter(0.15), rel=1e-9)


def test_slice_separates_pieces_and_picks_the_one_around_center() -> None:
    torso_v, torso_f = tube([0.0, 0.0, 0.0], [0.0, 1.0, 0.0], 1.0, 0.15)
    arm_v, arm_f = tube([0.3, 0.0, 0.0], [0.0, 1.0, 0.0], 1.0, 0.04)
    verts = np.vstack([torso_v, arm_v])
    faces = np.vstack([torso_f, arm_f + len(torso_v)])
    assert len(slice_loops(verts, faces, 0.5)) == 2
    section = torso_section(verts, faces, 0.5, np.array([0.0, 0.0]))
    assert convex_perimeter(section) == pytest.approx(ngon_perimeter(0.15))


def test_slice_above_mesh_is_empty_and_torso_section_raises() -> None:
    verts, faces = tube([0.0, 0.0, 0.0], [0.0, 1.0, 0.0], 1.0, 0.15)
    assert slice_loops(verts, faces, 2.0) == []
    with pytest.raises(ValueError):
        torso_section(verts, faces, 2.0, np.array([0.0, 0.0]))


def test_torso_faces_drop_arm_faces() -> None:
    body = make_body()
    kept = torso_faces(body.faces, body.weights)
    dominant = np.argmax(body.weights, axis=1)
    assert not np.isin(dominant[kept], [16, 17, 18, 19, 20, 21, 22, 23]).any()
    assert len(kept) < len(body.faces)


def measure(body):
    return measure_body(
        body.vertices, body.faces, body.joints, body.weights, body.acromion_vertices
    )


def test_swapped_acromion_vertices_are_rejected() -> None:
    body = make_body()
    left, right = body.acromion_vertices
    with pytest.raises(ValueError, match="어깨 끝점"):
        measure_body(body.vertices, body.faces, body.joints, body.weights, (right, left))


def test_measure_synthetic_body() -> None:
    body = make_body()
    result, debug = measure(body)

    assert result.height_cm == pytest.approx(170.0, abs=0.05)
    assert result.chest_cm == pytest.approx(ngon_perimeter(CHEST_RADIUS) * 100, abs=0.05)
    assert result.waist_cm == pytest.approx(ngon_perimeter(WAIST_RADIUS) * 100, abs=0.05)

    assert debug.left_shoulder_point == pytest.approx(LEFT_ACROMION)
    assert debug.right_shoulder_point == pytest.approx(RIGHT_ACROMION)
    assert result.shoulder_cm == pytest.approx(42.0, abs=0.05)

    expected_arm = np.linalg.norm(RIGHT_ACROMION - body.joints[19]) + FOREARM
    assert result.arm_length_cm == pytest.approx(expected_arm * 100, abs=0.05)
    assert result.arm_length_cm > (UPPER_ARM + FOREARM) * 100  # 관절 사이보다 어깨 끝만큼 길다

    assert body.joints[9][1] <= debug.chest_level_m <= 1.42 - 0.04
    assert body.joints[3][1] <= debug.waist_level_m < 1.15  # 아랫몸통(허리) 안
    # 결과 키는 계약의 Measurements 필드 이름과 같다
    assert set(result.to_dict()) == set(Measurements.model_fields)


def test_waist_is_the_narrowest_level_not_the_navel_height() -> None:
    """measure_v1: 허리는 자연 허리(가장 잘록한 곳). spine1(배꼽 높이 근사)에 고정하지 않는다."""
    body = make_body(belly_radius=0.15)  # spine1(1.05) 높이는 배(반지름 0.15)에 걸린다
    result, debug = measure(body)
    assert result.waist_cm == pytest.approx(ngon_perimeter(WAIST_RADIUS) * 100, abs=0.05)
    assert BELLY_TOP <= debug.waist_level_m < 1.15


def test_arm_length_uses_the_right_arm() -> None:
    """measure_v1: 팔길이는 오른쪽 어깨끝 → 팔꿈치 → 손목."""
    body = make_body()
    right_dir = (body.joints[21] - body.joints[19]) / FOREARM
    body.joints[21] = body.joints[19] + 0.20 * right_dir  # 오른쪽 아래팔만 짧게
    result, _ = measure(body)
    expected = np.linalg.norm(RIGHT_ACROMION - body.joints[19]) + 0.20
    assert result.arm_length_cm == pytest.approx(expected * 100, abs=0.05)


def test_fitted_metrics_use_the_body_builder_contract_keys() -> None:
    result, _ = measure(make_body())
    target = Measurements(
        height_cm=172.0,
        chest_cm=result.chest_cm,
        waist_cm=result.waist_cm,
        shoulder_cm=result.shoulder_cm,
        arm_length_cm=result.arm_length_cm,
    )
    metrics = result.fitted_metrics(target)
    assert metrics["fitted_height_cm"] == result.height_cm
    assert metrics["max_abs_error_cm"] == pytest.approx(2.0, abs=0.05)
    # 키 이름이 B1 계약(BodyBuilder 포트 설명)과 같다
    doc = BodyBuilder.__doc__ or ""
    for key in metrics:
        assert f"`{key}`" in doc
    assert "max_abs_error_cm" not in result.fitted_metrics()
