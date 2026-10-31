#!/usr/bin/env bash
set -euo pipefail
service_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "$service_dir/../.." && pwd)"
source "$project_dir/scripts/load-env.sh"
if [[ $# -gt 1 || ( $# -eq 1 && "$1" != '--setup' ) ]]; then
  echo "Usage: bash start.sh [--setup]" >&2; exit 2
fi
command -v uv >/dev/null || { echo "Missing uv" >&2; exit 1; }
if [[ "${1:-}" == '--setup' ]]; then
  if [[ ! -f "$service_dir/.env" ]]; then cp -- "$service_dir/.env.example" "$service_dir/.env"; fi
  for relative in config/server.json config/room_config.json; do
    if [[ ! -f "$service_dir/$relative" ]]; then
      cp -- "$service_dir/${relative%.json}.example.json" "$service_dir/$relative"
    fi
  done
  cd -- "$project_dir"
  uv sync --locked
  echo 'Environment prepared. Review .env and service configuration before starting.'
  exit 0
fi
load_service_environment "$service_dir/.env"
for relative in config/server.json config/room_config.json var/certs/fullchain.pem var/certs/privkey.pem; do
  [[ -f "$service_dir/$relative" ]] || { echo "Missing service file: $relative" >&2; exit 1; }
done
cd -- "$project_dir"
exec uv run --locked python -u "$service_dir/sky_boot.py"
