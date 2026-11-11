"""의류 치수와 가장 비슷한 견본 1개를 찾는다 (#5).

    python -m tools.search_sample --garment data/garments/tee_regular_m_navy_v1.json \\
        --samples tests/fixtures/catalog/samples_v0.json \\
        [--rules config/sample_search_v0.yaml] [--top 3]
    python -m tools.search_sample --chest 116 --shoulder 50 --sleeve 23 --length 71 \\
        --samples tests/fixtures/catalog/samples_v0.json

- `--garment`는 `GarmentVariant` JSON 파일이다. 치수를 직접 줄 때는 `--chest --shoulder --sleeve
  --length`(cm, 가슴은 둘레)를 쓴다. 둘 중 하나만 쓸 수 있다. 허리는 비교하지 않는다.
- `--samples`는 `{"note": "...", "samples": [SampleCandidate, ...]}` 형식의 견본집 JSON이다.
- 표준 출력에 `SampleSearchResult` JSON(UTF-8, 들여쓰기 2)을 쓴다. `--top N`이면 `ranking`을 앞의
  N개로 자른다.

종료 코드: 성공 0, 견본을 고를 수 없음(`SampleSearchError`) 2 — 표준 오류에 코드와 한국어 한 줄,
파일·형식·사용법 오류 1.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn

from pydantic import ValidationError

from fitting.catalog.search import (
    SampleCandidate,
    SampleSearchError,
    load_search_rules,
    search_sample,
)
from fitting.domain import GarmentMeasurements, GarmentVariant

EXIT_OK = 0
EXIT_INPUT_ERROR = 1
EXIT_NO_SAMPLE = 2

# 치수를 직접 주는 옵션 → GarmentMeasurements 필드
_DIMENSION_OPTIONS = {
    "chest": "chest_girth_cm",
    "shoulder": "shoulder_width_cm",
    "sleeve": "sleeve_length_cm",
    "length": "body_length_cm",
}


class _Parser(argparse.ArgumentParser):
    """사용법 오류도 종료 코드 1로 끝낸다. argparse 기본값(2)은 '견본 없음'과 겹친다."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(EXIT_INPUT_ERROR, f"오류: {message}\n")


class _InputError(Exception):
    """명령어에 준 파일·값이 잘못됐다."""


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"정수여야 합니다: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"1 이상이어야 합니다: {text}")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description=__doc__.splitlines()[0])
    parser.add_argument("--samples", type=Path, required=True, help="견본집 JSON 파일")
    parser.add_argument("--garment", type=Path, help="GarmentVariant JSON 파일")
    for option in _DIMENSION_OPTIONS:
        parser.add_argument(f"--{option}", type=float, metavar="CM", help=f"의류 {option} 치수(cm)")
    parser.add_argument(
        "--rules", type=Path, help="검색 규칙 YAML (기본 config/sample_search_v0.yaml)"
    )
    parser.add_argument("--top", type=_positive_int, metavar="N", help="순위를 앞의 N개만 출력")
    return parser


def _read_json(path: Path, what: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise _InputError(f"{what} 파일을 읽을 수 없습니다: {path} ({error.strerror})") from error
    except ValueError as error:
        raise _InputError(f"{what} 파일이 JSON 형식이 아닙니다: {path} ({error})") from error


def _describe(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()
    )


def _load_garment(args: argparse.Namespace) -> GarmentMeasurements:
    given = {
        field: getattr(args, option)
        for option, field in _DIMENSION_OPTIONS.items()
        if getattr(args, option) is not None
    }
    if args.garment is not None and given:
        raise _InputError("--garment와 치수 옵션(--chest 등)은 함께 쓸 수 없습니다")
    if args.garment is None and not given:
        raise _InputError("--garment 파일이나 치수 옵션(--chest --shoulder 등) 중 하나를 주세요")
    try:
        if args.garment is not None:
            data = _read_json(args.garment, "의류")
            return GarmentVariant.model_validate(data).measurements
        return GarmentMeasurements(**given)
    except ValidationError as error:
        raise _InputError(f"의류 치수가 올바르지 않습니다: {_describe(error)}") from error


def _load_samples(path: Path) -> list[SampleCandidate]:
    data = _read_json(path, "견본집")
    if not isinstance(data, dict) or not isinstance(data.get("samples"), list):
        raise _InputError(f'견본집 파일에 "samples" 목록이 없습니다: {path}')
    try:
        return [SampleCandidate.model_validate(item) for item in data["samples"]]
    except ValidationError as error:
        raise _InputError(
            f"견본집 형식이 올바르지 않습니다: {path} ({_describe(error)})"
        ) from error


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as stop:  # argparse가 사용법 오류(1)나 --help(0)로 끝낸 경우
        return stop.code if isinstance(stop.code, int) else EXIT_OK
    try:
        garment = _load_garment(args)
        samples = _load_samples(args.samples)
        rules = load_search_rules(args.rules) if args.rules is not None else None
        result = search_sample(garment, samples, rules)
    except SampleSearchError as error:
        print(f"{error.code.value}: {error.message}", file=sys.stderr)
        return EXIT_NO_SAMPLE
    except (_InputError, ValueError) as error:  # ValueError: SearchRulesError
        print(f"오류: {error}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    output = result.model_dump(mode="json")
    if args.top is not None:
        output["ranking"] = output["ranking"][: args.top]
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
