---
name: learn
description: Learn 모드로 전환 (설명·힌트 중심, 프로젝트 파일 수정 차단)
disable-model-invocation: true
---

모드 전환은 UserPromptSubmit 훅이 `/learn` 입력을 가로채 처리한다. 이 본문이 보인다면 훅이 설치되지 않았거나 동작하지 않은 것이다.
사용자에게 `python3 install.py status` 로 하네스 설치 상태를 확인하라고 한 줄로 안내하고, 모드는 전환되지 않았다고 알려라.
