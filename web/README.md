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

### 진행 상태 조회가 끊기는 화면 확인

티셔츠 선택 화면 주소 뒤에 붙이고 핏 보기를 누른다. 옵션은 작업을 만들 때 읽어 그 작업에 남긴다(결과 화면 주소에는 붙지 않는다). 여러 옵션을 `&`로 함께 쓸 수 있다.

| 주소 | 결과 화면에서 보이는 것 |
|---|---|
| `/garments?mockFlaky=2` | 진행 상태 조회가 처음 2번 연결 오류로 실패한다. "다시 시도하는 중 (1/5)" 안내가 나오고 1초·2초 뒤 다시 불러 이어서 진행한다. `6` 이상이면 5번째 연속 실패에서 멈추고 "다시 불러오기" 버튼이 나온다 |
| `/garments?mockLost=1` | 진행 상태 조회가 404 `JOB_NOT_FOUND`다(서버를 다시 켠 경우). "작업 기록을 찾을 수 없습니다" 안내와 "같은 조건으로 다시 시도"·"다른 티셔츠 고르기"가 나온다. 다시 시도로 만든 새 작업은 정상으로 진행한다 |
| `/garments?mockFail=realize&mockFlaky=2` | 두 경우를 함께 본다 |

`npm run dev`(React StrictMode)에서는 결과 화면이 열릴 때 첫 조회가 두 번 일어나, 처음 실패 2번이 한꺼번에 소진된다. 그래서 `mockFlaky=2`는 "(1/5)"만 잠깐 보인다. 간격이 늘어나는 모습은 `mockFlaky=4`(1/5 → 2/5 → 3/5, 1초·2초·4초)로 본다.

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

## 진행 상태 표시 (A4)

결과 화면의 "3D·실사 착장 진행" 카드다. 3D 미리보기는 정지 이미지로만 보여 준다. 웹 3D 뷰어(회전)는 docs/decisions.md "선택 기능"이 정해지지 않아 만들지 않았다.

- **한 줄 상태:** 카드 맨 위에 작업 전체 상태를 한 줄로 보여 준다(`src/progress/jobSummary.ts`).
  - 진행 중: "3D 착장 중 (2/5)"
  - 완료: "완료", 실사화 미동의면 "완료 (렌더까지)"
  - 일부 완료: "일부 완료 · 핏 리포트와 정면 렌더까지 준비됨"
  - 실패: "실패 · 핏 계산"
  - 화면 낭독기는 이 줄이 바뀔 때(단계가 넘어갈 때)만 알린다(`role="status"`).
- **경과 시간:** 서버가 단계별 시각을 주지 않는다. 그래서 화면이 진행 중인 상태를 처음 받은 때부터 브라우저 시계로 잰다. 작업이 끝나거나 조회가 멈추면 시간도 멈춘다.
- **진행 단계:** 진행 중인 단계에 깜빡이는 점을 단다. `prefers-reduced-motion`이면 움직이지 않는다. `attempt_no`가 2 이상이면 "재시도 2회째"를 붙인다.
- **실패·건너뜀:** 실패한 단계에는 오류 코드 안내(`ERROR_LABEL`)를 보여 준다. 표에 없는 코드는 "알 수 없는 오류(코드)"다. 그 뒤 단계는 "앞 단계가 실패해 건너뛰었습니다"다.
- **다시 부르기**(`src/progress/poller.ts`)
  - 진행 중에는 1초마다 다시 부른다. 실패하면 1초 → 2초 → 4초 …(최대 10초) 뒤 다시 부르고, 성공하면 1초로 돌아간다.
  - 연속 5번 실패하면 멈추고 "다시 불러오기" 버튼을 보여 준다.
  - 404 `JOB_NOT_FOUND`면 바로 멈춘다.
  - 끝난 상태(SUCCEEDED·PARTIAL_SUCCEEDED·FAILED·CANCELLED)면 더 부르지 않는다.
- **실패 뒤 할 일:** 영상 경로가 실패했거나 작업 기록이 없으면 "같은 조건으로 다시 시도"와 "다른 티셔츠 고르기"를 보여 준다.
  - 다시 시도는 티셔츠 선택 화면이 저장해 둔 요청(실사화 동의 포함)을 그대로 `POST /api/fittings`로 보내고 새 작업 주소로 간다(`src/progress/retry.ts`).
  - 저장된 요청이 없으면(새 탭에서 연 결과 등) 다시 시도 버튼은 없다.
- **3D 미리보기**(`src/components/ArtifactImage.tsx`)
  - 불러오는 동안 같은 크기(3:4)의 빈 자리를 둔다.
  - 주소가 열리지 않으면(가짜 어댑터는 `FIT_FAKE_PLACEHOLDERS=1`이 아니면 파일이 없다) "미리보기 파일을 불러오지 못했습니다"를 보여 준다.
  - 누르면 새 탭에서 원본 크기로 연다.

## 폴더

```text
web/
├─ src/
│  ├─ api/          계약 타입(types.ts)·계약 값(contract.ts)·실제 서버 호출·가짜/실제 선택
│  ├─ mocks/        가짜 API, 의류, 핏 계산(B2 기준표 이식), 계약 예시 비교 테스트
│  ├─ components/   진행 단계, 핏 리포트, 진행 상태 목록, 재는 법 안내
│  ├─ guides/       부위별 재는 법 문구 (measure_v1)
│  ├─ pages/        입력, 티셔츠 선택, 결과 화면
│  ├─ progress/     진행 상태 다시 부르기 규칙, 한 줄 상태·단계 문구, 같은 조건으로 다시 시도
│  ├─ state/        화면 사이에 신체 치수와 결과를 넘기는 상태
│  └─ validation/   신체 치수 입력 검사 규칙과 테스트
└─ index.html
```

## 다음에 할 일

- B3(`/api/fittings`, `/api/artifacts`)가 나오면 실제 서버로 3D·실사화 진행을 확인한다.
- 기준표가 바뀌면(`fit_rules_v1`) `src/mocks/fit.ts`와 `src/labels.ts`의 label_ko·구간 순서를 같이 고친다.
- 의류 표시 이름·색 표기는 C1 결정(docs/decisions.md "색·원단 표기")에 맞춰 `src/labels.ts`를 고친다.
