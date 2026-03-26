#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_ENV="$ROOT_DIR/.env"
BACKEND_ENV="$ROOT_DIR/backend/.env"
DEFAULT_CERT_DIR="$ROOT_DIR/deploy/certs"
SELF_SIGNED_SCRIPT="$ROOT_DIR/deploy/generate-self-signed.sh"

DOMAIN=""
AI_PROXY_BASE_URL=""
AI_PROXY_API_KEY="${AI_PROXY_API_KEY:-}"
AI_PROXY_MODEL="gpt-5.4"
AI_PROXY_REASONING_EFFORT="xhigh"
AI_PROXY_TIMEOUT_SECONDS="300"
AI_PROXY_VERIFY_TLS="true"
AI_PROXY_CA_SOURCE=""

APP_SECRET_KEY=""
APP_DEFAULT_TIMEZONE="Europe/Moscow"
APP_PROCESSING_WORKERS="3"
APP_BACKUP_CHUNK_MB="49"
APP_DELETE_LOCAL_BACKUPS_AFTER_TELEGRAM="true"

TELEGRAM_BOT_TOKEN=""
TELEGRAM_BACKUP_CHAT_ID=""
TELEGRAM_INLINE_BASE_URL=""

TLS_CERT_PATH="./deploy/certs/server.crt"
TLS_KEY_PATH="./deploy/certs/server.key"
EXTRA_CA_CERTS_PATH="./deploy/certs"

GENERATE_SELF_SIGNED="auto"
FORCE_WRITE="false"
RUN_DOCKER_UP="false"

usage() {
  cat <<'EOF'
Usage:
  ./deploy/setup.sh --domain <domain-or-ip> --ai-proxy-base-url <url> --ai-proxy-api-key <key> [options]

Required:
  --domain VALUE                    Public domain or IP for the site.
  --ai-proxy-base-url URL           Base URL of the OpenAI-compatible proxy, for example https://host:8317/v1
  --ai-proxy-api-key KEY            API key for the proxy. Can also be passed via AI_PROXY_API_KEY env var.

Optional:
  --telegram-bot-token TOKEN        Telegram bot token.
  --telegram-backup-chat-id ID      Telegram user/chat id for backups.
  --telegram-inline-base-url URL    Public HTTPS base URL for Telegram inline mode. Defaults to https://<domain>.
  --app-secret-key VALUE            App secret. Auto-generated if omitted.
  --timezone TZ                     Default timezone. Default: Europe/Moscow
  --workers N                       Processing workers. Default: 3
  --ai-proxy-model VALUE            Default: gpt-5.4
  --ai-proxy-reasoning VALUE        Default: xhigh
  --ai-proxy-timeout N              Default: 300
  --ai-proxy-insecure               Set AI_PROXY_VERIFY_TLS=false
  --ai-proxy-ca-source PATH         Copy CA file into deploy/certs and configure AI_PROXY_CA_BUNDLE
  --tls-cert-path PATH              Path written into root .env for TLS cert. Default: ./deploy/certs/server.crt
  --tls-key-path PATH               Path written into root .env for TLS key. Default: ./deploy/certs/server.key
  --extra-ca-certs-path PATH        Path written into root .env for mounted cert directory. Default: ./deploy/certs
  --generate-self-signed            Always generate self-signed certs into deploy/certs
  --skip-self-signed                Never generate self-signed certs automatically
  --force                           Overwrite/update config values even if files already exist
  --up                              Run docker compose up -d --build after writing config
  --help                            Show this help

Examples:
  ./deploy/setup.sh \
    --domain 95.62.49.206 \
    --ai-proxy-base-url https://95.62.49.206:8317/v1 \
    --ai-proxy-api-key sk-... \
    --telegram-bot-token 123:abc \
    --telegram-backup-chat-id 1133611562 \
    --generate-self-signed \
    --up
EOF
}

log() {
  printf '[setup] %s\n' "$*"
}

fail() {
  printf '[setup] error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Command not found: $1"
}

generate_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
    return
  fi

  if [[ -r /dev/urandom ]] && command -v od >/dev/null 2>&1; then
    od -An -N 32 -tx1 /dev/urandom | tr -d ' \n'
    return
  fi

  fail "Need openssl or /dev/urandom to generate APP_SECRET_KEY"
}

ensure_parent_dir() {
  mkdir -p "$(dirname "$1")"
}

resolve_host_path() {
  local path="$1"
  if [[ "$path" = /* ]]; then
    printf '%s\n' "$path"
  else
    printf '%s\n' "$ROOT_DIR/${path#./}"
  fi
}

upsert_env() {
  local file="$1"
  local key="$2"
  local value="$3"
  local tmp_file=""

  ensure_parent_dir "$file"
  touch "$file"

  tmp_file="$(mktemp)"
  awk -v key="$key" -v value="$value" '
    BEGIN { replaced = 0 }
    index($0, key "=") == 1 {
      print key "=" value
      replaced = 1
      next
    }
    { print }
    END {
      if (!replaced) {
        print key "=" value
      }
    }
  ' "$file" >"$tmp_file"
  mv "$tmp_file" "$file"
}

copy_ca_bundle_if_needed() {
  if [[ -z "$AI_PROXY_CA_SOURCE" ]]; then
    return
  fi

  [[ -f "$AI_PROXY_CA_SOURCE" ]] || fail "AI proxy CA file not found: $AI_PROXY_CA_SOURCE"

  local destination_dir
  destination_dir="$(resolve_host_path "$EXTRA_CA_CERTS_PATH")"
  mkdir -p "$destination_dir"

  local destination_path="$destination_dir/$(basename "$AI_PROXY_CA_SOURCE")"
  if [[ "$AI_PROXY_CA_SOURCE" != "$destination_path" ]]; then
    cp "$AI_PROXY_CA_SOURCE" "$destination_path"
    log "Copied AI proxy CA bundle to $destination_path"
  fi

  AI_PROXY_CA_SOURCE="/run/certs/$(basename "$destination_path")"
}

write_root_env() {
  upsert_env "$ROOT_ENV" "APP_DOMAIN" "$DOMAIN"
  upsert_env "$ROOT_ENV" "TLS_CERT_PATH" "$TLS_CERT_PATH"
  upsert_env "$ROOT_ENV" "TLS_KEY_PATH" "$TLS_KEY_PATH"
  upsert_env "$ROOT_ENV" "EXTRA_CA_CERTS_PATH" "$EXTRA_CA_CERTS_PATH"
}

write_backend_env() {
  local public_url="https://$DOMAIN"
  local inline_url="$TELEGRAM_INLINE_BASE_URL"

  if [[ -z "$inline_url" ]]; then
    inline_url="$public_url"
  fi

  upsert_env "$BACKEND_ENV" "APP_ENV" "production"
  upsert_env "$BACKEND_ENV" "APP_SECRET_KEY" "$APP_SECRET_KEY"
  upsert_env "$BACKEND_ENV" "APP_JWT_TTL_DAYS" "30"
  upsert_env "$BACKEND_ENV" "APP_DEFAULT_TIMEZONE" "$APP_DEFAULT_TIMEZONE"
  upsert_env "$BACKEND_ENV" "APP_DATA_ROOT" ""
  upsert_env "$BACKEND_ENV" "APP_PUBLIC_BASE_URL" "$public_url"
  upsert_env "$BACKEND_ENV" "APP_FRONTEND_URL" "$public_url"
  upsert_env "$BACKEND_ENV" "APP_TRUST_REVERSE_PROXY" "true"
  upsert_env "$BACKEND_ENV" "APP_PROCESSING_WORKERS" "$APP_PROCESSING_WORKERS"
  upsert_env "$BACKEND_ENV" "APP_THUMBNAIL_WIDTH" "640"
  upsert_env "$BACKEND_ENV" "APP_BACKUP_CHUNK_MB" "$APP_BACKUP_CHUNK_MB"
  upsert_env "$BACKEND_ENV" "APP_DELETE_LOCAL_BACKUPS_AFTER_TELEGRAM" "$APP_DELETE_LOCAL_BACKUPS_AFTER_TELEGRAM"

  upsert_env "$BACKEND_ENV" "AI_PROXY_BASE_URL" "$AI_PROXY_BASE_URL"
  upsert_env "$BACKEND_ENV" "AI_PROXY_API_KEY" "$AI_PROXY_API_KEY"
  upsert_env "$BACKEND_ENV" "AI_PROXY_MODEL" "$AI_PROXY_MODEL"
  upsert_env "$BACKEND_ENV" "AI_PROXY_REASONING_EFFORT" "$AI_PROXY_REASONING_EFFORT"
  upsert_env "$BACKEND_ENV" "AI_PROXY_TIMEOUT_SECONDS" "$AI_PROXY_TIMEOUT_SECONDS"
  upsert_env "$BACKEND_ENV" "AI_PROXY_VERIFY_TLS" "$AI_PROXY_VERIFY_TLS"
  upsert_env "$BACKEND_ENV" "AI_PROXY_CA_BUNDLE" "$AI_PROXY_CA_SOURCE"

  upsert_env "$BACKEND_ENV" "TELEGRAM_BOT_TOKEN" "$TELEGRAM_BOT_TOKEN"
  upsert_env "$BACKEND_ENV" "TELEGRAM_BACKUP_CHAT_ID" "$TELEGRAM_BACKUP_CHAT_ID"
  upsert_env "$BACKEND_ENV" "TELEGRAM_INLINE_BASE_URL" "$inline_url"
}

maybe_generate_certificates() {
  local cert_abs
  local key_abs
  local output_dir
  cert_abs="$(resolve_host_path "$TLS_CERT_PATH")"
  key_abs="$(resolve_host_path "$TLS_KEY_PATH")"

  if [[ "$GENERATE_SELF_SIGNED" == "false" ]]; then
    [[ -f "$cert_abs" ]] || fail "TLS certificate not found: $cert_abs"
    [[ -f "$key_abs" ]] || fail "TLS key not found: $key_abs"
    return
  fi

  if [[ "$GENERATE_SELF_SIGNED" == "true" || ! -f "$cert_abs" || ! -f "$key_abs" ]]; then
    require_command openssl
    [[ -x "$SELF_SIGNED_SCRIPT" ]] || chmod +x "$SELF_SIGNED_SCRIPT"
    output_dir="$(dirname "$cert_abs")"
    mkdir -p "$output_dir"
    log "Generating self-signed TLS certificate for $DOMAIN"
    "$SELF_SIGNED_SCRIPT" "$DOMAIN" "$output_dir"

    if [[ "$cert_abs" != "$output_dir/server.crt" ]]; then
      cp "$output_dir/server.crt" "$cert_abs"
    fi
    if [[ "$key_abs" != "$output_dir/server.key" ]]; then
      cp "$output_dir/server.key" "$key_abs"
    fi
  fi
}

run_docker_compose() {
  require_command docker
  docker compose version >/dev/null 2>&1 || fail "docker compose is not available"
  log "Starting docker compose stack"
  (cd "$ROOT_DIR" && docker compose up -d --build)
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain)
      DOMAIN="${2:-}"
      shift 2
      ;;
    --ai-proxy-base-url)
      AI_PROXY_BASE_URL="${2:-}"
      shift 2
      ;;
    --ai-proxy-api-key)
      AI_PROXY_API_KEY="${2:-}"
      shift 2
      ;;
    --telegram-bot-token)
      TELEGRAM_BOT_TOKEN="${2:-}"
      shift 2
      ;;
    --telegram-backup-chat-id)
      TELEGRAM_BACKUP_CHAT_ID="${2:-}"
      shift 2
      ;;
    --telegram-inline-base-url)
      TELEGRAM_INLINE_BASE_URL="${2:-}"
      shift 2
      ;;
    --app-secret-key)
      APP_SECRET_KEY="${2:-}"
      shift 2
      ;;
    --timezone)
      APP_DEFAULT_TIMEZONE="${2:-}"
      shift 2
      ;;
    --workers)
      APP_PROCESSING_WORKERS="${2:-}"
      shift 2
      ;;
    --ai-proxy-model)
      AI_PROXY_MODEL="${2:-}"
      shift 2
      ;;
    --ai-proxy-reasoning)
      AI_PROXY_REASONING_EFFORT="${2:-}"
      shift 2
      ;;
    --ai-proxy-timeout)
      AI_PROXY_TIMEOUT_SECONDS="${2:-}"
      shift 2
      ;;
    --ai-proxy-insecure)
      AI_PROXY_VERIFY_TLS="false"
      shift
      ;;
    --ai-proxy-ca-source)
      AI_PROXY_CA_SOURCE="${2:-}"
      shift 2
      ;;
    --tls-cert-path)
      TLS_CERT_PATH="${2:-}"
      shift 2
      ;;
    --tls-key-path)
      TLS_KEY_PATH="${2:-}"
      shift 2
      ;;
    --extra-ca-certs-path)
      EXTRA_CA_CERTS_PATH="${2:-}"
      shift 2
      ;;
    --generate-self-signed)
      GENERATE_SELF_SIGNED="true"
      shift
      ;;
    --skip-self-signed)
      GENERATE_SELF_SIGNED="false"
      shift
      ;;
    --force)
      FORCE_WRITE="true"
      shift
      ;;
    --up)
      RUN_DOCKER_UP="true"
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      fail "Unknown argument: $1"
      ;;
  esac
done

[[ -n "$DOMAIN" ]] || fail "--domain is required"
[[ -n "$AI_PROXY_BASE_URL" ]] || fail "--ai-proxy-base-url is required"
[[ -n "$AI_PROXY_API_KEY" ]] || fail "--ai-proxy-api-key is required (or set AI_PROXY_API_KEY in environment)"

if [[ -z "$APP_SECRET_KEY" ]]; then
  APP_SECRET_KEY="$(generate_secret)"
  log "Generated APP_SECRET_KEY"
fi

if [[ -f "$ROOT_ENV" || -f "$BACKEND_ENV" ]]; then
  if [[ "$FORCE_WRITE" != "true" ]]; then
    fail ".env files already exist. Re-run with --force to update them."
  fi
  timestamp="$(date +%Y%m%d-%H%M%S)"
  if [[ -f "$ROOT_ENV" ]]; then
    cp "$ROOT_ENV" "$ROOT_ENV.bak.$timestamp"
    log "Backed up $ROOT_ENV to $ROOT_ENV.bak.$timestamp"
  fi
  if [[ -f "$BACKEND_ENV" ]]; then
    cp "$BACKEND_ENV" "$BACKEND_ENV.bak.$timestamp"
    log "Backed up $BACKEND_ENV to $BACKEND_ENV.bak.$timestamp"
  fi
fi

mkdir -p "$DEFAULT_CERT_DIR"
mkdir -p "$ROOT_DIR/backend/storage"
mkdir -p "$ROOT_DIR/backend/storage/logs"
mkdir -p "$ROOT_DIR/backend/storage/backups"

maybe_generate_certificates
copy_ca_bundle_if_needed
write_root_env
write_backend_env

log "Configuration written:"
log "  $ROOT_ENV"
log "  $BACKEND_ENV"
log "Public URL: https://$DOMAIN"
log "TLS cert path in compose: $TLS_CERT_PATH"
log "TLS key path in compose:  $TLS_KEY_PATH"

if [[ "$RUN_DOCKER_UP" == "true" ]]; then
  run_docker_compose
  log "Done. Open https://$DOMAIN"
else
  log "Next step: cd $ROOT_DIR && docker compose up -d --build"
fi
