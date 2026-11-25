# my-harness

Claude Code용 경량 개인 개발 하네스. 모드(Learn / Collab / Normal)를 세션 중 즉시 전환하고, 모드별 행동 지침을 매 요청에 주입하며, Learn 모드에서는 프로젝트 파일 수정을 도구 수준에서 차단한다.

- Python 표준 라이브러리 + Markdown만 사용한다. 서버·DB·외부 의존성이 없다.
- 모드 전환은 훅이 직접 처리하므로 모델 호출과 토큰 소모가 없고 재시작도 필요 없다.

## 모드

| 모드 | 동작 | 강제 수준 |
|---|---|---|
| `/learn` | 원리 설명, 단계별 힌트, 직접 구현 유도. 완성 코드 제공 안 함 | 지침 + **프로젝트 파일 수정 도구 차단** |
| `/collab` | 설계 토론, 기술 비교, 리뷰. 명시적으로 요청할 때만 구현 | 지침만 |
| `/normal` | 하네스 제약 없음 (기본값) | 없음 |
| `/mode` | 현재 모드 확인 | - |

## 빠른 시작

요구사항: Python 3.8+, Claude Code.

```bash
git clone <저장소 URL> my-harness
cd my-harness
python3 install.py install --dry-run   # 변경 내용 미리보기
python3 install.py install              # ~/.claude 에 병합 (멱등)
python3 install.py status               # 설치 상태 확인
```

설치 후 **새 Claude Code 세션**에서 사용한다.

```
/learn      # 이후 질문에 힌트 위주로 응답, 파일 수정 불가
/collab     # 설계 토론
/normal     # 해제
/mode       # 현재 모드
```

### 업데이트 / 제거

```bash
git pull && python3 install.py install      # 업데이트
python3 install.py uninstall                # 우리 항목만 제거
python3 install.py uninstall --purge        # 상태 파일(~/.claude/harness/state)까지 삭제
```

저장소를 다른 위치로 옮기면 `install` 을 다시 실행한다.

### 설치기가 하는 일

- `~/.claude/settings.json` 에 우리 훅만 추가/제거한다. 수정 전 `settings.json.harness-bak-<시각>` 으로 백업한다.
- `skills/*` 를 `~/.claude/skills/` 에 심볼릭 링크한다. 같은 이름이 이미 있으면 덮어쓰지 않고 건너뛴다.
- `statusLine` 은 하나만 설정할 수 있다. 이미 있으면 건드리지 않고, 기존 스크립트에 이어 붙일 명령을 출력한다. 없을 때만 `🎓 LEARN` 같은 라벨을 설정한다.
- 설정 디렉터리는 `$CLAUDE_CONFIG_DIR` 또는 `~/.claude` 이며 `--claude-dir` 로 바꿀 수 있다.

## 동작 방식

- **상태**: `~/.claude/harness/state/<session_id>` JSON 파일(`mode`, `announced`). 세션별이라 터미널을 여러 개 써도 서로 영향이 없다. 30일 지난 파일은 SessionStart에서 정리한다. 새 세션의 기본 모드는 normal이다.
- **UserPromptSubmit 훅**
  - `/learn` `/collab` `/normal` `/mode` 는 훅이 직접 처리하고 `decision: block` 으로 모델 호출 없이 결과만 보여준다.
  - 그 외 프롬프트에는 현재 모드의 지침을 `additionalContext` 로 주입한다. Normal은 아무것도 주입하지 않는다.
  - 모드가 바뀐 직후 첫 프롬프트에는 "이전 모드 지침은 무효"라는 문구를 함께 주입해 대화에 남은 옛 지침과의 충돌을 막는다.
- **PreToolUse 훅** (Learn일 때만 동작): `Edit/Write/MultiEdit/NotebookEdit` 및 `Bash` 를 검사해 거부한다.
- **오류 처리**: 하네스 내부 오류는 fail-open이다(세션을 막지 않음).

## 구조

```
prompts/{learn,collab,normal}.md   모드별 행동 지침 (에이전트 비종속, 지침은 여기만 고치면 됨)
harness/core.py                    상태 파일, 지침 조합, Learn 정책 (에이전트 비종속)
harness/claude.py                  Claude Code 훅/statusLine 어댑터 (stdin JSON ↔ core)
skills/{learn,collab,normal,mode}  슬래시 명령 등록(자동완성). 실제 처리는 훅
install.py                         설치/제거
tests/test_harness.py              unittest
```

다른 에이전트를 지원하려면 `harness/<agent>.py` 어댑터를 추가한다. core는 에이전트에 대한 지식이 없다.

## 제약사항

- **프롬프트 제한 ≠ 도구 제한.** Learn의 "코드 주지 않기"는 지침일 뿐 모델이 어길 수 있다. 실제로 차단되는 것은 파일 수정 도구와 일부 Bash뿐이다. Collab의 "요청 시에만 구현"도 지침 수준이다.
- **Bash 차단은 best-effort 휴리스틱**이며 샌드박스가 아니다.
  - 막는 것: `>` 리다이렉션, `rm/mv/cp/touch/mkdir`, `sed -i`, `tee`, 변경성 `git` 하위 명령 등. `cp`/`mv` 는 프로젝트 밖 대상이어도 막는다(과차단).
  - 못 막는 것: `python -c`, `make`, `npm install`, 스크립트 내부 쓰기 등.
- Learn은 프로젝트 **디렉터리 밖** 파일(메모리, 임시 폴더 등) 수정은 허용한다. 기준은 훅 입력의 `cwd` 다.
- `/learn` 등은 훅이 가로채므로 훅이 없으면 skill 본문이 안내만 출력한다. `/mode` 응답은 `reason` 으로 표시되며, 표시 방식은 Claude Code 버전에 따라 다를 수 있다.
- 컴팩션 후에도 다음 프롬프트에서 지침이 다시 주입된다.
- 워크스페이스 신뢰(trust) 수락 전에는 훅이 동작하지 않는다.
- Claude Code만 지원한다.

## 테스트

```bash
python3 -m unittest discover -s tests
```

단위/통합 테스트가 훅을 실제 서브프로세스로 호출해 모드 전환, 지침 주입, Learn 차단, 설치/제거(기존 설정 보존)를 검증한다.

실제 세션에서의 수동 검증: 설치 후 `/learn` → "hello.txt 만들어줘" 요청 시 Write가 거부되는지, `/collab`·`/normal` 후에는 허용되는지 확인한다.
