# 설치와 실행

[문서 목차](README.md) · [프로젝트 소개](../README.md)

## 준비물

- Python 3.10 이상 (표준 라이브러리만 사용합니다)
- Node.js 18 이상 (화면 코드를 빌드할 때만)
- [GitHub CLI](https://cli.github.com/) (`gh auth login` 된 상태) — 내 커밋, 리뷰 요청, 내 PR
- 선택: Jira Cloud API 토큰 — 나에게 배정된 티켓 ([연결 설정](configuration.md#jira와-notion-연결))
- 선택: Notion integration 토큰 — 최근에 고친 Notion 페이지
- 선택: Obsidian vault 폴더 — 최근에 고친 노트 ([연결 설정](configuration.md#obsidian))
- 선택: [Claude Code](https://docs.claude.com/en/docs/claude-code) 세션 로그 (`~/.claude/projects/`)와 Codex 세션 로그 (`~/.codex/sessions/`) — 세션 단위의 집중과 백그라운드
- 선택: `claude` CLI — PR을 Claude에게 맡기는 기능. Claude Code 세션 로그도 읽을 때만 활성화됩니다. 내 PR을 맡길 때는 `tmux`도 필요합니다

Claude Code·Codex를 쓰지 않거나 세션 내용을 보이고 싶지 않으면 `sources`에서 `claude_code`와 `codex`를 제외합니다([출처 설정](configuration.md#설정-키)). 둘 다 제외하면 세션 로그를 전혀 읽지 않고, 커밋·티켓 변경·페이지 수정 시각으로 집중과 백그라운드를 정합니다.

## 설치와 실행

```bash
git clone https://github.com/choiahrok/thync.git
cd thync
npm ci
npm run build
python3 -m thync
```

서버는 `127.0.0.1:8765` 에만 열립니다. 처음 실행하면 `~/.thync/token`을 만듭니다. 서버를 실행한 터미널은 열어 두고, 다음 단계는 다른 터미널에서 진행합니다. 서버를 멈추려면 `Ctrl+C`를 누릅니다.

## 브라우저에서 처음 열기

서버를 실행한 채 다른 터미널에서 접근 토큰을 확인합니다.

```bash
cat "${THYNC_HOME:-$HOME/.thync}/token"
```

`http://127.0.0.1:8765/`을 열고 화면 위쪽의 입력 칸에 출력된 토큰을 넣습니다. 주소 끝에 `#t=<토큰>`을 붙여 열어도 됩니다. 브라우저가 토큰을 기억하므로 다음부터는 바로 열립니다.

## 다음 단계

화면이 열리면 [사용법](usage.md)에서 그래프와 칸반의 의미를 확인합니다. Jira·Notion을 연결하거나 세션 로그 사용 여부를 바꾸려면 [설정 안내](configuration.md)를 확인합니다.

## 원격 서버에서 실행하기

Claude Code를 원격 서버에서 쓰고 노트북 브라우저로 볼 때는 SSH 포트 포워딩을 켜 두고(창을 열어 둡니다), 다른 창에서 토큰을 넣어 엽니다.

```bash
ssh -N -L 8765:127.0.0.1:8765 <user>@<server>
```

아래 `open` 명령은 macOS용입니다. 다른 운영체제에서는 원격 토큰을 확인한 뒤 같은 주소를 브라우저에 직접 입력합니다.

```bash
open "http://localhost:8765/#t=$(ssh <user>@<server> 'cat ~/.thync/token')"
```

`~/.ssh/config`의 해당 `Host`에 `LocalForward 8765 127.0.0.1:8765`를 넣어 두면 SSH 로 접속할 때마다 포워딩이 같이 설정됩니다.

`THYNC_HOME`이나 포트를 바꿨다면 토큰 경로와 포워딩 주소도 그 설정에 맞춥니다.

## Tailscale로 접속하기

휴대폰처럼 SSH를 쓰기 어려운 기기에서는 thync를 실행한 컴퓨터에서 `tailscale serve`로 서버를 tailnet에 엽니다. tailnet 관리 콘솔에서 MagicDNS와 HTTPS 인증서가 켜져 있어야 합니다.

접속할 수 있는 기기는 tailnet 접근 규칙(ACL)이 정합니다. 이 컴퓨터를 공유받은 다른 tailnet의 사용자나, 회사 tailnet처럼 여러 사람이 쓰는 tailnet의 다른 구성원도 화면을 열 수 있습니다. 이들에게는 접근 토큰이 유일한 보호 수단이므로 토큰을 공유하지 않습니다.

```bash
tailscale serve --bg 8765
```

`port`를 바꿨다면 `8765` 대신 그 포트를 씁니다.

출력된 `https://<기기 이름>.<tailnet 이름>.ts.net/` 주소를 열고, 처음 한 번 화면 위쪽 입력 칸에 토큰을 넣습니다. 끄려면 `tailscale serve --https=443 off`를 실행합니다.

혼자 쓰는 컴퓨터라면 `config.json`의 `tailscale_users`에 내 Tailscale 로그인 이름을 넣어, tailnet의 어느 기기에서든 토큰 입력 없이 열 수 있습니다. `tailscale serve`가 요청마다 접속한 사람의 로그인 이름을 붙여 주고, thync는 목록에 있는 이름이면 토큰 없이 허용합니다. 이 주소(`https://<기기 이름>.<tailnet 이름>.ts.net/`)로 연 화면에만 적용되며, 서버가 시작할 때 `tailscale status`로 이 이름을 읽습니다. 바꾼 뒤에는 thync를 다시 시작합니다. 기기나 tailnet 이름을 바꿨을 때도 다시 시작합니다. 조건과 한계는 [데이터와 보안](security.md#접근-토큰과-저장-위치)에 있습니다.

```json
{"tailscale_users": ["me@example.com"]}
```

`tailscale funnel`은 서버를 인터넷 전체에 공개하므로 쓰지 않습니다.

## 실행이 잘되지 않을 때

| 증상 | 확인할 내용 |
| --- | --- |
| 화면 코드가 없거나 변경 사항이 보이지 않음 | 저장소 루트에서 `npm ci`와 `npm run build`를 실행하고 새로고침합니다 |
| 토큰이 필요하다는 안내가 나옴 | 현재 서버의 `token` 파일 내용을 화면 위쪽 입력 칸에 넣습니다. `localhost`와 `127.0.0.1`은 브라우저 저장소가 다릅니다 |
| GitHub 활동이 보이지 않음 | 서버를 실행하는 사용자로 `gh auth status`를 확인하고, `sources`에 `github`가 있는지 확인합니다 |
| Jira·Notion 활동이 보이지 않음 | [연결 설정](configuration.md#jira와-notion-연결)의 필수 값과 페이지 연결을 확인한 뒤 서버를 재시작합니다 |
| '맡긴 일' 칸이 보이지 않음 | `sources`에 `claude_code`가 있고 서버 환경에서 `claude` CLI를 찾을 수 있는지 확인합니다 |
| 포트가 이미 사용 중임 | 기존 서버를 종료하거나 `config.json`의 `port`를 바꾸고 서버를 재시작합니다 |
