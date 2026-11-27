import pytest

np = pytest.importorskip("numpy", reason="garment 선택 의존성 필요: pip install -e .[garment]")

from fitting.adapters.garmentcode.coords import (  # noqa: E402
    BODY_SCALE,
    GARMENT_SCALE,
    GC_TO_SPEC,
    arms_path_to_spec,
    to_spec,
    transform_record,
)


def test_rotation_is_proper():
    assert np.isclose(np.linalg.det(GC_TO_SPEC), 1.0)
    assert np.allclose(GC_TO_SPEC @ GC_TO_SPEC.T, np.eye(3))


def test_front_plus_z_becomes_minus_y():
    assert np.allclose(to_spec([[0, 0, 1]], 1.0), [[0, -1, 0]])


def test_up_y_becomes_z():
    assert np.allclose(to_spec([[0, 1, 0]], 1.0), [[0, 0, 1]])


def test_x_unchanged():
    assert np.allclose(to_spec([[1, 0, 0]], 1.0), [[1, 0, 0]])


def test_garment_cm_to_m():
    # GarmentCode 옷 점 (10 cm, 150 cm 높이, 앞 5 cm) -> (0.1, -0.05, 1.5) m
    assert GARMENT_SCALE == 0.01
    assert np.allclose(to_spec([[10, 150, 5]], GARMENT_SCALE), [[0.1, -0.05, 1.5]])


def test_arms_path_is_turned_like_the_body_frame_by_frame():
    path = np.array([[[0.1, 1.5, 0.05], [0.0, 0.0, 0.0]], [[0.2, 1.4, 0.0], [0.0, 1.0, 0.0]]])
    spec = arms_path_to_spec(path, 2)
    assert spec.shape == (2, 2, 3)
    assert np.allclose(spec[0, 0], [0.1, -0.05, 1.5])
    assert np.allclose(spec[1], to_spec(path[1], BODY_SCALE))


@pytest.mark.parametrize("shape", [(2, 3, 3), (2, 2), (0, 2, 3), (2, 2, 4)])
def test_arms_path_must_match_the_body_vertices(shape):
    with pytest.raises(ValueError, match="does not match"):
        arms_path_to_spec(np.zeros(shape), 2)


def test_arms_path_must_be_finite():
    path = np.zeros((1, 2, 3))
    path[0, 1, 2] = np.inf
    with pytest.raises(ValueError, match="non-finite"):
        arms_path_to_spec(path, 2)


def test_body_already_m():
    assert BODY_SCALE == 1.0
    assert np.allclose(to_spec([[0.1, 1.5, 0.05]], BODY_SCALE), [[0.1, -0.05, 1.5]])


def test_lengths_preserved_after_scale():
    # 의류 실측을 바꾸지 않는다(R06): 회전은 길이를 보존하고 단위만 바뀐다.
    rng = np.random.default_rng(0)
    p = rng.normal(size=(50, 3)) * 30
    q = to_spec(p, GARMENT_SCALE)
    d_src = np.linalg.norm(p[1:] - p[:-1], axis=1) * 0.01
    d_dst = np.linalg.norm(q[1:] - q[:-1], axis=1)
    assert np.allclose(d_src, d_dst)


def test_transform_record_has_axes_and_matrix():
    r = transform_record()
    assert r['source_axes']['up'] == '+Y' and r['source_axes']['front'] == '+Z'
    assert r['target_axes'] == {'up': '+Z', 'front': '-Y', 'units': 'm'}
    assert np.allclose(r['rotation'], GC_TO_SPEC)
