#!/usr/bin/env bash

set -euo pipefail

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$1" >&2
    exit 1
  fi
}

detect_repo() {
  local repo

  if repo="$(gh repo view --json nameWithOwner --jq '.nameWithOwner' 2>/dev/null)"; then
    printf '%s\n' "$repo"
    return
  fi

  repo="$(git remote get-url origin 2>/dev/null || true)"
  repo="${repo#git@github.com:}"
  repo="${repo#https://github.com/}"
  repo="${repo%.git}"

  if [[ -n "$repo" ]]; then
    printf '%s\n' "$repo"
    return
  fi

  printf 'Unable to detect target repository.\n' >&2
  exit 1
}

usage() {
  cat <<'USAGE'
Usage:
  setup-review-workflow-secrets.sh [--repo OWNER/REPO] [SECRET_NAME ...]

Secret names:
  TS_OAUTH_CLIENT_ID
  TS_OAUTH_SECRET
  LLM_API_KEY
  LLM_BASE_URL
  LLM_MODEL

If no SECRET_NAME is provided, all workflow secrets are configured.
USAGE
}

prompt_nonempty() {
  local prompt="$1"
  local silent="${2:-false}"
  local value=""

  while [[ -z "$value" ]]; do
    if [[ "$silent" == "true" ]]; then
      IFS= read -r -s -p "$prompt" value
      printf '\n' >&2
    else
      IFS= read -r -p "$prompt" value
    fi

    if [[ -z "$value" ]]; then
      printf 'Value cannot be empty.\n' >&2
    fi
  done

  printf -- '%s' "$value"
}

is_valid_secret() {
  case "$1" in
    TS_OAUTH_CLIENT_ID | TS_OAUTH_SECRET | LLM_API_KEY | LLM_BASE_URL | LLM_MODEL)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

secret_selected() {
  local needle="$1"
  local selected_secret

  for selected_secret in "${selected_secrets[@]}"; do
    if [[ "$selected_secret" == "$needle" ]]; then
      return 0
    fi
  done

  return 1
}

set_secret() {
  local name="$1"
  local value="$2"

  printf '%s' "$value" | gh secret set "$name" --repo "$repo"
}

repo=""
repo_provided=false
selected_secrets=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h | --help)
      usage
      exit 0
      ;;
    --repo)
      if [[ $# -lt 2 || -z "$2" ]]; then
        printf 'Missing value for --repo.\n' >&2
        exit 1
      fi
      repo="$2"
      repo_provided=true
      shift 2
      ;;
    --repo=*)
      repo="${1#--repo=}"
      if [[ -z "$repo" ]]; then
        printf 'Missing value for --repo.\n' >&2
        exit 1
      fi
      repo_provided=true
      shift
      ;;
    -*)
      printf 'Unknown option: %s\n\n' "$1" >&2
      usage >&2
      exit 1
      ;;
    *)
      if ! is_valid_secret "$1"; then
        printf 'Unknown secret name: %s\n\n' "$1" >&2
        usage >&2
        exit 1
      fi
      selected_secrets+=("$1")
      shift
      ;;
  esac
done

require_cmd gh
require_cmd git

if ! gh auth status >/dev/null 2>&1; then
  printf 'GitHub CLI is not authenticated. Run `gh auth login` first.\n' >&2
  exit 1
fi

default_repo="$(detect_repo)"
if [[ "$repo_provided" == "false" ]]; then
  repo="$default_repo"
fi

if [[ ${#selected_secrets[@]} -eq 0 ]]; then
  selected_secrets=(
    TS_OAUTH_CLIENT_ID
    TS_OAUTH_SECRET
    LLM_API_KEY
    LLM_BASE_URL
    LLM_MODEL
  )
fi

printf 'Review workflow secret bootstrap\n'
printf 'The following repository secrets will be written:\n'
for selected_secret in "${selected_secrets[@]}"; do
  case "$selected_secret" in
    TS_OAUTH_CLIENT_ID | TS_OAUTH_SECRET)
      printf '  - %s (Tailscale)\n' "$selected_secret"
      ;;
    *)
      printf '  - %s\n' "$selected_secret"
      ;;
  esac
done
printf '\n'
printf 'Workflow defaults live in .github/workflows/ai-pr-review.yml:\n'
printf '  - bot name: momo\n'
printf 'Workflow runtime values now come from repository secrets:\n'
printf '  - model secret: LLM_MODEL\n'
printf '  - base URL secret: LLM_BASE_URL\n'
printf 'Tailscale enables GitHub Actions to reach your local LLM service.\n'
printf '\n'

if [[ "$repo_provided" == "false" ]]; then
  read -r -p "Target repo [${default_repo}]: " repo_input
  repo="${repo_input:-$default_repo}"
else
  printf 'Target repo: %s\n' "$repo"
fi

default_llm_base_url="http://yangmacbook-pro.tail38308a.ts.net:20128/v1"

if secret_selected TS_OAUTH_CLIENT_ID; then
  ts_oauth_client_id="$(prompt_nonempty 'TS_OAUTH_CLIENT_ID: ')"
fi

if secret_selected TS_OAUTH_SECRET; then
  ts_oauth_secret="$(prompt_nonempty 'TS_OAUTH_SECRET (input hidden): ' true)"
fi

if secret_selected LLM_API_KEY; then
  llm_api_key="$(prompt_nonempty 'LLM_API_KEY (input hidden): ' true)"
fi

if secret_selected LLM_BASE_URL; then
  read -r -p "LLM_BASE_URL [${default_llm_base_url}]: " base_url_input
  llm_base_url="${base_url_input:-$default_llm_base_url}"
fi

if secret_selected LLM_MODEL; then
  llm_model="$(prompt_nonempty 'LLM_MODEL: ')"
fi

printf '\nAbout to write secret(s) to %s:\n' "$repo"
for selected_secret in "${selected_secrets[@]}"; do
  printf '  - %s\n' "$selected_secret"
done
read -r -p 'Continue? [y/N]: ' confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
  printf 'Aborted.\n'
  exit 0
fi

if secret_selected TS_OAUTH_CLIENT_ID; then
  set_secret TS_OAUTH_CLIENT_ID "$ts_oauth_client_id"
fi

if secret_selected TS_OAUTH_SECRET; then
  set_secret TS_OAUTH_SECRET "$ts_oauth_secret"
fi

if secret_selected LLM_API_KEY; then
  set_secret LLM_API_KEY "$llm_api_key"
fi

if secret_selected LLM_BASE_URL; then
  set_secret LLM_BASE_URL "$llm_base_url"
fi

if secret_selected LLM_MODEL; then
  set_secret LLM_MODEL "$llm_model"
fi

printf '\nSecret configured successfully for %s\n' "$repo"
printf 'You can verify with: gh secret list --repo %s\n' "$repo"
