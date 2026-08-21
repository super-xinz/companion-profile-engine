from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from sqlalchemy.exc import SQLAlchemyError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from profile_engine.db import SessionLocal  # noqa: E402
from profile_engine.template_seed import (  # noqa: E402
    LEGACY_TEMPLATE_PEOPLE,
    SHOWCASE_TEMPLATE_PEOPLE,
    latest_published_rule_pack,
    plan_template_people_seed,
    seed_template_people,
    select_template_people,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "向明确指定的租户补充内置模板人物。默认仅生成只读计划；"
            "只有添加 --apply 才会插入缺失记录。"
        ),
    )
    parser.add_argument(
        "--tenant",
        required=True,
        help="目标 tenant_id（必填，不读取默认租户配置）",
    )
    parser.add_argument(
        "--person",
        action="append",
        choices=[
            person.tenant_user_id
            for person in (*SHOWCASE_TEMPLATE_PEOPLE, *LEGACY_TEMPLATE_PEOPLE)
        ],
        help="只处理指定模板；可重复传入。省略时处理所选模式的全部 5 个模板。",
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="显式改用旧 person-* ID；默认使用不含生日的 showcase-* 安全 ID。",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="执行插入；不提供此参数时严格保持 dry-run。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        people = select_template_people(args.person, legacy=args.legacy)
        with SessionLocal() as db:
            if args.apply:
                pack = latest_published_rule_pack(db)
                report = seed_template_people(db, args.tenant, pack, people)
            else:
                report = plan_template_people_seed(db, args.tenant, people)
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, RuntimeError, SQLAlchemyError) as exc:
        print(f"模板人物播种失败: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
