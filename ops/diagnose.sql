\echo '=== django_migrations rows (trading app, kr_disclosure / 0022-0024) ==='
SELECT id, app, name, applied FROM django_migrations
WHERE app = 'trading'
  AND (name LIKE '%kr_disclosure%' OR name LIKE '0022%' OR name LIKE '0023%' OR name LIKE '0024%')
ORDER BY id;

\echo '=== trading_symbol.dart_corp_code column ==='
SELECT column_name, data_type, character_maximum_length
FROM information_schema.columns
WHERE table_name = 'trading_symbol' AND column_name = 'dart_corp_code';

\echo '=== KR tables present? ==='
SELECT table_name FROM information_schema.tables
WHERE table_name IN ('trading_krdisclosure', 'trading_krfinancialfact');

\echo '=== trading_krdisclosure indexes ==='
SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'trading_krdisclosure';

\echo '=== trading_krfinancialfact constraints ==='
SELECT conname, contype FROM pg_constraint WHERE conrelid = 'trading_krfinancialfact'::regclass;

\echo '=== row counts (sanity, should be >=0, confirms tables are queryable) ==='
SELECT 'trading_krdisclosure' AS tbl, count(*) FROM trading_krdisclosure
UNION ALL
SELECT 'trading_krfinancialfact', count(*) FROM trading_krfinancialfact;
