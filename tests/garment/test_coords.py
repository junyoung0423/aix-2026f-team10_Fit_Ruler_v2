import numpy as np

from coords import BODY_SCALE, GARMENT_SCALE, GC_TO_SPEC, to_spec, transform_record


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
