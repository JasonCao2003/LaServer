#!/usr/bin/env bash
set -euo pipefail
service_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "$service_dir/../.." && pwd)"
source "$project_dir/scripts/load-env.sh"
if [[ $# -gt 1 || ( $# -eq 1 && "$1" != '--setup' ) ]]; then
  echo "Usage: bash start.sh [--setup]" >&2; exit 2
fi
command -v node >/dev/null || { echo "Missing Node.js" >&2; exit 1; }
command -v npm >/dev/null
node -e 'if (Number(process.versions.node.split(".")[0]) < 20) process.exit(1)' || { echo "Node.js >= 20 required" >&2; exit 1; }
if [[ "${1:-}" == '--setup' ]]; then
  if [[ ! -f "$service_dir/.env" ]]; then cp -- "$service_dir/.env.example" "$service_dir/.env"; fi
  cd -- "$service_dir"
  npm ci
  echo 'Environment prepared. Review .env and service configuration before starting.'
  exit 0
fi
load_service_environment "$service_dir/.env"
[[ -d "$service_dir/node_modules" ]] || { echo 'Dependencies missing. Run with --setup first.' >&2; exit 1; }
cd -- "$service_dir"
exec node src/main.js
