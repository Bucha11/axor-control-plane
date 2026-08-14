-- Create the axor-identity service's own database (runs once, on first
-- postgres init, when the data volume is empty). Identity keeps its schema and
-- alembic history separate from the backend's — a shared database would collide
-- on the single alembic_version table.
CREATE DATABASE axor_identity;
GRANT ALL PRIVILEGES ON DATABASE axor_identity TO axor;
