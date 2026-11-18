# 어댑터 연결 안내 (C·D용)

피팅의 영상 경로는 5단계(몸 → 착장 → 렌더 → 실사화 → 검증)이고, 단계마다 **포트**(`fitting/ports/interfaces.py`)를 구현한 어댑터가 하나씩 있습니다. 어느 단계를 가짜로 돌리고 어느 단계를 실제 어댑터로 돌릴지는 설정 파일 한 줄로 정합니다. **자기 어댑터를 연결할 때 `server/`를 고치지 않습니다.**

| 설정 키 | 포트 (메서드) | 받는 산출물 → 만드는 산출물 | 담당 |
|---|---|---|---|
| `body` | `BodyBuilder.build_body(request, body)` | → `body_mesh` | D3 |
| `drape` | `Draper.drape(request, garment)` | `body_mesh` → `drape_result`, `drape_preview` | C4 |
| `render` | `Renderer.render(request, garment)` | `drape_result` → `render_clip` | C5 |
| `realize` | `Realizer.realize(request, garment)` | `render_clip` → `video` | D4 |
| `validate` | `Validator.validate(request, garment)` | `render_clip`, `video` → `frame`, `render_frame` | D5·D6 |

핏 계산기는 설정 대상이 아닙니다. 항상 실제 `RuleFitCalculator`입니다.

## 설정 파일과 고르는 순서

`config/adapters/*.yaml`:

```yaml
# config/adapters/default.yaml — 서버와 `python -m tools.run_fitting_once`의 기본값
version: adapters_v0
ports:
  body: fake                                  # 가짜 어댑터
  drape: "my.package.drape:make_draper"       # "파이썬.모듈.경로:팩토리이름" — 실제 어댑터
  render: fake
  realize: fake
  validate: fake
```

- 값은 `fake` 또는 `"모듈:팩토리"` 둘 중 하나입니다. 다섯 키가 모두 있어야 하고 모르는 키는 거절합니다.
- `fake.yaml`은 다섯 개 모두 `fake`이고 내용이 바뀌지 않습니다(테스트·CI·시연 연습용). `default.yaml`은 실제 어댑터가 머지될 때 담당자가 자기 줄을 바꿉니다.
- 고르는 순서: **함수 인자(`--adapters`, `create_app(adapters=…)`) > 환경변수 `FIT_ADAPTERS` > `default`**. 값이 `default`·`fake` 같은 이름이면 `config/adapters/{이름}.yaml`, `/`가 있거나 `.yaml`로 끝나면 그 경로의 파일입니다.
- **설정이 틀리면 가짜로 대신하지 않고 시작을 멈춥니다.** 모듈이 없거나, 팩토리가 없거나, 팩토리가 예외를 던지거나, 돌려준 객체에 필요한 메서드가 없으면 어느 포트의 어느 값이 왜 안 됐는지 한국어로 알려 줍니다(여러 개면 모아서). 실패를 성공처럼 보이게 하지 않으려는 것입니다(#12).

## 팩토리 약속

```python
def make_xxx(settings: AdapterSettings) -> 포트 객체    # BodyBuilder / Draper / Renderer / Realizer / Validator
```

`AdapterSettings`(`server/wiring.py`)는 바꿀 수 없는 설정 묶음입니다.

| 필드 | 출처 | 뜻 |
|---|---|---|
| `artifacts_dir` | 서버의 산출물 폴더(`FIT_ARTIFACTS_DIR`). `run_fitting_once`는 결과 폴더 안 `artifacts/` | 산출물을 **`{artifact_id}.{확장자}`로 여기에 쓴다** |
| `work_dir` | `FIT_WORK_DIR`, 기본 `artifacts/work/` | 중간 파일(.blend, 메시 등). 작업별 하위 폴더는 어댑터가 `request.job_id`로 만든다 |
| `blender_path` | `BLENDER_PATH`, 없으면 `BLENDER_EXE` | |
| `smpl_model_dir` | `SMPL_MODEL_DIR` | |
| `fal_key_present` | `FAL_KEY`가 비어 있지 않은가 | 키 **값은 넣지 않습니다**(로그·출력에 새지 않게). 필요하면 어댑터가 환경변수 `FAL_KEY`를 직접 읽습니다 |
| `repo_root` | | `config/` 등을 찾을 때 |

- 필요한 것이 없으면(예: Blender 경로가 없음) 팩토리가 `AdapterConfigError("…한국어 한 줄…")`를 던집니다. `from server.wiring import AdapterConfigError`.
- 팩토리는 어댑터 본체와 **별도 파일**(예: `fitting/adapters/blender/factory.py`)에 두면 어댑터 본체가 `server/`를 import하지 않습니다.
- 환경변수 이름은 `.env.example` 기준입니다. `BLENDER_PATH`가 없으면 기존 C 코드의 `BLENDER_EXE`도 받습니다. 기존 코드는 고치지 않아도 됩니다.

## 산출물 파일 이름 규칙

- 산출물 ID는 `{request.job_id}_{역할}` 같은 문자열이고 `^[A-Za-z0-9_-]{1,128}$`만 받습니다. 역할 이름과 순서는 `fitting/ports/interfaces.py`의 `BODY_OUTPUTS`·`DRAPE_OUTPUTS`·`RENDER_OUTPUTS`·`REALIZE_OUTPUTS`·`VALIDATE_OUTPUTS`가 정합니다(`PortResult.output_artifact_ids`가 이 순서).
- 파일은 `artifacts_dir/{artifact_id}.{확장자}`로 씁니다(예: 3D 미리보기 `…_drape_preview.png`, 렌더 클립·실사 영상 `….mp4`, 대표 프레임 `….png`). 서버의 `GET /api/artifacts/{artifact_id}`가 이 파일을 확장자로 정한 `Content-Type`으로 줍니다.
- **결과를 돌려주기 전에 파일을 다 쓰세요.** 상태에 ID가 보일 때는 이미 열 수 있어야 합니다.

## 실패 처리 약속

- 성공하면 `PortResult`(`schema_version`, `output_artifact_ids`, `metrics`, `tool_versions`)를 돌려줍니다. 산출물을 만드는 단계(몸·착장·렌더·실사화)는 `output_artifact_ids`가 비어 있으면 안 됩니다.
- 실패하면 `PortFailure(PortError(code=…, stage=…, retryable=…, safe_message=…))`를 던집니다. 화면에는 `safe_message`와 오류 코드만 보이고 내부 경로·원문은 `internal_details_ref`가 가리키는 기록에만 둡니다.
- **대체 결과를 성공으로 돌려주지 않습니다.** 못 만들었으면 실패를 던집니다(실사화 대신 렌더 영상을 돌려주는 식으로 성공 처리하지 않기).
- `PortFailure`가 아닌 예상 밖 예외는 서버가 그 단계의 실패(`FAILED`)로 바꿔 주지만 원인이 로그에만 남습니다. 예상되는 실패는 `PortFailure`로 던지세요.
- **시간 제한 (B5)**: `PortRequest.timeout_seconds`가 그 단계의 시간 제한입니다(`config/pipeline_v0.yaml`). 어댑터는 이 안에서 **스스로 끝내고**, Blender 같은 외부 프로그램은 어댑터가 정리합니다. 제한 + 그 단계의 감시 여유(`watchdog_grace_s`, 기본 60초·착장 660초·실사화 120초)가 지나도 돌아오지 않으면 서비스가 그 단계를 실패로 끝내고 늦게 온 결과는 버리지만, 멈춘 호출 자체는 끝내지 못합니다(파이썬은 스레드를 강제로 끝낼 수 없습니다).
- **정상적으로 `timeout_seconds`를 넘길 수 있는 단계**(예: 착장의 분석)는 B에게 알려 `config/pipeline_v0.yaml`의 그 단계 `watchdog_grace_s`를 키웁니다. 안 그러면 서비스가 정상 실행을 실패로 끊습니다.
- **`retryable=true`는 `external_request_id`가 있을 때만** 씁니다. 서비스는 이 둘이 모두 있는 실패만 같은 요청 ID로 다시 조회합니다(`config/pipeline_v0.yaml`의 `requery`, 최대 2번). 요청 ID가 없는 실패는 `retryable=false`입니다.
- **같은 `job_id`로 다시 불리면 재조회로 처리합니다.** 외부 생성 요청을 이미 접수했다면 다시 제출하지 말고 기록의 요청 ID로 상태·결과만 받으세요(생성 비용이 다시 나가지 않게). 다시 부르는 쪽은 `attempt_id`만 바꿉니다.

## 내 어댑터 연결하는 법

1. **팩토리를 만듭니다.** `make_xxx(settings) -> 포트 객체` (위 약속). 어댑터가 머지되기 전에는 가짜 어댑터를 쓰거나 `tests/stub_adapters.py`처럼 작은 파일을 쓰는 스텁으로 시험합니다.
2. **`config/adapters/default.yaml`에서 자기 줄 하나만 바꿉니다.** 예: `drape: "fitting.adapters.blender.factory:make_draper"`
3. **확인합니다.** 서버 없이 한 번 돌려 봅니다.
   ```bash
   python -m tools.run_fitting_once --adapters default --consent
   ```
   단계 진행이 한 줄씩 나오고, 끝나면 요약(핏 판정, 단계별 상태, 산출물 파일 위치와 **파일이 있는지**)이 나옵니다. 영상 경로가 실패하면 요약 첫 줄이 "영상 경로 실패"이고 종료 코드가 0이 아닙니다(3).
4. **그 출력을 PR에 붙입니다.** 다른 팀원 PC에서 같은 명령으로 다시 확인할 수 있게 `FIT_WORK_DIR`·`BLENDER_PATH` 같은 필요한 환경변수도 적어 주세요.

비용이 드는 실사화(D4)를 `--consent`로 돌리려면 `--yes`가 있어야 합니다. 없으면 아무것도 실행하지 않고 안내만 합니다.

## 일부만 실제로 섞어 돌리기

다른 단계는 가짜로 두고 착장만 실제로 돌려 볼 수 있습니다. 파일을 따로 만들고 `FIT_ADAPTERS`나 `--adapters`로 고르세요(기본 설정을 건드리지 않습니다).

- 몸만 실제 SMPL 어댑터(D3)로 돌리는 설정은 저장소에 있습니다: `--adapters smpl_body`(`config/adapters/smpl_body.yaml`). `SMPL_MODEL_DIR`이 필요합니다(docs/body-fit.md).

```yaml
# 내 PC 전용 /경로/mixed.yaml
version: adapters_v0
ports:
  body: fake
  drape: "fitting.adapters.blender.factory:make_draper"
  render: fake
  realize: fake
  validate: fake
```

```bash
python -m tools.run_fitting_once --adapters /경로/mixed.yaml --out /tmp/run1
FIT_ADAPTERS=/경로/mixed.yaml uvicorn server.app:app --port 8000
```

가짜 전용 옵션(`FIT_FAKE_STAGE_DELAY_S`, `FIT_FAKE_PLACEHOLDERS`)은 **영상 포트 5개가 모두 가짜일 때만** 적용됩니다. 하나라도 실제면 무시하고 경고를 남깁니다(실제 산출물 위에 자리표시 그림을 덮어쓰지 않게).

## 착장 어댑터 (C4, `fitting.adapters.blender.draper:make_draper`)

착장만 실제로 돌리는 설정은 `config/adapters/drape_blender.yaml`입니다(나머지 4단계는 가짜). `default.yaml`은 그대로 둡니다.

```powershell
$env:FIT_ADAPTERS = "drape_blender"
$env:BLENDER_PATH = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
$env:FIT_DRAPE_BODY_OBJ = "C:\dev\GarmentCode\assets\bodies\mean_all.obj"   # 개발·시연용 몸 (아래)
python -m tools.run_fitting_once --adapters drape_blender
```

| 환경변수 | 기본값 | 뜻 |
|---|---|---|
| `BLENDER_PATH` (없으면 `BLENDER_EXE`) | 없음 → 시작 멈춤 | Blender 5.2 실행 파일 |
| `FIT_GC_PYTHON` | `C:\dev\venvs\garmentcode\Scripts\python.exe` | GarmentCode용 Python 3.9. `run_drape`를 이 Python의 하위 프로세스로 돌린다 |
| `FIT_RULER_GC_ROOT` | `C:\dev\GarmentCode` | GarmentCode 루트 |
| `FIT_PREPARED_DIR` | `data/prepared/` | 의류 준비 기록 폴더(`tools/prepare_garments.py`, `data/prepared/README.md`) |
| `FIT_DRAPE_BODY_OBJ` | 없음 | **개발·시연용 몸.** 있으면 입력 몸 메시 대신 이 OBJ를 쓴다 |
| `FIT_DRAPE_FABRIC_MAP` | `config/drape/fabric_map_v0.yaml` | 원단 → 천 물성 매핑 표. `off`면 끄고 `drape_v1` 그대로 쓴다 |

없거나 틀리면 팩토리가 `AdapterConfigError`로 시작을 멈춥니다(가짜로 대신하지 않음).

**입력**
- 몸: `request.input_artifact_ids[0]`의 `artifacts_dir/{id}.obj`(D3 몸 메시: y 위·z 앞·미터, `docs/spikes/smpl-body-measure.md` 1절).
- 개발용 몸: D3 전에는 가짜 몸에 메시 파일이 없어서 `FIT_DRAPE_BODY_OBJ`(GarmentCode `mean_all.obj`)를 씁니다. 사용자 몸이 아니며 개발·시연용입니다. 지표 `body_source`·`body_sha256`에 남습니다. GarmentCode·SMPL 몸 파일은 저장소에 넣지 않습니다.
- 의류: `{FIT_PREPARED_DIR}/{garment_id}/prep.json`과 `specification.json`.

**실행**: GarmentCode용 Python으로 `python -m fitting.adapters.blender.run_drape`(export → Blender 착장 → 분석)를 저장소 루트에서 돌립니다. 작업 폴더는 `FIT_WORK_DIR/{job_id}/drape/`, 기록은 `FIT_WORK_DIR/{job_id}/drape_record.json`(`internal_details_ref`)입니다. 시간 제한은 `request.timeout_seconds`와 설정 `time_limits_s.drape`(600초) 중 작은 값이고, 넘으면 Blender까지 프로세스 트리를 끝냅니다(`DRAPE_INVALID`). 착장이 끝난 뒤의 analyze(Blender 없이 지표를 다시 계산, 판정에 쓰지 않음)에는 같은 길이의 한도를 따로 줍니다. 넘으면 analyze만 끝내고 판정은 Blender의 `drape_metrics.json`으로 합니다([제안], C3에서 analyze만 40~430초). 그래서 의류 1벌이 최대 약 2배 한도까지 걸릴 수 있습니다. 보통 4~8분입니다.

**원단 → 물성**: `config/drape/fabric_map_v0.yaml`([제안] 값)로 신축성 → 강성 배율, 섬유 → 면밀도를 정해 작업 폴더에 파생 설정(`drape_config_fabric.json`, `parent`·`changes` 기록)을 씁니다. `drape_v1.json`은 고치지 않고, 바뀌는 값이 없으면 `drape_v1`을 그대로 씁니다. 검사 기준은 바꾸지 않습니다.

**출력** (`DRAPE_OUTPUTS` 순서): `{job_id}_drape_result.blend`(착장 장면), `{job_id}_drape_preview.png`(정면 확인 이미지, Workbench. 렌더 클립이 아님).

**지표** (`PortResult.metrics`, 숫자 또는 문자열. 불리언은 1/0, 목록은 쉼표로 이은 문자열)

| 키 | 뜻 |
|---|---|
| `drape_ok` | 착장 검사 통과 1 (성공 결과에서는 항상 1) |
| `penetration_area_ratio` | 3 mm보다 깊이 몸에 들어간 면적 비율 |
| `stretch_max` | 최대 신장 비율(기준 1.5 이하) |
| `seam_gap_preweld_max_mm` | 용접 전 봉제선 최대 벌어짐(mm) |
| `frames`, `sim_seconds` | 시뮬레이션 프레임 수, 걸린 시간(초) |
| `sample_id`, `shoulder_diff_cm` | 준비에 쓴 견본, 의류 − 견본 어깨 차이(cm, 판정 안 함) |
| `body_source`, `body_sha256` | 몸 출처(`artifact:{id}` 또는 `dev_env:FIT_DRAPE_BODY_OBJ`)와 sha256 |
| `drape_config`, `drape_config_sha256` | 쓴 착장 설정 `이름@버전`과 sha256 |
| `fabric_map`, `fabric_stiffness_multiplier`, `fabric_areal_density_kg_m2`, `fabric_warnings` | 매핑 표 버전(꺼졌으면 `off`), 쓴 강성 배율·면밀도, 모르는 원단 값 경고 |

**실패** (`PortFailure`, `stage=DRAPE`, `retryable=false`. `safe_message`에 경로·로그 없음)

| 경우 | 오류 코드 |
|---|---|
| 준비 기록 없음 / 준비 실패 / 다른 의류·다른 치수의 기록 | `GARMENT_NOT_READY` |
| 패턴 파일 없음 / 패턴 해시가 기록과 다름 | `GARMENT_NOT_READY` |
| export(패턴 → 메시) 실패 (`run_drape`의 코드 그대로) | `GARMENT_NOT_READY` |
| 착장 검사 실패(신장·침투·봉제선 등) | `DRAPE_INVALID` |
| 시간 초과 | `DRAPE_INVALID` |
| 비정상 종료(결과 기록 없음) / 결과 파일 없음 | `DRAPE_INVALID` |
| 몸 메시 파일 없음(개발용 몸도 없음) | `DRAPE_INVALID` [제안: 전용 코드가 없어 단계 실패 코드를 쓴다] |

## 실사화 어댑터 (D4, `fitting.adapters.fal_ltx.factory:make_realizer`)

`realize: "fitting.adapters.fal_ltx.factory:make_realizer"`로 연결합니다. `FAL_KEY`가 필요하고 호출마다 비용이 듭니다(`run_fitting_once`는 `--consent`로 돌릴 때 `--yes`를 요구합니다). 렌더 클립 파일이 있어야 하므로 C5 전에는 `python -m tools.realize_clip`으로 클립 하나씩 시험합니다. 재시도·같은 요청 ID 재조회·오류 코드·기록은 docs/realize.md에 있습니다.

## 막혔을 때

| 메시지 | 뜻 |
|---|---|
| `…을 불러올 수 없습니다 (ModuleNotFoundError…)` | 설정의 모듈 경로가 틀렸거나 그 어댑터의 선택 의존성(`pip install -e ".[…]"`)이 없습니다 |
| `…에 '…'이 없습니다` | 모듈은 있는데 팩토리 이름이 다릅니다 |
| `팩토리가 설정 오류를 알렸습니다: …` | 팩토리의 `AdapterConfigError`입니다. 메시지대로 환경변수를 정하세요 |
| `…에 '…' 메서드가 없습니다` | 팩토리가 포트 모양이 아닌 객체를 돌려줬거나 다른 단계 자리에 꽂았습니다 |
| `실사화 어댑터(…)는 실제 외부 서비스를 부르고 비용이 들 수 있습니다…` | `--consent`로 실제 실사화를 돌리려면 `--yes`를 주세요 |
