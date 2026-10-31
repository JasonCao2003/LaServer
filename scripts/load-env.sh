# Bash helper: parse literal KEY=VALUE entries without executing shell code.
load_service_environment() {
  local env_path="$1" line name value
  [[ -f "$env_path" ]] || { echo "Missing $env_path. Run the service script with --setup first." >&2; return 1; }
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*$ || "$line" =~ ^[[:space:]]*# ]] && continue
    if [[ ! "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      echo "Invalid environment entry in $env_path" >&2; return 1
    fi
    name="${BASH_REMATCH[1]}"; value="${BASH_REMATCH[2]}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    if [[ "$value" == \"*\" || "$value" == \'*\' ]]; then value="${value:1:${#value}-2}"; fi
    if [[ ! -v "$name" ]]; then export "$name=$value"; fi
  done < "$env_path"
}
