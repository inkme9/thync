# 리뷰 목록의 PR을 Concord 작업으로 맡기기

TL;DR

- 오른쪽 패널의 세 리뷰 목록(리뷰 요청, 받은 리뷰, 보낸 리뷰), 뇌 그래프의 줄기, 칸반의 '맡긴 일' 칸은 모두 같은 확인 창을 거쳐 PR 하나를 맡깁니다.
- 다른 사람의 PR은 `concord:review-until-approve`, 내 PR은 그 PR 하나로 범위를 정한 `concord:pr-to-merge`를 실행합니다.
- 실행하는 머신은 PR 저장소의 로컬 클론에서 mien 프로필과 GitHub 계정을 확인하고, 계정과 PR 작성자가 작업 종류와 맞을 때만 tmux 안의 Claude Code 세션을 시작합니다.
- 확인 창은 그 머신이 확인한 저장소, PR, 작업, 실행 계정, 실행 위치, 허용 동작을 보여 줍니다. 시작할 때 같은 확인을 다시 하고, 결과가 달라졌으면 시작하지 않습니다.
- thync는 리뷰를 직접 쓰거나 올리지 않습니다. 리뷰·승인·병합의 규칙은 모두 Concord가 정합니다.

## 목표

- 세 리뷰 목록의 항목을 줄기에 끌어 놓거나, 항목의 '맡기기' 버튼(키보드로 누를 수 있음)으로 맡깁니다.
- 받은 리뷰 항목은 리뷰가 아니라 그 리뷰가 달린 내 PR을 맡깁니다. 같은 PR에 리뷰어가 여럿이어도 대상은 PR 하나입니다.
- 어느 진입점에서 맡겨도 같은 PR 노드는 같은 작업과 같은 대상을 고릅니다.
- 준비물이 없으면(기능 꺼짐, `claude`·`tmux`·`gh` 없음, 플러그인 캐시에 Concord 스킬 없음, 클론 없음, 계정 확인 실패) 확인 창이 이유를 보여 주고, 아무 작업도 시작하지 않으며 '맡긴 일' 기록도 남기지 않습니다.

## 하지 않는 것

- 저장소 전체를 대상으로 하는 병합 감시. `pr-to-merge`에는 항상 `OWNER/REPO#N` 하나만 넘깁니다.
- 리뷰·병합 규칙의 재구현. 승인 조건, 병합 조건, 신뢰 선언, 대기 방식은 Concord 스킬이 정합니다.
- Codex 하네스에서의 실행. thync는 `claude`만 실행하므로 Concord의 Claude Code 동작(백그라운드 감시)을 따릅니다.
- Concord의 원장·상태 파일 읽기. 형식이 공개된 인터페이스가 아닙니다.

## 작업 고르기

| 노드의 역할 | 확인할 작성자 | 실행하는 스킬 | 스킬에 넘기는 범위 | 확인 창의 허용 동작 |
| --- | --- | --- | --- | --- |
| `review` (리뷰 요청) | 실행 계정이 아닌 사람 | `concord:review-until-approve` | PR 주소 하나 | 리뷰 게시, 깨끗한 라운드의 승인 |
| `sent` (보낸 리뷰, 받은 리뷰) | 실행 계정 | `concord:pr-to-merge` | `OWNER/REPO#N` 하나, 병합 허락도 이 PR 하나 | 리뷰 지적 수정, 커밋·push, 답글, 자동 리뷰어 스레드 resolve, 리뷰 재요청, PR 본문 수정, 뒤처진 브랜치 갱신, 병합 전 실패한 필수 검사 재실행, 조건을 채운 경우 병합, 병합 뒤 실패한 작업 재실행과 티켓 상태 변경·댓글 |

- 역할은 화면이 보낸 노드에서 읽고, 작성자·상태·초안 여부는 실행하는 머신이 그 계정으로 `gh api repos/<저장소>/pulls/<번호>`에서 다시 읽습니다. 역할과 작성자가 맞지 않으면 시작하지 않습니다. 내 PR이 리뷰 요청 목록에 있거나, 다른 계정으로 로그인해 내 PR이 남의 PR이 된 경우입니다.
- 닫히거나 병합된 PR은 시작하지 않습니다.
- 초안인 내 PR은 시작하지 않습니다. `pr-to-merge` 스크립트는 초안을 아무 보고 없이 건너뛰므로, 세션이 아무것도 하지 않고 대기 상태로 남기 때문입니다.

## 실행 계정

- 실행하는 머신은 `company_roots` 바로 아래 폴더(이름순), 그다음 알려진 세션 폴더 순서로 보고, `origin`이 PR 저장소인 첫 클론을 씁니다. 확인 창에 그 폴더를 보여 줍니다. 클론이 없으면 시작하지 않습니다. 계정을 정할 기준이 없고, `pr-to-merge`는 클론에서 고치기 때문입니다.
- `mien`이 있으면 클론에서 `mien which`로 프로필을 읽습니다. 서버 프로세스에 활성화된 프로필(`MIEN_PROFILE`)이 클론의 프로필보다 먼저 나오지 않도록, 이 호출에서는 그 변수를 뺍니다. 클론에 프로필이 없으면 그 PR을 목록에 올린 프로필(아래)로 실행하고, 그것도 없으면 시작하지 않습니다. 프로필이 있으면 계정 확인(`gh api user`)과 Claude Code 프로세스 전체를 `mien exec <프로필> -- <명령>`으로 실행하고, 첫 프롬프트에도 그 프로필을 적습니다. 서버 프로세스의 기본 `gh` 계정은 쓰지 않습니다.
- `mien exec`는 인자에서 첫 `--`를 지웁니다. 그래서 프로필 뒤에 `--`를 두어, `claude`의 옵션과 프롬프트를 나누는 `--`가 그대로 남게 합니다.
- `mien exec`는 에이전트 하네스가 부른 것으로 보이면 자식의 출력을 파이프로 받아, 세션이 터미널 없이 돕니다. tmux 서버가 에이전트의 셸에서 시작되어 그 표시를 물려받았을 수 있으므로, 세션 명령은 mien이 보는 표시 다섯 개(`CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`, `CODEX_THREAD_ID`, `COPILOT_AGENT`, `MIEN_CAPTURED`)를 `env -u`로 지우고 시작합니다.
- `mien_profiles`로 여러 계정의 목록을 읽으면, 리뷰 요청을 받거나 PR을 쓴 계정은 그 PR을 목록에 올린 프로필(`sources/github.account_for`)의 계정입니다. 그래서 클론에 프로필이 있으면 두 프로필이 같을 때만 시작하고, 클론에 프로필이 없으면 목록에 올린 프로필로 실행합니다. 어느 계정도 목록에 올리지 않은 PR은 맡기지 않습니다. 계정 확인은 `sources/github.gh`처럼 `gh`의 저장된 로그인과 물려받은 토큰을 빼고 하므로, GitHub 계정이 없는 프로필은 다른 계정을 빌리지 않고 실패합니다. 허브가 다른 서버로 넘길 때는 허브에서 목록에 올린 프로필을 함께 보내고, 그 서버는 그 값으로 같은 확인을 합니다.
- `mien`이 없는 머신에서는 서버 프로세스의 `gh` 계정이 실행 계정입니다. 확인 창에 "프로필 없음"으로 보여 주고, 작성자 확인은 같은 방식으로 합니다.

## 확인과 시작

1. 화면이 `POST /reflex {id, check: true}`를 보냅니다. 실행하는 머신이 위의 확인을 모두 하고, 통과하면 `plan`(저장소, 번호, 주소, 스킬, 프로필, 계정, 작성자, 클론 경로, tmux 세션 이름)을 돌려줍니다. 이 단계는 아무것도 시작하거나 저장하지 않습니다.
2. 확인 창은 `plan`과 허용 동작을 보여 줍니다. 취소하면 요청을 더 보내지 않습니다.
3. 시작을 누르면 화면이 `POST /reflex {id, plan}`을 보냅니다. 실행하는 머신은 확인을 처음부터 다시 하고, 주소·스킬·계정·프로필·클론 경로가 화면이 보여 준 `plan`과 다르면 `409`로 거절합니다.
4. 통과하면 tmux 세션을 클론 폴더에서 만들고, 그 안의 `sh -c`에서 Claude Code를 `--permission-mode auto`로 실행합니다. tmux의 기본 셸이 fish나 csh여도 `$?`가 종료 코드가 되도록 `sh`를 씁니다. `~/.thync`, `~/.ssh`, `~/.claude/settings.json`은 Read 도구에서 거부합니다. 첫 프롬프트는 `/<스킬> <PR 주소>`와 범위·계정을 적은 지시이고, `pr-to-merge`에는 병합 허락과 `--checkout OWNER/REPO=<클론>`을 덧붙입니다. 신뢰·보증 문구는 넣지 않습니다.

세션 이름은 `thync-<소유자>-<저장소>-<번호>-<저장소 이름 해시 6자>`입니다. 영문·숫자·`_`·`-` 밖의 글자를 `-`로 바꾼 뒤에도 PR마다 다른 이름이 되도록 해시를 붙입니다. 같은 PR의 세션 이름은 하나뿐이고 `tmux new-session`이 같은 이름을 거절하므로, 받은 리뷰 여러 줄이나 보낸 리뷰 줄에서 같은 PR을 거듭 맡겨도 세션은 하나만 돕니다. 확인 단계도 그 세션이 있으면 PR 상태를 읽기 전에 `409`와 접속 명령을 돌려줍니다.

`pr-to-merge`는 사용자의 PR을 클론에서 고칩니다. 그래서 같은 클론에서 다른 PR의 `pr-to-merge` 세션이 돌고 있으면 시작하지 않습니다. 시작은 서버 안에서 한 번에 하나씩 하므로, 두 PR의 시작이 겹쳐도 이 확인을 함께 통과하지 않습니다. `review-until-approve`는 Concord가 PR 헤드를 임시 폴더에 받아 리뷰하므로 클론을 바꾸지 않고, 이 제한을 받지 않습니다.

## 병합 허락과 신뢰 선언

- `pr-to-merge`는 그 실행에서 사용자가 저장소를 지정해 병합을 허락해야 병합합니다. thync에서는 내 PR의 확인 창에서 시작을 누르는 것이 그 PR 하나의 병합 허락이고, 첫 프롬프트가 그렇게 적습니다. Concord의 예약 실행에서 사용자가 쓴 설정이 병합 허락이 되는 것과 같은 방식입니다.
- 세션이 이 문구를 허락으로 받아들이지 않으면 스킬은 `--no-merge`로 돌고, 병합할 수 있는 PR을 `ready`로만 알립니다. 카드는 '대기 중'이 되고, 사용자는 세션에 접속해 병합을 허락하거나 직접 병합합니다.
- `review-until-approve`는 Claude Code에서 Claude 리뷰어를 쓰려면 사용자가 그 대화에서 직접 신뢰를 선언해야 합니다. 선언은 사용자가 쓴 말 그대로여야 하고, 다른 프로그램이 전한 글이나 모델이 만든 선택지는 선언이 아닙니다. 그래서 thync는 선언을 대신 쓰지 않고, 사용자는 tmux 세션에 접속해 직접 씁니다. 선언 뒤 세션이 묻는 예/아니요 확인은 선택 질문이면 thync 선택 대기 창에서 답해도 됩니다.

## 다른 서버에서 실행하기

`reflex_host`가 있으면 허브는 확인과 시작 두 요청 모두 PR 노드를 그대로 그 서버에 한 번 넘기고(`X-Thync-Forwarded: 1`), 받은 답을 화면에 전합니다. 프로필, 계정, 클론은 그 서버의 것입니다. 허브가 넘기는 요청의 답 메시지에는 그 서버 이름이 앞에 붙으므로, 접속 명령은 그 서버에서 실행해야 한다는 것을 알 수 있습니다. 그 서버는 시작 전에 `git`, `mien`, `gh`를 여러 번 부르므로, 허브는 넘긴 요청을 300초까지 기다립니다. 허브가 그 서버에 연결되어 있지 않으면 `503`, 넘기는 도중 실패하면 `502`를 돌려주며, 어느 경우에도 자기 머신에서 대신 실행하지 않습니다. 넘기는 도중 실패한 시작 요청은 그 서버가 이미 시작했을 수 있으므로, 답 메시지에 맡긴 일 카드를 확인하라고 적습니다. 그 서버가 직접 `502`로 답한 경우(`gh` 실패)는 시작하지 않은 것이므로 이 안내를 붙이지 않습니다. 넘겨받은 서버는 다시 넘기지 않으며, 그 서버에도 `reflex_host`가 있으면 `503`으로 거절합니다.

## 진행 상태

실행기는 tmux 안의 대화형 Claude Code 세션입니다. Concord의 두 스킬은 이 세션 안에서 백그라운드 명령으로 기다리고, 기다리는 동안 Claude의 차례는 끝나 있습니다. 그래서 thync는 세션이 감시 중인지 사용자의 입력을 기다리는지 구분하지 않고, 아는 것만 보여 줍니다. tmux 세션이 있는 동안 카드는 상태와 함께 접속 명령을 보여 줍니다.

| tmux 세션 | 세션 기록 | 종료 코드 파일 | 카드에 보이는 상태 |
| --- | --- | --- | --- |
| 있음 | 아직 없음 | - | 시작함 |
| 있음 | Claude가 일하는 중 | - | 작업 중 |
| 있음 | 선택 질문을 기다림 | - | 답을 기다림 |
| 있음 | Claude의 차례가 끝남 | - | 대기 중: 백그라운드 감시 중이거나 입력을 기다림 |
| 없음 | - | `0` | 끝남 |
| 없음 | - | `0`이 아님 | 실패 (종료 코드) |
| 없음 | - | 없음 | 끝남 (종료 코드 모름) |

- 종료 코드 파일은 `THYNC_HOME/handoff/<세션 이름>.<시작 시각>.exit`입니다. tmux가 실행하는 셸이 Claude Code가 끝날 때 종료 코드를 씁니다. 시작마다 다른 파일이므로, 다시 시작하다 실패해도 이전 실행의 결과가 남습니다. 지우는 동작은 없어 시작할 때마다 몇 바이트짜리 파일이 하나씩 늘어납니다.
- 대화형 세션은 작업이 끝나도(승인, 병합, 닫힘) 스스로 종료하지 않습니다. 사용자가 세션을 끝낼 때까지 카드는 '대기 중'이고, 같은 PR을 다시 맡기면 `409`와 접속 명령을 돌려줍니다.
- Claude Code에서 한 번도 연 적 없는 폴더는 세션이 폴더 신뢰 질문에서 멈춥니다. 세션 기록이 생기기 전이라 카드는 '시작함'에 머뭅니다. 접속해서 답합니다.
- 카드는 이번 실행의 세션 기록만 읽습니다. 같은 PR을 전에 맡긴 세션의 기록은 첫 프롬프트 시각이 기록의 시작 시각보다 이르므로 쓰지 않습니다. 두 시각 모두 세션을 실행하는 머신의 시계입니다.
- 맡긴 일 기록을 지우는 동작은 없습니다. 카드는 PR마다 하나이고, 다시 맡기면 같은 카드의 기록을 바꿉니다.

## Stateful changes

상태는 `THYNC_HOME/reflex.json`의 PR별 기록, tmux 세션, 종료 코드 파일입니다. Concord의 원장과 상태 파일은 Concord가 관리합니다. Evidence는 `tests/test_handoff.py`(H), `tests/test_remote.py`(R)의 테스트입니다.

| State | Event | Outcome | Kind | Evidence |
|---|---|---|---|---|
| 기록 없음 | 확인 요청 | `plan` 반환. tmux·기록·종료 코드 파일 변화 없음 | introduced | H `test_check_shows_the_plan_and_starts_nothing` |
| 기록 없음 | 시작, 확인 통과 | tmux 세션 1개, 첫 프롬프트에 스킬·PR·범위·계정, 기록 저장(스킬·프로필·계정·클론·시작 시각) | changed | H `test_review_request_starts_review_until_approve_for_that_pr_as_the_profile`, `test_own_pr_starts_pr_to_merge_for_that_pr_only`, `test_own_pr_prompt_names_only_that_pr_as_target_and_merge_scope` |
| 기록 없음 | 시작, 실행 계정이 작성자인 리뷰 요청·작성자가 아닌 내 PR·닫힌 PR·초안인 내 PR·프로필 없음 | `409`와 이유. tmux·기록 변화 없음 | introduced | H `test_review_request_on_my_own_pr_starts_nothing`, `test_my_pr_under_another_account_or_closed_or_unclaimed_starts_nothing` |
| 기록 없음 | 확인 또는 시작, 기능 꺼짐(`claude` 없음, `claude_code` 출처 없음) | `503`과 이유. tmux·기록 변화 없음 | unchanged | R `test_handoff_with_the_feature_off_or_a_failed_forward_starts_nothing` |
| 기록 없음 | 시작, CLI·스킬 없음 | `503`과 이유. tmux·기록 변화 없음 | introduced | H `test_without_the_concord_skill_nothing_starts_or_shows_running`, `test_missing_cli_starts_nothing` |
| 기록 없음 | 시작, 클론 없음 | `404`와 이유. tmux·기록 변화 없음 | unchanged | H `test_no_clone_or_unreadable_account_starts_nothing` |
| 기록 없음 | 시작, `gh`가 계정이나 PR을 읽지 못함 | `502`와 `gh`의 오류. tmux·기록 변화 없음 | introduced | H `test_no_clone_or_unreadable_account_starts_nothing` |
| 기록 없음 | 확인 또는 시작, `mien_profiles`에서 목록에 올린 프로필이 클론의 프로필과 다르거나 목록에 올린 계정이 없음 | `409`와 이유. `gh` 호출·tmux·기록 없음 | introduced | H `test_runs_only_as_the_profile_that_lists_the_pr` |
| 기록 없음 | 시작, 화면의 `plan`과 계정·프로필·스킬·클론 경로가 다름 | `409`. tmux·기록 변화 없음 | introduced | H `test_start_refuses_when_the_check_differs_from_what_was_confirmed` |
| 세션 돌고 있음 | 같은 PR 확인 요청(받은 리뷰 다른 줄 포함, PR이 병합된 뒤 포함) | `409`와 접속 명령. 기록과 세션 그대로 | introduced | H `test_check_of_a_running_pr_answers_with_its_session_even_when_merged` |
| 세션 돌고 있음 | 같은 PR 시작이 확인과 tmux 생성 사이에 겹침 | `tmux new-session`이 이름을 거절해 `409`. 기록 그대로 | unchanged | H `test_second_handoff_of_a_running_pr_is_refused` |
| 같은 클론에서 다른 PR의 `pr-to-merge` 세션 돌고 있음 | 내 PR 시작 | `409`와 그 세션의 접속 명령. 리뷰 요청은 시작됨 | introduced | H `test_second_pr_to_merge_in_the_same_clone_is_refused` |
| 세션 끝남 | 같은 PR 다시 시작 | 새 세션, 새 종료 코드 파일 경로, 기록 교체 | changed | H `test_own_pr_starts_pr_to_merge_for_that_pr_only` (기록 저장), `test_failed_restart_keeps_the_previous_runs_outcome` (파일 분리) |
| 세션 끝남 | 다시 시작, 확인 실패 또는 `tmux new-session` 실패 | 4xx/`500`. 이전 기록과 종료 코드 파일 그대로, 카드는 이전 결과 | introduced | H `test_failed_restart_keeps_the_previous_runs_outcome` |
| 세션 끝남, 이전 실행의 세션 기록이 남음 | 같은 PR 다시 시작, 새 세션 기록 아직 없음 | 카드는 '시작함'. 이전 기록이 아니라 이번 실행의 기록만 읽음 | introduced | H `RunLabelTest.test_card_reads_the_session_of_this_run_not_an_earlier_one` |
| 세션 돌고 있음 | Claude Code 종료 | 종료 코드 파일 기록. 카드가 끝남 또는 실패 | introduced | H `test_exit_code_of_the_session_reaches_the_card` |
| 세션 돌고 있음 | tmux 세션을 직접 닫음, 머신 재부팅 | 종료 코드 파일 없음. 카드는 '끝남 (종료 코드 모름)' | introduced | H `RunLabelTest.test_labels_follow_tmux_session_log_and_exit_code` |
| `tmux` 키가 없는 기록(thync가 세션 없이 리뷰를 직접 올리던 방식이 남긴 기록) | 화면 갱신 | 카드로 보이지 않음. 파일은 그대로 | introduced | H `RunLabelTest.test_one_shot_review_records_are_not_shown` |
| 허브 | 확인 또는 시작, `reflex_host` 연결됨 | 같은 노드를 그 서버로 한 번 넘김. 허브에서는 실행·저장 없음 | changed | R `test_hub_sends_a_handoff_on_once_and_never_runs_it_itself` |
| 허브 | 확인 또는 시작, `reflex_host` 연결 안 됨 | `503`. 허브에서 실행·저장 없음 | unchanged | R `test_hub_sends_a_handoff_on_once_and_never_runs_it_itself` |
| 허브 | 확인 또는 시작, 넘기는 도중 실패(300초 초과 포함) | `502`와 이유, 시작이면 카드를 확인하라는 안내. 허브에서 실행·저장 없음. 그 서버는 이미 시작했을 수 있음 | changed | R `test_handoff_with_the_feature_off_or_a_failed_forward_starts_nothing` |
| 넘겨받은 서버 | 넘겨받은 확인·시작 요청 | 받은 노드로 확인·시작. 확인은 아무것도 시작하지 않음 | changed | R `test_forwarded_handoff_uses_the_node_it_is_sent` |
| 넘겨받은 서버 | 넘겨받은 요청, 자신에게도 `reflex_host` 있음 | `503`. 다시 넘기지 않음 | unchanged | R `test_forwarded_handoff_on_a_machine_with_its_own_reflex_host_is_refused` |

서버 재시작은 기록, tmux 세션, 종료 코드 파일을 바꾸지 않습니다. 카드는 재시작 뒤에도 tmux와 종료 코드 파일로 상태를 다시 계산합니다.

## 비용과 남는 위험

- 다른 사람의 PR 리뷰는 auto 권한의 대화형 세션에서 돕니다. Concord는 리뷰어를 PR 헤드의 임시 클론에서 실행하지만, 세션 자체는 사용자의 클론에서 셸을 쓸 수 있습니다. Claude 리뷰어는 격리되지 않아 홈 디렉터리의 자격 증명을 읽을 수 있고, 사용자의 신뢰 선언 뒤에만 실행됩니다.
- 내 PR의 세션도 외부 입력을 읽습니다. 리뷰어와 자동 리뷰어가 쓴 코멘트를 auto 권한과 push·병합 허락을 가진 세션이 읽습니다.
- 두 세션 모두 셸로 다음을 읽을 수 있습니다. Read 도구 거부 목록은 셸 읽기를 막지 않습니다.
  - `mien exec`가 넣은 프로필의 `GH_TOKEN` 등 환경 변수
  - `~/.thync/token`. 이 토큰은 thync의 모든 쓰기 요청(`/reflex`, `/answer`, `/ask`, `/board`, `/rename`)을 허락합니다. 지시를 숨긴 입력에 속은 세션은 같은 머신의 다른 PR 위임을 시작하거나(실행하는 머신의 작성자·계정 확인은 그대로 적용), thync가 붙잡고 있는 다른 세션의 선택 질문(신뢰 선언 뒤의 예/아니요 확인 포함)에 대신 답하거나, 카드와 이름을 바꿀 수 있습니다.
- 내 PR은 확인 창에서 시작을 누르는 것으로 그 PR 하나의 병합을 허락합니다. 병합 조건 판단은 `pr-to-merge` 스크립트가 합니다.
- 클론이 둘 이상이면 찾는 순서에서 먼저 나온 클론의 프로필이 실행 계정이 됩니다. 확인 창이 폴더와 계정을 보여 주고, 시작할 때 폴더가 바뀌었으면 거절합니다.
- `pr-to-merge`는 사용자가 쓰는 클론에서 내 PR을 고칩니다. 브랜치를 어떻게 다루는지는 Concord가 정합니다. 커밋하지 않은 변경이 있는 클론에서 어떻게 할지는 세션이 정합니다.
- 리뷰 요청이 온 저장소의 클론이 없으면 맡길 수 없습니다.
- `mien`이 없는 머신에서는 `gh api user`를 서버 프로세스의 환경에서 확인하고, 세션은 tmux 서버의 환경에서 돕니다. tmux 서버가 다른 환경으로 먼저 떠 있었다면 확인한 계정과 실행 계정이 다를 수 있습니다.
- 클론을 찾을 때 `origin`이 PR 저장소와 이름이 다르면 저장소 이름이 바뀌었는지 목록을 읽은 계정들에 차례로 묻습니다. 어느 계정도 볼 수 없는 비공개 저장소의 이름 변경은 찾지 못합니다.
- 스킬 확인은 플러그인 캐시에 스킬 파일이 있는지만 봅니다. 꺼 둔 플러그인도 통과하고, 그 경우 세션이 스킬을 찾지 못한 채 '대기 중'에 머뭅니다.
- '대기 중'은 감시 중과 입력 대기를 구분하지 않습니다. 구분하려면 세션 기록에서 백그라운드 명령 상태를 읽어야 하는데, 세션 기록 형식은 공개된 형식이 아닙니다.

## 고려했지만 택하지 않은 것

- **`claude -p`로 실행**: 한 번의 응답으로 끝나므로 두 스킬의 백그라운드 감시가 함께 끝납니다.
- **서버 프로세스의 기본 `gh` 계정 사용**: 여러 계정을 쓰는 머신에서 리뷰나 병합이 다른 계정으로 올라갈 수 있습니다.
- **확인 없이 바로 시작**: 확인 창이 실행 계정을 보여 줄 수 없습니다.
- **스킬 이름을 설정으로 바꾸기**: 첫 프롬프트의 범위·병합 허락 문구가 두 스킬의 인자 형식에 맞춰져 있어 다른 스킬과 바꿔 쓸 수 없습니다.
- **thync가 직접 PR을 받아 읽기 전용으로 리뷰하기**: 한 번 리뷰하고 끝나므로 작성자의 push를 기다려 다시 리뷰하고 승인하는 흐름이 없습니다. 그 흐름은 Concord가 맡습니다.
- **Concord 원장으로 상태 보여 주기**: 형식이 공개된 인터페이스가 아니어서 Concord 버전마다 깨질 수 있습니다.
- **PR마다 git worktree 만들기**: `pr-to-merge`의 `--checkout`은 클론 하나를 받고, 세션이 브랜치를 다루는 방식은 Concord가 정합니다. 대신 같은 클론에서 `pr-to-merge` 세션을 하나로 제한합니다.
