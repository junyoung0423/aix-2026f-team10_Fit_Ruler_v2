# Fit Ruler 웹 (A 화면과 사용성)

화면 세 개(입력 → 티셔츠 선택 → 결과)와 서버 없이도 화면을 개발할 수 있는 가짜 API가 들어 있다. A1에서 뼈대를, A2에서 입력 검사·재는 법 안내·핏 리포트 표시를 만들었다. 화면 설계는 [`docs/ux/wireframes.md`](../docs/ux/wireframes.md)를 따른다. 요청·응답 형식은 팀 계약 [`docs/api/`](../docs/api/README.md)(B1·B2)를 따른다.

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
- **결과 화면이 데이터를 받는 곳:** 핏 리포트는 `POST /api/fit`에서, 3D·실사화 진행은 `/api/fittings`에서 받는다. 작업 서버에 닿지 못해도 핏 리포트는 나온다. 이때 진행 상태 자리에는 "3D·실사 진행 상태를 받지 못해 핏 리포트만 보여 드립니다"라는 안내가 나온다.
  - 리포트만 보여 주는 경우는 작업 API에 닿지 못할 때(경로 없음 404, 연결 안 됨)뿐이다. `POST /api/fittings`가 422·409·500 등으로 실패하면 티셔츠 선택 화면에 오류를 보여 준다(`isFittingApiMissing`, `src/api/api.ts`).

## 가짜 API와 실제 서버

기본은 가짜 API(`src/mocks/`)다. 가짜 API가 하는 일은 다음과 같다.

- **의류 5벌:** 첫 벌은 팀 저장소 `data/garments/tee_regular_m_navy_v1.json`과 같고, 세 벌은 화면 개발용 시연 의류다. 마지막 한 벌은 AI Hub 변환 의류와 **모양만** 같은 합성 의류다(핏형·사이즈 `unknown`, 실측, 총장 없음, 허리 위치 미확인, 원단·`color_hex` 있음). 값과 ID는 지어낸 것이고 AI Hub 값을 옮기지 않았다.
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

### 서버가 치수를 거절하는 화면 확인

티셔츠 선택 화면 주소 뒤에 `?mockReject=칸`을 붙이고 핏 보기를 누르면, 가짜 API가 그 칸을 422 `INPUT_INVALID`로 거절한다. 화면은 `fields[]`를 칸별 오류로 바꿔 입력 화면으로 돌아간다.

```text
http://localhost:5173/garments?mockReject=shoulder_cm
```

칸 이름은 `height_cm`, `chest_cm`, `waist_cm`, `shoulder_cm`, `arm_length_cm` 중 하나다.

## 입력 검사

규칙은 `src/validation/body.ts`에 있다. 범위와 문구는 서버(`server/errors.py`)와 같게 맞췄다.

| 칸 | 지원 범위(cm, 경계 포함) |
|---|---|
| 키 | 140~210 |
| 가슴둘레 | 60~160 |
| 허리둘레 | 50~160 |
| 어깨너비 | 28~60 |
| 팔길이 | 40~85 |

- **서버와 같은 문구:** 빈칸, 숫자 아님, 범위 밖은 서버와 같은 문구로 칸 바로 아래에 보여 준다(예: "가슴둘레 값은 60~160 cm 사이로 입력해 주세요.").
- **화면에서만 하는 검사**
  - 소수 둘째 자리 이상을 막는다.
  - 범위 밖 값이 m·인치·mm로 잰 값처럼 보이면 cm로 바꾼 값을 알려 준다.
  - 끝의 `cm`와 쉼표 소수점은 받아 준다.
- **다음 버튼:** 모든 칸을 검사하고 첫 오류 칸으로 이동한다. 오류가 보이는 칸은 고치는 즉시 다시 검사한다.
- **서버가 거절하면(422):** `safe_message`를 폼 위에, `fields[].message`를 해당 칸 아래에 보여 준다. 404·409는 티셔츠 선택 화면에 서버 문구로 보여 준다.

재는 법 안내(`src/guides/measureGuides.ts`)는 팀 계약의 측정 위치 표 `docs/decisions.md` "신체 측정 위치 (measure_v1)"를 따른다. 허리는 자연 허리(가장 잘록한 곳), 어깨는 어깨끝 사이 직선 거리, 팔길이는 오른팔 기준이다.

## 핏 리포트 표시

- **맨 위:** 서버가 준 `summary`를 그대로 보여 준다.
- **부위마다:** 판정 배지, 차이(cm), 기준표 구간 중 위치, 서버 설명(`explanation`)을 보여 준다.
  - 배지 이름과 구간 순서는 기준표 `fit_rules_v0`의 label_ko를 `src/labels.ts`에 옮겨 쓴다. 응답에 부위별 한국어 이름이 없기 때문이다.
- **판정하지 않은 부위:** `UNKNOWN`·`NOT_EVALUATED`는 "판정 안 함"과 서버 설명으로 보여 준다.
- **경고:** `warnings`(`DEMO_GARMENT`, `WAIST_NOT_EVALUATED`)는 안내 문구로 바꿔 보여 준다.

## 폴더

```text
web/
├─ src/
│  ├─ api/          계약 타입(types.ts)·계약 값(contract.ts)·실제 서버 호출·가짜/실제 선택
│  ├─ mocks/        가짜 API, 의류, 핏 계산(B2 기준표 이식), 계약 예시 비교 테스트
│  ├─ components/   진행 단계, 핏 리포트, 진행 상태 목록, 재는 법 안내
│  ├─ guides/       부위별 재는 법 문구 (measure_v1)
│  ├─ pages/        입력, 티셔츠 선택, 결과 화면
│  ├─ state/        화면 사이에 신체 치수와 결과를 넘기는 상태
│  └─ validation/   신체 치수 입력 검사 규칙과 테스트
└─ index.html
```

## 다음에 할 일

- B3(`/api/fittings`, `/api/artifacts`)가 나오면 실제 서버로 3D·실사화 진행을 확인한다.
- 기준표가 바뀌면(`fit_rules_v1`) `src/mocks/fit.ts`와 `src/labels.ts`의 label_ko·구간 순서를 같이 고친다.
- 의류 표시 이름·색 표기는 C1 결정(docs/decisions.md "색·원단 표기")에 맞춰 `src/labels.ts`를 고친다.
