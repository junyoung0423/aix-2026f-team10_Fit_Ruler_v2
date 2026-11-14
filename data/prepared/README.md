# 의류 준비 기록 (#6, C4)

등록 의류(`data/garments/`)마다 견본 검색(#5) → 고른 견본의 **복사본** 최적화(#6) 결과를 둔다. 3D 착장 어댑터(#8)와
A4 관리 화면이 읽는다.

| 폴더 | 내용 | 커밋 |
|---|---|---|
| `data/prepared/` (기본) | 시연용 의류 2벌(`tee_regular_m_navy_v1`, `tee_relaxed_l_black_v1`) | 한다 |
| `FIT_PREPARED_DIR` (예: `C:\dev\aihub\prepared`) | AI Hub 의류 | 하지 않는다(AI Hub 이용정책, `docs/decisions.md`) |

준비 폴더는 `FIT_PREPARED_DIR` > `data/prepared/` 순서로 정한다(의류 폴더 `FIT_GARMENTS_DIR`와 같은 방식).

## 만들기

```powershell
# 저장소 .venv. 맞춤은 GarmentCode용 Python 3.9 하위 프로세스(FIT_GC_PYTHON, docs/spikes/garmentcode-blender.md 6절)
python -m tools.prepare_garments                       # data/garments → data/prepared
python -m tools.prepare_garments --garments C:\dev\aihub\garments --out C:\dev\aihub\prepared --jobs 4
```

- 의류·견본·설정 해시가 기존 기록과 같으면 건너뛴다. `--force`일 때만 다시 만든다. `--only ID ...`로 일부만.
- 견본집(`data/samples/tee_gc_v1/`)은 읽기만 한다. 견본 design.yaml을 메모리에서 복사해 맞춘다.
- 맞춤: 가슴·소매·기장(`config/pattern/pattern_fit_v0.json`의 `fit_params`), 생성용 몸 `mean_all`. 어깨는 맞추지 않는다.
- 의류 1벌 맞춤에 수 분~십수 분 걸린다(C3 견본 맞춤 6~16분).

## 폴더 구조

```
{준비 폴더}/{garment_id}/
  prep.json             준비 기록 (아래 키)
  design.yaml           맞춘 GarmentCode 디자인 (준비 실패여도 맞춘 결과가 있으면 둔다)
  specification.json    맞춘 패턴 (GarmentCode가 만든 바이트 그대로)
```

## `prep.json`

| 키 | 뜻 |
|---|---|
| `schema` | `garment_prep_v0` |
| `garment_id`, `garment_file`, `garment_sha256` | 의류 ID, 의류 파일 이름과 sha256 |
| `status` | `READY`(준비 성공) 또는 `FAILED`(준비 실패) |
| `failure_reason` | 실패 코드. 성공이면 null. `GARMENT_INVALID`(의류 파일 형식), `SAMPLE_SEARCH_FAILED`(가슴·어깨가 없어 견본을 못 고름), `FIT_ERROR`(맞춤·생성 실패), `OUT_OF_TOLERANCE`(잔여 오차 1.0 cm 초과), `SELF_INTERSECTING`(패턴 자기 교차) |
| `failure_detail` | 실패 설명 한 줄(사람용) |
| `sample_id`, `search_score_cm` | 고른 견본 ID와 검색 점수(cm, `sample_search_v0`) |
| `search` | `{score_cm, exact_match, tie_broken, compared, skipped, runner_up}` (검색 결과 요약) |
| `sample_pattern_sha256`, `sample_measurements_cm` | 고른 견본(원본)의 패턴 해시와 치수 |
| `targets_cm` | 맞춘 목표 `{chest_girth_cm, sleeve_length_cm, body_length_cm}`. 의류에 없는 값은 null(맞추지 않음) |
| `notes` | 예: `기장 목표 없음: 견본 기장을 그대로 둠(판정 안 함)` (AI Hub 의류) |
| `fitted_measurements_cm` | 맞춘 패턴을 다시 잰 치수 `{chest_girth_cm, shoulder_width_cm, sleeve_length_cm, body_length_cm}` |
| `residuals_cm` | 잔여 오차 = 다시 잰 값 − 목표 (가슴·소매·기장, 목표가 없으면 null) |
| `tolerance_cm` | 허용 잔여 오차(1.0 cm, `pattern_fit_v0`). 늘리지 않는다 |
| `shoulder` | `{judged: false, garment_cm, sample_cm, diff_cm}`. 어깨는 맞추지 않고 판정하지 않는다. `diff_cm` = 의류 − 견본 |
| `design_params`, `fitted_params` | 맞춘 디자인 파라미터 값, 그중 이번에 조정한 파라미터 목록 |
| `params_at_bound`, `any_param_at_bound` | 조정한 파라미터가 GarmentCode 범위 끝에 걸렸는지 |
| `self_intersecting` | 패턴 자기 교차 여부 |
| `optimizer` | `{method, nfev, status, message, seconds}` |
| `generation_body` | 생성용 몸 `{body_yaml, sha256}` (`mean_all`) |
| `design`, `specification`, `pattern_sha256` | 파일 이름과 패턴 정규형 sha256(`pattern_measure.canonical_sha256`). 착장 어댑터가 이 해시로 패턴 파일을 확인한다 |
| `configs` | `{pattern_fit, sample_search, catalog}` 각각 `{path, name, version, sha256}` |
| `inputs_key` | 건너뛰기 판단용 입력 해시(의류·견본·설정·도구 버전) |
| `tool` | `{name, version}` |
| `seconds`, `created_at` | 걸린 시간(초), 만든 날짜(YYYY-MM-DD) |

검색 전에 실패한 기록(`GARMENT_INVALID`, `SAMPLE_SEARCH_FAILED`)에는 견본·치수 키가 없다.

## 준비 실패의 뜻

- 의류 파일의 `status`는 바꾸지 않는다. 핏 리포트는 패턴 없이도 된다.
- 준비 실패는 3D 착장만 못 한다는 뜻이다. 착장 어댑터는 `GARMENT_NOT_READY`로 실패한다.
