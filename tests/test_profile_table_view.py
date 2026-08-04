from profile_engine.profile import build_profile_table_view


def test_unified_profile_table_view_contains_core_sections():
    profile = {
        "identity": {"user_id": "u1", "display_name": "测试"},
        "birth_analysis": {"numerology_code": "6318"},
        "digital_code_profile": {
            "status": "derived",
            "code": "6318",
            "confidence": 0.35,
            "domains": {"personality": {"label": "性格画像", "summary": "示例", "summary_coverage_weight": 1.0}},
        },
        "enneagram_profile": {"status": "confirmed", "identity": {"code": "SP/SX｜7w6"}, "confidence": 0.8, "interaction_strategy": {}},
        "core_traits": {"a": {"x": {"value": 0.5, "confidence": 0.2}}},
        "behavior_style": {"sample": True},
        "language_style": {"sample": True},
        "portrait": {"sample": True},
        "runtime": {"interaction_preferences": {}, "current_state": {}, "memories": []},
        "meta": {"profile_version": 1},
    }
    table_view = build_profile_table_view(profile)
    assert table_view["digital_code_profile"]["code"] == "6318"
    assert table_view["enneagram_profile"]["identity"]["code"] == "SP/SX｜7w6"
    assert "x" in table_view["core_traits"]["a"]