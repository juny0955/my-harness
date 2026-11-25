---
name: mode
description: 현재 하네스 모드(learn/collab/normal) 확인
disable-model-invocation: true
---

현재 모드 조회는 UserPromptSubmit 훅이 `/mode` 입력을 가로채 처리한다. 이 본문이 보인다면 훅이 설치되지 않았거나 동작하지 않은 것이다.
사용자에게 `python3 install.py status` 로 하네스 설치 상태를 확인하라고 한 줄로 안내하라.
