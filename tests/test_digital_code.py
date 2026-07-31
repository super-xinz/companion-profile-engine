import uuid
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from profile_engine.api import app
from profile_engine.digital_code import build_digital_code_profile, calculate_digital_code
from profile_engine.rule_bank import fragments_for_code, load_rule_index


def test_birth_date_reduces_to_supplied_four_digit_examples():
    assert calculate_digital_code("1998-12-06")[0] == "6318"
    assert calculate_digital_code("1989-10-15")[0] == "6118"
    assert calculate_digital_code("1988-08-09")[0] == "9817"
    assert calculate_digital_code("2000-01-01")[0] == "1129"


def test_workbook_builds_four_weighted_digital_code_domains():
    workbook = Path(__file__).parents[1] / "数字学画像2.xlsx"
    model = build_digital_code_profile("6318", fragments_for_code(str(workbook), "6318"))
    assert model["status"] == "derived"
    assert set(model["domains"]) == {"personality", "behavior", "work", "relationship"}
    assert all(domain["components"] and domain["summary"] for domain in model["domains"].values())
    assert all(0 < item["weight"] <= 1 for domain in model["domains"].values() for item in domain["components"])


def test_supported_birth_dates_cover_the_complete_workbook_code_set():
    workbook = Path(__file__).parents[1] / "数字学画像2.xlsx"
    workbook_codes = set(load_rule_index(str(workbook)))
    generated_codes = set()
    current = date(1900, 1, 1)
    end = date(2099, 12, 31)
    while current <= end:
        generated_codes.add(calculate_digital_code(current.isoformat())[0])
        current += timedelta(days=1)
    assert generated_codes == workbook_codes


def test_arbitrary_authorized_birth_date_gets_digital_code_and_trait_priors():
    tenant = f"digital-code-{uuid.uuid4().hex}"
    user = f"person-{uuid.uuid4().hex}"
    headers = {
        "X-API-Key": "local-development-key",
        "X-Tenant-ID": tenant,
        "Idempotency-Key": f"init-{user}",
    }
    with TestClient(app) as client:
        response = client.post("/v1/profiles:init", headers=headers, json={
            "tenant_user_id": user,
            "birth_date": "2001-02-03",
            "consent": {"profile": True, "sensitive_inference": True},
        })
        assert response.status_code == 200, response.text
        profile = response.json()["profile"]
        assert profile["birth_analysis"]["numerology_code"] == "3221"
        assert profile["digital_code_profile"]["code"] == "3221"
        values = [item["value"] for category in profile["core_traits"].values() for item in category.values()]
        assert any(value != .5 for value in values)
