"""check after typing fix

Revision ID: 2c481617145c
Revises: e9dd2c0eaf05
Create Date: 2025-12-28 08:19:32.164673

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2c481617145c'
down_revision: Union[str, None] = 'e9dd2c0eaf05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
