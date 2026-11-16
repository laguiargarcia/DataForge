#!/usr/bin/env bash
# Claude Code hooks for the quality gate (docs/quality-gate.md).
#   post: after Edit/Write of a .py file, run the fast gate on it and remember it for this session.
#   stop: when Claude ends a turn, run the full gate judging only the files this session edited,
#         so another session's work in progress never blocks this one.
# Exit 2 feeds the gate's output back to Claude, which then fixes the findings.
#
# Each file is judged by the gate of the checkout it lives in (the clone or one of its worktrees):
# a gate only sees the files of its own checkout, so the clone's gate answers "ok" for anything
# under .worktrees/ without looking at it (df-5wsy).
set -u
root="${CLAUDE_PROJECT_DIR:-$(pwd)}"
input=$(cat)
session=$(printf '%s' "$input" | jq -r '.session_id // "default"')
edited="$root/.claude/.quality-edited-$session"

# The checkout that holds a file, when it carries a gate; otherwise the project dir (a file outside
# git, or inside a private workspace that is its own repository, belongs to the clone's gate).
checkout_of() {
  local top
  top=$(git -C "$(dirname "$1")" rev-parse --show-toplevel 2>/dev/null) || top=""
  if [ -n "$top" ] && [ -f "$top/scripts/quality/gate.py" ]; then
    printf '%s\n' "$top"
  else
    printf '%s\n' "$root"
  fi
}

# run_gate <checkout> <gate args...>: that checkout's gate, with its venv (the clone's when it has none).
run_gate() {
  local top=$1 py
  shift
  py="$top/.venv/bin/python"
  [ -x "$py" ] || py="$root/.venv/bin/python"
  "$py" "$top/scripts/quality/gate.py" check "$@" >&2
}

case "${1:-}" in
  post)
    file=$(printf '%s' "$input" | jq -r '.tool_input.file_path // .tool_response.filePath // empty')
    case "$file" in *.py) ;; *) exit 0 ;; esac
    grep -qxF "$file" "$edited" 2>/dev/null || printf '%s\n' "$file" >> "$edited"
    run_gate "$(checkout_of "$file")" "$file" || exit 2
    ;;
  stop)
    [ -s "$edited" ] || exit 0
    # Second stop in a row: Claude already had one chance to fix; let the turn end.
    if [ "$(printf '%s' "$input" | jq -r '.stop_hook_active // false')" = "true" ]; then
      rm -f "$edited"
      exit 0
    fi
    mapfile -t files < "$edited"
    declare -A by_checkout=()
    for f in "${files[@]}"; do
      [ -f "$f" ] && by_checkout["$(checkout_of "$f")"]+="$f"$'\n'
    done
    failed=0
    for top in "${!by_checkout[@]}"; do
      mapfile -t group < <(printf '%s' "${by_checkout[$top]}")
      run_gate "$top" --full "${group[@]}" || failed=1
    done
    [ "$failed" -eq 0 ] || exit 2
    rm -f "$edited"
    ;;
esac
