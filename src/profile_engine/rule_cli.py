import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from .config import get_settings
from .rule_compiler import RuleValidationError, compile_rule_pack


def main() -> None:
    parser = argparse.ArgumentParser(description="编译并校验画像引擎规则包")
    parser.add_argument("--source", type=Path, default=get_settings().rule_source_dir)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--publish", action="store_true", help="校验后发布到配置的数据库")
    parser.add_argument("--rollback", metavar="SHA256", help="回滚到数据库中已有的规则包哈希")
    args = parser.parse_args()
    if args.rollback:
        from .db import SessionLocal, init_db
        from .models import RulePack
        init_db()
        with SessionLocal() as db:
            target = db.scalar(select(RulePack).where(RulePack.sha256 == args.rollback))
            if not target:
                raise SystemExit(f"找不到规则包: {args.rollback}")
            for current in db.scalars(select(RulePack).where(RulePack.status == "published")):
                current.status = "superseded"
            target.status = "published"
            target.published_at = datetime.now(timezone.utc)
            db.commit()
            print(json.dumps({"rolled_back": True, "version": target.version, "sha256": target.sha256}, ensure_ascii=False, indent=2))
        return
    try:
        pack = compile_rule_pack(args.source.resolve())
    except RuleValidationError as exc:
        print(json.dumps({"valid": False, "errors": exc.errors}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    result = {"version": pack.version, "sha256": pack.sha256, "report": pack.report}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.output:
        args.output.write_text(json.dumps(pack.canonical, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    if args.publish:
        from .db import SessionLocal, init_db
        from .service import ensure_rule_pack
        init_db()
        with SessionLocal() as db:
            record = ensure_rule_pack(db, pack)
            print(json.dumps({"published": True, "rule_pack_id": record.id, "sha256": record.sha256}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
