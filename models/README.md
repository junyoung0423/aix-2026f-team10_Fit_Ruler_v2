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

## 확인

```bash
SMPL_MODEL_PATH=models/smpl/basicmodel_neutral_lbs_10_207_0_v1.1.0.pkl python -m pytest -m smpl
python -m tools.smpl_body --model models/smpl/basicmodel_neutral_lbs_10_207_0_v1.1.0.pkl
```
