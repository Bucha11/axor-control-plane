"""SAML extras: the workspace's own key pair, signed requests, and sign-ins
started on the provider's dashboard."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    have = {c["name"] for c in insp.get_columns("org_sso")}
    for name in ("saml_sign_requests", "saml_idp_initiated"):
        if name not in have:
            op.add_column("org_sso", sa.Column(name, sa.Boolean, nullable=False,
                                               server_default=sa.false()))
    if "saml_sp_keys" not in insp.get_table_names():
        op.create_table(
            "saml_sp_keys",
            sa.Column("org_id", sa.String(64), primary_key=True),
            sa.Column("key_pem", sa.Text, nullable=False),
            sa.Column("cert_pem", sa.Text, nullable=False),
            sa.Column("created_ts", sa.String(40), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("saml_sp_keys")
    for name in ("saml_idp_initiated", "saml_sign_requests"):
        op.drop_column("org_sso", name)
