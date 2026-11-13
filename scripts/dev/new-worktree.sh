#!/usr/bin/env bash
# Create a git worktree of this clone, with its own venv, under <clone>/.worktrees/<name> (df-b86q).
#
#   scripts/dev/new-worktree.sh <name> <branch> [<base>]     # base defaults to origin/main
#
# The venv gets the exact versions of the clone's own .venv (uv pip freeze), so every worktree tests
# with the same dependencies, plus an editable install of the worktree itself: the `dataforge`
# command and `import dataforge` run the worktree's code, not the clone's. With uv's cache warm this
# takes about a second. Remove with: git worktree remove .worktrees/<name> && git branch -D <branch>
set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "usage: $0 <name> <branch> [<base>]" >&2
    exit 2
fi
name=$1
branch=$2
base=${3:-origin/main}
uv=${UV:-uv}

common=$(git rev-parse --path-format=absolute --git-common-dir)
root=$(dirname "$common")
wt="$root/.worktrees/$name"
if [[ ! -x "$root/.venv/bin/python" ]]; then
    echo "no venv at $root/.venv: build the clone's venv first (see docs/commands.md)" >&2
    exit 1
fi

git -C "$root" fetch --quiet origin
git -C "$root" worktree add --quiet "$wt" -b "$branch" "$base"
"$uv" venv --quiet --python "$root/.venv/bin/python" "$wt/.venv"
"$uv" pip freeze --python "$root/.venv/bin/python" --exclude-editable \
    | "$uv" pip install --quiet --python "$wt/.venv/bin/python" -r -
"$uv" pip install --quiet --python "$wt/.venv/bin/python" --no-deps -e "$wt"
echo "$wt"
