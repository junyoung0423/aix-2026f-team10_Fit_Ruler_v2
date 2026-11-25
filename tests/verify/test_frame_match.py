"""대응 프레임 계산(D5) 테스트. 순수 계산이라 영상 파일이 필요 없다."""

import pytest

from fitting.verify import ClipTiming, coverage, match_render_frame


def test_same_fps_keeps_the_frame_number_even_if_the_real_video_is_shorter():
    # D1·D4 실제 호출: 렌더 72프레임 → 실사 65프레임, 둘 다 24fps
    real, render = ClipTiming(24, 65), ClipTiming(24, 72)
    for index in (0, 36, 64):
        match = match_render_frame(index, real, render)
        assert match.render_index == index
        assert match.time_gap_s == 0
        assert not match.clamped


def test_different_fps_is_matched_by_time():
    real, render = ClipTiming(24, 121), ClipTiming(30, 150)  # 둘 다 약 5초
    match = match_render_frame(60, real, render)  # 2.5초
    assert match.render_index == 75  # 2.5초 × 30fps
    assert match.real_time_s == pytest.approx(2.5)
    assert match.render_time_s == pytest.approx(2.5)


def test_time_gap_is_at_most_half_a_render_frame():
    real, render = ClipTiming(24, 121), ClipTiming(30, 150)
    matches = [match_render_frame(i, real, render) for i in range(real.frame_count)]
    # 실사 마지막 프레임(5.0초)은 렌더 150프레임(마지막 4.967초)을 넘어 잘린다
    assert [m.real_index for m in matches if m.clamped] == [120]
    for match in matches:
        if not match.clamped:
            # 시각은 소수 넷째 자리로 저장해서 차이에 반올림 오차가 최대 1e-4 붙는다
            assert match.time_gap_s <= 0.5 / render.fps + 1e-4 + 1e-9


def test_half_frames_round_up():
    # 실사 1번 = 1/24초 = 렌더(12fps) 0.5프레임 → 1번 (파이썬 round라면 0)
    assert match_render_frame(1, ClipTiming(24, 10), ClipTiming(12, 10)).render_index == 1
    # 1/3 × 3 같은 부동소수 오차로 경계가 밀리지 않는다
    assert match_render_frame(1, ClipTiming(3, 10), ClipTiming(3, 10)).render_index == 1


def test_real_frames_after_the_render_ends_are_clamped_and_flagged():
    real, render = ClipTiming(24, 121), ClipTiming(24, 72)  # 실사가 더 길다
    match = match_render_frame(100, real, render)
    assert match.render_index == 71
    assert match.clamped
    assert coverage(real, render)["real_longer"] is True


def test_stretch_maps_the_whole_length():
    real, render = ClipTiming(24, 65), ClipTiming(24, 72)
    assert match_render_frame(0, real, render, "stretch").render_index == 0
    assert match_render_frame(64, real, render, "stretch").render_index == 71
    assert match_render_frame(32, real, render, "stretch").render_index == 36  # 32/64 × 71 = 35.5
    single = match_render_frame(0, ClipTiming(24, 1), render, "stretch")
    assert single.render_index == 0


def test_static_render_clip_any_frame_is_inside_the_clip():
    # C5 정지 클립: 768×1024, 24fps, 121프레임 → fal 720p 출력도 121프레임
    real, render = ClipTiming(24, 121), ClipTiming(24, 121)
    assert match_render_frame(72, real, render).render_index == 72


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf")])
def test_fps_must_be_positive_and_finite(bad):
    with pytest.raises(ValueError, match="fps"):
        ClipTiming(bad, 10)


def test_invalid_inputs_are_rejected():
    with pytest.raises(ValueError, match="프레임이 없는"):
        ClipTiming(24, 0)
    real, render = ClipTiming(24, 10), ClipTiming(24, 10)
    with pytest.raises(ValueError, match="범위 밖"):
        match_render_frame(10, real, render)
    with pytest.raises(ValueError, match="범위 밖"):
        match_render_frame(-1, real, render)
    with pytest.raises(ValueError, match="모르는 매칭 방식"):
        match_render_frame(0, real, render, "nearest")


def test_coverage_reports_durations():
    result = coverage(ClipTiming(24, 65), ClipTiming(24, 72))
    assert result == {
        "real_duration_s": pytest.approx(2.7083),
        "render_duration_s": 3.0,
        "duration_ratio": pytest.approx(0.9028),
        "real_longer": False,
    }
    assert match_render_frame(3, ClipTiming(24, 65), ClipTiming(24, 72)).to_dict()["mode"] == "time"
