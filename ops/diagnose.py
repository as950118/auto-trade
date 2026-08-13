import os
import sys

sys.path.insert(0, "/opt/auto-trade")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "autotrade.settings")

import django

django.setup()

from django.db import connection


def q(sql, params=None):
    with connection.cursor() as c:
        c.execute(sql, params or [])
        return c.fetchall()


print("=== django_migrations rows (trading app, kr_disclosure / 0022-0024) ===")
print(
    q(
        "SELECT id, app, name, applied FROM django_migrations "
        "WHERE app = 'trading' AND (name LIKE %s OR name LIKE %s OR name LIKE %s OR name LIKE %s) "
        "ORDER BY id",
        ["%kr_disclosure%", "0022%", "0023%", "0024%"],
    )
)

print("=== trading_symbol.dart_corp_code column ===")
print(
    q(
        "SELECT column_name, data_type, character_maximum_length FROM information_schema.columns "
        "WHERE table_name = 'trading_symbol' AND column_name = 'dart_corp_code'"
    )
)

print("=== KR tables present? ===")
print(
    q(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_name IN ('trading_krdisclosure', 'trading_krfinancialfact')"
    )
)

print("=== trading_krdisclosure indexes ===")
try:
    print(q("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'trading_krdisclosure'"))
except Exception as e:
    print("error:", e)

print("=== trading_krfinancialfact constraints ===")
try:
    print(q("SELECT conname, contype FROM pg_constraint WHERE conrelid = 'trading_krfinancialfact'::regclass"))
except Exception as e:
    print("error:", e)

print("=== row counts ===")
try:
    print(
        q(
            "SELECT 'trading_krdisclosure', count(*) FROM trading_krdisclosure "
            "UNION ALL SELECT 'trading_krfinancialfact', count(*) FROM trading_krfinancialfact"
        )
    )
except Exception as e:
    print("error:", e)
