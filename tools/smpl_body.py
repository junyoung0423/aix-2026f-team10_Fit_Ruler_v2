"""D2: SMPL 평균 체형(또는 주어진 betas)을 A자세로 내보내고 신체 치수 5개를 잰다.

    python -m tools.smpl_body --model models/smpl/basicmodel_neutral_lbs_10_207_0_v1.1.0.pkl

결과는 artifacts/body/<이름>/ 에 남는다(커밋 금지).
- body_apose.obj      A자세 몸 메시 (미터, C의 착장 스파이크 입력)
- measurements.json   치수 5개(cm), 측정 높이, 사용한 설정
- debug_front.png     정면 투영 위에 가슴·허리 높이와 어깨 끝점을 표시한 확인용 그림
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from fitting.adapters.smpl import SmplModel, a_pose, write_obj
from fitting.body.measure import BodyMeasurementsCm, MeasureDebug, measure_body

ROOT = Path(__file__).resolve().parents[1]


def save_debug_image(
    vertices: np.ndarray,
    result: BodyMeasurementsCm,
    debug: MeasureDebug,
    path: Path,
    height_px: int = 900,
) -> Path:
    """정면(x·y) 투영 그림. 글자는 OpenCV 기본 글꼴이라 영어로 쓴다."""
    xy = vertices[:, :2]
    lo, hi = xy.min(axis=0), xy.max(axis=0)
    margin = 40
    scale = (height_px - 2 * margin) / (hi[1] - lo[1])
    width_px = int((hi[0] - lo[0]) * scale) + 2 * margin + 260

    def to_px(x: float, y: float) -> tuple[int, int]:
        return int(margin + (x - lo[0]) * scale), int(
            height_px - margin - (y - lo[1]) * scale
        )

    image = np.full((height_px, width_px, 3), 255, dtype=np.uint8)
    for x, y in xy:
        cv2.circle(image, to_px(x, y), 1, (170, 170, 170), -1)

    accent, text = (223, 111, 47), (40, 40, 40)
    for level, label in ((debug.chest_level_m, f"chest {result.chest_circumference_cm} cm"),
                         (debug.waist_level_m, f"waist {result.waist_circumference_cm} cm")):  # fmt: skip
        left, right = to_px(lo[0], level), to_px(hi[0], level)
        cv2.line(image, left, right, accent, 2)
        cv2.putText(
            image,
            label,
            (right[0] + 10, right[1] + 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            text,
            1,
            cv2.LINE_AA,
        )
    l_pt = to_px(*debug.left_shoulder_point[:2])
    r_pt = to_px(*debug.right_shoulder_point[:2])
    cv2.line(image, l_pt, r_pt, (60, 160, 60), 2)
    for point in (l_pt, r_pt):
        cv2.circle(image, point, 6, (60, 160, 60), -1)
    lines = [
        f"height {result.height_cm} cm",
        f"shoulder {result.shoulder_width_cm} cm",
        f"arm {result.arm_length_cm} cm",
    ]
    for i, line in enumerate(lines):
        cv2.putText(
            image,
            line,
            (width_px - 250, 40 + 28 * i),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            text,
            1,
            cv2.LINE_AA,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), image)
    return path


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SMPL 몸을 A자세로 내보내고 신체 치수를 잰다"
    )
    parser.add_argument(
        "--model", type=Path, required=True, help="SMPL 모델 파일(.pkl 또는 .npz)"
    )
    parser.add_argument(
        "--betas", type=float, nargs="*", default=[], help="체형 계수(없으면 평균 체형)"
    )
    parser.add_argument(
        "--arm-angle", type=float, default=45.0, help="A자세에서 팔을 내리는 각도(도)"
    )
    parser.add_argument("--name", default=None, help="결과 폴더 이름")
    parser.add_argument("--out-dir", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.model.exists():
        print(
            f"모델 파일이 없습니다: {args.model}\nmodels/README.md를 보고 SMPL 모델을 받아 두세요.",
            file=sys.stderr,
        )
        return 2

    name = args.name or (args.model.stem + ("-betas" if args.betas else "-mean"))
    out_dir = args.out_dir or ROOT / "artifacts" / "body" / name
    model = SmplModel.load(args.model)
    betas = np.array(args.betas, dtype=float) if args.betas else None
    out = model.forward(betas=betas, body_pose=a_pose(args.arm_angle))
    result, debug = measure_body(out.vertices, model.faces, out.joints, model.weights)

    obj_path = write_obj(
        out_dir / "body_apose.obj",
        out.vertices,
        model.faces,
        comment=f"SMPL {args.model.name}, A-pose {args.arm_angle:g} deg, betas={args.betas or 'mean'}, unit=m",
    )
    debug_path = save_debug_image(
        out.vertices, result, debug, out_dir / "debug_front.png"
    )
    record = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "model_file": args.model.name,
        "betas": args.betas,
        "arm_angle_deg": args.arm_angle,
        "vertices": int(out.vertices.shape[0]),
        "measurements_cm": result.to_dict(),
        "levels_m": {
            "chest": round(debug.chest_level_m, 4),
            "waist": round(debug.waist_level_m, 4),
        },
        "shoulder_points_m": {
            "left": [round(v, 4) for v in debug.left_shoulder_point.tolist()],
            "right": [round(v, 4) for v in debug.right_shoulder_point.tolist()],
        },
    }
    (out_dir / "measurements.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        f"모델: {args.model.name} · A자세 {args.arm_angle:g}도 · betas {args.betas or '평균'}"
    )
    for label, value in (("키", result.height_cm), ("가슴둘레", result.chest_circumference_cm),
                         ("허리둘레", result.waist_circumference_cm), ("어깨너비", result.shoulder_width_cm),
                         ("팔길이", result.arm_length_cm)):  # fmt: skip
        print(f"  {label:<5} {value:6.1f} cm")
    print(f"OBJ: {obj_path}\n확인 그림: {debug_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
