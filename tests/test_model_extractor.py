import json

import httpx

from profile_engine.extractor import ModelSemanticExtractor, SemanticExtractorError
from profile_engine.model_gateway import ModelEndpoint


def endpoint(provider="deepseek"):
    return ModelEndpoint(
        provider=provider,
        label="DeepSeek V3.2" if provider == "deepseek" else "Claude",
        route_label="OpenRouter",
        api_key="secret",
        base_url="https://openrouter.example/v1",
        model="deepseek/deepseek-v3.2" if provider == "deepseek" else "anthropic/claude-test",
        timeout=30,
        extra_headers={"X-Title": "test"},
    )


def semantic_payload():
    return {"frames": [{"frame_id": 1, "subject": "user", "predicate": "socializing_requires_solitude_recovery", "object": None,
        "semantic_domain": "habit", "polarity": "positive", "negated": False, "modality": "asserted",
        "temporal_scope": "habitual", "frequency": "usually", "context": "general", "explicitness": .92,
        "extractor_confidence": .9, "supporting_span": "聚会后要独处"}],
        "trait_signals": [{"target_trait": "extroversion", "direction": "decrease", "strength": .8,
            "confidence": .9, "evidence_scope": "explicit_self_report", "supporting_span": "聚会后要独处",
            "rationale": "社交恢复方式支持较低外向性"}],
        "reply_guidance": {"intent": "self_disclosure", "tone": "warm", "empathy_first": True,
            "answer_first": False, "max_sentences": 3, "question_count": 1, "structure_level": "simple",
            "focus": "回应社交后的恢复需要", "avoid": ["下结论"], "requires_fresh_information": False}}


def test_deepseek_extractor_uses_openrouter_and_validates_structured_frames(monkeypatch):
    captured = {}

    def fake_post(*args, **kwargs):
        captured.update(kwargs)
        request = httpx.Request("POST", args[0])
        return httpx.Response(200, request=request, json={
            "model": "deepseek/deepseek-v3.2",
            "choices": [{"message": {"content": json.dumps(semantic_payload(), ensure_ascii=False)}}],
        })

    monkeypatch.setattr(httpx, "post", fake_post)
    analysis = ModelSemanticExtractor(endpoint()).analyze(
        "聚会后要独处", {"extroversion": {"label": "外向性", "current_value": .5}}
    )
    assert analysis.frames[0].frame_id.startswith("frm_")
    assert analysis.trait_signals[0].target_trait == "extroversion"
    assert analysis.reply_guidance.empathy_first is True
    assert captured["json"]["model"] == "deepseek/deepseek-v3.2"
    assert captured["json"]["response_format"] == {"type": "json_object"}
    assert captured["json"]["reasoning"] == {"enabled": False}


def test_claude_extractor_accepts_fenced_json_without_forcing_response_format(monkeypatch):
    captured = {}

    def fake_post(*args, **kwargs):
        captured.update(kwargs)
        request = httpx.Request("POST", args[0])
        content = "```json\n" + json.dumps(semantic_payload(), ensure_ascii=False) + "\n```"
        return httpx.Response(200, request=request, json={"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    analysis = ModelSemanticExtractor(endpoint("claude")).analyze("聚会后要独处")
    assert analysis.frames[0].predicate == "socializing_requires_solitude_recovery"
    assert "response_format" not in captured["json"]
    assert "reasoning" not in captured["json"]


def test_model_extractor_rejects_invalid_output(monkeypatch):
    def fake_post(*args, **kwargs):
        request = httpx.Request("POST", args[0])
        return httpx.Response(200, request=request, json={"choices": [{"message": {"content": "not json"}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    try:
        ModelSemanticExtractor(endpoint()).extract("hello")
        assert False, "invalid output must fail closed"
    except SemanticExtractorError:
        pass
