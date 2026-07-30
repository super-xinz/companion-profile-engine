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
from profile_engine.models import AuditLog, IdempotencyRecord, RulePack, User  # noqa: E402
from profile_engine.rule_compiler import compile_rule_pack  # noqa: E402
from profile_engine.schemas import Consent, ProfileInitRequest  # noqa: E402
from profile_engine.service import ensure_rule_pack, find_user, init_profile  # noqa: E402
from profile_engine.workspace import _ensure_conversation  # noqa: E402


SEEDS = (
    ("person-1988-08-09", "1988年8月9日", "1988-08-09"),
    ("person-1989-10-15", "1989年10月15日", "1989-10-15"),
    ("person-1998-12-06", "1998年12月6日", "1998-12-06"),
)


def main() -> None:
    init_db()
    tenant_id = get_settings().demo_tenant_id
    with SessionLocal() as db:
        db.execute(delete(AuditLog).where(AuditLog.user_id.is_not(None)))
        db.execute(delete(IdempotencyRecord))
        db.execute(delete(User))
        db.commit()

        pack = db.scalar(select(RulePack).where(
            RulePack.status == "published"
        ).order_by(desc(RulePack.published_at)).limit(1))
        if not pack:
            source = get_settings().rule_source_dir
            if not source.is_absolute():
                source = (PROJECT_ROOT / source).resolve()
            pack = ensure_rule_pack(db, compile_rule_pack(source))

        for user_id, display_name, birth_date in SEEDS:
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

        people = db.scalars(select(User).order_by(User.birth_date)).all()
        print([(person.tenant_user_id, person.display_name, person.birth_date.isoformat()) for person in people])


if __name__ == "__main__":
    main()
