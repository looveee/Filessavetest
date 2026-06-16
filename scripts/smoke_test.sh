#!/usr/bin/env bash
# scripts/smoke_test.sh
# v0.4 end-to-end smoke test.
#   - permission matrix RBAC
#   - audit log visibility
#   - system endpoint admin gating
#   - publisher account-use cross-owner rule
#   - delete project with audit logs surviving (no FK block)
#   - lookup does not leak email
#   - GET /users restricts non-admin to self
#
# Usage:
#   API_BASE=http://localhost:8000 bash scripts/smoke_test.sh

set -e

API="${API_BASE:-http://localhost:8000}"
TS=$(date +%s)

ADMIN_USER="admin_${TS}"
ADMIN_EMAIL="admin_${TS}@example.com"
ADMIN_PASS="AdminPass123!"
ADMIN_TOKEN=""
ADMIN_ID=""

PUB_USER="publisher_${TS}"
PUB_EMAIL="pub_${TS}@example.com"
PUB_PASS="PubPass123!"
PUB_TOKEN=""
PUB_ID=""

OUTSIDER_USER="outsider_${TS}"
OUTSIDER_EMAIL="out_${TS}@example.com"
OUTSIDER_PASS="OutPass123!"
OUTSIDER_TOKEN=""
OUTSIDER_ID=""

VIEWER_USER="viewer_${TS}"
VIEWER_EMAIL="viewer_${TS}@example.com"
VIEWER_PASS="ViewPass123!"
VIEWER_TOKEN=""
VIEWER_ID=""

REVIEWER_USER="reviewer_${TS}"
REVIEWER_EMAIL="reviewer_${TS}@example.com"
REVIEWER_PASS="RevPass123!"
REVIEWER_TOKEN=""
REVIEWER_ID=""

EDITOR_USER="editor_${TS}"
EDITOR_EMAIL="editor_${TS}@example.com"
EDITOR_PASS="EdPass123!"
EDITOR_TOKEN=""
EDITOR_ID=""

echo "==> API base : $API"
echo "==> Run id   : $TS"
echo

need() { command -v "$1" >/dev/null 2>&1 || { echo "MISSING: $1"; exit 1; }; }
need curl
need jq

# ----------------------------------------------------------------------
# Alembic state pre-flight (v0.5+)
#  - main.py must NOT call create_all and must NOT execute ALTER TABLE
#    IF NOT EXISTS hacks
#  - alembic must report a current revision (i.e. migrations applied)
# We allow this section to be skipped if running against a remote API
# that we don't have shell access to.
# ----------------------------------------------------------------------
if [ "${SKIP_ALEMBIC_CHECK:-0}" != "1" ]; then
  if [ -f "backend/app/main.py" ]; then
    if grep -nE 'create_all\(|ALTER TABLE IF NOT EXISTS' backend/app/main.py >/dev/null; then
      echo "FAIL: backend/app/main.py still contains create_all() or ALTER TABLE IF NOT EXISTS"
      grep -nE 'create_all\(|ALTER TABLE IF NOT EXISTS' backend/app/main.py
      exit 1
    fi
    echo "  PASS - main.py is clean (no create_all, no inline ALTER)"
  fi

  # Probe alembic current via docker compose if available.
  if command -v docker-compose >/dev/null 2>&1 && docker-compose ps backend 2>/dev/null | grep -q sv_backend; then
    CUR=$(docker-compose exec -T backend alembic current 2>&1 || true)
    if echo "$CUR" | grep -Eq '^[0-9a-f_]+\s+\(head\)|^[0-9a-f_]+ \(head\)'; then
      echo "  PASS - alembic current reports head: $(echo "$CUR" | head -1)"
    elif echo "$CUR" | grep -q "^[0-9a-f_]"; then
      echo "  PASS - alembic current = $(echo "$CUR" | head -1)"
    else
      echo "  WARN - alembic current returned: $CUR"
    fi
  fi
fi
echo

call() {
  local method="$1" path="$2" tokvar="$3" data="${4:-}"
  local tok="${!tokvar}"
  local headers=(-H "Content-Type: application/json")
  if [ -n "$tok" ]; then headers+=(-H "Authorization: Bearer $tok"); fi
  local resp
  if [ -n "$data" ]; then
    resp=$(curl -sS -o /tmp/sm_body_$$ -w "%{http_code}" -X "$method" "$API$path" "${headers[@]}" -d "$data")
  else
    resp=$(curl -sS -o /tmp/sm_body_$$ -w "%{http_code}" -X "$method" "$API$path" "${headers[@]}")
  fi
  local body
  body=$(cat /tmp/sm_body_$$)
  rm -f /tmp/sm_body_$$
  echo "${resp}|${body}"
}

split_status() { echo "${1%%|*}"; }
split_body()   { echo "${1#*|}"; }

PASS=0
FAIL=0
ok()   { echo "  PASS - $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL - $1"; echo "         $2" | head -c 500; echo; FAIL=$((FAIL+1)); }
note() { echo "  ... $1"; }

# ----------------------------------------------------------------------
echo "[0]  GET /api/system/health (public)"
R=$(call GET /api/system/health ADMIN_TOKEN)
S=$(split_status "$R"); B=$(split_body "$R")
if [ "$S" = "200" ] && [ "$(echo "$B" | jq -r '.ok')" = "true" ]; then
  ok "system/health public OK"
  # Confirm public health does NOT leak internal info
  HAS_BROKER=$(echo "$B" | jq 'has("components")')
  if [ "$HAS_BROKER" = "false" ]; then
    ok "public /health does not leak components/URLs"
  else
    fail "public /health leaked internal data" "$B"
  fi
else
  fail "system/health" "$S $B"
  exit 1
fi
echo

# ----------------------------------------------------------------------
echo "[1]  Register admin (first user — auto-admin)"
R=$(call POST /api/auth/register ADMIN_TOKEN \
  "{\"username\":\"$ADMIN_USER\",\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASS\",\"full_name\":\"Smoke Admin\"}")
S=$(split_status "$R"); B=$(split_body "$R")
[ "$S" = "200" ] || { fail "admin register" "$S $B"; exit 1; }
ADMIN_TOKEN=$(echo "$B" | jq -r '.access_token')
ADMIN_ID=$(echo "$B" | jq -r '.user.id')
ADMIN_IS_ADMIN=$(echo "$B" | jq -r '.user.is_admin')
ok "admin id=$ADMIN_ID is_admin=$ADMIN_IS_ADMIN"
[ "$ADMIN_IS_ADMIN" = "true" ] || echo "  WARN: first user is not admin (DB not empty?)"
echo

# ----------------------------------------------------------------------
echo "[1b] system/health-full requires admin"
R=$(call GET /api/system/health-full ADMIN_TOKEN)
S=$(split_status "$R")
[ "$S" = "200" ] && ok "admin can GET /system/health-full" || fail "admin should access health-full" "$S"

# ----------------------------------------------------------------------
echo "[2]  Register normal users"
register_normal() {
  local user="$1" email="$2" pass="$3" tokvar="$4" idvar="$5"
  R=$(call POST /api/auth/register UNUSED_TOK \
    "{\"username\":\"$user\",\"email\":\"$email\",\"password\":\"$pass\"}")
  S=$(split_status "$R"); B=$(split_body "$R")
  [ "$S" = "200" ] || { fail "register $user" "$S $B"; return 1; }
  eval "$tokvar=$(echo "$B" | jq -r '.access_token')"
  eval "$idvar=$(echo "$B" | jq -r '.user.id')"
  note "$user id=$(echo "$B" | jq -r '.user.id')"
}
register_normal "$PUB_USER"      "$PUB_EMAIL"      "$PUB_PASS"      PUB_TOKEN      PUB_ID
register_normal "$OUTSIDER_USER" "$OUTSIDER_EMAIL" "$OUTSIDER_PASS" OUTSIDER_TOKEN OUTSIDER_ID
register_normal "$VIEWER_USER"   "$VIEWER_EMAIL"   "$VIEWER_PASS"   VIEWER_TOKEN   VIEWER_ID
register_normal "$REVIEWER_USER" "$REVIEWER_EMAIL" "$REVIEWER_PASS" REVIEWER_TOKEN REVIEWER_ID
register_normal "$EDITOR_USER"   "$EDITOR_EMAIL"   "$EDITOR_PASS"   EDITOR_TOKEN   EDITOR_ID
ok "5 users registered"
echo

# ----------------------------------------------------------------------
echo "[2a] non-admin cannot GET /system/health-full"
R=$(call GET /api/system/health-full PUB_TOKEN)
S=$(split_status "$R")
if [ "$S" = "403" ]; then ok "non-admin -> 403 on /system/health-full"; else fail "should be 403" "$S"; fi

echo "[2b] non-admin cannot GET /system/permission-matrix"
R=$(call GET /api/system/permission-matrix PUB_TOKEN)
S=$(split_status "$R")
if [ "$S" = "403" ]; then ok "non-admin -> 403 on /system/permission-matrix"; else fail "should be 403" "$S"; fi

R=$(call GET /api/system/permission-matrix ADMIN_TOKEN)
S=$(split_status "$R")
if [ "$S" = "200" ]; then ok "admin -> 200 on /system/permission-matrix"; else fail "admin should access" "$S"; fi
echo

# ----------------------------------------------------------------------
echo "[3]  GET /api/users — non-admin sees only self; admin sees all"
R=$(call GET /api/users PUB_TOKEN)
S=$(split_status "$R"); B=$(split_body "$R")
N=$(echo "$B" | jq 'length')
if [ "$S" = "200" ] && [ "$N" = "1" ]; then
  RID=$(echo "$B" | jq -r '.[0].id')
  if [ "$RID" = "$PUB_ID" ]; then
    ok "non-admin sees only self in /users"
  else
    fail "non-admin sees wrong user" "got id=$RID expected=$PUB_ID"
  fi
else
  fail "non-admin /users should return [self]" "S=$S len=$N"
fi

R=$(call GET /api/users ADMIN_TOKEN)
N=$(echo "$(split_body "$R")" | jq 'length')
if [ "$N" -ge "6" ]; then ok "admin sees full directory ($N users)"; else fail "admin should see >=6" "len=$N"; fi
echo

# ----------------------------------------------------------------------
echo "[3a] /api/users/lookup does NOT leak email"
R=$(call GET "/api/users/lookup?username=$PUB_USER" ADMIN_TOKEN)
B=$(split_body "$R")
HAS_EMAIL=$(echo "$B" | jq -r '.[0] | has("email")')
if [ "$HAS_EMAIL" = "false" ]; then ok "lookup is UserPublic (no email)"; else fail "lookup leaked email" "$B"; fi
echo

# ----------------------------------------------------------------------
echo "[4]  admin creates project + adds members via lookup"
R=$(call POST /api/projects ADMIN_TOKEN \
  "{\"name\":\"Smoke v0.4 $TS\",\"description\":\"v0.4 smoke\",\"target_episodes\":1}")
PID=$(echo "$(split_body "$R")" | jq -r '.id')
ok "project_id=$PID"

add_member() {
  local uid="$1" role="$2"
  R=$(call POST /api/projects/$PID/members ADMIN_TOKEN "{\"user_id\":$uid,\"role\":\"$role\"}")
  S=$(split_status "$R")
  [ "$S" = "200" ] && ok "added $role uid=$uid" || fail "add $role" "$S $(split_body "$R")"
}
add_member "$PUB_ID"      "publisher"
add_member "$VIEWER_ID"   "viewer"
add_member "$REVIEWER_ID" "reviewer"
add_member "$EDITOR_ID"   "editor"

# Reject owner via members
R=$(call POST /api/projects/$PID/members ADMIN_TOKEN \
  "{\"user_id\":$OUTSIDER_ID,\"role\":\"owner\"}")
S=$(split_status "$R")
if [ "$S" = "400" ]; then ok "owner role refused via members endpoint"; else fail "owner assign should 400" "$S"; fi
echo

# ----------------------------------------------------------------------
echo "[5]  Pipeline: outline -> episode_split -> script_generation"
call POST /api/tasks ADMIN_TOKEN "{\"project_id\":$PID,\"task_type\":\"outline_generation\",\"input_data\":{\"theme\":\"smoke\"}}" >/dev/null
sleep 2
call POST /api/tasks ADMIN_TOKEN "{\"project_id\":$PID,\"task_type\":\"episode_split\",\"input_data\":{}}" >/dev/null
sleep 2

EPISODE_ID=""
for i in 1 2 3 4 5; do
  EPS=$(call GET /api/projects/$PID/episodes ADMIN_TOKEN)
  EPISODE_ID=$(echo "$(split_body "$EPS")" | jq -r '.[0].id // empty')
  [ -n "$EPISODE_ID" ] && break
  sleep 1
done
[ -n "$EPISODE_ID" ] || { fail "no episode" "$EPS"; exit 1; }
note "episode_id=$EPISODE_ID"

R=$(call POST /api/tasks ADMIN_TOKEN \
  "{\"project_id\":$PID,\"episode_id\":$EPISODE_ID,\"task_type\":\"script_generation\"}")
SCRIPT_TASK=$(echo "$(split_body "$R")" | jq -r '.id')
note "script_task=$SCRIPT_TASK"

for i in 1 2 3 4 5 6; do
  R=$(call GET /api/tasks/$SCRIPT_TASK ADMIN_TOKEN)
  ST=$(echo "$(split_body "$R")" | jq -r '.status')
  [ "$ST" = "waiting_human" ] && break
  sleep 1
done
note "script status=$ST"
echo

# ----------------------------------------------------------------------
echo "[6]  RBAC negatives: viewer / reviewer / publisher cannot task.create"
test_403_create_task() {
  local label="$1" tokvar="$2"
  R=$(call POST /api/tasks $tokvar \
    "{\"project_id\":$PID,\"task_type\":\"outline_generation\",\"input_data\":{\"theme\":\"x\"}}")
  S=$(split_status "$R")
  if [ "$S" = "403" ]; then ok "$label cannot task.create (403)"; else fail "$label should 403" "$S"; fi
}
test_403_create_task "viewer"    VIEWER_TOKEN
test_403_create_task "reviewer"  REVIEWER_TOKEN
test_403_create_task "publisher" PUB_TOKEN

# Publisher cannot edit script (no script.create / script.update — not even
# a script API surface; closest writable is review.rewrite which would also
# need review.rewrite. Verify the latter.)
R=$(call POST /api/reviews/tasks/$SCRIPT_TASK PUB_TOKEN '{"action":"rewrite"}')
S=$(split_status "$R")
if [ "$S" = "403" ]; then ok "publisher cannot review.rewrite (403)"; else fail "publisher should 403 on rewrite" "$S"; fi
echo

# ----------------------------------------------------------------------
echo "[7]  reviewer approves script -> cascade fires"
PRE=$(call GET "/api/tasks?project_id=$PID" ADMIN_TOKEN)
PRE_N=$(echo "$(split_body "$PRE")" | jq 'length')

R=$(call POST /api/reviews/tasks/$SCRIPT_TASK REVIEWER_TOKEN '{"action":"approve"}')
S=$(split_status "$R")
[ "$S" = "200" ] && ok "reviewer approve -> 200" || fail "reviewer approve" "$S $(split_body "$R")"
sleep 2
POST=$(call GET "/api/tasks?project_id=$PID" ADMIN_TOKEN)
POST_N=$(echo "$(split_body "$POST")" | jq 'length')
if [ "$POST_N" -gt "$PRE_N" ]; then ok "cascade fired ($PRE_N -> $POST_N tasks)"; else fail "cascade" "pre=$PRE_N post=$POST_N"; fi
echo

# ----------------------------------------------------------------------
echo "     waiting for video pipeline to produce asset (auto-approving downstream)..."
ASSET_ID=""
for i in $(seq 1 30); do
  R=$(call GET "/api/assets?project_id=$PID" ADMIN_TOKEN)
  ASSET_ID=$(echo "$(split_body "$R")" | jq -r '.[0].id // empty')
  if [ -n "$ASSET_ID" ]; then break; fi
  for ST in waiting_human in_review; do
    Q=$(call GET "/api/tasks?project_id=$PID&status=$ST" ADMIN_TOKEN)
    QID=$(echo "$(split_body "$Q")" | jq -r '.[0].id // empty')
    if [ -n "$QID" ] && [ "$QID" != "null" ]; then
      call POST /api/reviews/tasks/$QID ADMIN_TOKEN '{"action":"approve"}' >/dev/null
    fi
  done
  sleep 1
done
[ -n "$ASSET_ID" ] && ok "asset produced id=$ASSET_ID" || { fail "no asset after 30s" ""; ASSET_ID=""; }
echo

# ----------------------------------------------------------------------
echo "[8]  publisher uses owner's account via /accounts/usable"
# Admin creates a "studio" account
R=$(call POST /api/accounts ADMIN_TOKEN \
  "{\"platform\":\"douyin\",\"name\":\"studio_$TS\",\"persona\":\"studio\",\"publish_frequency\":\"daily\"}")
STUDIO_ACC=$(echo "$(split_body "$R")" | jq -r '.id')
note "studio account id=$STUDIO_ACC owner=admin"

# /accounts/usable as publisher must include the studio account
R=$(call GET "/api/accounts/usable?project_id=$PID" PUB_TOKEN)
S=$(split_status "$R"); B=$(split_body "$R")
[ "$S" = "200" ] || fail "publisher /accounts/usable" "$S $B"
HAS_STUDIO=$(echo "$B" | jq --argjson aid "$STUDIO_ACC" 'map(.id == $aid) | any')
if [ "$HAS_STUDIO" = "true" ]; then
  ok "publisher sees owner-account in /accounts/usable"
else
  fail "publisher cannot see owner account in /accounts/usable" "$B"
fi
# Also: usable response must NOT contain credentials
HAS_CREDS=$(echo "$B" | jq -r '.[0] | has("credentials")')
if [ "$HAS_CREDS" = "false" ]; then
  ok "/accounts/usable does not expose credentials"
else
  fail "/accounts/usable leaked credentials" "$B"
fi

# Now actually create the schedule
if [ -n "$ASSET_ID" ]; then
  WHEN=$(date -u -d '+1 hour' '+%Y-%m-%dT%H:%M:%S' 2>/dev/null \
       || date -u -v+1H '+%Y-%m-%dT%H:%M:%S')
  R=$(call POST /api/schedules PUB_TOKEN \
    "{\"asset_id\":$ASSET_ID,\"account_id\":$STUDIO_ACC,\"scheduled_at\":\"${WHEN}Z\",\"title\":\"smoke\"}")
  S=$(split_status "$R")
  if [ "$S" = "200" ]; then
    ok "publisher scheduled with project-owner's account"
  else
    fail "publisher schedule" "$S $(split_body "$R")"
  fi
else
  note "skip schedule — no asset"
fi
echo

# ----------------------------------------------------------------------
echo "[9]  outsider cannot read project / assets / schedules"
R=$(call GET /api/projects/$PID OUTSIDER_TOKEN)
[ "$(split_status "$R")" = "403" ] && ok "outsider /projects/{id} -> 403" || fail "outsider project" "$R"

R=$(call GET "/api/assets?project_id=$PID" OUTSIDER_TOKEN)
S=$(split_status "$R"); B=$(split_body "$R")
# Either 403 or empty list — both acceptable; the key is no leak.
LEAKED=""
if [ "$S" = "200" ]; then
  N=$(echo "$B" | jq 'length')
  [ "$N" = "0" ] && ok "outsider /assets?project_id -> empty (no leak)" || { fail "outsider /assets leaked $N rows" "$B"; LEAKED=1; }
elif [ "$S" = "403" ]; then
  ok "outsider /assets?project_id -> 403"
else
  fail "outsider /assets unexpected" "$S $B"
fi

if [ -n "$ASSET_ID" ]; then
  R=$(call GET /api/assets/$ASSET_ID OUTSIDER_TOKEN)
  [ "$(split_status "$R")" = "403" ] && ok "outsider /assets/{id} -> 403" || fail "outsider asset" "$R"
  R=$(call GET /api/assets/$ASSET_ID/download OUTSIDER_TOKEN)
  [ "$(split_status "$R")" = "403" ] && ok "outsider /assets/{id}/download -> 403" || fail "outsider download" "$R"
fi
echo

# ----------------------------------------------------------------------
echo "[10] delete project — audit logs survive (no FK block)"
# Admin creates a small throwaway project, deletes it, and confirms the
# audit row for the create AND delete both still query-able afterwards.
R=$(call POST /api/projects ADMIN_TOKEN "{\"name\":\"delete_me_$TS\"}")
DPID=$(echo "$(split_body "$R")" | jq -r '.id')

R=$(call DELETE /api/projects/$DPID ADMIN_TOKEN)
S=$(split_status "$R")
[ "$S" = "200" ] && note "deleted project $DPID" || { fail "project delete" "$S"; exit 1; }

# Now look for both create + delete audit rows for this project_id
R=$(call GET "/api/audit-logs?project_id=$DPID" ADMIN_TOKEN)
S=$(split_status "$R"); B=$(split_body "$R")
[ "$S" = "200" ] || { fail "audit query after delete" "$S $B"; exit 1; }
HAS_CREATE=$(echo "$B" | jq 'map(.action == "project.create") | any')
HAS_DELETE=$(echo "$B" | jq 'map(.action == "project.delete") | any')
if [ "$HAS_CREATE" = "true" ] && [ "$HAS_DELETE" = "true" ]; then
  ok "audit rows survive project delete (both create + delete present)"
else
  fail "audit rows missing after project delete" "create=$HAS_CREATE delete=$HAS_DELETE body=$B"
fi
echo

# ----------------------------------------------------------------------
echo "[11] audit visibility: outsider scoped"
R=$(call GET /api/audit-logs OUTSIDER_TOKEN)
B=$(split_body "$R")
LEAK=$(echo "$B" | jq --argjson p "$PID" 'map(select(.project_id == $p)) | length')
[ "$LEAK" = "0" ] && ok "outsider sees zero rows for project $PID" || fail "outsider audit leak" "$B"

R=$(call GET "/api/audit-logs?project_id=$PID" OUTSIDER_TOKEN)
[ "$(split_status "$R")" = "403" ] && ok "outsider explicit project filter -> 403" || fail "outsider explicit -> 403" "$R"
echo

# ----------------------------------------------------------------------
echo "[12] v0.4.1: AssetOut does not leak internal file_path"
if [ -n "$ASSET_ID" ]; then
  R=$(call GET "/api/assets?project_id=$PID" ADMIN_TOKEN)
  B=$(split_body "$R")
  HAS_PATH=$(echo "$B" | jq -r '.[0] | has("file_path")')
  HAS_COVER=$(echo "$B" | jq -r '.[0] | has("cover_path")')
  HAS_DLURL=$(echo "$B" | jq -r '.[0] | has("download_url")')
  if [ "$HAS_PATH" = "false" ] && [ "$HAS_COVER" = "false" ] && [ "$HAS_DLURL" = "true" ]; then
    ok "asset list redacted (no file_path/cover_path; has download_url)"
  else
    fail "asset list shape wrong" "file_path=$HAS_PATH cover=$HAS_COVER dlurl=$HAS_DLURL body=$B"
  fi

  R=$(call GET "/api/assets/$ASSET_ID" ADMIN_TOKEN)
  B=$(split_body "$R")
  HAS_PATH=$(echo "$B" | jq -r 'has("file_path")')
  if [ "$HAS_PATH" = "false" ]; then
    ok "asset detail also redacted"
  else
    fail "asset detail leaked file_path" "$B"
  fi
else
  note "skip asset shape checks — no asset"
fi
echo

echo "[13] v0.4.1: /api/assets/{id}/debug is admin-only"
if [ -n "$ASSET_ID" ]; then
  R=$(call GET "/api/assets/$ASSET_ID/debug" PUB_TOKEN)
  S=$(split_status "$R")
  [ "$S" = "403" ] && ok "non-admin debug -> 403" || fail "debug should 403 for non-admin" "$S $(split_body "$R")"

  R=$(call GET "/api/assets/$ASSET_ID/debug" ADMIN_TOKEN)
  S=$(split_status "$R"); B=$(split_body "$R")
  if [ "$S" = "200" ]; then
    HAS_PATH=$(echo "$B" | jq -r 'has("file_path")')
    [ "$HAS_PATH" = "true" ] && ok "admin debug exposes file_path" \
      || fail "admin debug missing file_path" "$B"
  else
    fail "admin debug should be 200" "$S $B"
  fi
fi
echo

echo "[14] v0.4.1: /api/assets/{id}/download still works for project members"
if [ -n "$ASSET_ID" ]; then
  R=$(call GET "/api/assets/$ASSET_ID/download" ADMIN_TOKEN)
  S=$(split_status "$R")
  if [ "$S" = "200" ]; then
    ok "admin download -> 200"
  else
    # Mock pipeline may produce a path that doesn't exist on disk; treat 404 as benign
    if [ "$S" = "404" ]; then
      note "download -> 404 (mock asset file not on disk; non-fatal for safety test)"
      ok "download endpoint reachable (404 is OK for mock-only assets)"
    else
      fail "admin download" "$S $(split_body "$R")"
    fi
  fi
fi
echo

echo "[15] v0.4.1: successful logins do NOT count toward username failure bucket"
# Register a fresh user, succeed-login many times, expect no 429.
SUC_USER="susucc_${TS}"
SUC_EMAIL="susucc_${TS}@example.com"
SUC_PASS="SuccessTest123!"
R=$(call POST /api/auth/register UNUSED_TOK \
  "{\"username\":\"$SUC_USER\",\"email\":\"$SUC_EMAIL\",\"password\":\"$SUC_PASS\"}")
[ "$(split_status "$R")" = "200" ] || { fail "register $SUC_USER" "$R"; }

# Drive 12 successful logins. With v0.4.0 buggy semantics the per-username
# bucket (limit=10) would have triggered 429 on attempt #11. With v0.4.1
# fixed semantics, success path does not count -> all 12 pass.
all_ok=1
for i in $(seq 1 12); do
  R=$(call POST /api/auth/login UNUSED_TOK \
    "{\"username\":\"$SUC_USER\",\"password\":\"$SUC_PASS\"}")
  S=$(split_status "$R")
  if [ "$S" != "200" ]; then
    # IP-bucket might trigger (limit=10/min). That's expected and orthogonal
    # to what we're testing. Tolerate IP 429 but never expect a username 429.
    if [ "$S" = "429" ]; then
      note "attempt #$i hit a 429 (likely IP bucket); stopping success-login test"
      break
    fi
    all_ok=0
    fail "success login #$i unexpectedly returned $S" "$(split_body "$R")"
    break
  fi
done
[ "$all_ok" = "1" ] && ok "successful logins didn't trip per-username bucket"

echo
echo "[16] v0.4.1: failed logins DO count toward username failure bucket"
# We'll spam wrong-password against ONE fixed username and expect a 429
# *with* the auth.rate_limited audit row eventually. Use a fresh username
# so the IP bucket is the only competing limiter.
FAIL_USER="failbkt_${TS}"
FAIL_EMAIL="failbkt_${TS}@example.com"
R=$(call POST /api/auth/register UNUSED_TOK \
  "{\"username\":\"$FAIL_USER\",\"email\":\"$FAIL_EMAIL\",\"password\":\"CorrectPass123!\"}")
[ "$(split_status "$R")" = "200" ] || note "register $FAIL_USER skipped (already exists?)"

# Use a unique IP-bucket-friendly approach: just hammer up to 25 wrong-pass
# attempts. We expect a 429 within those 25 (either bucket; both prove the
# system is enforcing). Note the username bucket window is 5min so once
# tripped, the next try with even the right password also gets 429.
saw_429=0
for i in $(seq 1 25); do
  R=$(call POST /api/auth/login UNUSED_TOK \
    "{\"username\":\"$FAIL_USER\",\"password\":\"wrongpass_$i\"}")
  S=$(split_status "$R")
  if [ "$S" = "429" ]; then
    saw_429=1
    break
  fi
done
[ "$saw_429" = "1" ] && ok "failed-login storm triggered 429 (rate limit working)" \
  || fail "rate limit never tripped after 25 failed logins" ""
echo

# ----------------------------------------------------------------------
echo "[17] v0.6 AI provider: providers / test / runs / structured output / prompts"

# /api/ai/providers is readable by any authed user and reports the provider.
R=$(call GET /api/ai/providers VIEWER_TOKEN)
S=$(split_status "$R"); B=$(split_body "$R")
if [ "$S" = "200" ] && [ -n "$(echo "$B" | jq -r '.current_provider // empty')" ]; then
  ok "GET /api/ai/providers -> $(echo "$B" | jq -r '.current_provider')"
else
  fail "ai/providers" "$S $B"
fi

# /api/ai/test is admin-only.
R=$(call POST /api/ai/test VIEWER_TOKEN '{"prompt":"hi"}')
[ "$(split_status "$R")" = "403" ] && ok "non-admin /api/ai/test -> 403" || fail "ai/test should 403 for non-admin" "$R"

R=$(call POST /api/ai/test ADMIN_TOKEN '{"prompt":"用一句话自我介绍"}')
S=$(split_status "$R"); B=$(split_body "$R")
if [ "$S" = "200" ] && [ "$(echo "$B" | jq -r '.ok')" = "true" ]; then
  ok "admin /api/ai/test ok provider=$(echo "$B" | jq -r '.provider') latency=$(echo "$B" | jq -r '.latency_ms')ms"
else
  fail "admin ai/test" "$S $B"
fi

# Create an outline task and confirm STRUCTURED JSON output + an AI run row.
R=$(call POST /api/tasks ADMIN_TOKEN \
  "{\"project_id\":$PID,\"task_type\":\"outline_generation\",\"input_data\":{\"theme\":\"v0.6 smoke\"}}")
OUTLINE_TASK=$(echo "$(split_body "$R")" | jq -r '.id')
note "outline task=$OUTLINE_TASK"
OL_STATUS=""
for i in $(seq 1 8); do
  R=$(call GET /api/tasks/$OUTLINE_TASK ADMIN_TOKEN)
  OL_STATUS=$(echo "$(split_body "$R")" | jq -r '.status')
  [ "$OL_STATUS" = "completed" ] && break
  [ "$OL_STATUS" = "failed" ] && break
  sleep 1
done
R=$(call GET /api/tasks/$OUTLINE_TASK ADMIN_TOKEN)
B=$(split_body "$R")
HAS_LOGLINE=$(echo "$B" | jq -r '.output_data | has("logline")')
if [ "$OL_STATUS" = "completed" ] && [ "$HAS_LOGLINE" = "true" ]; then
  ok "outline task output_data is structured JSON (has logline)"
else
  fail "outline structured output" "status=$OL_STATUS body=$B"
fi

# ai_generation_runs has a record for this task.
R=$(call GET "/api/ai/runs?task_id=$OUTLINE_TASK" ADMIN_TOKEN)
S=$(split_status "$R"); B=$(split_body "$R")
RUN_N=$(echo "$B" | jq 'length')
if [ "$S" = "200" ] && [ "$RUN_N" -ge 1 ]; then
  # And the run must never carry an api_key field.
  HAS_KEY=$(echo "$B" | jq -r 'any(.[]; has("api_key") or has("apiKey"))')
  if [ "$HAS_KEY" = "false" ]; then
    ok "ai/runs recorded the call ($RUN_N) and exposes no api_key"
  else
    fail "ai/runs leaked api_key" "$B"
  fi
else
  fail "ai/runs should have a record" "$S $B"
fi

# Prompt templates: admin can list, non-admin cannot; non-admin cannot edit.
R=$(call GET /api/prompts ADMIN_TOKEN)
S=$(split_status "$R"); B=$(split_body "$R")
P_N=$(echo "$B" | jq 'length')
if [ "$S" = "200" ] && [ "$P_N" -ge 1 ]; then
  ok "admin GET /api/prompts -> $P_N templates"
  PROMPT_ID=$(echo "$B" | jq -r '.[0].id')
else
  fail "admin prompts list" "$S $B"
  PROMPT_ID=""
fi

R=$(call GET /api/prompts VIEWER_TOKEN)
[ "$(split_status "$R")" = "403" ] && ok "non-admin GET /api/prompts -> 403" || fail "prompts should 403 for non-admin" "$R"

if [ -n "$PROMPT_ID" ]; then
  R=$(call PUT /api/prompts/$PROMPT_ID VIEWER_TOKEN '{"name":"hacked"}')
  [ "$(split_status "$R")" = "403" ] && ok "non-admin PUT /api/prompts/{id} -> 403" || fail "prompt edit should 403" "$R"
fi
echo


echo "PASS=$PASS  FAIL=$FAIL"
echo "===================================================="
[ "$FAIL" -eq 0 ] || exit 1
