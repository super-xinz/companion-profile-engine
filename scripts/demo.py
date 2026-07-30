"""Run the documented POC flow against a locally running server."""
import json
from datetime import datetime, timezone

import httpx

BASE = "http://localhost:8000"
HEADERS = {"X-API-Key": "local-development-key", "X-Tenant-ID": "demo-tenant"}


def post(path, key, body):
    response = httpx.post(BASE + path, headers={**HEADERS, "Idempotency-Key": key}, json=body, timeout=10)
    response.raise_for_status()
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    return response.json()


if __name__ == "__main__":
    result = post("/v1/profiles:init", "demo-init-v1", {"tenant_user_id": "demo-user", "birth_date": "1998-12-06",
        "timezone": "Asia/Shanghai", "consent": {"profile": True, "sensitive_inference": True}})
    version = result["profile_version"]
    for i, text in enumerate(("其实聚会后我一般要自己待一会儿才能恢复。", "以后回答短一点，先听我把话说完。", "我今天很累。"), 1):
        result = post("/v1/profiles/demo-user/messages:ingest", f"demo-message-{i}", {"conversation_id": "demo-conversation",
            "message_id": f"m{i}", "expected_profile_version": version, "occurred_at": datetime.now(timezone.utc).isoformat(), "text": text})
        version = result["profile_version"]

