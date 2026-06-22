#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(realpath "$(dirname "$(realpath "$0")")/../../..")"
cd "$PROJECT_ROOT"
source .venv/bin/activate

dataforge jobs run finance_daily --workspace GetFinance

echo "Done."
