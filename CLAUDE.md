# thync 작업 규칙

이 저장소의 개발, PR, 리뷰는 [concord](https://github.com/arinyaho/concord) 플러그인의 skill로 진행합니다.

## 요청별로 쓰는 skill

| 요청 | skill |
| --- | --- |
| 한 작업을 설계부터 PR까지 진행 | `concord:ticket-to-pr` |
| 큰 요청을 여러 이슈와 PR로 나누어 진행 | `concord:initiative-to-prs` |
| 이슈 작성과 수정 | `concord:ticket-writing` |
| 내 브랜치, 내 PR, 설계 문서, 이슈의 리뷰와 수정 | `concord:review-and-fix` |
| 다른 사람의 PR 리뷰 | `concord:review-until-approve` |
| 내 PR에 달린 리뷰에 답하기, 승인된 PR 병합 | `concord:pr-to-merge` |

- 사용자 수준의 `review`, `reviewee` agent와 skill은 이 저장소에서 쓰지 않습니다.
- 여러 단계 작업이 아닌 단순 PR 생성과 본문 수정에만 사용자 수준의 `pr` agent를 씁니다.
- 병합은 사용자가 그 요청에서 저장소를 지정해 허락한 경우에만 합니다. 이 파일은 병합을 허락하지 않습니다.

## 이슈

- 이슈는 `inkme9/thync`의 GitHub Issues로 관리하고 `gh issue`로 다룹니다.
  - 읽기: `gh issue view <번호> --comments`
  - 만들기: `gh issue create --title <제목> --body-file <파일>`
  - 기록 남기기: `gh issue comment <번호> --body-file <파일>`
  - 본문 수정: `gh issue edit <번호> --body-file <파일>`
- GitHub Issues에는 진행 중 상태가 없습니다. 작업을 시작하면 이슈를 자신에게 배정하고(`gh issue edit <번호> --add-assignee @me`) 시작했다는 댓글을 남깁니다.
- 이슈는 직접 닫지 않습니다. PR 본문의 `Closes #<번호>`로 병합할 때 닫습니다.

## 저장소와 브랜치

- 저장소는 `inkme9/thync` 하나이고, 함께 바꿔야 하는 다른 저장소는 없습니다. `vendor/brain-atlas`는 가져온 외부 코드의 사본입니다. 수정하면 `vendor/brain-atlas/README.md`의 수정 내역도 고칩니다.
- 기준 브랜치는 `main`입니다. `main`에 직접 커밋하지 않고 작업 브랜치에서 PR을 엽니다.
- worktree는 저장소 밖의 `../thync-worktrees/<브랜치 이름>`에 만듭니다.
- 설계 문서는 `docs/design/`에 둡니다. 사용자와 운영자가 읽는 문서는 `docs/`에 있고, 목차는 `docs/README.md`입니다.

## 검증

저장소 최상위에서 실행합니다. CI(`.github/workflows/ci.yml`)도 같은 명령을 실행합니다.

```bash
python3 -m unittest discover tests
npm ci
npm run typecheck
npm run build
```

Python 코드는 표준 라이브러리만 쓰고 Python 3.10에서 동작해야 합니다.

## PR

- PR 양식 파일은 없습니다.
- 제목과 커밋 메시지는 영어로, `feat:`, `fix:`, `chore:`, `docs:` 같은 접두어로 시작합니다.
- 본문과 사람에게 쓰는 답글은 한국어로 씁니다.
- 지정해서 요청할 리뷰어는 없습니다.
