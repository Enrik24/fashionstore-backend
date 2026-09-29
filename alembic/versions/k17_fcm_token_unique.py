"""fcm token unico + dedup (push duplicado)

Revision ID: k17_fcm_token_unique
Revises: k16_antidoble_reserva
Create Date: 2026-09-29
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'k17_fcm_token_unique'
down_revision: Union[str, None] = 'k16_antidoble_reserva'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    # 1. Consolidar tokens duplicados: conservar el id menor por token.
    #    Si el mismo token estaba en 2 usuarios, queda con el más reciente
    #    reasignado al usuario del registro mayor (último login).
    conn.execute(sa.text("""
        UPDATE dispositivos_fcm d SET usuario_id = sub.usuario_id, activo = true
        FROM (SELECT token, max(id) AS max_id, max(usuario_id) AS usuario_id
              FROM dispositivos_fcm GROUP BY token HAVING count(*) > 1) sub
        WHERE d.token = sub.token AND d.id = sub.max_id
    """))
    conn.execute(sa.text("""
        DELETE FROM dispositivos_fcm a USING dispositivos_fcm b
        WHERE a.token = b.token AND a.id > b.id
    """))
    # 2. Unique real en BD (el modelo ya declara unique=True).
    op.create_unique_constraint("uq_dispositivos_fcm_token", "dispositivos_fcm", ["token"])


def downgrade() -> None:
    op.drop_constraint("uq_dispositivos_fcm_token", "dispositivos_fcm", type_="unique")
