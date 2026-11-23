"""Single sign-on over SAML 2.0 as well: the protocol, and the SAML provider."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    have = {c["name"] for c in insp.get_columns("org_sso")}
    for name, col in (
        ("protocol", sa.Column("protocol", sa.String(8), nullable=False, server_default="oidc")),
        ("saml_sso_url", sa.Column("saml_sso_url", sa.String(1000), nullable=False,
                                   server_default="")),
        ("saml_entity_id", sa.Column("saml_entity_id", sa.String(500), nullable=False,
                                     server_default="")),
        ("saml_cert", sa.Column("saml_cert", sa.Text, nullable=False, server_default="")),
    ):
        if name not in have:
            op.add_column("org_sso", col)


def downgrade() -> None:
    for name in ("saml_cert", "saml_entity_id", "saml_sso_url", "protocol"):
        op.drop_column("org_sso", name)
