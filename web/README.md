# Fit Ruler 웹 (A 화면과 사용성)

A1에서 만든 웹 뼈대다. 화면 세 개(입력 → 티셔츠 선택 → 결과)와 이동 순서, 그리고 서버 없이도 화면을 개발할 수 있는 가짜 API가 들어 있다. 화면 설계는 [`docs/ux/wireframes.md`](../docs/ux/wireframes.md)를 따른다.

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
| `npm test` | 가짜 API·임시 핏 계산 테스트 |
| `npm run typecheck` | 타입 검사 |
| `npm run build` | 타입 검사 후 배포용 빌드 |

## 가짜 API

기본으로 가짜 API(`src/mocks/`)가 동작한다. 실제 서버를 쓰려면 `.env.example`을 `.env`로 복사하고 `VITE_USE_MOCK=false`, `VITE_API_BASE_URL=서버 주소`로 바꾼다.

가짜 API가 하는 일은 다음과 같다.

- 예시 티셔츠 4벌을 돌려준다. 실제 등록 의류가 아니다.
- 핏 리포트를 활동지 1-3의 식(가슴 여유 = 가슴단면 × 2 − 가슴둘레, 어깨 차이 = 옷 어깨 − 몸 어깨)과 **임시 경계값**으로 계산한다. 총장은 키로 기준을 추정하고 '추정'으로 표시한다.
- 3D·실사화 단계를 몇 초씩 걸려 진행하는 것처럼 보여 준다(몸 생성 2초 → 착장 3초 → 렌더 2초 → 실사화 4초 → 검증 1초).
- 실사화 동의를 하지 않으면 실사화와 검증을 '건너뜀'으로 돌려준다.

### 실패 화면 확인

티셔츠 선택 화면 주소 뒤에 `?mockFail=단계`를 붙이고 핏 보기를 누르면 그 단계가 실패한다.

```text
http://localhost:5173/garments?mockFail=realize
```

단계 이름은 `body`, `drape`, `render`, `realize`, `verify` 중 하나다.

## 임시 API 형식

B의 공통 계약(B1)과 핏 API(B2)가 나오기 전까지 쓰는 형식이다. 형식은 `src/api/types.ts`, 엔드포인트는 `src/api/api.ts`에 있다. 계약이 나오면 이 두 파일과 `src/api/http.ts`만 맞추면 된다.

| 요청 | 응답 |
|---|---|
| `GET /api/garments` | 티셔츠 목록 |
| `POST /api/fit` | 핏 리포트 (`body`, `garmentId`) |
| `POST /api/fittings` | 피팅 작업: 핏 리포트 즉시 + 단계별 상태 (`body`, `garmentId`, `realizeConsent`) |
| `GET /api/fittings/{jobId}` | 피팅 작업의 현재 상태 |

오류는 `422 { message, errors: { 필드: 문구 } }`, `404 { message }` 형식이다. 단위는 모두 cm이고, 옷은 단면(펼쳐 잰 한쪽 폭), 몸은 둘레로 잰다.

## 폴더

```text
web/
├─ src/
│  ├─ api/          임시 API 형식, 실제 서버 호출, 가짜/실제 선택
│  ├─ mocks/        가짜 API, 예시 티셔츠, 임시 핏 계산과 테스트
│  ├─ components/   진행 단계, 핏 리포트 표, 진행 상태 목록
│  ├─ pages/        입력, 티셔츠 선택, 결과 화면
│  └─ state/        화면 사이에 신체 치수를 넘기는 상태
└─ index.html
```

## A2에서 이어서 할 일

- 입력 폼의 범위 검사, 칸별 오류 문구, 재는 법 안내
- 핏 리포트를 읽기 쉽게 다듬기
