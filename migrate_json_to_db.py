"""One-time migration of data/app_state.json into MySQL.

Idempotent: products are upserted by id, so it is safe to run once locally and
once on the server. Reads DB connection settings from the environment (.env).

Usage:
    python migrate_json_to_db.py            # uses data/app_state.json
    python migrate_json_to_db.py path.json  # custom source file
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from kaymio.database import init_db, save_product_entry  # noqa: E402  (after load_dotenv)
from kaymio.database.db import session_scope  # noqa: E402
from kaymio.database.models import AppMeta  # noqa: E402
from kaymio.database.state_store import LAST_PRODUCT_ID_KEY  # noqa: E402

DEFAULT_SOURCE = Path(__file__).resolve().parent / "data" / "app_state.json"


def _load_source(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"Source file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Could not parse {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"Unexpected JSON shape in {path} (expected an object).")
    return payload


def main() -> None:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    payload = _load_source(source)
    products = payload.get("products") or {}
    last_product_id = payload.get("last_product_id") or ""

    print(f"Initialising database schema...")
    init_db()

    migrated = 0
    for product_id, entry in products.items():
        if not product_id or not isinstance(entry, dict):
            continue
        save_product_entry(str(product_id), entry)
        migrated += 1
        print(f"  migrated product {product_id}")

    if last_product_id:
        with session_scope() as session:
            meta = session.get(AppMeta, LAST_PRODUCT_ID_KEY)
            if meta is None:
                session.add(AppMeta(key=LAST_PRODUCT_ID_KEY, value=str(last_product_id)))
            else:
                meta.value = str(last_product_id)

    print(
        f"Done. Migrated {migrated} product(s) from {source}. "
        f"last_product_id={last_product_id or '(none)'}"
    )


if __name__ == "__main__":
    main()
