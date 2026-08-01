#!/bin/sh
set -eu

BASE_URL="${1:-http://127.0.0.1:8000}"
BASE_URL="${BASE_URL%/}"
API_KEY="${PROFILE_API_KEY:-local-development-key}"
TENANT_ID="${PROFILE_TENANT_ID:-test-tenant}"
STAMP="$(date +%s)"
USER_ID="smoke-${STAMP}"
AUTH="X-API-Key: ${API_KEY}"
TENANT="X-Tenant-ID: ${TENANT_ID}"

echo "[1/4] health"
curl -fsS "$BASE_URL/health" | python -c 'import json,sys; d=json.load(sys.stdin); assert d["services"]["database"] == "ok"'

echo "[2/4] initialize and read"
curl -fsS -H "$AUTH" -H "$TENANT" -H "Idempotency-Key: init-$USER_ID" -H 'Content-Type: application/json' \
  -d "{\"tenant_user_id\":\"$USER_ID\",\"consent\":{\"profile\":true,\"sensitive_inference\":false}}" \
  "$BASE_URL/v1/profiles:init" >/dev/null
PROFILE="$(curl -fsS -H "$AUTH" -H "$TENANT" "$BASE_URL/v1/profiles/$USER_ID")"
VERSION="$(printf '%s' "$PROFILE" | python -c 'import json,sys; print(json.load(sys.stdin)["profile_version"])')"

echo "[3/4] ingest one turn"
TURN_ID="turn-$STAMP"
BODY="$(STAMP="$STAMP" TURN_ID="$TURN_ID" VERSION="$VERSION" python -c 'import datetime,json,os; print(json.dumps({"conversation_id":"session-"+os.environ["STAMP"],"message_id":os.environ["TURN_ID"],"expected_profile_version":int(os.environ["VERSION"]),"occurred_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"text":"以后回答短一点。","context":{"recent_turns":[]}},ensure_ascii=False))')"
curl -fsS -H "$AUTH" -H "$TENANT" -H "Idempotency-Key: $TURN_ID" -H 'Content-Type: application/json' -d "$BODY" "$BASE_URL/v1/profiles/$USER_ID/messages:ingest" >/dev/null

echo "[4/4] verify latest profile"
curl -fsS -H "$AUTH" -H "$TENANT" "$BASE_URL/v1/profiles/$USER_ID" | python -c 'import json,sys; assert json.load(sys.stdin)["profile_version"] >= 1'
echo "Smoke test passed: user=$USER_ID"
