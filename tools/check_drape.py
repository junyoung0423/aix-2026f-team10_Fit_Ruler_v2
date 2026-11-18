"""D4: 착장 점검 — 몸과 착장된 옷 메시로 관통을 재고 판정해 기록을 남긴다 (Blender 불필요).

    python -m tools.check_drape --body body.obj --garment garment_draped.obj
    python -m tools.check_drape --c2-run artifacts/garment_runs/<실행 폴더>   # C2 착장 결과와 비교

기준값은 착장 설정(`--config`, 기본 `config/drape/drape_v1.json`)의 `thresholds`를 쓴다
(명세서 6장 초기값: 3 mm보다 깊은 관통 면적 1 % 이하).

결과는 `--out-dir`(기본 `artifacts/drape_check/<시각>/`)에 남는다(커밋 금지).
- `drape_check.json`  판정(PASS·FAIL·UNVERIFIABLE), 관통 지표, 기준, 가장 깊은 곳, 입력 정보
- `debug_front.png`   정면 투영: 몸(회색)·옷(파랑)·기준보다 깊은 관통(빨강)

종료 코드: 0 통과, 3 기준 초과(`DRAPE_INVALID`), 4 검증 불가, 1 입력 오류.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from fitting.adapters.meshcheck import (
    MeshInput,
    check_drape,
    face_centers,
    load_c2_run,
    load_obj,
    load_thresholds,
    write_record,
)
from fitting.verify import FAIL, PASS, DrapeCheckResult

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "drape" / "drape_v1.json"
EXIT = {PASS: 0, FAIL: 3}
EXIT_UNVERIFIABLE, EXIT_INPUT = 4, 1


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="몸·옷 메시로 착장 관통을 점검한다")
    parser.add_argument("--body", type=Path, help="몸 메시 OBJ")
    parser.add_argument("--garment", type=Path, help="착장된 옷 메시 OBJ")
    parser.add_argument("--c2-run", type=Path, help="C2 착장 실행 폴더(drape_input.json 등)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="착장 설정 파일")
    parser.add_argument("--out-dir", type=Path, help="결과 폴더")
    parser.add_argument("--no-image", action="store_true", help="확인 그림을 만들지 않는다")
    args = parser.parse_args(argv)
    if args.c2_run is None and (args.body is None or args.garment is None):
        parser.error("--body와 --garment를 함께 주거나 --c2-run을 주세요")
    return args


def save_debug_image(
    body: MeshInput,
    garment: MeshInput,
    signed: np.ndarray,
    depth_m: float,
    path: Path,
    height_px: int = 900,
) -> Path:
    """정면 투영 그림. 키 방향(가장 긴 축)을 세로, x를 가로로 둔다."""
    import cv2

    extent = np.ptp(body.vertices, axis=0)
    up = int(np.argmax(extent))
    across = 0 if up != 0 else 2
    pts = np.vstack([body.vertices, garment.vertices])[:, [across, up]]
    finite = np.all(np.isfinite(pts), axis=1)
    lo, hi = pts[finite].min(axis=0), pts[finite].max(axis=0)
    margin = 30
    scale = (height_px - 2 * margin) / max(hi[1] - lo[1], 1e-9)
    width_px = int((hi[0] - lo[0]) * scale) + 2 * margin

    def to_px(xy: np.ndarray) -> np.ndarray:
        x = margin + (xy[:, 0] - lo[0]) * scale
        y = height_px - margin - (xy[:, 1] - lo[1]) * scale
        return np.stack([x, y], axis=1).astype(int)

    image = np.full((height_px, width_px, 3), 255, dtype=np.uint8)
    for points, color in (
        (body.vertices[:, [across, up]], (180, 180, 180)),
        (garment.vertices[:, [across, up]], (200, 140, 60)),
    ):
        ok = np.all(np.isfinite(points), axis=1)
        for x, y in to_px(points[ok]):
            cv2.circle(image, (int(x), int(y)), 1, color, -1)
    centers = face_centers(np.asarray(garment.vertices), np.asarray(garment.faces))
    deep = np.nan_to_num(signed, nan=0.0) < -depth_m
    for x, y in to_px(centers[deep][:, [across, up]]):
        cv2.circle(image, (int(x), int(y)), 3, (40, 40, 220), -1)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", image)  # 한글 경로에서 cv2.imwrite가 실패해서 직접 쓴다
    if not ok:
        raise OSError(f"확인 그림을 만들지 못했습니다: {path}")
    path.write_bytes(encoded.tobytes())
    return path


def print_summary(result: DrapeCheckResult, out_dir: Path, c2: dict | None) -> None:
    p = result.penetration
    t = result.thresholds
    print(f"판정: {result.status}" + (f" ({result.error_code})" if result.error_code else ""))
    print(
        f"관통(>{t.penetration_depth_m * 1000:g} mm) 면적 {p.area_ratio * 100:.3f} % "
        f"(기준 {t.penetration_area_ratio_max * 100:g} % 이하) · "
        f"가장 깊은 곳 {p.max_depth_m * 1000:.1f} mm · 몸 안 면적 {p.inside_area_ratio * 100:.2f} %"
    )
    if p.clearance_median_m is not None:
        print(f"몸 밖 옷 면의 표면까지 거리 중앙값 {p.clearance_median_m * 1000:.1f} mm")
    print(f"몸 메시 닫힘 {result.body_closed} · 옷 좌표 유한 {result.garment_finite}")
    for reason in result.reasons:
        print(f"  - {reason}")
    if c2 is not None and "penetration_area_ratio" in c2:
        print(
            f"C2(Blender) 결과: 관통 면적 {float(c2['penetration_area_ratio']) * 100:.3f} %, "
            f"판정 {c2.get('penetration_ok')}"
        )
    print(f"결과 폴더: {out_dir}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_dir = args.out_dir or ROOT / "artifacts" / "drape_check" / datetime.now().strftime(
        "%Y%m%d-%H%M%S"
    )
    started = time.monotonic()
    try:
        thresholds, config = load_thresholds(args.config)
        c2 = None
        if args.c2_run is not None:
            body, garment, c2 = load_c2_run(args.c2_run)
        else:
            body, garment = load_obj(args.body), load_obj(args.garment)
    except (OSError, ValueError, KeyError) as error:
        print(f"입력 오류: {error}", file=sys.stderr)
        return EXIT_INPUT

    result, signed = check_drape(body, garment, thresholds)
    extra = {
        "c2_metrics": None
        if c2 is None
        else {
            key: c2.get(key)
            for key in (
                "penetration_area_ratio",
                "penetration_ok",
                "penetration_status",
                "drape_ok",
            )
        }
    }
    write_record(out_dir, result, config=config, seconds=time.monotonic() - started, extra=extra)
    if not args.no_image:
        save_debug_image(
            body, garment, signed, thresholds.penetration_depth_m, out_dir / "debug_front.png"
        )
    print_summary(result, out_dir, c2)
    return EXIT.get(result.status, EXIT_UNVERIFIABLE)


if __name__ == "__main__":
    sys.exit(main())
