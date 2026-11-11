"""AI Hub 의류 통합 데이터(dataSetSn 71501) 라벨 1개를 등록 의류 1벌로 저장한다 (#3, C1).

    python -m tools.register_aihub_garment <라벨 파일> [--out data/garments] [--force]

- 라벨을 `GarmentVariant`로 바꿔 `{out}/{id}.json`에 쓰고, 쓴 파일을 다시 읽어 확인한다.
- 같은 ID 파일이 있으면 덮어쓰지 않고 멈춘다. `--force`일 때만 덮어쓴다.
- 반팔 라운드넥 티셔츠가 아니거나 치수 단위가 맞지 않으면 이유를 출력하고 멈춘다.

종료 코드: 0 성공, 1 같은 ID 파일 있음·다시 읽기 실패, 2 라벨 거절·읽기 실패.
AI Hub 원본(라벨 파일)은 저장소 밖에 두고, 저장소에는 변환한 파일만 둔다.
"""

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from fitting.adapters.aihub import AihubLabelRejected, label_to_garment, read_label
from fitting.domain import GarmentVariant

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "data" / "garments"

EXIT_OK = 0
EXIT_EXISTS = 1
EXIT_REJECTED = 2


def garment_json(garment: GarmentVariant) -> str:
    """등록 파일 내용. 시연용 의류 파일과 같은 모양(들여쓰기 2, UTF-8, 끝 줄바꿈)이다."""
    return json.dumps(garment.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"


def register(
    label_path: Path, out_dir: Path, *, force: bool = False, chest_waist_flat_width: bool = False
) -> Path:
    """라벨 1개를 등록하고 쓴 파일 경로를 돌려준다.

    거절이면 `AihubLabelRejected`, 같은 ID 파일이 있으면 `FileExistsError`.
    """
    label = read_label(label_path)
    garment = label_to_garment(label, chest_waist_flat_width=chest_waist_flat_width)
    path = Path(out_dir) / f"{garment.id}.json"
    if path.exists() and not force:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(garment_json(garment), encoding="utf-8", newline="\n")

    reloaded = GarmentVariant.model_validate_json(path.read_bytes())
    if reloaded != garment:
        raise ValueError(f"다시 읽은 의류가 변환 결과와 다릅니다: {path}")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("label", type=Path, help="AI Hub 라벨 JSON 파일")
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="등록 의류 디렉터리 (기본: data/garments)"
    )
    parser.add_argument("--force", action="store_true", help="같은 ID 파일이 있으면 덮어쓴다")
    parser.add_argument(
        "--chest-waist-flat-width",
        action="store_true",
        help="가슴·허리 값을 단면 너비로 보고 2배 한다 (기본: 둘레로 본다, [제안])",
    )
    args = parser.parse_args(argv)

    try:
        path = register(
            args.label,
            args.out,
            force=args.force,
            chest_waist_flat_width=args.chest_waist_flat_width,
        )
    except AihubLabelRejected as error:
        print(f"등록하지 않았습니다(라벨 거절): {args.label}", file=sys.stderr)
        for reason in error.reasons:
            print(f"  - {reason}", file=sys.stderr)
        return EXIT_REJECTED
    except FileExistsError as error:
        print(
            f"같은 ID 파일이 이미 있어 멈췄습니다: {error.args[0]}\n"
            "  덮어쓰려면 --force를 붙이세요.",
            file=sys.stderr,
        )
        return EXIT_EXISTS
    except (OSError, json.JSONDecodeError) as error:
        print(f"라벨을 읽을 수 없습니다: {args.label}: {error}", file=sys.stderr)
        return EXIT_REJECTED
    except (ValidationError, ValueError) as error:
        print(f"등록 파일 확인에 실패했습니다: {error}", file=sys.stderr)
        return EXIT_EXISTS

    garment = GarmentVariant.model_validate_json(path.read_bytes())
    print(f"등록: {path} (id={garment.id}, status={garment.status.value})")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
