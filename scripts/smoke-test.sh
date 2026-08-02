#!/bin/sh
set -eu

BASE_URL="${1:-http://127.0.0.1:8000}"
BASE_URL="${BASE_URL%/}"
API_KEY="${PROFILE_API_KEY:-local-development-key}"
TENANT_ID="${PROFILE_TENANT_ID:-test-tenant}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
STAMP="$(date +%s)"
USER_ID="smoke-${STAMP}"
AUTH="X-API-Key: ${API_KEY}"
TENANT="X-Tenant-ID: ${TENANT_ID}"

echo "[1/5] liveness and readiness"
curl -fsS "$BASE_URL/livez" | "$PYTHON_BIN" -c 'import json,sys; assert json.load(sys.stdin)["status"] == "ok"'
curl -fsS "$BASE_URL/readyz" | "$PYTHON_BIN" -c 'import json,sys; d=json.load(sys.stdin); assert d["services"]["database"] == "ok"'

echo "[2/5] capabilities and version contract"
curl -fsS -H "$AUTH" -H "$TENANT" "$BASE_URL/v1/capabilities" | \
  "$PYTHON_BIN" -c 'import json,sys; d=json.load(sys.stdin); assert d["api_version"] == "v1"; assert d["profile_schema_version"]'

echo "[3/5] initialize and read"
curl -fsS -H "$AUTH" -H "$TENANT" -H "Idempotency-Key: init-$USER_ID" -H 'Content-Type: application/json' \
  -d "{\"tenant_user_id\":\"$USER_ID\",\"consent\":{\"profile\":true,\"sensitive_inference\":false}}" \
  "$BASE_URL/v1/profiles:init" >/dev/null
PROFILE="$(curl -fsS -H "$AUTH" -H "$TENANT" "$BASE_URL/v1/profiles/$USER_ID")"
VERSION="$(printf '%s' "$PROFILE" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["profile_version"])')"

echo "[4/5] ingest one turn"
TURN_ID="turn-$STAMP"
BODY="$(STAMP="$STAMP" TURN_ID="$TURN_ID" VERSION="$VERSION" "$PYTHON_BIN" -c 'import datetime,json,os; print(json.dumps({"conversation_id":"session-"+os.environ["STAMP"],"message_id":os.environ["TURN_ID"],"expected_profile_version":int(os.environ["VERSION"]),"occurred_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"text":"以后回答短一点。","context":{"recent_turns":[]}},ensure_ascii=False))')"
UPDATE="$(curl -fsS -H "$AUTH" -H "$TENANT" -H "Idempotency-Key: $TURN_ID" -H 'Content-Type: application/json' -d "$BODY" "$BASE_URL/v1/profiles/$USER_ID/messages:ingest")"
printf '%s' "$UPDATE" | "$PYTHON_BIN" -c 'import json,sys; d=json.load(sys.stdin); assert d["no_profile_change"] is False; assert d["profile_version"] > int(sys.argv[1])' "$VERSION"

echo "[5/5] verify latest profile"
curl -fsS -H "$AUTH" -H "$TENANT" "$BASE_URL/v1/profiles/$USER_ID" | "$PYTHON_BIN" -c 'import json,sys; assert json.load(sys.stdin)["profile_version"] > int(sys.argv[1])' "$VERSION"
echo "Smoke test passed: user=$USER_ID"
