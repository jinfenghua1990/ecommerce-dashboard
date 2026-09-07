"""align consumable code index with unique constraint

Revision ID: u3d5f7b9c1e2
Revises: t2c4e6a8b0d1
"""

from alembic import op


revision = "u3d5f7b9c1e2"
down_revision = "t2c4e6a8b0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_consumables_code", table_name="consumables")


def downgrade() -> None:
    op.create_index("ix_consumables_code", "consumables", ["code"])
