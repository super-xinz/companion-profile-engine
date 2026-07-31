from __future__ import annotations

from datetime import date
from pathlib import Path
import sys
import uuid

from sqlalchemy import delete, desc, select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from profile_engine.config import get_settings  # noqa: E402
from profile_engine.db import SessionLocal, init_db  # noqa: E402
from profile_engine.models import AuditLog, RulePack, User  # noqa: E402
from profile_engine.rule_compiler import compile_rule_pack  # noqa: E402
from profile_engine.schemas import Consent, ProfileInitRequest  # noqa: E402
from profile_engine.service import ensure_rule_pack, find_user, init_profile  # noqa: E402
from profile_engine.template_people import TEMPLATE_PEOPLE  # noqa: E402
from profile_engine.workspace import _ensure_conversation  # noqa: E402


def main() -> None:
    init_db()
    tenant_id = get_settings().demo_tenant_id
    with SessionLocal() as db:
        template_user_ids = [person.user_id for person in TEMPLATE_PEOPLE]
        stored_user_ids = select(User.id).where(
            User.tenant_id == tenant_id,
            User.tenant_user_id.in_(template_user_ids),
        )
        db.execute(delete(AuditLog).where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.user_id.in_(stored_user_ids),
        ))
        db.execute(delete(User).where(
            User.tenant_id == tenant_id,
            User.tenant_user_id.in_(template_user_ids),
        ))
        db.commit()

        pack = db.scalar(select(RulePack).where(
            RulePack.status == "published"
        ).order_by(desc(RulePack.published_at)).limit(1))
        if not pack:
            source = get_settings().rule_source_dir
            if not source.is_absolute():
                source = (PROJECT_ROOT / source).resolve()
            pack = ensure_rule_pack(db, compile_rule_pack(source))

        for person in TEMPLATE_PEOPLE:
            user_id = person.user_id
            display_name = person.display_name
            birth_date = person.birth_date
            init_profile(
                db,
                tenant_id,
                ProfileInitRequest(
                    tenant_user_id=user_id,
                    display_name=display_name,
                    birth_date=date.fromisoformat(birth_date),
                    timezone="Asia/Shanghai",
                    consent=Consent(profile=True, sensitive_inference=True),
                ),
                pack,
                f"reset_{uuid.uuid4().hex}",
                f"reset-{tenant_id}-{user_id}",
            )
            user = find_user(db, tenant_id, user_id)
            _ensure_conversation(db, user, title=f"{display_name}的第一段对话")
            db.commit()

        people = db.scalars(select(User).where(
            User.tenant_id == tenant_id,
            User.tenant_user_id.in_(template_user_ids),
        ).order_by(User.birth_date)).all()
        print([(person.tenant_user_id, person.display_name, person.birth_date.isoformat()) for person in people])


if __name__ == "__main__":
    main()
