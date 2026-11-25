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
| `FIT_DRAPE_ANALYZE` | `off` | `on`이면 착장 뒤 analyze(Blender 없이 지표 재계산·패널별 요약, 판정에 쓰지 않음)까지 돌린다. 개발·일괄 점검용 |

없거나 틀리면 팩토리가 `AdapterConfigError`로 시작을 멈춥니다(가짜로 대신하지 않음).

**입력**
- 몸: `request.input_artifact_ids[0]`의 `artifacts_dir/{id}.obj`(D3 몸 메시: y 위·z 앞·미터, `docs/spikes/smpl-body-measure.md` 1절).
- 개발용 몸: D3 전에는 가짜 몸에 메시 파일이 없어서 `FIT_DRAPE_BODY_OBJ`(GarmentCode `mean_all.obj`)를 씁니다. 사용자 몸이 아니며 개발·시연용입니다. 지표 `body_source`·`body_sha256`에 남습니다. GarmentCode·SMPL 몸 파일은 저장소에 넣지 않습니다.
- 의류: `{FIT_PREPARED_DIR}/{garment_id}/prep.json`과 `specification.json`.

**실행**: GarmentCode용 Python으로 `python -m fitting.adapters.blender.run_drape`(export → Blender 착장 → 분석)를 저장소 루트에서 돌립니다. 작업 폴더는 `FIT_WORK_DIR/{job_id}/drape/`, 기록은 `FIT_WORK_DIR/{job_id}/drape_record.json`(`internal_details_ref`)입니다. 시간 제한은 `request.timeout_seconds`와 설정 `time_limits_s.drape`(600초) 중 작은 값이고, 넘으면 Blender까지 프로세스 트리를 끝냅니다(`DRAPE_INVALID`). 착장이 끝난 뒤의 analyze(Blender 없이 지표를 다시 계산, 판정에 쓰지 않음)는 **기본으로 건너뜁니다**(`--skip-analyze`). 판정은 처음부터 Blender의 `drape_metrics.json`으로 하므로 결과는 같고, 서버 착장 단계가 analyze 시간(C3 실측 40~430초)만큼 짧아집니다. `FIT_DRAPE_ANALYZE=on`으로 켜면 analyze에 같은 길이의 한도를 따로 주고, 넘으면 analyze만 끝내고 판정은 `drape_metrics.json`으로 합니다. 이때는 의류 1벌이 최대 약 2배 한도까지 걸릴 수 있습니다.

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

## 렌더 어댑터 (C5, `fitting.adapters.blender.renderer:make_renderer`)

착장과 렌더를 실제로 돌리는 설정은 `config/adapters/drape_render_blender.yaml`입니다(몸·실사화·검증은 가짜). `default.yaml`과 `drape_blender.yaml`은 그대로 둡니다. 렌더 설정 값과 근거·실측은 `docs/render.md`에 있습니다.

```powershell
$env:FIT_ADAPTERS = "drape_render_blender"
$env:BLENDER_PATH = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
$env:FIT_DRAPE_BODY_OBJ = "C:\dev\GarmentCode\assets\bodies\mean_all.obj"   # 착장 어댑터의 개발용 몸
python -m tools.run_fitting_once --adapters drape_render_blender

# 서버 없이 이미 있는 착장 결과 하나만 렌더 (D에게 넘길 클립 만들기)
python -m tools.render_clip --blend <...>_drape_result.blend --garment-id tee_regular_m_navy_v1
```

| 환경변수 | 기본값 | 뜻 |
|---|---|---|
| `BLENDER_PATH` (없으면 `BLENDER_EXE`) | 없음 → 시작 멈춤 | Blender 5.2 실행 파일 |

렌더 설정은 `config/render/render_v1.json` 하나입니다. Blender 경로가 없거나 파일이 없으면, 또는 렌더 설정을 읽지 못하면 팩토리가 `AdapterConfigError`로 시작을 멈춥니다.

**입력**: `request.input_artifact_ids[0]`의 `artifacts_dir/{id}.blend`(착장 결과). 옷 색은 `garment.color_hex` > 색 키 `garment.color`로 찾은 `COLOR_HEX`(AI Hub 색 표) > 중립 회색(경고를 지표·기록에 남김).

**실행**: Blender를 `--background --factory-startup <착장 .blend> --python fitting/adapters/blender/blender_render.py`로 하위 프로세스로 돌립니다. 작업 폴더는 `FIT_WORK_DIR/{job_id}/render/`, 기록은 `FIT_WORK_DIR/{job_id}/render_record.json`(`internal_details_ref`), 로그는 `FIT_WORK_DIR/{job_id}/render_log.txt`입니다. 시간 제한은 `request.timeout_seconds`와 설정 `time_limits_s.render`(300초) 중 작은 값이고, 넘으면 프로세스 트리를 끝냅니다. 이 PC 실측 40~62초입니다. 같은 작업을 다시 부르면 그 작업의 예전 렌더 산출물을 먼저 지웁니다.

**출력**
- `{job_id}_render_clip.mp4` (`RENDER_OUTPUTS`): 768 × 1024, 24 fps, 121프레임, H264, 무음. **정지 클립**(한 번 렌더한 프레임을 반복, 모든 프레임 동일).
- 계약 출력이 아닌 산출물(같은 `artifacts_dir`, ID는 지표로 알림)
  - `{job_id}_render_mask.png`: 옷 마스크. 클립과 같은 크기, 8비트 흑백 PNG, 보이는 옷 255·나머지 0
  - `{job_id}_render_still.png`: 정지 프레임(무손실 PNG, 클립의 원본 프레임)
- 작업 폴더의 `render_settings.json`: 쓴 카메라·조명·재질 색·엔진·색 관리 설정

**지표** (`PortResult.metrics`, 숫자 또는 문자열)

| 키 | 뜻 |
|---|---|
| `width`, `height`, `fps`, `frames`, `duration_s`, `codec` | 클립 (설정 값과 같은지 Blender가 MP4를 다시 열어 확인한 뒤 성공) |
| `static_clip` | 정지 클립 1 |
| `render_seconds`, `still_seconds` | Blender 프로세스 전체 시간, 정지 프레임 렌더 시간(초) |
| `engine`, `samples` | 렌더 엔진(`CYCLES`), 샘플 수 |
| `garment_mask_ratio` | 마스크 흰 픽셀 비율 |
| `head_vertices_in_frame` | 화면 안에 투영되는 머리 정점 수(옷 맨 위보다 높은 몸 정점). 성공이면 0 |
| `garment_vertices_out_of_frame`, `top_limited_by` | 화면 밖 옷 정점 수(판정 안 함), 위쪽 경계를 정한 것(`head`·`collar_margin`) |
| `garment_color_hex`, `garment_color_source`, `garment_color_warnings` | 쓴 옷 색, 출처(color_hex·color_table·default), 경고 |
| `garment_median_hex`, `garment_delta_e00` | 마스크 안쪽(5 px 줄임) 렌더 색 중앙값과 `color_hex` 대비 CIEDE2000(기록용, D6 기준점) |
| `still_pixel_sha256` | 정지 프레임 픽셀 해시(재현성 확인) |
| `render_config`, `render_config_sha256` | 쓴 렌더 설정 `이름@버전`과 sha256 |
| `render_mask_artifact_id`, `render_still_artifact_id` | 옷 마스크·정지 프레임 산출물 ID |

**실패** (`PortFailure`, 모두 `RENDER_FAILED`, `stage=RENDER`, `retryable=false`. `safe_message`에 경로·로그 없음)

| 경우 | 화면 문장 |
|---|---|
| 입력 ID 없음 / 착장 결과 파일 없음 | 렌더에 쓸 착장 결과가 없습니다. |
| `.blend`에서 몸·옷 객체를 못 찾음 | 착장 결과에서 몸이나 옷을 찾지 못했습니다. |
| 시간 초과(프로세스 트리 종료) | 렌더가 시간 제한(N초) 안에 끝나지 않았습니다. |
| 하위 프로세스 비정상 종료(종료 코드 ≠ 0, 지표 없음) | 렌더 프로그램이 비정상 종료했습니다. |
| 클립·마스크·정지 프레임 파일 없음 | 렌더 결과 파일이 없습니다. |
| 클립 크기·fps·프레임 수가 설정과 다름 | 렌더 클립의 크기·fps·프레임 수가 설정과 다릅니다. |
| 옷 마스크가 비었음 / 비율이 설정 범위(0.05~0.6, [제안]) 밖 | 옷 마스크가 비었습니다. / 옷 마스크 비율이 기준 범위 밖입니다. |
| 머리 정점이 화면 안에 있음 | 머리가 화면 안에 들어와 얼굴 처리 기준을 지키지 못했습니다. |

`retryable=false`는 착장 어댑터와 같은 기준입니다(같은 입력이면 같은 결과: 같은 입력 두 번 렌더에서 파일 해시가 같았다).

## 실사화 어댑터 (D4, `fitting.adapters.fal_ltx.factory:make_realizer`)

`realize: "fitting.adapters.fal_ltx.factory:make_realizer"`로 연결합니다. `FAL_KEY`가 필요하고 호출마다 비용이 듭니다(`run_fitting_once`는 `--consent`로 돌릴 때 `--yes`를 요구합니다). 렌더 클립 파일이 있어야 하므로 실제 렌더(C5)와 함께 돌리거나 `python -m tools.realize_clip`으로 클립 하나씩 시험합니다. 재시도·같은 요청 ID 재조회·오류 코드·기록은 docs/realize.md, 프롬프트 템플릿(D5)은 docs/realize-pipeline.md 1절에 있습니다.

## 검증 어댑터 (D5·D6, `fitting.adapters.media.factory:make_validator`)

실사 영상을 렌더 클립과 비교해 색·실루엣을 판정합니다(명세서 7장). 실사 영상 길이의 0~100 % 11프레임을 같은 시각의 렌더 프레임과 비교하고, 9프레임 이상이 모든 기준(IoU ≥ 0.85, 가슴·밑단 폭 변화 ≤ 5 %, CIEDE2000 ≤ 8)을 통과하면 `PASS`입니다. 기준은 `config/validate/visual_check_v1.json`입니다. 필요한 것은 OpenCV(`pip install -e ".[realize]"`)뿐이고 모델 파일·GPU는 필요 없습니다.

- 입력: `input_artifact_ids` = [렌더 클립, 실사 영상]의 `artifacts_dir/{id}.mp4`, 렌더 옷 마스크 `artifacts_dir/{job_id}_render_mask.png`(C5 렌더 어댑터가 쓰는 이름. 계약 출력이 아니어서 이름 규칙으로 찾습니다)
- 출력(`VALIDATE_OUTPUTS`): `{job_id}_frame.png`(대표 프레임), `{job_id}_render_frame.png`(대응 렌더 프레임). 각 영상의 원래 크기 PNG
- 지표: `quality_status`(`PASS`·`FAIL`·`UNVERIFIABLE`), 숫자 `color_delta_e`·`silhouette_iou`·`width_change_ratio`(측정된 프레임의 중앙값)·`frames_passed`. 숫자는 화면의 `quality_metrics`로 갑니다
- 판정이 `PASS`가 아니어도 결과를 돌려줍니다. 서비스가 프레임 ID를 남기고 검증 단계를 `VALIDATION_FAILED`(FAIL)·`VALIDATION_UNVERIFIABLE`(그 밖)로 끝냅니다. 렌더 옷 마스크가 없으면 비교하지 않고 `UNVERIFIABLE`입니다
- 기록: `FIT_WORK_DIR/{job_id}/validate/validate.json`(`internal_details_ref`), 같은 폴더의 `contact_sheet.png`(11프레임 사람 확인용)·`masks/`(프레임별 실사 옷 영역)
- 실패(`PortFailure`): 입력이 없거나 읽지 못하면 `VALIDATION_UNVERIFIABLE`, `retryable=false`
- 중대한 변형·얼굴은 자동으로 찾지 않습니다. `PASS`에도 기록에 `human_review_required: true`가 남습니다

`default.yaml`의 `validate`는 아직 `fake`입니다(바꿀 시점은 팀 결정, `docs/visual-check.md` 6절). 영상 경로 5단계를 모두 실제로 돌리는 설정은 `config/adapters/visual_real.yaml`입니다(몸 D3 · 착장 C4 · 렌더 C5 · 실사화 D4 · 검증 D6). Blender·GarmentCode·SMPL 모델·FAL_KEY가 모두 있어야 시작합니다.

```bash
python -m tools.run_fitting_once --adapters visual_real --consent --yes   # fal 비용 발생(C5 클립 기준 약 $0.2)
python -m tools.check_visual --run artifacts/realize/<이름>               # 이미 있는 결과로 판정만(비용 없음)
```

기준·방법·실제 결과는 docs/visual-check.md, 대응 프레임 매칭 근거는 docs/realize-pipeline.md 3절에 있습니다.

## 막혔을 때

| 메시지 | 뜻 |
|---|---|
| `…을 불러올 수 없습니다 (ModuleNotFoundError…)` | 설정의 모듈 경로가 틀렸거나 그 어댑터의 선택 의존성(`pip install -e ".[…]"`)이 없습니다 |
| `…에 '…'이 없습니다` | 모듈은 있는데 팩토리 이름이 다릅니다 |
| `팩토리가 설정 오류를 알렸습니다: …` | 팩토리의 `AdapterConfigError`입니다. 메시지대로 환경변수를 정하세요 |
| `…에 '…' 메서드가 없습니다` | 팩토리가 포트 모양이 아닌 객체를 돌려줬거나 다른 단계 자리에 꽂았습니다 |
| `실사화 어댑터(…)는 실제 외부 서비스를 부르고 비용이 들 수 있습니다…` | `--consent`로 실제 실사화를 돌리려면 `--yes`를 주세요 |
