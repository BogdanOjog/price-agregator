"""a store outside Pisa may be visited (Lidl Ghezzano, by the user's decision)

Revision ID: a3c1e7d05b92
Revises: f85f9895fb8c
Create Date: 2026-10-24 16:10:00.000000

SQLite does not report CHECK constraints when Alembic reads the table, so the table is rebuilt
from its full definition (the initial one without ck_stores_outside_scope_not_visitable), with the
same ids. Foreign keys are off while the old table is dropped: every other table references it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3c1e7d05b92'
down_revision: Union[str, Sequence[str], None] = 'f85f9895fb8c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _stores(visitable_outside: bool) -> sa.Table:
    checks = [
        sa.CheckConstraint("(cap BETWEEN '56121' AND '56128') OR outside_scope_approved = 1", name='ck_stores_cap_in_pisa'),
        sa.CheckConstraint("chain IN ('coop', 'conad', 'lidl', 'eurospin')", name='ck_stores_chain'),
        sa.CheckConstraint("source_mode IN ('catalog', 'promo_only')", name='ck_stores_source_mode'),
    ]
    if not visitable_outside:
        checks.append(sa.CheckConstraint('outside_scope_approved = 0 OR visitable = 0', name='ck_stores_outside_scope_not_visitable'))
    return sa.Table(
        'stores', sa.MetaData(),
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('slug', sa.String(), nullable=False),
        sa.Column('chain', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('address', sa.String(), nullable=True),
        sa.Column('external_id', sa.String(), nullable=True),
        sa.Column('cap', sa.String(), nullable=False),
        sa.Column('outside_scope_approved', sa.Boolean(), nullable=False),
        sa.Column('source_mode', sa.String(), nullable=False),
        sa.Column('visitable', sa.Boolean(), nullable=False),
        sa.Column('price_source_store_id', sa.Integer(), nullable=True),
        *checks,
        sa.ForeignKeyConstraint(['price_source_store_id'], ['stores.id'], name='fk_stores_price_source_store_id_stores'),
        sa.PrimaryKeyConstraint('id', name='pk_stores'),
        sa.UniqueConstraint('slug', name='uq_stores_slug'),
    )


def _rebuild(table: sa.Table) -> None:
    op.execute("PRAGMA foreign_keys = OFF")   # effective only outside a transaction: first statement of the migration
    with op.batch_alter_table('stores', copy_from=table, recreate='always'):
        pass
    op.execute("PRAGMA foreign_keys = ON")


def upgrade() -> None:
    """Upgrade schema."""
    _rebuild(_stores(visitable_outside=True))


def downgrade() -> None:
    """Downgrade schema."""
    _rebuild(_stores(visitable_outside=False))
