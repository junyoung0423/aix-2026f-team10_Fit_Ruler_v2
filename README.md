# AIMAX 가상 피팅 (Fit Ruler)

신체 치수와 등록 티셔츠의 실측을 비교해 핏 리포트를 주고, 3D 착장과 실사화 영상으로 확인하는 반팔 티셔츠 가상 피팅 프로젝트입니다.

## 문서

| 문서 | 역할 |
| --- | --- |
| [활동지 (week-07 수정안)](docs/plan/week-07-activity.md) | 무엇을 만드나: 범위, 이슈 #1~#13과 완료 조건, 마일스톤 |
| [3주 개발 계획](docs/plan/aimax-3week-plan.md) | 언제·어떻게: 역할, PR 목록, 리뷰 규칙, 주고받을 것, 위험과 대체안 |
| [개발 명세서 초안 v0.1](docs/AIMAX-specification-draft-v0.1.docx) | 세부 기준: 판정 규칙, 착장 점검 수치, 포트·API·오류 처리. 위 두 문서와 충돌하지 않는 부분은 그대로 따른다 |
| [결정 기록](docs/decisions.md) | 정한 기준과, 위 문서끼리 다른 부분을 어떻게 정했는지 |

## 목표

- **치수로 설명할 수 있는 핏 리포트:** 신체와 의류 실측의 차이, 부위별 판정과 근거를 제공한다.
- **선택한 옷의 치수를 유지하는 착장:** 사용자 치수로 몸을 만들고, 등록 의류에 맞춘 3D 옷을 입힌다.
- **실사화 결과의 품질 확인:** 렌더와 실사 영상의 색·실루엣을 비교하고, 기준을 넘으면 검증 실패로 표시한다.
- **독립적인 결과 제공:** 3D·실사화가 늦거나 실패해도 핏 리포트는 확인할 수 있다.

## 범위

| 항목 | 범위 |
| --- | --- |
| 의류 | 성인용 반팔 라운드넥 티셔츠 |
| 사용자 입력 | 키·가슴둘레·허리둘레·어깨너비·팔길이 (cm) |
| 의류 데이터 | AI Hub 의류 통합 데이터의 티셔츠 10~20벌 (치수·색·원단) |
| 패턴 견본집 | GarmentCode로 만든 반팔 라운드넥 패턴 10개 이상 |
| 수치 결과 | 부위별 신체 치수·옷 치수·여유량·판정·설명 |
| 시각 결과 | 3D 착장 미리보기, 정면 렌더 클립, 실사 영상과 대표 프레임, 색·실루엣 검증 결과 |

## 흐름

1. **의류 사전 준비**: AI Hub 티셔츠 데이터 등록(#3) → GarmentCode 패턴 견본집 생성(#4) → 치수가 비슷한 견본 검색(#5) → 실제 의류에 맞게 최적화(#6)
2. **피팅 요청**: 치수 입력 → 핏 리포트(#2) → SMPL 몸 생성(#7) → Blender 착장(#8) → 정면 렌더 클립·실사화(#10) → 대표 프레임 검증(#11)
3. 3D·실사화가 실패해도 핏 리포트는 제공합니다(#12).

## 저장소 구조

```text
web/        A  입력·결과·관리 화면
server/     B  FastAPI 핏 API, 피팅 작업 API
fitting/       공통 스키마·포트(domain, ports), 핏 계산(fit), 서비스, 견본(catalog),
               몸(body), 검증(verify), 어댑터(adapters/fake·aihub·garmentcode·blender·smpl·fal_ltx·…)
tools/         명령어: 견본집 생성·의류 등록, 피팅 1회 실행
config/        핏 기준표·허용 오차·검증 기준
data/          작은 메타데이터만 커밋
docs/          plan/(활동지·3주 계획), 명세서, decisions.md, spikes/, ux/
```

폴더별 담당과 협업 규칙은 [CONTRIBUTING.md](CONTRIBUTING.md)에 있습니다.

## 개발 환경

```bash
python -m venv .venv
.venv\Scripts\activate      # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
ruff check .
pytest
```

`.env.example`을 `.env`로 복사해 `FAL_KEY` 등을 채웁니다. `.env`는 커밋하지 않습니다.

## 팀 공유 드라이브

`.blend`·영상·메시·SMPL 모델처럼 커밋하지 않는 파일의 위치: (첫 공유 때 적는다)

## 이전 저장소

이 저장소 이전의 작업 기록은 [aix-2026f-team10_Fit_Ruler](https://github.com/junyoung0423/aix-2026f-team10_Fit_Ruler) (보관됨)에 있습니다. 공통 계약·핏 엔진(B)과 GarmentCode→Blender 착장 스파이크(C)는 이 저장소로 계획 순서에 맞춰 PR로 다시 옮깁니다.
