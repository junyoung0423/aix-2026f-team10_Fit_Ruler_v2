# models/

외부에서 받은 모델 파일을 두는 곳이다. **이 폴더의 파일은 커밋하지 않는다.** SMPL 모델은 재배포가 금지돼 있다(계획서 규칙 9). 이 README와 `.gitignore`만 저장소에 들어간다.

## SMPL

1. [SMPL 공식 사이트](https://smpl.is.tue.mpg.de)에 가입하고 라이선스에 동의한다. 가입과 동의는 각자 직접 한다.
2. Download 메뉴에서 **SMPL for Python** (v1.1.0)을 받는다.
3. 압축을 풀고 모델 파일(`.pkl`)을 `models/smpl/` 아래에 둔다. 중성 모델을 기본으로 쓰고, 남성·여성 모델은 필요할 때 쓴다. 아래 파일 이름은 예시이므로, 받은 파일 이름이 다르면 그 이름을 그대로 쓰면 된다.

```text
models/smpl/basicmodel_neutral_lbs_10_207_0_v1.1.0.pkl   # 중성(기본으로 쓴다)
models/smpl/basicmodel_m_lbs_10_207_0_v1.1.0.pkl         # 남성(선택)
models/smpl/basicmodel_f_lbs_10_207_0_v1.1.0.pkl         # 여성(선택)
```

공식 `.pkl`에는 chumpy 객체가 들어 있지만 chumpy를 설치할 필요는 없다. `fitting/adapters/smpl`이 배열만 꺼내 읽는다. 읽기에 실패하면 chumpy를 뺀 `.npz`로 바꿔서 써도 된다(키 이름은 같다).

## 라이선스에서 지킬 것

SMPL은 비상업적 연구·교육용 라이선스다([라이선스 전문](https://smpl.is.tue.mpg.de/modellicense.html)).

- **각자 받는다**: 라이선스는 1인용이고 파일 공유가 금지돼 있다. 모델을 쓰는 팀원은 각자 가입해 받는다. 다른 PC에서 재현할 때도 마찬가지다.
- **커밋하지 않는다**: 모델 파일은 저장소나 공유 드라이브에 올리지 않는다.
- **논문을 인용한다**: 보고서·발표에 Loper et al., "SMPL: A Skinned Multi-Person Linear Model," ACM TOG (SIGGRAPH Asia) 2015를 적는다.
- **상업적으로 쓰지 않는다**: 상용화하려면 별도 상업 라이선스가 필요하다.

## 확인

```bash
SMPL_MODEL_PATH=models/smpl/basicmodel_neutral_lbs_10_207_0_v1.1.0.pkl python -m pytest -m smpl
python -m tools.smpl_body --model models/smpl/basicmodel_neutral_lbs_10_207_0_v1.1.0.pkl
```
