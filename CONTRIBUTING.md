# Contributing

Contributions are welcome! 🤗 (Bug reports, feature requests, and pull requests alike.)

## Setup

Follow the [installation steps in the README](README.md#installation) to get the backend and frontend running.

> [!NOTE]
> API keys are shared via the Cryptpad document linked in Discord.

## Working on an issue
From the bsky-collective-eng-agent directory:
- Create and switch to the new branch, setting `origin/main` as your remote upstream branch: 
```sh 
git checkout -b issue-[ISSUE NUMBER] origin/main
```
- When it's time to commit your changes:
```sh
git add . && git commit -m "[DESCRIPTIVE COMMIT MESSAGE]"
```
- To push your changes to Github: 
```sh
git push -u origin HEAD
```
This will push your code to a branch of the same name on Github.

> [!IMPORTANT]
> Before your commit any code to your local repo, make sure that it's in sync with `origin/main`. There could have been some code merged into that branch while you were working on your issue.
> To sync your work-in-progress:
>```sh
> git stash
> git pull
> git stash pop
> ```

> [!NOTE]
> Here's a handy [cheat sheet](https://education.github.com/git-cheat-sheet-education.pdf) for Git commands.

If a `git pull` shows merge conflicts, open the affected files in an editor and resolve the conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`), then:

```sh
git add <resolved-file>
git commit
```

## Running tests

```sh
python -m pytest tests/test_routing.py tests/test_voting.py -v
```

Tests mock all LLM and graph calls. No API keys required.

## Opening a pull request

- Branch off `main` and keep PRs focused on a single change.
- Run the test suite before submitting.
- Briefly describe what the change does and why in the PR description.

## Reporting issues

Open a [GitHub issue](../../issues) with enough context to reproduce the problem: relevant logs, the message that triggered unexpected behavior, and your `MODEL_PROVIDER` setting.
