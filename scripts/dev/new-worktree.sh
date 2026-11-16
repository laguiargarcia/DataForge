#!/usr/bin/env bash
# Create a git worktree of this clone, with its own venv, under <clone>/.worktrees/<name> (df-b86q).
#
#   scripts/dev/new-worktree.sh <name> <branch> [<base>]     # base defaults to origin/main
#
# The venv is `uv sync --frozen` of the worktree's own uv.lock: the Python of .python-version and the
# exact versions CI and prod install, plus an editable install of the worktree itself, so the
# `dataforge` command and `import dataforge` run the worktree's code, not the clone's. With uv's
# cache warm this takes about a second. Remove with: git worktree remove .worktrees/<name> && git branch -D <branch>
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
git -C "$root" fetch --quiet origin
git -C "$root" worktree add --quiet "$wt" -b "$branch" "$base"
"$uv" sync --quiet --frozen --project "$wt"
echo "$wt"
