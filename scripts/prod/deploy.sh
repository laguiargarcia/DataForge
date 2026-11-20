#!/usr/bin/env bash
# Deploy the main checkout's HEAD to prod (df-n8ac.11). The post-merge hook runs it after
# `git pull --ff-only` in the main checkout (.pre-commit-config.yaml), so prod follows main; run it
# by hand to retry a deploy that prod refused. Main is what its PRs' CI tested (the ruleset requires
# a PR to be up to date), so the deploy does not wait for main's own CI.
#
#   scripts/prod/deploy.sh
#
# Steps: the wheel of HEAD (git archive + uv build), uv.lock's runtime versions as pip constraints
# (uv export), then scripts/prod/update-prod.ps1 of that same commit, which moves the venv to the
# release's Python when it changed, installs, runs `pip check` and `dataforge --help`, and records
# the release; last, scripts/prod/mirror_workspace.py copies workspaces/GetFinance of that same commit
# over prod's, leaving prod's own files (data/, .env, triggers/, local settings) alone (df-bsb2.2.6.2).
# update-prod exit 3 means prod was busy and nothing changed: run this again later.
# Exit 4 means the move to a new Python failed and update-prod put the old venv back as it was.
# Any other failure, a failed mirror included (it first puts the workspace back), rolls back to the
# previous release.
#
# Paths come from ~/.config/dataforge/prod.env (DATAFORGE_PROD_CONFIG overrides), never from the
# repository: PROD_HOME, the Windows prod folder as WSL sees it, and PROD_TMP, a work folder on C:
# outside it. Without that file the hook does nothing (a clone that is not the prod machine).
set -euo pipefail

# post-merge also fires in a worktree (main merged into a branch): only the main checkout on main deploys.
[[ "$(git rev-parse --absolute-git-dir)" == "$(git rev-parse --path-format=absolute --git-common-dir)" ]] || exit 0
[[ "$(git symbolic-ref --quiet --short HEAD || true)" == main ]] || exit 0

config=${DATAFORGE_PROD_CONFIG:-$HOME/.config/dataforge/prod.env}
if [[ ! -f $config ]]; then
    echo "deploy: no $config, nothing deployed (docs/commands.md, 'Deploying to prod')"
    exit 0
fi
# shellcheck source=/dev/null
source "$config"
: "${PROD_HOME:?set PROD_HOME in $config}" "${PROD_TMP:?set PROD_TMP in $config}"
uv=${UV:-uv}
top=$(git rev-parse --show-toplevel)
sha=$(git -C "$top" rev-parse HEAD)
work="$PROD_TMP/df-deploy-${sha:0:12}"

rm -rf "$work"
mkdir -p "$work/src"
git -C "$top" archive "$sha" | tar -x -C "$work/src"
"$uv" build --quiet --wheel --out-dir "$work" "$work/src"
"$uv" export --quiet --frozen --project "$work/src" --no-default-groups --no-emit-project --no-hashes \
    --output-file "$work/constraints.txt"
wheels=("$work"/dataforge-*.whl)

win() { wslpath -w "$1"; }
update_prod() {
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(win "$work/src/scripts/prod/update-prod.ps1")" \
        -VenvPath "$(win "$PROD_HOME/.venv-prod")" -DataForgeHome "$(win "$PROD_HOME")" "$@"
}
args=(-Wheel "$(win "${wheels[0]}")" -Constraints "$(win "$work/constraints.txt")"
      -Python "$(tr -d '[:space:]' < "$work/src/.python-version")")

echo "deploy: ${sha:0:12} -> $PROD_HOME"
rc=0
update_prod "${args[@]}" || rc=$?
if [[ $rc == 0 ]]; then
    python3 "$work/src/scripts/prod/mirror_workspace.py" "$work/src/workspaces/GetFinance" \
        "$PROD_HOME/workspaces/GetFinance" "$work/workspace-backup" || rc=$?
fi
case $rc in
    0) echo "deploy: ${sha:0:12} OK" ;;
    3) echo "deploy: prod is busy, nothing changed; run scripts/prod/deploy.sh again later" >&2
       exit 3 ;;
    4) echo "deploy: ${sha:0:12} FAILED moving prod to the release's Python; prod is as it was" >&2
       exit 4 ;;
    *) echo "deploy: ${sha:0:12} FAILED (exit $rc); rolling back to the previous release" >&2
       update_prod -Rollback || echo "deploy: the rollback failed too; prod needs a look" >&2
       exit "$rc" ;;
esac
