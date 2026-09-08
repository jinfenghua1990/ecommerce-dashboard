"""add configurable warehouse master and receipt references

Revision ID: b9d4f6a8c2e1
Revises: c4f8a2e6b9d1
"""

from alembic import op
import sqlalchemy as sa

revision = "b9d4f6a8c2e1"
down_revision = "c4f8a2e6b9d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "warehouses",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("warehouse_type", sa.String(length=32), nullable=False, server_default="other"),
        sa.Column("purpose", sa.String(length=16), nullable=False, server_default="both"),
        sa.Column("is_sellable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("code", name="uq_warehouses_code"),
    )
    op.create_index("ix_warehouses_code", "warehouses", ["code"])
    op.create_index("ix_warehouses_name", "warehouses", ["name"])
    op.create_index("ix_warehouses_type", "warehouses", ["warehouse_type"])
    op.create_index("ix_warehouses_status", "warehouses", ["status"])

    op.execute(
        """
        INSERT INTO warehouses (code, name, warehouse_type, purpose, is_sellable, status, note)
        VALUES
          ('FACTORY', '工厂仓库', 'factory', 'both', false, 'active', '默认工厂仓，可自行改名或新增更多工厂仓'),
          ('B2C', 'B2C仓库', 'b2c', 'goods', true, 'active', '默认B2C发货仓，可自行改名或新增更多B2C仓')
        ON CONFLICT (code) DO NOTHING
        """
    )

    op.add_column("consumable_receipts", sa.Column("warehouse_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_consumable_receipts_warehouse_id", "consumable_receipts", ["warehouse_id"])
    op.create_foreign_key(
        "fk_consumable_receipts_warehouse_id",
        "consumable_receipts",
        "warehouses",
        ["warehouse_id"],
        ["id"],
    )
    op.add_column("consumable_transactions", sa.Column("warehouse_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_consumable_transactions_warehouse_id", "consumable_transactions", ["warehouse_id"])
    op.create_foreign_key(
        "fk_consumable_transactions_warehouse_id",
        "consumable_transactions",
        "warehouses",
        ["warehouse_id"],
        ["id"],
    )

    # factory 的历史含义明确，可安全回填；旧 own 可能并非当前 B2C 仓，保留为空避免错误归仓。
    op.execute(
        """
        UPDATE consumable_receipts
        SET warehouse_id = (SELECT id FROM warehouses WHERE code = 'FACTORY')
        WHERE location = 'factory' AND warehouse_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE consumable_transactions
        SET warehouse_id = (SELECT id FROM warehouses WHERE code = 'FACTORY')
        WHERE location = 'factory' AND warehouse_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_consumable_transactions_warehouse_id", "consumable_transactions", type_="foreignkey")
    op.drop_index("ix_consumable_transactions_warehouse_id", table_name="consumable_transactions")
    op.drop_column("consumable_transactions", "warehouse_id")
    op.drop_constraint("fk_consumable_receipts_warehouse_id", "consumable_receipts", type_="foreignkey")
    op.drop_index("ix_consumable_receipts_warehouse_id", table_name="consumable_receipts")
    op.drop_column("consumable_receipts", "warehouse_id")
    op.drop_index("ix_warehouses_status", table_name="warehouses")
    op.drop_index("ix_warehouses_type", table_name="warehouses")
    op.drop_index("ix_warehouses_name", table_name="warehouses")
    op.drop_index("ix_warehouses_code", table_name="warehouses")
    op.drop_table("warehouses")
