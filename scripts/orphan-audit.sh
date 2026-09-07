#!/usr/bin/env bash
# 老业务表 ForeignKey 补强前的只读孤儿数据审计。
# 不修改任何数据；发现孤儿记录时以非 0 退出，禁止直接加 FK。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$ROOT/.env" ]]; then
  set -a
  source "$ROOT/.env"
  set +a
fi

if ! command -v psql >/dev/null 2>&1; then
  echo "缺少 psql，无法执行孤儿数据审计。"
  exit 2
fi

export PGPASSWORD="${POSTGRES_PASSWORD:-}"
PSQL=(psql -X -v ON_ERROR_STOP=1 \
  -h "${POSTGRES_HOST:-localhost}" \
  -p "${POSTGRES_PORT:-5432}" \
  -U "${POSTGRES_USER:-ecommerce}" \
  -d "${POSTGRES_DB:-ecommerce}")

SQL="
WITH checks AS (
  SELECT 'consumable_sku_mappings.sku_id -> product_skus.id' AS relation,
         m.id
  FROM consumable_sku_mappings m
  LEFT JOIN product_skus p ON p.id = m.sku_id
  WHERE p.id IS NULL

  UNION ALL
  SELECT 'consumable_sku_mappings.consumable_id -> consumables.id', m.id
  FROM consumable_sku_mappings m
  LEFT JOIN consumables c ON c.id = m.consumable_id
  WHERE c.id IS NULL

  UNION ALL
  SELECT 'consumable_transactions.consumable_id -> consumables.id', t.id
  FROM consumable_transactions t
  LEFT JOIN consumables c ON c.id = t.consumable_id
  WHERE c.id IS NULL

  UNION ALL
  SELECT 'inbound_consumable_usages.consumable_id -> consumables.id', u.id
  FROM inbound_consumable_usages u
  LEFT JOIN consumables c ON c.id = u.consumable_id
  WHERE c.id IS NULL

  UNION ALL
  SELECT 'inbound_consumable_usages.link_id -> procurement_chain_links.id', u.id
  FROM inbound_consumable_usages u
  LEFT JOIN procurement_chain_links l ON l.id = u.link_id
  WHERE l.id IS NULL

  UNION ALL
  SELECT 'inbound_consumable_usages.inbound_document_id -> jackyun_goods_documents.id', u.id
  FROM inbound_consumable_usages u
  LEFT JOIN jackyun_goods_documents d ON d.id = u.inbound_document_id
  WHERE d.id IS NULL
), ranked AS (
  SELECT relation, id, row_number() OVER (PARTITION BY relation ORDER BY id) AS rn
  FROM checks
), names(relation) AS (
  VALUES
    ('consumable_sku_mappings.sku_id -> product_skus.id'),
    ('consumable_sku_mappings.consumable_id -> consumables.id'),
    ('consumable_transactions.consumable_id -> consumables.id'),
    ('inbound_consumable_usages.consumable_id -> consumables.id'),
    ('inbound_consumable_usages.link_id -> procurement_chain_links.id'),
    ('inbound_consumable_usages.inbound_document_id -> jackyun_goods_documents.id')
)
SELECT n.relation,
       count(r.id) AS orphan_count,
       coalesce(string_agg(r.id::text, ',' ORDER BY r.id) FILTER (WHERE r.rn <= 10), '') AS sample_ids
FROM names n
LEFT JOIN ranked r ON r.relation = n.relation
GROUP BY n.relation
ORDER BY n.relation;
"

echo "==> ForeignKey orphan audit（只读）"
"${PSQL[@]}" -P pager=off -c "$SQL"

TOTAL_SQL="
SELECT
  (SELECT count(*) FROM consumable_sku_mappings m LEFT JOIN product_skus p ON p.id=m.sku_id WHERE p.id IS NULL) +
  (SELECT count(*) FROM consumable_sku_mappings m LEFT JOIN consumables c ON c.id=m.consumable_id WHERE c.id IS NULL) +
  (SELECT count(*) FROM consumable_transactions t LEFT JOIN consumables c ON c.id=t.consumable_id WHERE c.id IS NULL) +
  (SELECT count(*) FROM inbound_consumable_usages u LEFT JOIN consumables c ON c.id=u.consumable_id WHERE c.id IS NULL) +
  (SELECT count(*) FROM inbound_consumable_usages u LEFT JOIN procurement_chain_links l ON l.id=u.link_id WHERE l.id IS NULL) +
  (SELECT count(*) FROM inbound_consumable_usages u LEFT JOIN jackyun_goods_documents d ON d.id=u.inbound_document_id WHERE d.id IS NULL);
"
TOTAL="$("${PSQL[@]}" -Atc "$TOTAL_SQL")"

if [[ "$TOTAL" != "0" ]]; then
  echo "==> 发现 $TOTAL 条孤儿引用：暂时禁止新增对应 ForeignKey。先核对/修复真实数据。"
  exit 1
fi

echo "==> 通过：上述老表未发现孤儿引用，可进入 ForeignKey migration 设计阶段。"
