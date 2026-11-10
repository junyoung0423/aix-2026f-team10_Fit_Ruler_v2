import numpy as np
import pytest

from fitting.body.measure import (
    convex_hull,
    convex_perimeter,
    measure_body,
    shoulder_point,
    slice_loops,
    torso_faces,
    torso_section,
)

from .synthetic import (
    ARM_RADIUS,
    CHEST_RADIUS,
    FOREARM,
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


def test_shoulder_point_is_outermost_vertex_above_joint() -> None:
    verts = np.array(
        [[0.10, 1.50, 0.0], [0.25, 1.43, 0.0], [0.40, 1.30, 0.0], [-0.24, 1.45, 0.0]]
    )
    joint = np.array([0.2, 1.42, 0.0])
    # 0.40은 어깨 관절보다 낮아(팔 아래쪽) 빠지고, 음수 x는 반대쪽이라 빠진다
    assert shoulder_point(verts, joint, side=+1) == pytest.approx([0.25, 1.43, 0.0])


def test_measure_synthetic_body() -> None:
    body = make_body()
    result, debug = measure_body(body.vertices, body.faces, body.joints, body.weights)

    assert result.height_cm == pytest.approx(170.0, abs=0.05)
    assert result.chest_circumference_cm == pytest.approx(
        ngon_perimeter(CHEST_RADIUS) * 100, abs=0.05
    )
    assert result.waist_circumference_cm == pytest.approx(
        ngon_perimeter(WAIST_RADIUS) * 100, abs=0.05
    )

    # 어깨 끝점: 위팔 원기둥 시작 고리에서 가장 바깥(어깨 관절 + 반지름 × 팔에 수직인 위쪽 방향)
    d = body.arm_dir_left
    up_normal = np.array([-d[1], d[0], 0.0])  # 팔 방향에 수직이고 위를 향하는 벡터
    left_tip = body.joints[16] + ARM_RADIUS * up_normal
    assert debug.left_shoulder_point == pytest.approx(left_tip, abs=1e-6)
    assert result.shoulder_width_cm == pytest.approx(2 * left_tip[0] * 100, abs=0.05)

    expected_arm = np.linalg.norm(left_tip - body.joints[18]) + FOREARM
    assert result.arm_length_cm == pytest.approx(expected_arm * 100, abs=0.05)
    assert (
        result.arm_length_cm > (UPPER_ARM + FOREARM) * 100
    )  # 관절 사이보다 어깨 끝만큼 길다

    assert body.joints[9][1] <= debug.chest_level_m <= 1.42 - 0.04
    assert debug.waist_level_m == pytest.approx(1.05)
    assert set(result.to_dict()) == {
        "heightCm",
        "chestCircumferenceCm",
        "waistCircumferenceCm",
        "shoulderWidthCm",
        "armLengthCm",
    }
