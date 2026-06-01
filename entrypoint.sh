#!/usr/bin/env bash
set -e

DB_HOST="${DB_HOST:-db}"
DB_PORT="${DB_PORT:-3306}"

echo "Waiting for MySQL at ${DB_HOST}:${DB_PORT}..."
until mysqladmin ping -h "${DB_HOST}" -P "${DB_PORT}" --silent 2>/dev/null; do
  sleep 2
done
echo "MySQL is reachable."

# Create tables + seed the default admin / app_meta.
python -c "from kaymio.database import init_db; init_db()"

# First-run migration: if the DB has no products yet and the JSON snapshot
# exists, import it. Idempotent and safe to leave in the entrypoint.
python - <<'PY'
import json
from pathlib import Path

from kaymio.database.db import session_scope
from kaymio.database.models import Product
from kaymio.database.state_store import save_app_state

snapshot = Path("data/app_state.json")
with session_scope() as session:
    has_products = session.query(Product.id).first() is not None

if has_products:
    print("Products already present; skipping JSON migration.")
elif snapshot.exists():
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    save_app_state(payload if isinstance(payload, dict) else {})
    print(f"Migrated {len(payload.get('products', {}))} product(s) from {snapshot}.")
else:
    print("No app_state.json snapshot found; starting with an empty database.")
PY

echo "Starting Kaymio app on port ${PORT:-8081}..."
exec python run.py
