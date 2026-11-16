# Codex 세션의 프롬프트 읽기

## 결정

`thync/sources/codex.py`는 Codex 로그의 두 형식에서 사용자가 친 글을 프롬프트로 읽습니다.

| 형식 | 줄 | 글이 있는 곳 |
| --- | --- | --- |
| 현재 형식 | `type`이 `response_item`, `payload.type`이 `message`, `payload.role`이 `user` | `payload.content`의 `input_text` 블록 `text` |
| 예전 형식 | `type`이 `event_msg`, `payload.type`이 `user_message` | `payload.message` |

Codex 0.144.6부터 0.169.0-alpha.11.1까지(Codex CLI, Codex Desktop, ChatGPT iOS 원격 실행, `codex exec`)가 쓴 로그에는 현재 형식만 있습니다. 예전 형식만 읽으면 이 로그의 세션은 프롬프트가 0개여서 `load_sessions()`가 버립니다.

### 블록에서 사용자 글 고르기

현재 형식의 `role: user` 메시지에는 사용자가 친 글과 Codex가 넣은 문맥이 같은 모양의 `input_text` 블록으로 섞여 있습니다. 블록마다 아래 순서로 판단합니다(`request()`).

1. 줄 첫머리에 `## My request` 제목이 있으면 마지막 제목 뒤의 글만 사용자 글입니다. Codex는 언급한 파일(`# Files mentioned by the user:`), 응답 주석(`# Response annotations:`), 앱 안 브라우저(`<in-app-browser-context>`) 같은 문맥을 사용자 글 앞에 붙이고, 그 사이에 이 제목을 넣습니다. 이미지를 붙인 메시지도 글이 이 블록 안에 있습니다.
2. 제목이 없고 공백을 뺀 첫 글자가 `<` 또는 `#`이면 Codex가 넣은 문맥으로 보고 버립니다. 예: `<environment_context>`, `<codex_internal_context …>`, `# AGENTS.md instructions …`, `<image name=…>`, `</image>`, `<skill>`, `<external_browser_tabs>`.
3. 그 밖의 블록은 사용자 글입니다.

- 한 메시지에서 남은 사용자 글을 공백 하나로 이어 프롬프트 하나로 셉니다. 남은 글이 없으면 프롬프트가 아닙니다.
- 공백은 Claude Code 세션처럼 하나로 줄입니다.
- 예전 형식의 `message`에는 2번 규칙을 쓰지 않고 1번만 씁니다. 예전 형식의 결과는 바뀌지 않습니다.

문맥을 태그 이름 목록이 아니라 첫 글자로 가르는 이유:

- Codex가 새 문맥 종류를 계속 더합니다. 한 컴퓨터의 최근 7일 로그 236개에서 본 문맥 블록 머리말은 15종이었습니다. 15종 모두 `<`나 `#`으로 시작했습니다.
- 문맥을 프롬프트로 잘못 세면 그 글이 세션 제목, MCP 응답, 다른 thync 서버로 보내는 `/sessions.json`(`thync/export.py`)에 들어갑니다. 브라우저 탭과 페이지 내용도 문맥 블록에 있습니다. 모르는 태그를 버리는 쪽이 이 노출을 막습니다.
- Claude Code 세션도 `<`로 시작하는 사용자 메시지를 프롬프트로 세지 않습니다(`thync/sources/claude_code.py`).

### 질문에 대한 답

`<send_user_message_question_reply>` 블록은 Codex가 `request_user_input`으로 물은 질문에 사용자가 한 답입니다. 2번 규칙에 따라 프롬프트로 세지 않습니다. Claude Code에서 `AskUserQuestion`에 대한 답이 `tool_result`로 기록되어 프롬프트가 아닌 것과 같습니다. 답을 기다리는 상태(`waiting`)는 지금처럼 `function_call_output`으로 풀립니다.

### 두 형식으로 기록된 입력

예전 Codex는 한 입력을 `response_item`과 `event_msg` 두 줄로 남긴 적이 있습니다. 한 입력을 두 번 세지 않도록 `CodexSession.prompt()`가 바로 앞 프롬프트의 줄 종류와 글(`last_prompt`)을 기억합니다.

- 새 프롬프트의 글이 바로 앞 프롬프트와 같고 줄 종류가 다르면 같은 입력으로 보고 세지 않습니다. 두 줄의 순서는 따지지 않습니다.
- 그렇게 짝을 맞춘 뒤에는 기억을 지웁니다. 다음 입력이 같은 글이어도 새 입력으로 셉니다.
- 줄 종류가 같으면 글이 같아도 새 입력입니다. 현재 형식만 있는 로그에서 짧은 답(`ok`, `ㅇㅇ`)을 두 번 친 경우가 한 컴퓨터의 최근 7일 로그에서 17건이었습니다.
- 기억은 세션 객체에 있으므로, 두 줄이 서로 다른 `update()` 호출에서 읽혀도 짝을 맞춥니다.

### 세는 세션

- 다른 Codex 스레드가 띄운 하위 세션(`session_meta.source`가 객체)은 지금처럼 세지 않습니다.
- `codex exec`로 실행한 세션(`session_meta.source`가 `"exec"`)은 일반 세션으로 셉니다. Claude Code의 `claude -p` 실행을 세는 것과 같습니다.

## 치르는 비용

- 사용자가 `<`나 `#`으로 시작하는 글(HTML 조각, Markdown 제목)을 `## My request` 제목 없이 바로 보내면 프롬프트로 세지 않습니다. 그 메시지만 있는 세션은 그래프에 나오지 않습니다.
- `codex exec` 실행이 세션마다 그래프에 나옵니다. 위의 7일 로그 236개에서 세션 151개가 나왔고 그 가운데 133개가 `exec`였습니다. 다른 도구가 띄운 실행이 많으면 그래프가 붐빕니다.
- 두 형식이 섞인 로그에서 한 형식의 줄이 빠지면, 그 뒤에 같은 글을 다시 친 입력 하나를 짝으로 보고 세지 않을 수 있습니다.

## 남는 위험

- Codex 로그 형식은 공개된 규격이 아닙니다. 사용자 글이 `<`나 `#`으로 시작하지 않는 다른 머리말 뒤에 오도록 바뀌면 문맥이 프롬프트로 들어가고, 위의 경로로 나갑니다. 2번 규칙에 걸리지 않는 문맥 블록은 이 형식 변화로만 생깁니다.
- 예전 형식의 두 줄이 실제로 어떤 순서로 기록됐는지는 확인한 로그가 없습니다. 짝 맞추기는 두 순서를 모두 처리합니다.
- 읽지 못하는 줄(`content`가 목록이 아니거나 블록이 객체가 아닌 줄)은 `Session.update()`가 잡는 예외(`JSONDecodeError`, `AttributeError`, `TypeError`)로 건너뜁니다. 다른 예외를 내는 형식이 생기면 그 세션 읽기가 실패합니다.
- 세션 제목은 바꾸지 않습니다. 현재 로그에는 `thread_name_updated`가 없어 Codex 세션의 제목은 마지막 프롬프트로 정해집니다.

## 상태가 바뀌는 경우

`CodexSession`은 로그를 이어 읽는 동안 `prompts`와 `last_prompt`를 메모리에 유지합니다. 파일이나 다른 저장소에는 쓰지 않습니다. 근거는 `tests/test_codex.py`와 `tests/test_model.py`의 테스트입니다.

| 상태 | 사건 | 결과 | 종류 | 근거 |
| --- | --- | --- | --- | --- |
| 프롬프트 없음 | 사용자 글이 든 `role: user` 메시지 | 프롬프트 1개, 활동 기록 1개, `last_at` 갱신. `load_sessions()`가 세션을 돌려줌 | introduced | `test_typed_text_is_a_prompt_and_the_session_shows` |
| 아무 상태 | 문맥 블록만 든 메시지(질문에 대한 답 포함) | 프롬프트, 활동 기록, `last_at`, `last_prompt` 모두 그대로 | introduced | `test_codex_context_is_not_a_prompt` |
| 아무 상태 | `## My request` 제목이 든 문맥 블록과 이미지 | 제목 뒤 글만 프롬프트 1개 | introduced | `test_text_after_my_request_with_an_image`, `test_in_app_browser_context_keeps_the_request` |
| 프롬프트 없음 | 예전 형식 `user_message` | 지금과 같이 `## My request` 뒤 글이 프롬프트 1개 | unchanged | `test_legacy_user_message_only`, `Parsing.test_codex_log` |
| 바로 앞 프롬프트가 다른 줄 종류의 같은 글 | 같은 입력의 두 번째 줄 | 세지 않음, `last_prompt` 지움 | introduced | `test_one_input_in_both_formats_counts_once` (두 순서) |
| 위와 같고, 두 줄이 다른 `update()` 호출에서 읽힘 | 같은 입력의 두 번째 줄 | 세지 않음 | introduced | `test_both_formats_split_across_reads` |
| 바로 앞 프롬프트가 같은 줄 종류의 같은 글 | 같은 글을 다시 입력 | 프롬프트 하나 더 | introduced | `test_same_text_typed_twice_counts_twice` (두 형식 각각) |
| 하위 세션(`source`가 객체) | 아무 줄 | 무시. 세션이 나오지 않음 | unchanged | `test_subagent_is_not_a_session_but_exec_is` |
| `source`가 `"exec"` | 사용자 글 메시지 | 일반 세션으로 나옴 | introduced | `test_subagent_is_not_a_session_but_exec_is` |
| 아무 상태 | `content`가 문자열, `text`가 없음·`null`, 블록이 객체가 아님, 반쯤 쓴 줄 | 그 줄만 건너뛰고 다음 줄을 읽음 | introduced | `test_malformed_lines_are_skipped` |

- 같은 `CodexSession`을 두 스레드가 함께 읽는 경우는 없습니다. `load_sessions()`가 전역 잠금(`_lock`) 안에서만 `update()`를 부릅니다.
- 서버를 다시 켜면 로그를 처음부터 다시 읽으므로 `last_prompt`도 처음부터 다시 만들어집니다.
