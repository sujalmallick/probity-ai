-- Runtime role for the API/worker: not a superuser, not the table owner, so row-level security applies.
CREATE ROLE probity_app LOGIN PASSWORD 'probity_app' NOSUPERUSER NOBYPASSRLS;
GRANT CONNECT ON DATABASE probity TO probity_app;
