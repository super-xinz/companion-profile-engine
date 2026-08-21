"""Export the server-to-server API contract handed to integrating partners."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from profile_engine.api import app


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "integration" / "b2b" / "openapi.json"


RESPONSE_SCHEMAS: dict[str, dict[str, Any]] = {
    "RulePackSummary": {
        "type": "object",
        "required": ["version", "sha256", "status"],
        "properties": {
            "version": {"type": "string"},
            "sha256": {"type": "string"},
            "status": {"type": "string"},
        },
    },
    "HealthResponse": {
        "type": "object",
        "required": ["status", "service", "version", "services"],
        "properties": {
            "status": {"type": "string", "enum": ["ok", "degraded"]},
            "service": {"type": "string"},
            "version": {"type": "string"},
            "services": {
                "type": "object",
                "required": ["application", "database"],
                "properties": {
                    "application": {"type": "string"},
                    "database": {"type": "string"},
                },
            },
        },
    },
    "ProfileInitResponse": {
        "type": "object",
        "required": ["request_id", "profile_version", "rule_pack", "profile", "warnings"],
        "properties": {
            "request_id": {"type": "string"},
            "profile_version": {"type": "integer", "minimum": 1},
            "rule_pack": {"$ref": "#/components/schemas/RulePackSummary"},
            "profile": {"type": "object", "additionalProperties": True},
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
    },
    "ProfileReadResponse": {
        "type": "object",
        "required": ["profile_version", "profile", "rule_pack_versions"],
        "properties": {
            "profile_version": {"type": "integer", "minimum": 1},
            "profile": {"type": "object", "additionalProperties": True},
            "rule_pack_versions": {"type": "object", "additionalProperties": {"type": "string"}},
        },
    },
    "ReplyHints": {
        "type": "object",
        "required": ["max_sentences", "answer_first", "empathy_first", "question_count", "structure_level"],
        "properties": {
            "max_sentences": {"type": "integer"},
            "answer_first": {"type": "boolean"},
            "empathy_first": {"type": "boolean"},
            "question_count": {"type": "integer"},
            "structure_level": {"type": "string"},
            "intent": {"type": "string"},
            "tone": {"type": "string"},
            "focus": {"type": "string"},
            "avoid": {"type": "array", "items": {"type": "string"}},
            "requires_fresh_information": {"type": "boolean"},
        },
        "additionalProperties": True,
    },
    "MessageIngestResponse": {
        "type": "object",
        "required": [
            "request_id", "profile_version", "rule_pack", "semantic_frames",
            "profile_patch", "runtime_operations", "reply_hints", "no_profile_change",
        ],
        "properties": {
            "request_id": {"type": "string"},
            "profile_version": {"type": "integer", "minimum": 1},
            "rule_pack": {"$ref": "#/components/schemas/RulePackSummary"},
            "semantic_extractor_version": {"type": "string"},
            "semantic_frames": {"type": "array", "items": {"type": "object"}},
            "evidence": {"type": "array", "items": {"type": "object"}},
            "candidate_trait_signals": {"type": "array", "items": {"type": "object"}},
            "accepted_trait_signals": {"type": "array", "items": {"type": "object"}},
            "rejected_trait_signals": {"type": "array", "items": {"type": "object"}},
            "profile_patch": {"type": "array", "items": {"type": "object"}},
            "runtime_operations": {"type": "array", "items": {"type": "object"}},
            "derived_patch": {"type": "array", "items": {"type": "object"}},
            "model_reply_guidance": {"type": "object"},
            "reply_hints": {"$ref": "#/components/schemas/ReplyHints"},
            "strategy_trace": {"type": "object"},
            "no_profile_change": {"type": "boolean"},
        },
    },
    "ProfileExplainResponse": {
        "type": "object",
        "required": ["profile_version", "supporting_evidence", "counter_evidence", "invalidated_evidence", "version_history"],
        "properties": {
            "profile_version": {"type": "integer"},
            "field": {"type": ["string", "null"]},
            "supporting_evidence": {"type": "array", "items": {"type": "object"}},
            "counter_evidence": {"type": "array", "items": {"type": "object"}},
            "invalidated_evidence": {"type": "array", "items": {"type": "object"}},
            "version_history": {"type": "array", "items": {"type": "object"}},
        },
    },
    "ProfileCorrectionResponse": {
        "type": "object",
        "required": ["request_id", "profile_version", "rule_pack", "corrected_field", "before", "requested_value", "after"],
        "properties": {
            "request_id": {"type": "string"},
            "profile_version": {"type": "integer"},
            "rule_pack": {"$ref": "#/components/schemas/RulePackSummary"},
            "corrected_field": {"type": "string"},
            "before": {},
            "requested_value": {},
            "after": {},
            "derived_patch": {"type": "array", "items": {"type": "object"}},
        },
    },
    "SetEnneagramResponse": {
        "type": "object",
        "required": ["request_id", "profile_version", "before", "enneagram_profile", "rule_pack"],
        "properties": {
            "request_id": {"type": "string"},
            "profile_version": {"type": "integer"},
            "before": {},
            "enneagram_profile": {"type": "object"},
            "rule_pack": {"$ref": "#/components/schemas/RulePackSummary"},
        },
    },
    "ForgetResponse": {
        "type": "object",
        "required": ["request_id", "profile_version", "scope", "affected_ids", "rule_pack"],
        "properties": {
            "request_id": {"type": "string"},
            "profile_version": {"type": "integer"},
            "scope": {"type": "string"},
            "affected_ids": {"type": "array", "items": {"type": "string"}},
            "rule_pack": {"$ref": "#/components/schemas/RulePackSummary"},
        },
    },
    "RulePackCurrentResponse": {
        "type": "object",
        "required": ["version", "sha256", "status", "validation_report", "published_at"],
        "properties": {
            "version": {"type": "string"},
            "sha256": {"type": "string"},
            "status": {"type": "string"},
            "validation_report": {"type": "object"},
            "published_at": {"type": ["string", "null"], "format": "date-time"},
        },
    },
}


SUCCESS_RESPONSES = {
    ("/health", "get"): "HealthResponse",
    ("/v1/profiles:init", "post"): "ProfileInitResponse",
    ("/v1/profiles/{user_id}", "get"): "ProfileReadResponse",
    ("/v1/profiles/{user_id}/messages:ingest", "post"): "MessageIngestResponse",
    ("/v1/profiles/{user_id}/explain", "get"): "ProfileExplainResponse",
    ("/v1/profiles/{user_id}:correct", "post"): "ProfileCorrectionResponse",
    ("/v1/profiles/{user_id}:set-enneagram", "post"): "SetEnneagramResponse",
    ("/v1/profiles/{user_id}:forget", "post"): "ForgetResponse",
    ("/v1/rule-packs/current", "get"): "RulePackCurrentResponse",
}


def referenced_schemas(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            found.add(ref.rsplit("/", 1)[-1])
        for child in value.values():
            found.update(referenced_schemas(child))
    elif isinstance(value, list):
        for child in value:
            found.update(referenced_schemas(child))
    return found


def build_contract() -> dict[str, Any]:
    source = deepcopy(app.openapi())
    source["info"] = {
        "title": "Companion Profile Engine - B2B API",
        "version": source["info"]["version"],
        "description": "服务端到服务端的画像初始化、读取、消息摄取、更正与用户数据处置接口。",
    }
    source["servers"] = [{"url": "http://localhost:8000", "description": "本地；联调时替换为交付的环境地址"}]
    source["paths"] = {
        path: item
        for path, item in source["paths"].items()
        if (path == "/health" or path.startswith("/v1/"))
        and path != "/v1/profiles/{user_id}:reset"
    }

    all_schemas = source.get("components", {}).get("schemas", {})
    required = referenced_schemas(source["paths"])
    pending = list(required)
    while pending:
        name = pending.pop()
        for dependency in referenced_schemas(all_schemas.get(name, {})) - required:
            required.add(dependency)
            pending.append(dependency)
    source.setdefault("components", {})["schemas"] = {
        name: schema for name, schema in all_schemas.items() if name in required
    }
    source["components"]["schemas"].update(RESPONSE_SCHEMAS)
    for (path, method), schema_name in SUCCESS_RESPONSES.items():
        source["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"] = {
            "$ref": f"#/components/schemas/{schema_name}"
        }
    return source


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(build_contract(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(OUTPUT)


if __name__ == "__main__":
    main()
