"""Controlled showroom database migrations. A server-side command, not an Admin panel action.

    python scripts/migrate_showrooms.py --plan
    python scripts/migrate_showrooms.py --all --execute
    python scripts/migrate_showrooms.py --tenant <showroom id> --execute

Runs in the main process's environment. Without --execute it only reports. Showroom owners should back up
their databases first. Exit code 1 means at least one showroom failed; rerunning it is safe.
"""
import argparse
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database.control_plane import dispose_control_engine  # noqa: E402
from app.database.migration_orchestrator import migrate_all_required, migrate_showroom, plan_migrations  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    try:
        if args.tenant:
            if not args.execute:
                print("Add --execute to migrate this showroom.")
                return 2
            result = await migrate_showroom(uuid.UUID(args.tenant))
            print(f"{result.tenant_id} {result.outcome} {result.schema_version or ''} {result.error_code or ''}".strip())
            return 0 if result.outcome == "SUCCEEDED" else 1
        planned = await plan_migrations()
        if not args.all or not args.execute:
            for item in planned:
                print(f"{item.public_code} {item.tenant_id} {item.tenant_status} {item.connection_status} {item.schema_version} -> {item.required_version} {'REQUIRED' if item.required else 'CURRENT'}")
            print(f"{sum(i.required for i in planned)} of {len(planned)} showrooms need migrating. Nothing was changed.")
            return 0
        results = await migrate_all_required()
        for result in results:
            print(f"{result.public_code} {result.outcome} {result.schema_version or ''} {result.error_code or ''}".strip())
        failed = sum(r.outcome == "FAILED" for r in results)
        print(f"{len(results)} attempted, {failed} failed.")
        return 1 if failed else 0
    finally:
        await dispose_control_engine()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate showroom databases one at a time.")
    parser.add_argument("--plan", action="store_true", help="Report only. This is the default.")
    parser.add_argument("--all", action="store_true", help="Every showroom that needs it")
    parser.add_argument("--tenant", help="One showroom id")
    parser.add_argument("--execute", action="store_true", help="Actually migrate")
    sys.exit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
