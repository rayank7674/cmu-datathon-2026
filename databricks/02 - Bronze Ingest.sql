-- Databricks notebook source
-- MAGIC %md
-- MAGIC # 02 Bronze ingest
-- MAGIC Copies the three raw Virtue Foundation tables, unchanged, into `workspace.vf_bronze`.
-- MAGIC Adds three columns only: `_ingested_at`, `_source_table`, `_row_num`.
-- MAGIC Safe to rerun: every table is `CREATE OR REPLACE`.
-- MAGIC
-- MAGIC Run on a SQL warehouse or serverless compute. If the NFHS cell fails because the table is not visible, skip it; the rest still works.

-- COMMAND ----------

CREATE SCHEMA IF NOT EXISTS workspace.vf_bronze
COMMENT 'Raw, unchanged copies of the Virtue Foundation Marketplace tables';

-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.vf_bronze.facilities_raw
COMMENT 'Raw copy of virtue_foundation_dataset.facilities'
AS
SELECT
  *,
  current_timestamp() AS _ingested_at,
  'databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities' AS _source_table,
  row_number() OVER (ORDER BY unique_id, source_content_id, content_table_id, name) AS _row_num
FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities;

-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.vf_bronze.pincode_raw
COMMENT 'Raw copy of virtue_foundation_dataset.india_post_pincode_directory'
AS
SELECT
  *,
  current_timestamp() AS _ingested_at,
  'databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.india_post_pincode_directory' AS _source_table,
  monotonically_increasing_id() AS _row_num
FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.india_post_pincode_directory;

-- COMMAND ----------

-- Skip this cell if the NFHS table is not visible in your workspace.
CREATE OR REPLACE TABLE workspace.vf_bronze.nfhs5_district_raw
COMMENT 'Raw copy of virtue_foundation_dataset.nfhs_5_district_health_indicators'
AS
SELECT
  *,
  current_timestamp() AS _ingested_at,
  'databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.nfhs_5_district_health_indicators' AS _source_table,
  monotonically_increasing_id() AS _row_num
FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.nfhs_5_district_health_indicators;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## Check: bronze row counts match the source
-- MAGIC Expect facilities = 10,088 and pincode = 165,627. `matches` should be true on every row.

-- COMMAND ----------

SELECT 'facilities' AS tbl,
       (SELECT count(*) FROM workspace.vf_bronze.facilities_raw) AS bronze_rows,
       (SELECT count(*) FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities) AS source_rows
UNION ALL
SELECT 'pincode',
       (SELECT count(*) FROM workspace.vf_bronze.pincode_raw),
       (SELECT count(*) FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.india_post_pincode_directory);

-- COMMAND ----------

-- Optional: let teammates in this workspace read the bronze tables.
-- GRANT USE SCHEMA, SELECT ON SCHEMA workspace.vf_bronze TO `account users`;