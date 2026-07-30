import json

import httpx

from profile_engine.extractor import QwenSemanticExtractor, SemanticExtractorError


def test_qwen_extractor_validates_structured_frames(monkeypatch):
    payload = {"frames": [{"frame_id": 1, "subject": "user", "predicate": "socializing_requires_solitude_recovery", "object": None,
        "semantic_domain": "habit", "polarity": "positive", "negated": False, "modality": "asserted",
        "temporal_scope": "habitual", "frequency": "usually", "context": "general", "explicitness": .92,
        "extractor_confidence": .9, "supporting_span": "聚会后要独处"}],
        "trait_signals": [{"target_trait": "extroversion", "direction": "decrease", "strength": .8,
            "confidence": .9, "evidence_scope": "explicit_self_report", "supporting_span": "聚会后要独处",
            "rationale": "社交恢复方式支持较低外向性"}],
        "reply_guidance": {"intent": "self_disclosure", "tone": "warm", "empathy_first": True,
            "answer_first": False, "max_sentences": 3, "question_count": 1, "structure_level": "simple",
            "focus": "回应社交后的恢复需要", "avoid": ["下结论"], "requires_fresh_information": False}}
    def fake_post(*args, **kwargs):
        request = httpx.Request("POST", args[0])
        return httpx.Response(200, request=request, json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]})
    monkeypatch.setattr(httpx, "post", fake_post)
    analysis = QwenSemanticExtractor("secret", "https://example.test/v1", "qwen-test").analyze(
        "聚会后要独处", {"extroversion": {"label": "外向性", "current_value": .5}})
    frames = analysis.frames
    assert len(frames) == 1
    assert frames[0].subject == "user"
    assert frames[0].frame_id.startswith("frm_")
    assert frames[0].frame_id != "1"
    assert analysis.trait_signals[0].target_trait == "extroversion"
    assert analysis.reply_guidance.empathy_first is True


def test_qwen_extractor_rejects_invalid_output(monkeypatch):
    def fake_post(*args, **kwargs):
        request = httpx.Request("POST", args[0])
        return httpx.Response(200, request=request, json={"choices": [{"message": {"content": "not json"}}]})
    monkeypatch.setattr(httpx, "post", fake_post)
    try:
        QwenSemanticExtractor("secret", "https://example.test/v1", "qwen-test").extract("hello")
        assert False, "invalid output must fail closed"
    except SemanticExtractorError:
        pass
