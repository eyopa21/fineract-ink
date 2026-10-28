#!/bin/sh
# Creates one database per service inside the single Postgres container.
# Runs once, on first start of an empty data volume.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  CREATE USER fineract WITH PASSWORD '${FINERACT_DB_PASSWORD}';
  CREATE DATABASE fineract_tenants OWNER fineract;
  CREATE DATABASE fineract_default OWNER fineract;

  CREATE USER erp WITH PASSWORD '${ERP_DB_PASSWORD}';
  CREATE DATABASE erp OWNER erp;

  CREATE USER integration WITH PASSWORD '${INTEGRATION_DB_PASSWORD}';
  CREATE DATABASE integration OWNER integration;
EOSQL
