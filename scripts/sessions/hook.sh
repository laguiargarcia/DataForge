#!/usr/bin/env bash
# Claude Code hook entry for session coordination (df-0dql). Thin on purpose: the logic and its
# tests live in the Python modules next to this file. Usage: hook.sh guard|start|end|agent-start|agent-stop|cwd
# Fails open: no python3, a crash, anything odd -> exit 0 and the tool call or session goes on.
here=$(cd "$(dirname "$0")" && pwd) || exit 0
command -v python3 >/dev/null 2>&1 || exit 0
case "${1:-}" in
  guard) python3 -S "$here/guard.py" 2>/dev/null ;;
  start|end|agent-start|agent-stop|cwd) python3 -S "$here/lifecycle.py" "$1" 2>/dev/null ;;
esac
exit 0
