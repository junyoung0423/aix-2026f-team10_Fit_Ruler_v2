"""tools.spike_fal_ltx의 드라이런과 --resume 테스트. fal을 부르지 않는다."""

import json
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")  # 선택 의존성 realize가 없으면 건너뛴다
np = pytest.importorskip("numpy")

from tools import spike_fal_ltx  # noqa: E402 (cv2가 있을 때만 불러온다)


def write_clip(path: Path, frames: int = 12, size: tuple[int, int] = (48, 64)) -> Path:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 24.0, size)
    for i in range(frames):
        writer.write(np.full((size[1], size[0], 3), 40 + i * 10, dtype=np.uint8))
    writer.release()
    return path


def test_dry_run_writes_plan_without_calling_fal(tmp_path: Path) -> None:
    out_dir = tmp_path / "바탕 화면" / "dry"
    assert spike_fal_ltx.main(["--make-test-clip", "--out-dir", str(out_dir)]) == 0
    plan = json.loads((out_dir / "plan.json").read_text(encoding="utf-8"))
    assert plan["estimate"]["frames"] == 65
    assert (out_dir / "frame_render.png").exists()
    assert not (out_dir / "request.json").exists()  # 호출하지 않았다


def test_resume_downloads_output_and_finishes_record(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    (out_dir / "plan.json").write_text(json.dumps({"endpoint": "x"}), encoding="utf-8")
    (out_dir / "response.json").write_text(
        json.dumps(
            {
                "video": {"url": "https://fal.example/out.mp4"},
                "seed": 42,
                "prompt": "3DREAL. p",
            }
        ),
        encoding="utf-8",
    )
    (out_dir / "error.json").write_text(
        json.dumps(
            {
                "stage": "download",
                "request_id": "req-1",
                "durations": {
                    "upload_s": 4.2,
                    "queue_s": 24.4,
                    "processing_s": 43.4,
                    "download_s": None,
                    "total_s": 72.6,
                },
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_download(url: str, path: Path) -> None:
        calls.append(url)
        write_clip(path)

    assert spike_fal_ltx.resume(out_dir, downloader=fake_download) == 0

    assert calls == ["https://fal.example/out.mp4"]
    record = json.loads((out_dir / "record.json").read_text(encoding="utf-8"))
    assert record["result"]["request_id"] == "req-1"
    assert record["result"]["resumed_after"] == "download"
    assert record["result"]["durations"]["processing_s"] == 43.4
    assert record["result"]["durations"]["download_s"] is not None
    assert record["output"]["frame_count"] == 12
    assert record["cost_from_output"]["megapixels"] == 1  # 48×64×12 = 0.04MP → 올림 1
    assert record["port_metrics"] == {
        "external_request_id": "req-1",
        "cost_usd": record["cost_from_output"]["usd"],
        "queue_s": 24.4,
        "processing_s": 43.4,
    }
    assert (out_dir / "frame_real.png").exists()
    assert not (out_dir / "error.json").exists()
    assert (out_dir / "error_first_attempt.json").exists()


def test_resume_without_records_reports_error(tmp_path: Path) -> None:
    assert spike_fal_ltx.resume(tmp_path) == 2
