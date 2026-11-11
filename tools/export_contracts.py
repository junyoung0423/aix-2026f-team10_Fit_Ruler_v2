"""계약 모델에서 JSON 스키마와 API 응답 예시를 만든다 (#1).

    python -m tools.export_contracts          # docs/api/schemas, docs/api/examples를 다시 만든다
    python -m tools.export_contracts --check  # 저장된 파일이 최신인지만 확인한다 (CI 테스트도 확인)

손으로 쓰는 예시는 `fitting_request.json`, `fit_request.json`, `garment.json` 셋이다.
`fit_report.json`은 #2 핏 계산기(`RuleFitCalculator`, 기본 기준표)로 위 요청·의류를 계산해 만든다.
작업 상태 예시(`fitting_status_*.json`)는 핏 경로에 같은 계산기를, 영상 경로에 가짜 어댑터를 넣어
피팅 흐름을 실제로 돌려서 만든다.
핏 API 예시(`garment_list.json`, `api_error_*.json`)는 서버와 같은 코드(`server.errors`)로 만든다.
견본 검색 예시(`sample_search_result.json`)는 예시 의류(`garment.json`)와 합성 견본집
(`tests/fixtures/catalog/samples_v0.json`)으로 실제 검색(`fitting.catalog.search`)을 돌려 만든다.
모든 예시는 시연용이며 실제 사용자 결과와 섞지 않는다.
"""

import argparse
import json
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from fitting.adapters.fake import FakeDraper, FakeRealizer, FakeValidator, fake_ports
from fitting.catalog.search import (
    SampleCandidate,
    SampleSearchResult,
    load_search_rules,
    search_sample,
)
from fitting.domain import (
    ApiError,
    BodyInput,
    ErrorCode,
    FitReport,
    FitRequest,
    FittingRequest,
    FittingStatus,
    GarmentList,
    GarmentListItem,
    GarmentStatus,
    GarmentVariant,
    JobStatus,
    Mode,
    QualityStatus,
    ReportPart,
    Stage,
    StageProgress,
    StageStatus,
    VisualPart,
)
from fitting.fit import FitEngineError, RuleFitCalculator, compute_report
from fitting.ports import PortError, PortRequest, PortResult
from fitting.services.fitting import FittingPorts, run_fitting
from server.errors import (
    garment_not_found_error,
    garment_not_ready_error,
    input_invalid_error,
    job_not_found_error,
)

EXAMPLE_JOB_ID = "fit_sample_001"
EXAMPLE_CREATED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)

SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "fitting_request": FittingRequest,
    "fit_request": FitRequest,
    "body_input": BodyInput,
    "garment_variant": GarmentVariant,
    "fit_report": FitReport,
    "fitting_status": FittingStatus,
    "garment_list": GarmentList,
    "api_error": ApiError,
    "sample_search_result": SampleSearchResult,
    "port_request": PortRequest,
    "port_result": PortResult,
    "port_error": PortError,
}

# 손으로 쓰는 예시 -> 따라야 하는 스키마
HAND_WRITTEN_EXAMPLES = {
    "fitting_request.json": "fitting_request",
    "fit_request.json": "fit_request",
    "garment.json": "garment_variant",
}


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def api_dir(root: Path) -> Path:
    return root / "docs" / "api"


def _dump(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _read(root: Path, name: str) -> str:
    return (api_dir(root) / "examples" / name).read_text(encoding="utf-8")


def build_report(root: Path) -> FitReport:
    """예시 요청·의류를 실제 핏 계산기로 계산한 리포트 (`fit_report.json`)."""
    request = FittingRequest.model_validate_json(_read(root, "fitting_request.json"))
    garment = GarmentVariant.model_validate_json(_read(root, "garment.json"))
    return RuleFitCalculator().calculate(
        request.to_body_input(), garment, job_id=EXAMPLE_JOB_ID, created_at=EXAMPLE_CREATED_AT
    )


def _status_cases() -> dict[str, tuple[Mode, bool, Callable[[], dict]]]:
    """예시 파일 이름 -> (실행 방식, 실사화 동의, 바꿀 가짜 어댑터)."""
    return {
        "fitting_status_succeeded.json": (Mode.FULL, True, dict),
        "fitting_status_no_consent.json": (Mode.FULL, False, dict),
        "fitting_status_report_only.json": (Mode.REPORT_ONLY, True, dict),
        "fitting_status_realize_failed.json": (
            Mode.FULL,
            True,
            lambda: {"realize": FakeRealizer(fail_with=ErrorCode.REALIZE_TIMEOUT)},
        ),
        "fitting_status_drape_failed.json": (
            Mode.FULL,
            True,
            lambda: {"drape": FakeDraper(fail_with=ErrorCode.DRAPE_INVALID)},
        ),
        "fitting_status_validation_failed.json": (
            Mode.FULL,
            True,
            lambda: {"validate": FakeValidator(quality=QualityStatus.FAIL)},
        ),
    }


def build_running_status(report: FitReport) -> FittingStatus:
    """진행 중 화면용: 리포트는 끝났고 영상은 실사화 단계를 실행 중이다 (명세서 11장 예시)."""
    progress = [
        (Stage.FIT_CALCULATE, StageStatus.SUCCEEDED),
        (Stage.FIT_REPORT, StageStatus.SUCCEEDED),
        (Stage.BODY_FIT, StageStatus.SUCCEEDED),
        (Stage.DRAPE, StageStatus.SUCCEEDED),
        (Stage.RENDER, StageStatus.SUCCEEDED),
        (Stage.REALIZE, StageStatus.RUNNING),
        (Stage.VALIDATE, StageStatus.WAITING),
    ]
    return FittingStatus(
        job_id=EXAMPLE_JOB_ID,
        status=JobStatus.RUNNING,
        report=ReportPart(
            status=StageStatus.SUCCEEDED,
            rules_version=report.rules_version,
            regions=report.regions,
            summary=report.summary,
            warnings=report.warnings,
        ),
        visual=VisualPart(
            status=StageStatus.RUNNING,
            stage=Stage.REALIZE,
            drape_preview_artifact_id=f"{EXAMPLE_JOB_ID}_drape_preview",
            render_artifact_id=f"{EXAMPLE_JOB_ID}_render_clip",
        ),
        stages=[StageProgress(stage=s, status=st, attempt_no=1) for s, st in progress],
    )


def build_statuses(root: Path) -> dict[str, FittingStatus]:
    request = FittingRequest.model_validate_json(_read(root, "fitting_request.json"))
    garment = GarmentVariant.model_validate_json(_read(root, "garment.json"))
    report = build_report(root)

    statuses = {"fitting_status_running.json": build_running_status(report)}
    for name, (mode, consent, overrides) in _status_cases().items():
        ports: FittingPorts = fake_ports(fit=RuleFitCalculator(), **overrides())
        outcome = run_fitting(
            request.model_copy(update={"mode": mode, "realize_consent": consent}),
            garment,
            ports,
            job_id=EXAMPLE_JOB_ID,
            created_at=EXAMPLE_CREATED_AT,
        )
        statuses[name] = outcome.status
    return statuses


def build_api_examples(root: Path) -> dict[str, BaseModel]:
    """핏 API(#2) 응답 예시: 의류 목록과 세 가지 오류."""
    garment = GarmentVariant.model_validate_json(_read(root, "garment.json"))
    fit_request = FitRequest.model_validate_json(_read(root, "fit_request.json"))

    # 범위 밖 가슴둘레와 문자열 숫자 키를 보낸 요청
    bad = fit_request.model_dump(mode="json")
    bad["measurements"].update({"chest_cm": 200.0, "height_cm": "175"})
    try:
        FitRequest.model_validate(bad)
    except ValidationError as error:
        input_invalid = input_invalid_error(error.errors())
    else:  # pragma: no cover - 예시 요청이 틀리지 않으면 예시를 만들 수 없다
        raise AssertionError("입력 오류 예시 요청이 검증을 통과했습니다")

    draft = garment.model_copy(update={"status": GarmentStatus.DRAFT})
    try:
        compute_report(fit_request.to_body_input(), draft, job_id=EXAMPLE_JOB_ID)
    except FitEngineError as error:
        not_ready = garment_not_ready_error(error)
    else:  # pragma: no cover
        raise AssertionError("DRAFT 의류가 계산되었습니다")

    return {
        "garment_list.json": GarmentList(
            garments=[GarmentListItem(garment=garment, is_demo=garment.is_demo)]
        ),
        "api_error_input_invalid.json": input_invalid,
        "api_error_garment_not_found.json": garment_not_found_error(),
        "api_error_garment_not_ready.json": not_ready,
        "api_error_job_not_found.json": job_not_found_error(),
    }


def build_search_example(root: Path) -> SampleSearchResult:
    """예시 의류(`garment.json`)와 합성 견본집으로 실제 검색을 돌린 결과 (기준 사례 1)."""
    garment = GarmentVariant.model_validate_json(_read(root, "garment.json"))
    catalog = json.loads(
        (root / "tests" / "fixtures" / "catalog" / "samples_v0.json").read_text(encoding="utf-8")
    )
    samples = [SampleCandidate.model_validate(item) for item in catalog["samples"]]
    rules = load_search_rules(root / "config" / "sample_search_v0.yaml")
    return search_sample(garment.measurements, samples, rules)


def build_artifacts(root: Path) -> dict[Path, str]:
    """저장소 루트 기준으로 만들어야 하는 파일과 그 내용."""
    files: dict[Path, str] = {}
    for name, model in SCHEMA_MODELS.items():
        path = api_dir(root) / "schemas" / f"{name}.schema.json"
        files[path] = _dump(model.model_json_schema())
    files[api_dir(root) / "examples" / "fit_report.json"] = _dump(
        build_report(root).model_dump(mode="json")
    )
    for name, status in build_statuses(root).items():
        files[api_dir(root) / "examples" / name] = _dump(status.model_dump(mode="json"))
    for name, model in build_api_examples(root).items():
        files[api_dir(root) / "examples" / name] = _dump(model.model_dump(mode="json"))
    files[api_dir(root) / "examples" / "sample_search_result.json"] = _dump(
        build_search_example(root).model_dump(mode="json")
    )
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="파일을 쓰지 않고 최신인지 확인한다")
    parser.add_argument("--root", type=Path, default=None, help="저장소 루트 (기본: 자동 탐지)")
    args = parser.parse_args(argv)

    root = args.root or default_repo_root()
    files = build_artifacts(root)
    stale = [
        path
        for path, content in files.items()
        if not path.exists() or path.read_text(encoding="utf-8") != content
    ]
    if args.check:
        if stale:
            print("최신이 아닌 파일이 있습니다. 아래 명령을 실행하세요:")
            print("  python -m tools.export_contracts")
            for path in stale:
                print(f"  - {path.relative_to(root).as_posix()}")
            return 1
        print("계약 스키마와 예시가 최신입니다.")
        return 0

    for path in stale:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(files[path], encoding="utf-8", newline="\n")
        print(f"갱신: {path.relative_to(root).as_posix()}")
    if not stale:
        print("바꿀 파일이 없습니다. 이미 최신입니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
