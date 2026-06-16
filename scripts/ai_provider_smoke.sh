#!/usr/bin/env bash
# scripts/ai_provider_smoke.sh
# v0.6.1 real-AI-provider acceptance smoke test.
#
# Exercises the full text link against whatever provider .env selects and
# asserts the run is recorded safely:
#   1.  source .env (so AI_PROVIDER / keys match the running backend)
#   2.  GET  /api/ai/providers              (no secret values leak)
#   3.  register/login an admin
#   4.  POST /api/ai/test                    (live provider round-trip)
#   5.  create a throwaway project
#   6.  create an outline_generation task
#   7.  poll the task until it leaves running/pending
#   8.  GET  /api/ai/runs?task_id=...        (the recorded run)
#   9.  assert: provider+model recorded / output_data is JSON / no
#       error_message / response_hash present / NO API key anywhere
#
# Works with AI_PROVIDER=mock with zero config. Set AI_PROVIDER=claude |
# openai | openai_compatible (+ that provider's keys) in .env to validate a
# real model end to end.
#
# Usage:
#   API_BASE=http://localhost:8000 bash scripts/ai_provider_smoke.sh
#   # admin: by default registers the first user (fresh DB => auto-admin).
#   # On a populated DB, pass an existing admin token:
#   ADMIN_TOKEN=eyJ... API_BASE=http://localhost:8000 bash scripts/ai_provider_smoke.sh

set -e

API="${API_BASE:-http://localhost:8000}"
TS=$(date +%s)
TASK_WAIT_SECONDS="${TASK_WAIT_SECONDS:-60}"

# ----------------------------------------------------------------------
# Load .env so the script knows which provider/keys the backend uses.
# We only read it for AI_PROVIDER + the key sentinels (leak check).
# ----------------------------------------------------------------------
ENV_FILE="${ENV_FILE:-.env}"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  echo "==> loaded $ENV_FILE"
else
  echo "==> no $ENV_FILE (using process env / defaults)"
fi

AI_PROVIDER="${AI_PROVIDER:-mock}"

echo "==> API base    : $API"
echo "==> AI_PROVIDER : $AI_PROVIDER"
echo "==> Run id      : $TS"
echo

need() { command -v "$1" >/dev/null 2>&1 || { echo "MISSING: $1"; exit 1; }; }
need curl
need jq

PASS=0
FAIL=0
ok()   { echo "  PASS - $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL - $1"; echo "         $2" | head -c 800; echo; FAIL=$((FAIL+1)); }
note() { echo "  ... $1"; }

# curl wrapper: call METHOD PATH TOKEN_VARNAME [json] -> "status|body"
call() {
  local method="$1" path="$2" tokvar="$3" data="$4"
  local tok="${!tokvar}"
  local headers=(-H "Content-Type: application/json")
  if [ -n "$tok" ]; then headers+=(-H "Authorization: Bearer $tok"); fi
  local resp
  if [ -n "$data" ]; then
    resp=$(curl -sS -o /tmp/ai_body_$$ -w "%{http_code}" -X "$method" "$API$path" "${headers[@]}" -d "$data")
  else
    resp=$(curl -sS -o /tmp/ai_body_$$ -w "%{http_code}" -X "$method" "$API$path" "${headers[@]}")
  fi
  local body
  body=$(cat /tmp/ai_body_$$)
  rm -f /tmp/ai_body_$$
  echo "${resp}|${body}"
}
split_status() { echo "${1%%|*}"; }
split_body()   { echo "${1#*|}"; }

# Collect all configured API keys so we can assert none leak to the client.
SECRETS=()
for v in AI_API_KEY CLAUDE_API_KEY OPENAI_API_KEY; do
  val="${!v}"
  if [ -n "$val" ]; then SECRETS+=("$val"); fi
done
assert_no_secret() {
  local label="$1" body="$2"
  local leaked=0
  for s in "${SECRETS[@]}"; do
    if echo "$body" | grep -qF "$s"; then leaked=1; fi
  done
  if [ "$leaked" = "0" ]; then ok "$label contains no API key"; else fail "$label LEAKED an API key" "$body"; fi
}

# ----------------------------------------------------------------------
echo "[1]  GET /api/ai/providers (auth required; booleans only)"
# Need a token even for providers; register admin first below, but the
# endpoint itself just needs any authed user. We register the admin now.
echo
echo "[2]  Obtain admin token"
if [ -n "$ADMIN_TOKEN" ]; then
  ok "using ADMIN_TOKEN from env"
else
  AU="ai_adm_${TS}"
  R=$(call POST /api/auth/register ADMIN_TOKEN \
    "{\"username\":\"$AU\",\"email\":\"$AU@example.com\",\"password\":\"AdmPass123!\"}")
  S=$(split_status "$R"); B=$(split_body "$R")
  [ "$S" = "200" ] || { fail "admin register" "$S $B"; exit 1; }
  ADMIN_TOKEN=$(echo "$B" | jq -r '.access_token')
  IS_ADMIN=$(echo "$B" | jq -r '.user.is_admin')
  ok "registered admin id=$(echo "$B" | jq -r '.user.id') is_admin=$IS_ADMIN"
  if [ "$IS_ADMIN" != "true" ]; then
    fail "registered user is NOT admin (DB not empty)" \
         "Re-run against a fresh DB, or pass ADMIN_TOKEN=<existing admin token>."
    exit 1
  fi
fi
echo

# ----------------------------------------------------------------------
echo "[3]  GET /api/ai/providers"
R=$(call GET /api/ai/providers ADMIN_TOKEN)
S=$(split_status "$R"); B=$(split_body "$R")
if [ "$S" = "200" ]; then
  CUR=$(echo "$B" | jq -r '.current_provider')
  ok "providers OK; current_provider=$CUR"
  # Each provider entry must expose only safe metadata keys.
  BADKEYS=$(echo "$B" | jq '[.providers[] | keys[]] | unique - ["name","model","configured","missing_config"] | length')
  [ "$BADKEYS" = "0" ] && ok "provider entries expose only safe keys" || fail "provider entries expose extra keys" "$B"
  assert_no_secret "/api/ai/providers" "$B"
else
  fail "GET /api/ai/providers" "$S $B"; exit 1
fi
echo

# ----------------------------------------------------------------------
echo "[4]  POST /api/ai/test (live provider round-trip)"
R=$(call POST /api/ai/test ADMIN_TOKEN "{\"prompt\":\"Return a tiny JSON object describing today.\"}")
S=$(split_status "$R"); B=$(split_body "$R")
if [ "$S" = "200" ]; then
  TEST_OK=$(echo "$B" | jq -r '.ok')
  TEST_PROVIDER=$(echo "$B" | jq -r '.provider')
  if [ "$TEST_OK" = "true" ]; then
    ok "ai/test ok provider=$TEST_PROVIDER model=$(echo "$B" | jq -r '.model') latency=$(echo "$B" | jq -r '.latency_ms')ms"
  else
    # A real provider may legitimately fail (bad key/model/unreachable). Surface it.
    fail "ai/test returned ok=false" "$(echo "$B" | jq -r '.error')"
  fi
  assert_no_secret "/api/ai/test response" "$B"
else
  fail "POST /api/ai/test" "$S $B"
fi
echo

# ----------------------------------------------------------------------
echo "[5]  Create throwaway project"
R=$(call POST /api/projects ADMIN_TOKEN \
  "{\"name\":\"AI smoke $TS\",\"description\":\"v0.6.1 provider smoke\",\"genre\":\"都市\",\"style\":\"快节奏\",\"target_episodes\":3}")
S=$(split_status "$R"); B=$(split_body "$R")
[ "$S" = "200" ] || { fail "create project" "$S $B"; exit 1; }
PID=$(echo "$B" | jq -r '.id')
ok "project_id=$PID"
echo

# ----------------------------------------------------------------------
echo "[6]  Create outline_generation task"
R=$(call POST /api/tasks ADMIN_TOKEN \
  "{\"project_id\":$PID,\"task_type\":\"outline_generation\",\"input_data\":{\"theme\":\"复仇女王\"}}")
S=$(split_status "$R"); B=$(split_body "$R")
[ "$S" = "200" ] || { fail "create task" "$S $B"; exit 1; }
TID=$(echo "$B" | jq -r '.id')
ok "task_id=$TID created (status=$(echo "$B" | jq -r '.status'))"
echo

# ----------------------------------------------------------------------
echo "[7]  Poll task until it leaves pending/running (max ${TASK_WAIT_SECONDS}s)"
STATUS=""
DEADLINE=$(( $(date +%s) + TASK_WAIT_SECONDS ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  R=$(call GET /api/tasks/$TID ADMIN_TOKEN)
  B=$(split_body "$R")
  STATUS=$(echo "$B" | jq -r '.status')
  case "$STATUS" in
    completed|waiting_human|failed) break ;;
  esac
  sleep 2
done
note "final task status=$STATUS"
if [ "$STATUS" = "completed" ] || [ "$STATUS" = "waiting_human" ]; then
  ok "task reached terminal-success status ($STATUS)"
  # output_data must be structured JSON (an object), not a string blob.
  OUT_TYPE=$(echo "$B" | jq -r '.output_data | type')
  [ "$OUT_TYPE" = "object" ] && ok "task.output_data is a JSON object" || fail "task.output_data is not an object" "type=$OUT_TYPE"
elif [ "$STATUS" = "failed" ]; then
  fail "task failed" "$(echo "$B" | jq -r '.error')"
else
  fail "task did not finish in time" "status=$STATUS (is the celery worker running?)"
fi
echo

# ----------------------------------------------------------------------
echo "[8]  GET /api/ai/runs?task_id=$TID"
R=$(call GET "/api/ai/runs?task_id=$TID" ADMIN_TOKEN)
S=$(split_status "$R"); B=$(split_body "$R")
[ "$S" = "200" ] || { fail "GET /api/ai/runs" "$S $B"; exit 1; }
N=$(echo "$B" | jq 'length')
if [ "$N" -ge 1 ]; then
  ok "found $N run row(s) for task $TID"
  RUN=$(echo "$B" | jq '.[0]')
  RUN_PROVIDER=$(echo "$RUN" | jq -r '.provider')
  RUN_MODEL=$(echo "$RUN" | jq -r '.model')
  RUN_STATUS=$(echo "$RUN" | jq -r '.status')
  RUN_ERR=$(echo "$RUN" | jq -r '.error_message')
  RUN_RHASH=$(echo "$RUN" | jq -r '.response_hash')
  RUN_QHASH=$(echo "$RUN" | jq -r '.request_hash')

  [ -n "$RUN_PROVIDER" ] && [ "$RUN_PROVIDER" != "null" ] && ok "run.provider recorded ($RUN_PROVIDER)" || fail "run.provider missing" "$RUN"
  [ -n "$RUN_MODEL" ] && [ "$RUN_MODEL" != "null" ] && ok "run.model recorded ($RUN_MODEL)" || fail "run.model missing" "$RUN"
  [ "$RUN_STATUS" = "completed" ] && ok "run.status=completed" || fail "run.status not completed" "status=$RUN_STATUS err=$RUN_ERR"
  { [ -z "$RUN_ERR" ] || [ "$RUN_ERR" = "null" ]; } && ok "run.error_message empty" || fail "run.error_message not empty" "$RUN_ERR"
  [ -n "$RUN_RHASH" ] && [ "$RUN_RHASH" != "null" ] && ok "run.response_hash present" || fail "run.response_hash missing" "$RUN"
  [ -n "$RUN_QHASH" ] && [ "$RUN_QHASH" != "null" ] && ok "run.request_hash present" || fail "run.request_hash missing" "$RUN"

  # The run row must never carry a prompt or raw response field.
  HASPROMPT=$(echo "$RUN" | jq 'has("prompt") or has("raw_response") or has("system_prompt") or has("user_prompt")')
  [ "$HASPROMPT" = "false" ] && ok "run row carries no prompt / raw_response" || fail "run row leaked prompt/raw_response" "$RUN"
  assert_no_secret "/api/ai/runs response" "$B"
else
  fail "no ai_generation_runs row for task $TID" "$B"
fi
echo

# ----------------------------------------------------------------------
echo "======================================================="
echo "  PASS=$PASS  FAIL=$FAIL  (provider=$AI_PROVIDER)"
echo "======================================================="
[ "$FAIL" = "0" ] || exit 1
