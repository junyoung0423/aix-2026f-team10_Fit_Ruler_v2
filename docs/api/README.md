# API 계약: 스키마와 응답 예시

`fitting/domain/`(공통 스키마)과 `fitting/ports/`(포트 요청·결과·오류) 모델에서 만든 파일입니다.
화면(A)은 Python 없이 이 JSON만 보고 mock API를 만들 수 있습니다.

## 파일

| 파일 | 내용 | 만드는 법 |
|---|---|---|
| `schemas/*.schema.json` | 요청·의류·핏 리포트·작업 상태·포트 DTO의 JSON Schema (draft 2020-12) | 자동 |
| `examples/fitting_request.json` | 피팅 요청 본문 (실사화 동의 `realize_consent: true`) | 손으로 |
| `examples/garment.json` | 등록 의류 1벌 (시연용, 실제 의류 아님) | 손으로 |
| `examples/fit_report.json` | 핏 리포트. 이전 저장소 핏 엔진의 출력이고, #2 핏 계산기가 들어오면 그 결과로 바꾼다 | 손으로 |
| `examples/fitting_status_running.json` | 리포트 완료, 실사화 진행 중 | 자동 |
| `examples/fitting_status_succeeded.json` | 전체 성공 | 자동 (가짜 어댑터) |
| `examples/fitting_status_no_consent.json` | 실사화 미동의: 렌더까지 하고 성공, 실사화·검증 건너뜀 | 자동 (가짜 어댑터) |
| `examples/fitting_status_report_only.json` | 리포트만 요청 (영상 경로 건너뜀) | 자동 (가짜 어댑터) |
| `examples/fitting_status_drape_failed.json` | 착장 실패: 리포트는 있고 3D 미리보기는 없음 | 자동 (가짜 어댑터) |
| `examples/fitting_status_realize_failed.json` | 실사화 실패: 리포트·3D 미리보기·렌더 클립은 있음 | 자동 (가짜 어댑터) |
| `examples/fitting_status_validation_failed.json` | 검증 실패: 영상은 있지만 `quality_status: FAIL` | 자동 (가짜 어댑터) |

자동 파일은 직접 고치지 않습니다. 모델이나 손으로 쓰는 예시를 바꾸면 다시 만듭니다.

```bash
python -m tools.export_contracts          # 다시 만들기
python -m tools.export_contracts --check  # 최신인지 확인 (pytest도 확인한다)
```

## 읽는 법

- `report`(핏 경로)와 `visual`(영상 경로)은 서로 독립입니다. `visual.status`가 `FAILED`여도 `report.regions`는 그대로 보여 줍니다(#12). 이때 작업 전체 `status`는 `PARTIAL_SUCCEEDED`입니다.
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
