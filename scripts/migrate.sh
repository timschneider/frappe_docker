#!/bin/bash
# migrate.sh — Migrate .env from pre-v2.1.0 to v2.1.0 frappe_docker format
#
# Changes applied:
#   1. Converts SITES= (backtick-delimited) to SITES_RULE=Host(...) format
#   2. Adds new env vars with empty defaults (CUSTOM_IMAGE, CUSTOM_TAG, etc.)
#
# Usage:
#   ./scripts/migrate.sh [--dry-run] [path/to/.env]
#
# Options:
#   --dry-run   Show what would change without modifying any files
#
# The script is idempotent — running it twice produces no changes the second time.

set -euo pipefail

# --- Argument parsing ---

DRY_RUN=false
ENV_FILE=""

for arg in "$@"; do
  case "$arg" in
    --dry-run)
      DRY_RUN=true
      ;;
    -*)
      echo "Unknown option: $arg" >&2
      echo "Usage: $0 [--dry-run] [path/to/.env]" >&2
      exit 1
      ;;
    *)
      if [ -n "$ENV_FILE" ]; then
        echo "Error: multiple .env paths provided" >&2
        exit 1
      fi
      ENV_FILE="$arg"
      ;;
  esac
done

if [ -z "$ENV_FILE" ]; then
  ENV_FILE=".env"
fi

if [ ! -f "$ENV_FILE" ]; then
  echo "Error: $ENV_FILE not found" >&2
  exit 1
fi

# --- State ---

CHANGES=()

# --- Helper: add a line to the env file (or report in dry-run) ---

append_line() {
  local line="$1"
  if [ "$DRY_RUN" = true ]; then
    CHANGES+=("Add: $line")
  else
    echo "$line" >>"$ENV_FILE"
    CHANGES+=("Added: $line")
  fi
}

# --- Step 1: Convert SITES= to SITES_RULE= ---

convert_sites_to_sites_rule() {
  # Skip if SITES_RULE already exists (not commented out)
  if grep -qE '^SITES_RULE=' "$ENV_FILE"; then
    echo "SITES_RULE already present — skipping conversion."
    return
  fi

  # Check for old SITES= line (not commented out)
  local sites_line
  sites_line=$(grep -E '^SITES=' "$ENV_FILE" || true)
  if [ -z "$sites_line" ]; then
    echo "No SITES= line found — skipping conversion."
    return
  fi

  # Extract value after SITES=
  local sites_value
  sites_value="${sites_line#SITES=}"

  # Strip surrounding whitespace and quotes
  sites_value=$(echo "$sites_value" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
  sites_value=$(echo "$sites_value" | sed 's/^"//;s/"$//')
  sites_value=$(echo "$sites_value" | sed "s/^'//;s/'$//")

  # Parse backtick-delimited domains: `a.com`,`b.com` or `a.com`
  # Strip backticks and split on comma
  local domains
  domains="${sites_value//\`/}"

  # Build SITES_RULE value
  local sites_rule=""
  local first=true
  IFS=',' read -ra DOMAIN_ARRAY <<<"$domains"
  for domain in "${DOMAIN_ARRAY[@]}"; do
    # Trim whitespace
    domain=$(echo "$domain" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    if [ -z "$domain" ]; then
      continue
    fi
    if [ "$first" = true ]; then
      sites_rule="Host(\`${domain}\`)"
      first=false
    else
      sites_rule="${sites_rule} || Host(\`${domain}\`)"
    fi
  done

  if [ -z "$sites_rule" ]; then
    echo "Warning: could not parse domains from SITES= line — skipping." >&2
    return
  fi

  if [ "$DRY_RUN" = true ]; then
    CHANGES+=("Comment out: ${sites_line}")
    CHANGES+=("Add: SITES_RULE=${sites_rule}")
  else
    # Comment out old SITES= line (only the first uncommented match)
    sed -i '0,/^SITES=/{s/^SITES=/#SITES=/}' "$ENV_FILE"
    # Append new SITES_RULE line
    echo "SITES_RULE=${sites_rule}" >>"$ENV_FILE"
    CHANGES+=("Commented out: ${sites_line}")
    CHANGES+=("Added: SITES_RULE=${sites_rule}")
  fi
}

# --- Step 2: Add new env vars with empty defaults ---

add_if_missing() {
  local var_name="$1"
  local comment="${2:-}"

  if grep -qE "^${var_name}=" "$ENV_FILE"; then
    return
  fi

  if [ -n "$comment" ]; then
    if [ "$DRY_RUN" = true ]; then
      CHANGES+=("Add: ${comment}")
    else
      echo "$comment" >>"$ENV_FILE"
    fi
  fi
  append_line "${var_name}="
}

add_new_env_vars() {
  local any_added=false

  # Check if any of the new vars are missing before adding the header
  for var in CUSTOM_IMAGE CUSTOM_TAG PULL_POLICY RESTART_POLICY; do
    if ! grep -qE "^${var}=" "$ENV_FILE"; then
      any_added=true
      break
    fi
  done

  if [ "$any_added" = false ]; then
    echo "All new env vars already present — skipping."
    return
  fi

  # Add a blank line separator if the file doesn't end with one
  if [ "$DRY_RUN" = false ]; then
    # Ensure trailing newline
    if [ -n "$(tail -c 1 "$ENV_FILE")" ]; then
      echo "" >>"$ENV_FILE"
    fi
    echo "# v2.1.0: new compose.yaml variables (empty = use defaults)" >>"$ENV_FILE"
  else
    CHANGES+=("Add: # v2.1.0: new compose.yaml variables (empty = use defaults)")
  fi

  add_if_missing "CUSTOM_IMAGE" "# Custom image (default: frappe/erpnext)"
  add_if_missing "CUSTOM_TAG" "# Custom tag (default: \$ERPNEXT_VERSION)"
  add_if_missing "PULL_POLICY" "# Pull policy (default: always)"
  add_if_missing "RESTART_POLICY" "# Restart policy (default: unless-stopped)"
}

# --- Backup ---

create_backup() {
  if [ "$DRY_RUN" = true ]; then
    return
  fi
  local backup="${ENV_FILE}.backup"
  cp "$ENV_FILE" "$backup"
  echo "Backup created: $backup"
}

# --- Summary ---

print_summary() {
  echo ""
  if [ ${#CHANGES[@]} -eq 0 ]; then
    echo "No changes needed — .env is already up to date."
    return
  fi

  if [ "$DRY_RUN" = true ]; then
    echo "=== Dry run — the following changes would be made ==="
  else
    echo "=== Changes applied ==="
  fi

  for change in "${CHANGES[@]}"; do
    echo "  - $change"
  done

  echo ""
  echo "=== Next steps ==="
  echo "  1. Regenerate the flattened compose file:"
  echo "     docker compose -f compose.yaml \\"
  echo "       -f overrides/compose.mariadb.yaml \\"
  echo "       -f overrides/compose.redis.yaml \\"
  echo "       -f overrides/compose.https.yaml \\"
  echo "       config > docker-compose.yml"
  echo ""
  echo "  2. Review MariaDB upgrade (10.6 -> 11.8):"
  echo "     MARIADB_AUTO_UPGRADE=1 is set in compose.mariadb.yaml."
  echo "     Consider backing up your database before upgrading."
  echo ""
  echo "  3. Review Traefik upgrade (v2 -> v3):"
  echo "     compose.https.yaml now uses traefik:v3.6."
  echo "     Verify your TLS/routing config after upgrade."
  echo ""
  echo "  4. Pull new images and restart:"
  echo "     docker compose -f docker-compose.yml pull"
  echo "     docker compose -f docker-compose.yml up -d"
}

# --- Main ---

echo "frappe_docker v2.1.0 migration"
echo "Env file: $ENV_FILE"
if [ "$DRY_RUN" = true ]; then
  echo "Mode: dry-run"
fi
echo ""

create_backup
convert_sites_to_sites_rule
add_new_env_vars
print_summary
