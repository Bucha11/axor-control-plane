-- Axor Lab's own database on the shared Postgres (runs once, on first init,
-- when the data volume is empty). Separate from the backend's and identity's
-- so each app owns its schema and a restore can target one of them.
CREATE DATABASE axor_lab;
GRANT ALL PRIVILEGES ON DATABASE axor_lab TO axor;
