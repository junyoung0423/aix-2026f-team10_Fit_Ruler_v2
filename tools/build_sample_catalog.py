"""반팔 티셔츠 패턴 견본집을 만든다 (#4, C3). GarmentCode용 Python 3.9로 실행한다.

    <venv>\\Scripts\\python.exe -m tools.build_sample_catalog \\
        [--config config/samples/tee_gc_v1.yaml] [--out data/samples/<name>] [--force] \\
        [--gc-root <GarmentCode>] [--jobs N]

1. 설정(`config/samples/*.yaml`)을 `fitting.catalog.sample_plan`으로 검사한다(ID·style 구간·번호).
2. 견본마다 `pattern_fit.fit_design`으로 디자인 파라미터(shirt.width·shirt.length·sleeve.length)를
   목표 가슴·기장·소매에 맞춘다. 생성용 몸은 패턴 맞춤 설정의 body_yaml(mean_all)이다.
3. 맞춘 디자인 yaml로 새 프로세스에서 패턴을 만들고(`pattern_fit generate`), 그 파일을 다시 읽어
   `pattern_measure`로 잰다. 한 프로세스에서 여러 번 만든 패턴은 봉제선 순서가 바뀔 수 있어서다.
4. `{out}/{sample_id}/`에 design.yaml·specification.json·sample.json을, `{out}/`에 catalog.json·
   SOURCE.md를 쓴다. 키 이름은 `data/samples/README.md`에 있다.

- 가슴·기장·소매 중 하나라도 허용 오차(패턴 맞춤 설정 tolerance_cm, 1.0 cm) 밖이거나 패턴이
  자기 교차하면 그 견본은 빼고 이유를 catalog.json의 excluded와 화면에 남긴다.
  허용 오차를 늘리지 않는다.
- 남은 견본이 MIN_SAMPLES(10)개보다 적으면 결과를 쓴 뒤 종료 코드 1로 끝난다.
- 출력 폴더가 있으면 덮어쓰지 않고 멈춘다(종료 코드 1). `--force`일 때만 지우고 다시 만든다.
- GarmentCode 경로: --gc-root > 환경변수 FIT_RULER_GC_ROOT > C:\\dev\\GarmentCode
  (run_drape와 같다).

종료 코드: 0 성공, 1 출력 폴더 있음·견본 부족·생성 실패, 2 설정 오류.
이 파일은 Python 3.9에서 돈다. 3.10 문법을 쓰지 않는다.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

import yaml

from fitting.adapters.garmentcode import pattern_fit as pf
from fitting.adapters.garmentcode import pattern_measure as pm
from fitting.catalog.sample_plan import TARGET_FIELDS, SamplePlanError, parse_sample_plan

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "config" / "samples" / "tee_gc_v1.yaml"
DEFAULT_OUT_PARENT = REPO_ROOT / "data" / "samples"
MIN_SAMPLES = 10
MEASURE_FIELDS = (
    "chest_girth_cm",
    "waist_girth_cm",
    "shoulder_width_cm",
    "sleeve_length_cm",
    "body_length_cm",
)
BODY_CHEST_MATCH_CM = 0.1  # 설정의 generation_body_chest_cm와 몸 yaml bust 차이 한도
GC_REPO_URL = "https://github.com/maria-korosteleva/GarmentCode"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2


def rel_repo(p):
    """기록용 경로: 저장소 안이면 저장소 기준 상대경로(/), 아니면 그대로."""
    p = Path(p).resolve()
    try:
        return p.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(p)


def rel_gc(p, gc_root):
    p = Path(p).resolve()
    try:
        return p.relative_to(Path(gc_root).resolve()).as_posix()
    except ValueError:
        return str(p)


def git_head(path):
    try:
        return subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def write_text(path, text):
    # Path.write_text(newline=)는 Python 3.10부터라 open을 쓴다(이 파일은 3.9에서 돈다)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def write_json(path, data):
    write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def load_plan(config_path):
    data = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    return parse_sample_plan(data)


def check_generation_body(plan, fit_cfg):
    """설정의 생성용 몸이 패턴 맞춤 설정의 body_yaml과 같은지(이름, 가슴둘레) 확인한다."""
    body_yaml = Path(fit_cfg["body_yaml"])
    problems = []
    if body_yaml.stem != plan["generation_body"]:
        problems.append(
            f"generation_body {plan['generation_body']} != pattern fit body_yaml {body_yaml.name}"
        )
    bust = yaml.safe_load(body_yaml.read_text(encoding="utf-8"))["body"]["bust"]
    if abs(bust - plan["generation_body_chest_cm"]) > BODY_CHEST_MATCH_CM:
        problems.append(
            f"generation_body_chest_cm {plan['generation_body_chest_cm']} != body bust {bust}"
        )
    return bust, problems


def generate_in_new_process(design_yaml, body_yaml, work, gc_root):
    """맞춘 디자인 yaml로 새 프로세스에서 패턴을 만든다. 반환: specification JSON 경로."""
    env = dict(
        os.environ,
        PYTHONIOENCODING="utf-8",
        PYTHONPATH=os.pathsep.join([str(REPO_ROOT), str(gc_root)]),
    )
    cmd = [
        sys.executable,
        "-m",
        "fitting.adapters.garmentcode.pattern_fit",
        "generate",
        str(design_yaml),
        str(body_yaml),
        str(work),
        "--gc-root",
        str(gc_root),
    ]
    p = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if p.returncode != 0:
        raise RuntimeError(f"generate failed (rc={p.returncode}): {p.stderr.strip()[-2000:]}")
    spec = Path(work) / "pattern_specification.json"
    if not spec.is_file():
        raise RuntimeError(f"generate wrote no pattern_specification.json in {work}")
    return spec


def measure_spec_file(path):
    """specification.json을 다시 읽어 잰 값(cm)과 정규형 sha256."""
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    m, _ = pm.measure(spec)
    return m, pm.canonical_sha256(spec)


def rounded_measurements(m):
    """견본 치수(cm, 소수 셋째 자리). 허리는 견본에 측정 정의가 없어 null이다."""
    return {f: (None if f == "waist_girth_cm" else round(float(m[f]), 3)) for f in MEASURE_FIELDS}


def failure_reason(res):
    bad = [
        f"{f} {r['pattern']} (목표 {r['target']}, 오차 {r['error']:+.3f})"
        for f, r in res["measurements"].items()
        if r["ok"] is False
    ]
    if res["self_intersecting"]:
        bad.append("패턴 자기 교차")
    at = [n for n, v in res["params_at_bound"].items() if v]
    reason = "허용 오차 밖: " + ", ".join(bad) if bad else "FIT_FAILED"
    if at:
        reason += f" (범위 끝에 닿은 파라미터: {', '.join(at)})"
    return reason


SOURCE_TEMPLATE = """# 출처: GarmentCode (견본집 {name})

이 폴더의 패턴은 GarmentCode로 생성한 것이다. 우리가 손으로 고치지 않았다.
`python -m tools.build_sample_catalog --config {config_path}`가 만들었다.

- 저장소: {url}
- 커밋: {commit_short} ({commit})
- 라이선스: MIT (아래 전문)

| 파일 | 출처 |
|---|---|
| `{{sample_id}}/design.yaml` | GarmentCode 기본 디자인 값 `{design_yaml}`(sha256 `{design_sha_short}…`)에서 `{params}`만 목표 치수에 맞게 바꾼 사본(`pattern_fit.fit_design`) |
| `{{sample_id}}/specification.json` | 위 yaml + 기준 몸 `{body_yaml}`(sha256 `{body_sha_short}…`)로 GarmentCode가 생성한 패턴(`pattern_fit generate`, {created_at}). 파일 바이트를 그대로 둔다 |
| `{{sample_id}}/sample.json`, `catalog.json` | 위 패턴을 `pattern_measure`로 잰 값과 생성 기록. 우리가 만든 파일이다 |

이 패턴의 치수는 GarmentCode 기준 몸(`mean_all`, 실제 사용자가 아님)으로 만든 견본 치수이며 의류 실측이 아니다.
GarmentCode는 패널·봉제선 저장 순서가 실행마다 바뀔 수 있다. 같은 패턴인지는 파일 sha256 대신
정규형 sha256(`pattern_measure.canonical_sha256`, sample.json의 `pattern_sha256`)으로 확인한다.

## MIT License (GarmentCode LICENSE 전문)

```
{license}
```
"""  # noqa: E501 (마크다운 표 줄은 나누지 않는다)


_WORKER = {}  # 작업 프로세스마다 Generator 하나 (--jobs)


def _fit_one(args):
    """견본 하나를 맞춘다. 작업 프로세스에서 돈다(모듈 맨 위 함수라 pickle된다)."""
    gc_root, body_yaml, design_yaml, fit_cfg, sid, targets = args
    if "gen" not in _WORKER:
        pf.enter_gc_root(gc_root)
        _WORKER["base"] = pf.load_design(design_yaml)
        _WORKER["gen"] = pf.Generator(body_yaml, _WORKER["base"])
    t0 = time.time()
    res = pf.fit_design(targets, _WORKER["base"], body_yaml, fit_cfg, generator=_WORKER["gen"])
    res["seconds"] = round(time.time() - t0, 1)
    return sid, res


def fit_all(samples, gc_root, body_yaml, design_yaml, fit_cfg, jobs, log):
    """견본마다 fit_design. 반환: {sample_id: 결과}. jobs > 1이면 프로세스 jobs개로 나눈다.
    견본끼리 독립이고 각 맞춤은 같은 입력이면 같은 결과다(최적화에 난수 없음)."""
    tasks = [
        (
            str(gc_root),
            str(body_yaml),
            str(design_yaml),
            fit_cfg,
            s["id"],
            {f: float(s[f]) for f in TARGET_FIELDS},
        )
        for s in samples
    ]
    for t in tasks:
        log(f"[{t[4]}] fit {t[5]}", flush=True)
    results = {}
    if jobs <= 1:
        for t in tasks:
            sid, res = _fit_one(t)
            results[sid] = res
            log(f"[{sid}] {res['status']} {res['seconds']} s", flush=True)
        return results
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        for sid, res in ex.map(_fit_one, tasks):
            results[sid] = res
            log(f"[{sid}] {res['status']} {res['seconds']} s", flush=True)
    return results


def build(plan, config_path, out, gc_root, fit_cfg, fit_rec, log=print, jobs=1):
    """견본집을 out에 쓴다. 반환: (catalog dict, 남은 견본 수)."""
    gc_root = Path(gc_root).resolve()
    body_yaml = Path(fit_cfg["body_yaml"])
    design_yaml = Path(fit_cfg["design_yaml"])
    bust, problems = check_generation_body(plan, fit_cfg)
    if problems:
        raise SamplePlanError(problems)
    created_at = date.today().isoformat()
    commit = git_head(gc_root)
    config_rec = {
        "path": rel_repo(config_path),
        "name": plan["name"],
        "version": plan["version"],
        "sha256": pf.sha256_file(config_path),
    }
    body_rec = {
        "name": plan["generation_body"],
        "body_yaml": rel_gc(body_yaml, gc_root),
        "body_yaml_sha256": pf.sha256_file(body_yaml),
        "chest_cm": bust,
    }
    gc_rec = {
        "commit": commit,
        "base_design_yaml": rel_gc(design_yaml, gc_root),
        "base_design_sha256": pf.sha256_file(design_yaml),
    }
    tol = fit_cfg["tolerance_cm"]

    pf.enter_gc_root(gc_root)
    fits = fit_all(plan["samples"], gc_root, body_yaml, design_yaml, fit_cfg, jobs, log)
    out.mkdir(parents=True, exist_ok=False)
    kept, excluded = [], []
    work_root = Path(tempfile.mkdtemp(prefix="build_sample_catalog_"))
    try:
        for s in plan["samples"]:
            sid = s["id"]
            targets = {f: float(s[f]) for f in TARGET_FIELDS}
            res = fits[sid]
            if res["status"] != "FITTED":
                reason = failure_reason(res)
                log(f"[{sid}] 제외: {reason}", flush=True)
                excluded.append({"sample_id": sid, "reason": reason})
                continue
            d = out / sid
            d.mkdir()
            write_text(d / "design.yaml", pf.design_yaml_text(res["design"]))
            work = work_root / sid
            work.mkdir()
            spec_tmp = generate_in_new_process(d / "design.yaml", body_yaml, work, gc_root)
            shutil.copyfile(spec_tmp, d / "specification.json")
            m, sha = measure_spec_file(d / "specification.json")
            # + 0.0: -0.0을 0.0으로
            residuals = {f: round(float(m[f]) - targets[f], 3) + 0.0 for f in TARGET_FIELDS}
            over = [f for f in TARGET_FIELDS if abs(float(m[f]) - targets[f]) > tol]
            if over:
                reason = f"새 프로세스로 만든 패턴의 오차가 허용 오차 밖: {over} {residuals}"
                log(f"[{sid}] 제외: {reason}", flush=True)
                excluded.append({"sample_id": sid, "reason": reason})
                shutil.rmtree(d)
                continue
            measured = rounded_measurements(m)
            base_shoulder = float(res["default_measured_cm"]["shoulder_width_cm"])
            sample = {
                "catalog": plan["name"],
                "sample_id": sid,
                "style": s["style"],
                "targets_cm": targets,
                "measurements_cm": measured,
                "residuals_cm": residuals,
                "tolerance_cm": tol,
                "shoulder": {
                    "judged": False,
                    "shoulder_width_cm": measured["shoulder_width_cm"],
                    "base_design_shoulder_width_cm": round(base_shoulder, 3),
                    "diff_from_base_design_cm": round(
                        float(m["shoulder_width_cm"]) - base_shoulder, 3
                    ),
                },
                "pattern_sha256": sha,
                "files": {"design": "design.yaml", "specification": "specification.json"},
                "design_params": {k: round(v, 6) for k, v in res["fitted_params"].items()},
                "params_at_bound": res["params_at_bound"],
                "self_intersecting": res["self_intersecting"],
                "optimizer": {
                    k: res["optimizer"][k] for k in ("method", "nfev", "status", "message")
                },
                "generation_body": body_rec,
                "garmentcode": gc_rec,
                "pattern_fit_config": fit_rec,
                "sample_config": config_rec,
                "created_at": created_at,
            }
            write_json(d / "sample.json", sample)
            kept.append(sample)
            log(f"[{sid}] ok measured {measured} residual {residuals} sha {sha[:12]}", flush=True)
    finally:
        shutil.rmtree(work_root, ignore_errors=True)

    catalog = {
        "catalog": plan["name"],
        "version": plan["version"],
        "note": (
            "GarmentCode 반팔 티셔츠 견본집. samples는 견본 검색 "
            "(fitting.catalog.search.SampleCandidate) 형식이다. 단위 cm, 가슴은 둘레, "
            "허리는 견본에 측정 정의가 없어 null이다. "
            "견본별 생성 기록은 {sample_id}/sample.json에 있다."
        ),
        "sample_config": config_rec,
        "pattern_fit_config": fit_rec,
        "generation_body": body_rec,
        "generation_body_chest_cm": plan["generation_body_chest_cm"],
        "style_bands": plan["style_bands"],
        "garmentcode": gc_rec,
        "tolerance_cm": tol,
        "created_at": created_at,
        "excluded": excluded,
        "samples": [
            {"sample_id": s["sample_id"], "measurements": s["measurements_cm"]} for s in kept
        ],
    }
    write_json(out / "catalog.json", catalog)
    license_text = (gc_root / "LICENSE").read_text(encoding="utf-8").rstrip("\n")
    write_text(
        out / "SOURCE.md",
        SOURCE_TEMPLATE.format(
            name=plan["name"],
            config_path=config_rec["path"],
            url=GC_REPO_URL,
            commit=commit,
            commit_short=(commit or "")[:7],
            design_yaml=gc_rec["base_design_yaml"],
            design_sha_short=gc_rec["base_design_sha256"][:8],
            params="`, `".join(fit_cfg["fit_params"]),
            body_yaml=body_rec["body_yaml"],
            body_sha_short=body_rec["body_yaml_sha256"][:8],
            created_at=created_at,
            license=license_text,
        ),
    )
    return catalog, len(kept)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=str(DEFAULT_CONFIG), help="견본집 설정 yaml")
    ap.add_argument("--out", help="출력 폴더 (기본: data/samples/<설정 name>)")
    ap.add_argument("--force", action="store_true", help="출력 폴더가 있으면 지우고 다시 만든다")
    ap.add_argument("--gc-root", default=str(pf.DEFAULT_GC_ROOT))
    ap.add_argument(
        "--jobs", type=int, default=1, help="패턴 맞춤을 나눠 돌릴 프로세스 수 (기본 1)"
    )
    a = ap.parse_args(argv)

    config_path = Path(a.config).resolve()
    gc_root = Path(a.gc_root).resolve()
    try:
        plan = load_plan(config_path)
        fit_cfg, fit_rec = pf.load_fit_config(
            pf.resolve_path(plan["pattern_fit_config"], REPO_ROOT), gc_root=gc_root
        )
    except (OSError, ValueError, KeyError) as e:  # SamplePlanError는 ValueError
        print(f"설정 오류: {e}", file=sys.stderr)
        return EXIT_CONFIG
    out = Path(a.out).resolve() if a.out else (DEFAULT_OUT_PARENT / plan["name"]).resolve()
    if out.exists() and any(out.iterdir()):
        if not a.force:
            print(
                f"출력 폴더가 있어 덮어쓰지 않습니다(--force로 다시 만든다): {out}", file=sys.stderr
            )
            return EXIT_FAILED
        shutil.rmtree(out)
    elif out.exists():
        out.rmdir()

    try:
        catalog, n = build(plan, config_path, out, gc_root, fit_cfg, fit_rec, jobs=a.jobs)
    except SamplePlanError as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        return EXIT_CONFIG
    except RuntimeError as e:
        print(f"생성 실패: {e}", file=sys.stderr)
        return EXIT_FAILED
    print(f"견본 {n}개 -> {out}")
    for x in catalog["excluded"]:
        print(f"  제외 {x['sample_id']}: {x['reason']}")
    if n < MIN_SAMPLES:
        print(f"견본이 {MIN_SAMPLES}개보다 적습니다: {n}", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
