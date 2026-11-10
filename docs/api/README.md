# API 계약: 스키마와 응답 예시

`fitting/domain/`(공통 스키마)과 `fitting/ports/`(포트 요청·결과·오류) 모델에서 만든 파일입니다.
화면(A)은 Python 없이 이 JSON만 보고 mock API를 만들 수 있습니다.

## 파일

| 파일 | 내용 | 만드는 법 |
|---|---|---|
| `schemas/*.schema.json` | 요청·의류·핏 리포트·작업 상태·핏 API·포트 DTO의 JSON Schema (draft 2020-12) | 자동 |
| `examples/fitting_request.json` | 피팅 요청 본문 (`POST /api/fittings`, B3). 실사화 동의 `realize_consent: true` | 손으로 |
| `examples/fit_request.json` | 핏 요청 본문 (`POST /api/fit`). `fitting_request.json`에서 `mode`·`realize_consent`를 뺀 모양 | 손으로 |
| `examples/garment.json` | 등록 의류 1벌 (시연용, 실제 의류 아님). `data/garments/`에도 같은 파일이 있다 | 손으로 |
| `examples/fit_report.json` | 핏 리포트. 위 요청·의류를 #2 핏 계산기(기본 기준표 `fit_rules_v0`)로 계산한 결과 | 자동 (실제 계산기) |
| `examples/garment_list.json` | 의류 목록 (`GET /api/garments`) | 자동 |
| `examples/api_error_input_invalid.json` | 입력 오류 422 `INPUT_INVALID` (범위 밖 가슴둘레, 문자열 숫자 키) | 자동 (서버와 같은 코드) |
| `examples/api_error_garment_not_found.json` | 없는 의류 404 `GARMENT_NOT_FOUND` | 자동 (서버와 같은 코드) |
| `examples/api_error_garment_not_ready.json` | 계산할 수 없는 의류 409 `GARMENT_NOT_READY` | 자동 (실제 계산기 + 서버와 같은 코드) |
| `examples/fitting_status_running.json` | 리포트 완료, 실사화 진행 중 | 자동 |
| `examples/fitting_status_succeeded.json` | 전체 성공 | 자동 (실제 계산기 + 가짜 영상 어댑터) |
| `examples/fitting_status_no_consent.json` | 실사화 미동의: 렌더까지 하고 성공, 실사화·검증 건너뜀 | 자동 (실제 계산기 + 가짜 영상 어댑터) |
| `examples/fitting_status_report_only.json` | 리포트만 요청 (영상 경로 건너뜀) | 자동 (실제 계산기 + 가짜 영상 어댑터) |
| `examples/fitting_status_drape_failed.json` | 착장 실패: 리포트는 있고 3D 미리보기는 없음 | 자동 (실제 계산기 + 가짜 영상 어댑터) |
| `examples/fitting_status_realize_failed.json` | 실사화 실패: 리포트·3D 미리보기·렌더 클립은 있음 | 자동 (실제 계산기 + 가짜 영상 어댑터) |
| `examples/fitting_status_validation_failed.json` | 검증 실패: 영상은 있지만 `quality_status: FAIL` | 자동 (실제 계산기 + 가짜 영상 어댑터) |

자동 파일은 직접 고치지 않습니다. 모델이나 손으로 쓰는 예시, 핏 기준표(`config/fit_rules_v0.yaml`)를 바꾸면 다시 만듭니다.

```bash
python -m tools.export_contracts          # 다시 만들기
python -m tools.export_contracts --check  # 최신인지 확인 (pytest도 확인한다)
```

## 핏 API (#2)

3D·AI 없이 신체 치수와 의류 실측만으로 핏 리포트를 즉시 돌려줍니다.

```bash
uvicorn server.app:app --reload   # http://127.0.0.1:8000/docs 에서 OpenAPI 문서
```

### `GET /api/garments`

등록 의류 중 `status`가 `READY`인 것만 돌려줍니다(명세서 S04). 응답은 `GarmentList`(`garment_list.json`)입니다.

- 각 항목은 `{garment, is_demo}`입니다. `is_demo`가 `true`면 실제 의류와 대조하지 않은 시연용 의류이므로 화면에 "시연용 의류"로 표시합니다.
- 의류군·측정 프로토콜이 맞지 않는 READY 의류도 목록에는 나오고, `POST /api/fit`에서 `GARMENT_NOT_READY`로 거절됩니다.

### `POST /api/fit`

요청 본문은 `FitRequest`(`fit_request.json`), 응답은 `FitReport`(`fit_report.json`)입니다.

- 다섯 신체 치수의 범위(경계 포함)는 `Measurements`와 같습니다: 키 140~210, 가슴둘레 60~160, 허리둘레 50~160, 어깨너비 28~60, 팔길이 40~85 cm.
- 숫자만 받습니다. 문자열 숫자(`"100"`), 불리언, `null`, 모르는 필드(`mode` 포함)는 입력 오류입니다.
- 리포트의 `regions[].is_estimated`가 `true`면 그 부위의 `body_cm`은 입력이 아니라 추정값입니다. 화면에 추정임을 표시합니다. 지금은 총장(`body_length`)에만 쓰고 기본 기준표에서는 꺼져 있어 항상 `false`입니다(`docs/decisions.md` "아직 정할 것").

### 오류 응답 `ApiError`

모든 오류는 한 형식입니다(`api_error.schema.json`). FastAPI 기본 422 형식(`{"detail": [...]}`)은 쓰지 않습니다.

```json
{"schema_version": "v1", "code": "INPUT_INVALID", "safe_message": "입력한 값을 확인해 주세요.",
 "fields": [{"field": "measurements.chest_cm", "code": "OUT_OF_RANGE",
             "message": "가슴둘레 값은 60~160 cm 사이로 입력해 주세요.", "min": 60.0, "max": 160.0}]}
```

| 상황 | HTTP | `code` | `fields` |
|---|---|---|---|
| 입력 범위·형식 오류 | 422 | `INPUT_INVALID` | 틀린 필드마다 하나 |
| 없는 의류 ID | 404 | `GARMENT_NOT_FOUND` [제안] | `null` |
| 준비 안 된 의류, 의류군·프로토콜 불일치, 필수 실측 누락 | 409 [제안] | `GARMENT_NOT_READY` | `null` |

- `safe_message`는 화면에 그대로 보여 줄 수 있는 문장입니다. 내부 사유(어떤 프로토콜이 다른지 등)는 응답에 넣지 않고 서버 로그에만 남깁니다.
- `fields[].field`는 요청 본문 안의 위치(`measurements.chest_cm`)이고, 본문 전체 문제(잘못된 JSON 등)면 `""`입니다.
- `fields[].code`(`FieldErrorCode`, [제안]): `MISSING`, `UNKNOWN_FIELD`, `NOT_A_NUMBER`, `OUT_OF_RANGE`, `UNSUPPORTED_VALUE`, `INVALID`. `OUT_OF_RANGE`일 때만 `min`·`max`(cm, 경계 포함)가 있습니다.

### 의류 등록 형식 (C1이 쓰는 곳)

- 디렉터리: 기본 `data/garments/`. 환경변수 `FIT_GARMENTS_DIR`이나 `create_app(garments_dir=...)`로 바꿉니다.
- 파일 하나에 의류 한 벌, `*.json`, UTF-8. 내용은 `GarmentVariant`(`garment_variant.schema.json`, 예시 `garment.json`)입니다. 파일 이름은 `{id}.json`을 권장합니다.
- `READY`는 가슴·어깨 실측이 있어야 합니다. 없는 실측은 0이 아니라 `null`로 둡니다. 단면 너비를 2배 해서 둘레로 바꿨으면 `measurement_meta.chest_from_flat_width: true`로 적습니다.
- 서버는 요청마다 디렉터리를 다시 읽습니다. 형식에 맞지 않는 파일과 이미 나온 `id`의 파일(파일 이름 순서로 뒤)은 건너뛰고 서버 로그에 경고를 남깁니다.

## 읽는 법 (작업 상태)

- `report`(핏 경로)와 `visual`(영상 경로)은 서로 독립입니다. `visual.status`가 `FAILED`여도 `report.regions`는 그대로 보여 줍니다(#12). 이때 작업 전체 `status`는 `PARTIAL_SUCCEEDED`입니다.
- 핏 계산기가 계산할 수 없는 의류면(`GARMENT_NOT_READY`) `report.status`는 `FAILED`(`summary`는 `null`, `warnings`는 `[]`), `visual.status`는 `SKIPPED`, 작업 `status`는 `FAILED`입니다. 실사화 동의 여부와 상관없이 같고, 영상 경로를 시작하지 않으므로 `visual.quality_status`·`skip_reason`·`quality_metrics`는 `null`입니다.
- `visual.stage`와 `visual.error_code`는 어디서 왜 멈췄는지 알려 줍니다. 화면에는 오류 코드에 맞는 안내 문구를 씁니다.
- `visual.quality_status`가 `PASS`일 때만 실사 영상을 정상 결과로 보여 줍니다. `FAIL`·`UNVERIFIABLE`은 검증 실패로 구분해 표시합니다(#11).
- `visual.quality_status`는 검증 판정이 있을 때만 `PASS`·`FAIL`·`UNVERIFIABLE`입니다. `PENDING`은 영상 경로가 아직 진행 중일 때만 씁니다(`fitting_status_running.json`).
- 작업이 끝났는데 검증 판정이 없으면 `null`입니다: 영상 경로 `SKIPPED`, 실사화 미동의, 검증 판정 전 실패(몸·착장·렌더·실사화 실패나 검증 단계 자체의 실패). 이때 `quality_metrics`도 `null`입니다.
- `visual.skip_reason`은 사용자가 골라서 건너뛴 이유입니다.
  - `REPORT_ONLY`: 핏 리포트만 요청했습니다. 영상 경로 전체가 `SKIPPED`입니다.
  - `CONSENT_NOT_GIVEN`: 실사화(외부 전송)에 동의하지 않았습니다(`realize_consent: false`, 기본값). 렌더까지 하고 `visual.status`는 `SUCCEEDED`, `visual.stage`는 `RENDER`, 작업 `status`도 `SUCCEEDED`입니다. 3D 미리보기와 렌더 클립을 보여 줍니다.
- `report.summary`·`report.warnings`는 핏 리포트의 요약 줄과 경고입니다. 예: `DEMO_GARMENT`면 "시연용 의류"를 표시합니다.
- `visual.render_frame_artifact_id`는 대표 프레임(`frame_artifact_id`)에 대응하는 렌더 프레임입니다. 두 장을 나란히 보여 줍니다(#11).
- `visual.quality_metrics`는 검증 숫자 지표입니다. 예: `color_delta_e`(색 차이), `silhouette_iou`(실루엣 겹침). 검증을 하지 않았으면 `null`입니다. 예시의 값은 가짜입니다.
- 산출물 ID(`*_artifact_id`)를 파일 주소로 바꾸는 방법은 B3에서 정합니다. 예시의 ID는 가짜입니다.

## 산출물 파일 (임시 약속)

- `GET /api/artifacts/{artifact_id}`는 그 산출물 파일(3D 미리보기, 렌더 클립, 실사 영상, 프레임)을 돌려줍니다.
- 지금은 경로 모양만 정한 약속이고, 구현은 B3에서 합니다. 화면 mock은 이 경로로 만듭니다.
