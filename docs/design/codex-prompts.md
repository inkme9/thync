# Codex 세션의 프롬프트 읽기

## 결정

`thync/sources/codex.py`는 Codex 로그의 두 형식에서 사용자가 친 글을 프롬프트로 읽습니다.

| 형식 | 줄 | 글이 있는 곳 |
| --- | --- | --- |
| 현재 형식 | `type`이 `response_item`, `payload.type`이 `message`, `payload.role`이 `user` | `payload.content`의 `input_text` 블록 `text` |
| 예전 형식 | `type`이 `event_msg`, `payload.type`이 `user_message` | `payload.message` |

확인한 로그에는 모두 현재 형식만 있었습니다. 한 컴퓨터에서는 Codex 0.144.6부터 0.169.0-alpha.11.1까지의 Codex Desktop, ChatGPT iOS 원격 실행, `codex exec` 로그 237개, 다른 컴퓨터에서는 Codex CLI 0.163.0·0.164.0-alpha.11.1·0.166.0 로그 37개입니다. 예전 형식만 읽으면 이 로그의 세션은 프롬프트가 0개여서 `load_sessions()`가 버립니다.

### 블록에서 사용자 글 고르기

현재 형식의 `role: user` 메시지에는 사용자가 친 글과 Codex가 넣은 문맥이 같은 모양의 `input_text` 블록으로 섞여 있습니다. `input_text`가 아닌 블록(`input_image` 등)은 `text`가 있어도 읽지 않습니다(`test_only_input_text_blocks_are_read`). `input_text` 블록마다 아래 순서로 판단합니다(`request()`).

1. 줄 첫머리에 `## My request` 제목이 있으면 마지막 제목 뒤의 글만 사용자 글입니다. Codex는 언급한 파일(`# Files mentioned by the user:`), 응답 주석(`# Response annotations:`), 앱 안 브라우저(`<in-app-browser-context>`) 같은 문맥을 사용자 글 앞에 붙이고, 그 사이에 이 제목을 넣습니다. 이미지를 붙인 메시지도 글이 이 블록 안에 있습니다. 제목 뒤가 비어 있으면(파일만 첨부했거나 응답 주석만 단 경우) 이 블록에는 사용자 글이 없습니다.
2. 제목이 없고 공백을 뺀 첫 글자가 `<` 또는 `#`이면 Codex가 넣은 문맥으로 보고 버립니다. 예: `<environment_context>`, `<codex_internal_context …>`, `# AGENTS.md instructions …`, `<image name=…>`, `</image>`, `<skill>`, `<external_browser_tabs>`.
3. 그 밖의 블록은 사용자 글입니다.

- 한 메시지에서 남은 사용자 글을 이어 프롬프트 하나로 셉니다. 남은 글이 없으면 프롬프트가 아닙니다.
- 연속된 공백과 줄바꿈은 예전 형식과 Claude Code 세션처럼 공백 하나로 줄입니다. 두 형식의 글을 비교할 때도 줄인 글끼리 비교합니다.
- 예전 형식의 `message`에는 2번 규칙을 쓰지 않고 1번만 씁니다. 예전 형식의 결과는 바뀌지 않습니다. 예전 형식의 글이 `<`로 시작하면 `event_msg` 쪽만 프롬프트가 되고 `response_item` 쪽은 2번 규칙으로 버려지므로, 두 형식이 함께 있어도 한 번 셉니다.
- `# Response annotations:` 블록의 `<response-annotations>` 안에는 사용자가 Codex 답의 일부(`text`)에 단 의견(`annotation`)이 JSON으로 들어 있습니다. 이 의견은 읽지 않습니다. `text`는 Codex 답을 인용한 것이어서, 블록을 통째로 읽으면 답 내용이 프롬프트가 됩니다.

문맥을 태그 이름 목록이 아니라 첫 글자로 가르는 이유:

- Codex가 새 문맥 종류를 계속 더합니다. 한 컴퓨터의 최근 7일 로그 236개에서 본 문맥 블록 머리말은 15종이었습니다. 15종 모두 `<`나 `#`으로 시작했습니다.
- 이 규칙이 사용자 글을 문맥으로 잘못 보지 않는지와, 다른 글자로 시작하는 문맥이 없는지는 같은 로그에서 따로 확인했습니다. 2번 규칙을 통과한 블록 가운데 Codex가 넣는 고정 문구(`Another language model…`, `The user interrupted…` 등)로 시작하는 것은 0개였습니다. `## My request` 제목 뒤의 글 30개에는 태그와 `#` 제목 줄이 없었습니다. 프롬프트가 나오지 않은 차례 384개는 모두 Codex 문맥, 질문에 대한 답, 파일만 첨부한 차례, 응답 주석만 단 차례였습니다.
- 문맥을 프롬프트로 잘못 세면 그 글이 세션 제목, MCP 응답, 허브가 가져가는 `/sessions.json`(`thync/export.py`, 프롬프트마다 300자까지)에 들어갑니다. 브라우저 탭과 페이지 내용도 문맥 블록에 있습니다. 모르는 태그를 버리는 쪽이 이 노출을 막습니다.
- Claude Code 세션도 `<`로 시작하는 사용자 메시지를 프롬프트로 세지 않습니다(`thync/sources/claude_code.py`).

### 질문에 대한 답

`<send_user_message_question_reply>` 블록은 Codex가 `request_user_input_async`로 물은 질문에 사용자가 한 답입니다. 2번 규칙에 따라 프롬프트로 세지 않습니다. Claude Code에서 `AskUserQuestion`에 대한 답이 `tool_result`로 기록되어 프롬프트가 아닌 것과 같습니다.

답을 기다리는 상태(`waiting`)는 바꾸지 않습니다. 지금처럼 질문 호출의 `function_call_output`이 오면 풀립니다. 이 질문은 비동기여서 `function_call_output`이 호출 뒤 1초 안에(확인한 104건 가운데 가장 늦은 것이 0.5초) 오고, 답 블록은 그보다 훨씬 뒤에 옵니다. 그래서 Codex 질문은 thync의 선택 대기 표시에 거의 나타나지 않습니다.

### 두 형식으로 기록된 입력

예전 Codex가 한 입력을 `response_item`과 `event_msg` 두 줄로 남겼다고 보고 대비합니다. 확인한 로그에는 `event_msg`/`user_message` 줄이 하나도 없어 이 형식은 확인하지 못했습니다. 한 입력을 두 번 세지 않도록 `CodexSession.prompt()`가 바로 앞 프롬프트의 줄 종류와 글(`last_prompt`)을 기억합니다.

- 새 프롬프트의 글이 바로 앞 프롬프트와 같고 줄 종류가 다르면 같은 입력으로 보고 세지 않습니다. 두 줄의 순서는 따지지 않습니다.
- 그렇게 짝을 맞춘 뒤에는 기억을 지웁니다. 다음 입력이 같은 글이어도 새 입력으로 셉니다.
- 줄 종류가 같으면 글이 같아도 새 입력입니다. 한 컴퓨터의 최근 7일 로그(현재 형식만 있음)에서 같은 세션에 같은 글이 다른 시각으로 다시 기록된 경우가 17건이었고, 15건은 10자 이하였습니다. 모두 새 차례(`task_started` 또는 `turn_context`) 뒤에 기록됐습니다.
- 기억은 세션 객체에 있으므로, 두 줄이 서로 다른 `update()` 호출에서 읽혀도 짝을 맞춥니다.

### 세는 세션

- 다른 Codex 스레드가 띄운 하위 세션은 지금처럼 세지 않습니다. 첫 `session_meta`의 `source`가 객체이거나 `thread_source`가 `"subagent"`이면 하위 세션입니다. `source`가 문자열인 하위 세션도 있기 때문입니다.
- `session_meta`는 첫 줄만 씁니다. 부모 스레드에서 갈라진 하위 세션의 로그는 자기 `session_meta` 뒤에 부모의 `session_meta`와 부모의 대화를 복사해 둡니다. 뒤의 `session_meta`를 쓰면 하위 세션이 부모의 id를 가진 일반 세션이 되고, 부모의 프롬프트가 한 번 더 세어집니다.
- `codex exec`로 실행한 세션(`session_meta.source`가 `"exec"`)은 일반 세션으로 셉니다. Claude Code의 `claude -p` 실행을 세는 것과 같습니다.

## 치르는 비용

- 사용자가 `<`나 `#`으로 시작하는 글(HTML 조각, Markdown 제목)을 `## My request` 제목 없이 바로 보내면 프롬프트로 세지 않습니다. 그 메시지만 있는 세션은 그래프에 나오지 않습니다.
- `codex exec` 실행이 세션마다 그래프에 나옵니다. 위의 7일 로그 236개에서 Codex 세션 144개가 나왔고 그 가운데 132개가 `exec`였습니다. 다른 도구가 띄운 실행이 많으면 그래프가 붐빕니다.
- 파일만 첨부하거나 응답 주석만 단 차례는 프롬프트가 되지 않으므로 그 차례의 활동 기록과 `last_at`도 생기지 않습니다. 위의 로그에서 각각 4개, 6개였습니다.
- 두 형식이 섞인 로그에서 한 형식의 줄이 빠지면, 그 뒤에 같은 글을 다시 친 입력 하나를 짝으로 보고 세지 않을 수 있습니다.

## 남는 위험

- Codex 로그 형식은 공개된 규격이 아닙니다. 사용자 글이 `<`나 `#`으로 시작하지 않는 다른 머리말 뒤에 오도록 바뀌면 문맥이 프롬프트로 들어가고, 위의 경로로 나갑니다. 2번 규칙에 걸리지 않는 문맥 블록은 이 형식 변화로만 생깁니다.
- 예전 형식의 두 줄이 실제로 어떤 순서로 기록됐는지는 확인한 로그가 없습니다. 짝 맞추기는 두 순서를 모두 처리합니다.
- 읽지 못하는 줄(`content`가 목록이 아니거나 블록이 객체가 아닌 줄)은 `Session.update()`가 잡는 예외(`JSONDecodeError`, `AttributeError`, `TypeError`)로 건너뜁니다. 다른 예외를 내는 형식이 생기면 그 세션 읽기가 실패합니다.
- `codex exec`를 세면서, 다른 프로그램이 쓴 프롬프트도 세션 제목, MCP 응답, `/sessions.json`에 들어갑니다. 이런 프롬프트에는 파일 내용이나 로그가 들어 있을 수 있습니다. `/sessions.json`은 접근 토큰이 있어야 읽을 수 있고(`thync/server.py`의 `authorized()`), 허브는 SSH로 각 서버의 토큰 파일을 읽어 이 요청에 씁니다(`thync/sources/remote.py`). MCP 서버는 같은 컴퓨터에서 실행됩니다.
- 부모 스레드를 사용자가 갈라 만든 세션(`thread_source`가 `"user"`이고 `forked_from_id`가 있음, 위의 로그에서 5개)은 일반 세션으로 셉니다. 이 로그들은 `session_meta`가 한 줄이고, 부모 로그가 남아 있는 4개는 부모와 같은 프롬프트가 하나도 없었습니다. 형식이 바뀌어 부모의 대화를 복사하게 되면 그 프롬프트가 두 세션에 함께 들어갑니다.
- 세션 제목은 바꾸지 않습니다. 현재 로그에는 `thread_name_updated`가 없어 Codex 세션의 제목은 마지막 프롬프트로 정해집니다.

## 상태가 바뀌는 경우

`CodexSession`은 로그를 이어 읽는 동안 `prompts`와 `last_prompt`를 메모리에 유지합니다. 파일이나 다른 저장소에는 쓰지 않습니다. 근거는 `tests/test_codex.py`와 `tests/test_model.py`의 테스트입니다.

| 상태 | 사건 | 결과 | 종류 | 근거 |
| --- | --- | --- | --- | --- |
| 프롬프트 없음 | 사용자 글이 든 `role: user` 메시지 | 프롬프트 1개, 활동 기록 1개, `last_at` 갱신. `load_sessions()`가 세션을 돌려줌 | introduced | `test_typed_text_is_a_prompt_and_the_session_shows` |
| 아무 상태 | 문맥 블록만 든 메시지(질문에 대한 답 포함) | 프롬프트, 활동 기록, `last_at`, `last_prompt` 모두 그대로 | introduced | `test_codex_context_is_not_a_prompt` |
| 아무 상태 | `## My request` 제목이 든 문맥 블록과 이미지 | 제목 뒤 글만 프롬프트 1개 | introduced | `test_text_after_my_request_with_an_image`, `test_in_app_browser_context_keeps_the_request` |
| 프롬프트 없음 | 예전 형식 `user_message` | 지금과 같이 `## My request` 뒤 글이 프롬프트 1개 | unchanged | `test_legacy_user_message_only`, `Parsing.test_codex_log` |
| 바로 앞 프롬프트가 다른 줄 종류의 같은 글 | 같은 입력의 두 번째 줄 | 세지 않음, `last_prompt` 지움 | introduced | `test_one_input_in_both_formats_counts_once` (두 순서, 여러 줄 글) |
| 짝을 맞춰 `last_prompt`를 지운 뒤 | 같은 글을 두 형식으로 다시 입력 | 프롬프트 하나 더 | introduced | `test_the_same_input_twice_in_both_formats_counts_twice` |
| 아무 상태 | 예전 형식에서 `<`로 시작하는 글이 두 형식으로 기록됨 | `event_msg` 쪽만 프롬프트 1개 | introduced | `test_legacy_text_starting_with_a_tag_counts_once` |
| 위와 같고, 두 줄이 다른 `update()` 호출에서 읽힘 | 같은 입력의 두 번째 줄 | 세지 않음 | introduced | `test_both_formats_split_across_reads` |
| 바로 앞 프롬프트가 같은 줄 종류의 같은 글 | 같은 글을 다시 입력 | 프롬프트 하나 더 | introduced | `test_same_text_typed_twice_counts_twice` (두 형식 각각) |
| 하위 세션(`source`가 객체) | 아무 줄 | 무시. 세션이 나오지 않음 | unchanged | `test_subagent_is_not_a_session_but_exec_is` |
| 하위 세션(`source`가 문자열, `thread_source`가 `"subagent"`) | 아무 줄 | 무시. 세션이 나오지 않음 | introduced | `test_thread_source_subagent_with_a_string_source` |
| 첫 `session_meta`를 읽음 | 두 번째 `session_meta`(갈라진 하위 세션의 부모 복사본) | 무시. id, `cwd`, 브랜치, 하위 세션 여부는 첫 줄의 값 그대로. 하위 세션이면 뒤의 복사된 프롬프트도 세지 않음 | introduced | `test_a_fork_keeps_its_own_session_meta` |
| `source`가 `"exec"` | 사용자 글 메시지 | 일반 세션으로 나옴 | introduced | `test_subagent_is_not_a_session_but_exec_is` |
| 아무 상태 | `content`가 문자열, `text`가 없음·`null`, 블록이 객체가 아님, JSON이 아닌 줄 | 그 줄만 건너뛰고 다음 줄을 읽음 | introduced | `test_malformed_lines_are_skipped` |
| 아무 상태 | 줄바꿈 없이 반쯤 쓴 마지막 줄 | 읽지 않고 남겨 두었다가, 줄이 끝난 뒤 다음 `update()`에서 읽음 | unchanged | `test_half_written_last_line_waits`, `Parsing.test_codex_log` |

- 같은 `CodexSession`을 두 스레드가 함께 읽는 경우는 없습니다. `load_sessions()`가 전역 잠금(`_lock`) 안에서만 `update()`를 부릅니다.
- 서버를 다시 켜면 로그를 처음부터 다시 읽으므로 `last_prompt`도 처음부터 다시 만들어집니다.
