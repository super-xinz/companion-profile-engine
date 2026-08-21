from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from profile_engine.db import Base
from profile_engine.models import AuditLog, ProfileEvidence, ProfileVersion, RulePack, User
from profile_engine.rule_compiler import compile_rule_pack
from profile_engine.template_seed import (
    LEGACY_TEMPLATE_PEOPLE,
    SHOWCASE_TEMPLATE_PEOPLE,
    plan_template_people_seed,
    seed_template_people,
    select_template_people,
    validate_tenant_id,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def seed_db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as db:
        compiled = compile_rule_pack(PROJECT_ROOT / "rules")
        pack = RulePack(
            version=compiled.version,
            sha256=compiled.sha256,
            status="published",
            canonical_json=compiled.canonical,
            validation_report=compiled.report,
            published_at=datetime.now(timezone.utc),
        )
        db.add(pack)
        db.commit()
        yield db, pack
    engine.dispose()


def _count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


def test_default_plan_is_read_only_and_uses_safe_showcase_ids(seed_db):
    db, _pack = seed_db
    before = {model: _count(db, model) for model in (User, ProfileVersion, ProfileEvidence, AuditLog)}

    report = plan_template_people_seed(db, "production-tenant")

    assert report.mode == "dry_run"
    assert report.create_count == 5
    assert report.skip_count == 0
    assert [item.tenant_user_id for item in report.items] == [
        "showcase-explorer",
        "showcase-innovator",
        "showcase-strategist",
        "showcase-supporter",
        "showcase-anchor",
    ]
    assert [item.display_name for item in report.items] == [
        "灵感探索者", "观点开拓者", "果断策略者", "温暖协调者", "稳健守护者",
    ]
    assert [item.birth_date for item in report.items] == [
        "1988-08-09", "1989-10-15", "1989-11-28", "1996-03-28", "1998-12-06",
    ]
    assert all(not item.tenant_user_id.startswith("person-") for item in report.items)
    assert before == {
        model: _count(db, model) for model in (User, ProfileVersion, ProfileEvidence, AuditLog)
    }


def test_apply_creates_all_five_once_and_profile_uses_safe_public_id(seed_db):
    db, pack = seed_db

    first = seed_template_people(db, "production-tenant", pack)

    assert first.mode == "apply"
    assert first.create_count == 5
    assert first.skip_count == 0
    users = db.scalars(select(User).where(User.tenant_id == "production-tenant")).all()
    assert {user.tenant_user_id for user in users} == {
        person.tenant_user_id for person in SHOWCASE_TEMPLATE_PEOPLE
    }
    assert _count(db, ProfileVersion) == 5
    for user in users:
        version = db.scalar(select(ProfileVersion).where(ProfileVersion.user_id == user.id))
        assert version.snapshot["identity"]["template_person_id"] == user.tenant_user_id
        assert not version.snapshot["identity"]["template_person_id"].startswith("person-")
        assert "person-" not in json.dumps(version.snapshot, ensure_ascii=False)
        audit = db.scalar(select(AuditLog).where(AuditLog.user_id == user.id))
        assert "person-" not in json.dumps(audit.after, ensure_ascii=False)

    counts_after_first = {
        model: _count(db, model) for model in (User, ProfileVersion, ProfileEvidence, AuditLog)
    }
    second = seed_template_people(db, "production-tenant", pack)

    assert second.create_count == 0
    assert second.skip_count == 5
    assert counts_after_first == {
        model: _count(db, model) for model in (User, ProfileVersion, ProfileEvidence, AuditLog)
    }


def test_existing_record_is_skipped_without_any_field_or_profile_change(seed_db):
    db, pack = seed_db
    spec = SHOWCASE_TEMPLATE_PEOPLE[-1]
    existing = User(
        tenant_id="production-tenant",
        tenant_user_id=spec.tenant_user_id,
        display_name="负责人已有的名称",
        birth_date=date(2000, 1, 2),
        profile_consent=False,
        sensitive_inference_consent=False,
        inference_enabled=False,
    )
    db.add(existing)
    db.commit()
    original_id = existing.id
    original_updated_at = existing.updated_at

    report = seed_template_people(db, "production-tenant", pack, (spec,))

    assert report.create_count == 0
    assert report.skip_count == 1
    db.expire_all()
    preserved = db.scalar(select(User).where(User.id == original_id))
    assert preserved.display_name == "负责人已有的名称"
    assert preserved.birth_date == date(2000, 1, 2)
    assert preserved.profile_consent is False
    assert preserved.sensitive_inference_consent is False
    assert preserved.inference_enabled is False
    assert preserved.updated_at.replace(tzinfo=None) == original_updated_at.replace(tzinfo=None)
    assert db.scalar(select(ProfileVersion).where(ProfileVersion.user_id == original_id)) is None


def test_legacy_ids_require_explicit_selection_and_tenants_are_isolated(seed_db):
    db, pack = seed_db
    legacy = select_template_people(legacy=True)
    assert legacy == LEGACY_TEMPLATE_PEOPLE
    assert all(person.tenant_user_id.startswith("person-") for person in legacy)
    with pytest.raises(ValueError, match="未知模板人物"):
        select_template_people(["person-1998-12-06"])

    other_tenant = User(
        tenant_id="other-tenant",
        tenant_user_id="showcase-anchor",
        display_name="其他租户人物",
    )
    db.add(other_tenant)
    db.commit()
    report = seed_template_people(
        db,
        "production-tenant",
        pack,
        select_template_people(["showcase-anchor"]),
    )
    assert report.create_count == 1
    assert db.scalar(select(func.count()).select_from(User).where(
        User.tenant_user_id == "showcase-anchor",
    )) == 2


@pytest.mark.parametrize("tenant_id", ["", " tenant", "tenant ", "x" * 129, "bad\ntenant"])
def test_tenant_must_be_explicit_and_exact(tenant_id):
    with pytest.raises(ValueError):
        validate_tenant_id(tenant_id)
