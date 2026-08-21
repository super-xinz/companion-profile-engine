from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Iterable, Literal, Sequence

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import RulePack, User
from .schemas import Consent, ProfileInitRequest
from .service import VersionConflictError, init_profile
from .source_profiles import load_source_document
from .template_people import TEMPLATE_BY_BIRTH_DATE, TEMPLATE_PEOPLE, TemplatePerson


PlanAction = Literal["would_create", "skip_existing"]
ApplyAction = Literal["created", "skipped_existing"]


@dataclass(frozen=True)
class TemplateSeedSpec:
    tenant_user_id: str
    display_name: str
    source: TemplatePerson

    @property
    def birth_date(self) -> str:
        return self.source.birth_date

    @property
    def source_file(self) -> str:
        return self.source.source_file


SHOWCASE_TEMPLATE_PEOPLE = (
    TemplateSeedSpec("showcase-explorer", "灵感探索者", TEMPLATE_BY_BIRTH_DATE["1988-08-09"]),
    TemplateSeedSpec("showcase-innovator", "观点开拓者", TEMPLATE_BY_BIRTH_DATE["1989-10-15"]),
    TemplateSeedSpec("showcase-strategist", "果断策略者", TEMPLATE_BY_BIRTH_DATE["1989-11-28"]),
    TemplateSeedSpec("showcase-supporter", "温暖协调者", TEMPLATE_BY_BIRTH_DATE["1996-03-28"]),
    TemplateSeedSpec("showcase-anchor", "稳健守护者", TEMPLATE_BY_BIRTH_DATE["1998-12-06"]),
)

LEGACY_TEMPLATE_PEOPLE = tuple(
    TemplateSeedSpec(person.user_id, person.display_name, person)
    for person in TEMPLATE_PEOPLE
)


@dataclass(frozen=True)
class TemplateSeedPlanItem:
    tenant_user_id: str
    display_name: str
    birth_date: str
    action: PlanAction


@dataclass(frozen=True)
class TemplateSeedResultItem:
    tenant_user_id: str
    display_name: str
    birth_date: str
    action: ApplyAction


@dataclass(frozen=True)
class TemplateSeedReport:
    tenant_id: str
    mode: Literal["dry_run", "apply"]
    items: tuple[TemplateSeedPlanItem | TemplateSeedResultItem, ...]

    @property
    def create_count(self) -> int:
        return sum(item.action in {"would_create", "created"} for item in self.items)

    @property
    def skip_count(self) -> int:
        return sum(item.action in {"skip_existing", "skipped_existing"} for item in self.items)

    def as_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "mode": self.mode,
            "create_count": self.create_count,
            "skip_count": self.skip_count,
            "items": [asdict(item) for item in self.items],
        }


def validate_tenant_id(tenant_id: str) -> str:
    """Require an exact tenant identifier instead of falling back to configuration."""
    if not tenant_id:
        raise ValueError("tenant_id 不能为空")
    if tenant_id != tenant_id.strip():
        raise ValueError("tenant_id 首尾不能包含空白字符")
    if len(tenant_id) > 128:
        raise ValueError("tenant_id 不能超过 128 个字符")
    if any(ord(character) < 32 for character in tenant_id):
        raise ValueError("tenant_id 不能包含控制字符")
    return tenant_id


def select_template_people(
    user_ids: Iterable[str] | None = None,
    *,
    legacy: bool = False,
) -> tuple[TemplateSeedSpec, ...]:
    """Return templates in canonical order and reject unknown identifiers."""
    catalog = LEGACY_TEMPLATE_PEOPLE if legacy else SHOWCASE_TEMPLATE_PEOPLE
    if user_ids is None:
        return catalog
    requested = set(user_ids)
    known = {person.tenant_user_id for person in catalog}
    unknown = sorted(requested - known)
    if unknown:
        raise ValueError(f"未知模板人物: {unknown}")
    return tuple(person for person in catalog if person.tenant_user_id in requested)


def plan_template_people_seed(
    db: Session,
    tenant_id: str,
    people: Sequence[TemplateSeedSpec] = SHOWCASE_TEMPLATE_PEOPLE,
) -> TemplateSeedReport:
    """Build a read-only insert plan for one explicitly named tenant."""
    tenant_id = validate_tenant_id(tenant_id)
    user_ids = [person.tenant_user_id for person in people]
    existing_ids = set()
    if user_ids:
        existing_ids = set(db.scalars(select(User.tenant_user_id).where(
            User.tenant_id == tenant_id,
            User.tenant_user_id.in_(user_ids),
        )).all())
    items = tuple(
        TemplateSeedPlanItem(
            tenant_user_id=person.tenant_user_id,
            display_name=person.display_name,
            birth_date=person.birth_date,
            action="skip_existing" if person.tenant_user_id in existing_ids else "would_create",
        )
        for person in people
    )
    return TemplateSeedReport(tenant_id=tenant_id, mode="dry_run", items=items)


def latest_published_rule_pack(db: Session) -> RulePack:
    pack = db.scalar(select(RulePack).where(
        RulePack.status == "published",
    ).order_by(desc(RulePack.published_at)).limit(1))
    if not pack:
        raise RuntimeError("数据库中没有已发布规则包；请先完成迁移并启动一次画像服务")
    return pack


def _preflight_source_documents(people: Sequence[TemplateSeedSpec]) -> None:
    missing = [
        person.source_file
        for person in people
        if load_source_document(person.birth_date) is None
    ]
    if missing:
        raise RuntimeError(f"缺少模板人物原始画像文件: {missing}")


def seed_template_people(
    db: Session,
    tenant_id: str,
    pack: RulePack,
    people: Sequence[TemplateSeedSpec] = SHOWCASE_TEMPLATE_PEOPLE,
) -> TemplateSeedReport:
    """Insert missing templates while leaving every existing record untouched.

    ``init_profile`` commits each complete profile. If a run stops midway, rerunning
    this function safely continues with the remaining identifiers.
    """
    tenant_id = validate_tenant_id(tenant_id)
    if pack.status != "published":
        raise ValueError("只能使用已发布规则包播种模板人物")

    plan = plan_template_people_seed(db, tenant_id, people)
    people_by_id = {person.tenant_user_id: person for person in people}
    pending_people = [
        people_by_id[item.tenant_user_id]
        for item in plan.items
        if item.action == "would_create"
    ]
    _preflight_source_documents(pending_people)

    results: list[TemplateSeedResultItem] = []
    for item in plan.items:
        person = people_by_id[item.tenant_user_id]
        if item.action == "skip_existing":
            results.append(TemplateSeedResultItem(
                tenant_user_id=person.tenant_user_id,
                display_name=person.display_name,
                birth_date=person.birth_date,
                action="skipped_existing",
            ))
            continue

        try:
            init_profile(
                db,
                tenant_id,
                ProfileInitRequest(
                    tenant_user_id=person.tenant_user_id,
                    display_name=person.display_name,
                    birth_date=person.birth_date,
                    timezone="Asia/Shanghai",
                    enneagram=person.source.enneagram,
                    consent=Consent(profile=True, sensitive_inference=True),
                ),
                pack,
                f"req_seed_{uuid.uuid4().hex}",
                f"template-seed-v1-{tenant_id}-{person.tenant_user_id}",
                template_public_id=person.tenant_user_id,
            )
            action: ApplyAction = "created"
        except (IntegrityError, VersionConflictError):
            # A concurrent runner may have inserted the same tenant/user pair
            # after the plan query. Treat only that exact collision as a safe skip.
            db.rollback()
            existing = db.scalar(select(User.id).where(
                User.tenant_id == tenant_id,
                User.tenant_user_id == person.tenant_user_id,
            ))
            if not existing:
                raise
            action = "skipped_existing"
        except Exception:
            db.rollback()
            raise

        results.append(TemplateSeedResultItem(
            tenant_user_id=person.tenant_user_id,
            display_name=person.display_name,
            birth_date=person.birth_date,
            action=action,
        ))

    return TemplateSeedReport(tenant_id=tenant_id, mode="apply", items=tuple(results))
