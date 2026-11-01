"""Zugangstoken zusätzlich im Klartext halten

Damit bleibt der persönliche Link über die gesamte Laufzeit stabil: Das
Veröffentlichen des Helferplans kann die Bestätigungsmail mit dem bereits
bekannten Link versenden, statt alle gespeicherten Lesezeichen zu entwerten.

Revision ID: b7c2e9a41d38
Revises: a5ef5d7feb55
"""

from alembic import op
import sqlalchemy as sa

revision = "b7c2e9a41d38"
down_revision = "a5ef5d7feb55"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("participant", sa.Column("token", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("participant", "token")
