# Fit Ruler 웹 (A 화면과 사용성)

A1에서 만든 웹 뼈대다. 화면 세 개(입력 → 티셔츠 선택 → 결과)와 이동 순서, 그리고 서버 없이도 화면을 개발할 수 있는 가짜 API가 들어 있다. 화면 설계는 [`docs/ux/wireframes.md`](../docs/ux/wireframes.md)를 따른다. 요청·응답 형식은 팀 계약 [`docs/api/`](../docs/api/README.md)(B1·B2)를 따른다.

## 실행

Node.js 20 이상이 필요하다.

```bash
cd web
npm install
npm run dev
```

브라우저에서 http://localhost:5173 을 연다.

| 명령 | 하는 일 |
|---|---|
| `npm run dev` | 개발 서버 실행 |
| `npm test` | 가짜 API를 팀 계약 예시(`docs/api/examples`)와 비교하는 테스트 |
| `npm run lint` | ESLint (경고도 실패로 본다) |
| `npm run typecheck` | 타입 검사 |
| `npm run build` | 타입 검사 후 배포용 빌드 |

CI(`.github/workflows/ci.yml`의 `web` job)가 lint·타입 검사·테스트·빌드를 돌린다.

## API

형식은 `src/api/types.ts`에 팀 계약 스키마를 그대로 옮겨 두었다. 필드 이름(`height_cm`, `chest_girth_cm` 등)과 값을 바꾸지 않는다.

| 요청 | 응답 | 서버 |
|---|---|---|
| `GET /api/garments` | `GarmentList` (READY 의류, `is_demo`) | B2 |
| `POST /api/fit` | `FitReport` (3D·AI 없이 즉시) | B2 |
| `POST /api/fittings` | `FittingStatus` (3D·실사화 작업, `realize_consent`) | B3 |
| `GET /api/fittings/{job_id}` | `FittingStatus` (단계별 상태·산출물 ID) | B3 |
| `GET /api/artifacts/{artifact_id}` | 산출물 파일 | B3 |

- **오류:** 모두 `ApiErrorBody {code, safe_message, fields}`다. 화면에는 `safe_message`와 `fields[].message`를 그대로 보여 준다.
- **결과 화면이 데이터를 받는 곳:** 핏 리포트는 `POST /api/fit`에서, 3D·실사화 진행은 `/api/fittings`에서 받는다. 그래서 B3 전에 실제 서버에 붙여도 핏 리포트는 나온다. 진행 상태 자리에는 "B3가 준비되면 나온다"는 안내가 나온다.
  - 리포트만 보여 주는 경우는 작업 API가 아직 없을 때(경로 없음 404, 연결 안 됨)뿐이다. `POST /api/fittings`가 422·409·500 등으로 실패하면 티셔츠 선택 화면에 오류를 보여 준다(`isFittingApiMissing`, `src/api/api.ts`).

## 가짜 API와 실제 서버

기본은 가짜 API(`src/mocks/`)다. 가짜 API가 하는 일은 다음과 같다.

- **의류 4벌:** 첫 벌은 팀 저장소 `data/garments/tee_regular_m_navy_v1.json`과 같고, 나머지는 화면 개발용 시연 의류다.
- **핏 계산:** B2 계산기와 기준표 `config/fit_rules_v0.yaml`을 옮겨서 한다. 팀 예시 요청으로 계산한 결과가 `docs/api/examples/fit_report.json`과 같은지 테스트한다. 기준표가 바뀌면 `src/mocks/fit.ts`도 고친다.
- **입력 오류:** 서버와 같은 형식과 문구로 돌려준다. 범위 밖은 422 `INPUT_INVALID`, 없는 의류는 404 `GARMENT_NOT_FOUND`다.
- **3D·실사화 단계:** 몇 초씩 걸려 진행되는 것처럼 보여 준다(몸 생성 2초 → 착장 3초 → 렌더 2초 → 실사화 4초 → 검증 1초). `realize_consent`가 false면 렌더까지만 하고 성공으로 끝낸다.

실제 서버를 쓰려면 저장소 맨 위에서 핏 API를 띄우고, `web/.env`를 만들어 `VITE_USE_MOCK=false`로 둔다. 개발 서버가 `/api`를 `http://127.0.0.1:8000`으로 넘기므로 CORS 설정은 필요 없다.

```bash
uvicorn server.app:app --reload   # 저장소 맨 위에서
```

### 실패 화면 확인

티셔츠 선택 화면 주소 뒤에 `?mockFail=단계`를 붙이고 핏 보기를 누르면 그 단계가 실패한다.

```text
http://localhost:5173/garments?mockFail=realize
```

단계 이름은 `body`, `drape`, `render`, `realize`, `validate` 중 하나다.

## 폴더

```text
web/
├─ src/
│  ├─ api/          계약 타입(types.ts)·계약 값(contract.ts)·실제 서버 호출·가짜/실제 선택
│  ├─ mocks/        가짜 API, 의류, 핏 계산(B2 기준표 이식), 계약 예시 비교 테스트
│  ├─ components/   진행 단계, 핏 리포트 표, 진행 상태 목록
│  ├─ pages/        입력, 티셔츠 선택, 결과 화면
│  └─ state/        화면 사이에 신체 치수와 결과를 넘기는 상태
└─ index.html
```

## A2에서 이어서 할 일

- 입력 폼의 범위 검사, 칸별 오류 문구(서버 422 `fields` 포함), 재는 법 안내(`measure_v1` 위치)
- 핏 리포트를 읽기 쉽게 다듬기
