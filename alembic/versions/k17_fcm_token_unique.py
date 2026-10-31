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
    # [EXAMEN-2] La tabla se crea aquí si el despliegue no ejecutó create_all
    # antes de las migraciones; así la cadena funciona en una base limpia.
    from sqlalchemy import inspect

    if "dispositivos_fcm" not in inspect(conn).get_table_names():
        op.create_table(
            "dispositivos_fcm",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("usuario_id", sa.Integer(), nullable=False),
            sa.Column("token", sa.String(length=500), nullable=False),
            sa.Column("tipo_dispositivo", sa.String(length=50), nullable=False),
            sa.Column("activo", sa.Boolean(), nullable=False),
            sa.Column("fecha_registro", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.Column("ultimo_uso", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_dispositivos_fcm_id"), "dispositivos_fcm", ["id"], unique=False)
        op.create_index(op.f("ix_dispositivos_fcm_usuario_id"), "dispositivos_fcm", ["usuario_id"], unique=False)
        op.create_index(op.f("ix_dispositivos_fcm_token"), "dispositivos_fcm", ["token"], unique=True)
        # Marca de procedencia: solo esta migración elimina la tabla al bajar,
        # para no borrar una tabla creada previamente por create_all.
        op.execute("COMMENT ON TABLE dispositivos_fcm IS 'created_by=k17_fcm_token_unique'")
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
    restricciones = {c["name"] for c in inspect(conn).get_unique_constraints("dispositivos_fcm")}
    indices = {i["name"] for i in inspect(conn).get_indexes("dispositivos_fcm") if i.get("unique")}
    if "uq_dispositivos_fcm_token" not in restricciones and "ix_dispositivos_fcm_token" not in indices:
        op.create_unique_constraint("uq_dispositivos_fcm_token", "dispositivos_fcm", ["token"])


def downgrade() -> None:
    # [EXAMEN-2] La restricción puede no existir si la unicidad ya la aportaba
    # el índice único del modelo (bases creadas por `create_all`).
    conn = op.get_bind()
    from sqlalchemy import inspect

    restricciones = {c["name"] for c in inspect(conn).get_unique_constraints("dispositivos_fcm")}
    if "uq_dispositivos_fcm_token" in restricciones:
        op.drop_constraint("uq_dispositivos_fcm_token", "dispositivos_fcm", type_="unique")
    # [EXAMEN-2] Si la tabla la creó esta migración (base limpia), se elimina aquí
    # para que la cadena baje hasta `base` sin dejar tablas huérfanas.
    comentario = conn.execute(
        sa.text(
            "SELECT obj_description('dispositivos_fcm'::regclass, 'pg_class')"
        )
    ).scalar()
    if comentario == "created_by=k17_fcm_token_unique":
        op.drop_table("dispositivos_fcm")
