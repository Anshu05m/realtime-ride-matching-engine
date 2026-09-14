-- Runs once, automatically, on the Postgres container's first startup
-- (docker-entrypoint-initdb.d only executes against a fresh data volume).
-- Provisions a second database so integration tests don't run against
-- the same data as local dev.
CREATE DATABASE ride_matching_test;
