-- Databricks notebook source
-- MAGIC %md
-- MAGIC # 06 External checks: match outside facility lists to ours
-- MAGIC Confirms (or questions) each facility's care level using outside lists. Safe to rerun.
-- MAGIC
-- MAGIC **Sources**
-- MAGIC - **OpenStreetMap** health facilities for India, the Humanitarian OpenStreetMap Team export of October 2026 (139,664 records, from [HDX](https://data.humdata.org/dataset/hotosm_ind_health_facilities)). Already in `/Volumes/workspace/vf_bronze/external/osm/`.
-- MAGIC - **Government lists** (hospital directory, health centres directory, CGHS list): upload the files to the matching folder in `/Volumes/workspace/vf_bronze/external/`, run notebook **05 - External Gov Upload**, then rerun this one. Until then they contribute no rows.
-- MAGIC
-- MAGIC Steps: bronze copy of OSM, a common `vf_silver.external_facilities` table (one schema for every source), candidate pairs within 2 km, name similarity, best match per facility, and `vf_gold.facility_external_check` saying whether the outside record agrees with our level.

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 1. Bronze: OpenStreetMap export as delivered

-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.vf_bronze.osm_health_facilities_raw
COMMENT 'HOT OpenStreetMap export of India health facilities (HDX hotosm_ind_health_facilities, Oct 2026); lat/lon = bbox centre'
AS SELECT *, current_timestamp() AS _ingested_at, _metadata.file_path AS _source_file
FROM read_files('/Volumes/workspace/vf_bronze/external/osm/', format => 'parquet');

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 2. Silver: one common table for every outside source
-- MAGIC Government sources are appended by notebook 05 into `external_facilities_gov` with the same columns; this step unions them in.

-- COMMAND ----------

CREATE TABLE IF NOT EXISTS workspace.vf_silver.external_facilities_gov (
  source STRING, ext_id STRING, name STRING, ext_kind STRING, ext_tier INT, ext_operator STRING,
  beds INT, specialities STRING, pincode STRING, state STRING, district STRING, latitude DOUBLE, longitude DOUBLE)
COMMENT 'Government facility lists, standardised by notebook 05 (empty until files are uploaded)';

-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.vf_silver.external_facilities
COMMENT 'Outside facility records from all sources in one schema, with a tier read from the name and tags'
AS
WITH osm AS (
  SELECT 'OpenStreetMap' AS source, id AS ext_id,
    coalesce(name_latin, name_en, name) AS name,
    lower(coalesce(name_latin, name_en, name, '')) AS lname,
    coalesce(healthcare, amenity) AS raw_type,
    lower(operator_type) AS ext_operator,
    try_cast(capacity_persons AS INT) AS beds,
    healthcare_speciality AS specialities,
    CAST(NULL AS STRING) AS pincode,
    adm1_name AS state, adm2_name AS district,
    lat AS latitude, lon AS longitude
  FROM workspace.vf_bronze.osm_health_facilities_raw
  WHERE coalesce(amenity, healthcare) IN ('hospital','clinic','doctors','doctor','health_post','centre','dentist')
    AND lat BETWEEN 6 AND 37.5 AND lon BETWEEN 68 AND 97.5
)
SELECT source, ext_id, name,
  CASE WHEN raw_type = 'hospital' THEN 'hospital' WHEN raw_type IN ('clinic','centre','health_post') THEN 'clinic or health centre'
       WHEN raw_type IN ('doctors','doctor') THEN 'doctor' WHEN raw_type = 'dentist' THEN 'dentist' END AS ext_kind,
  CASE
      WHEN lname rlike 'aiims|all india institute of medical|medical college|institute of medical sciences|\\bgmch\\b|pgimer' AND NOT lname rlike 'ayurved|homoeo|homeopath|unani|siddha|dental|nursing' THEN 5
      WHEN lname rlike 'district hospital|district headquarters? hospital|civil hospital|zilla hospital|sub ?-?district hospital|sub ?divisional hospital|taluka? (government |headquarters? )?hospital|area hospital|(government|govt|state) general hospital|\\bsdh\\b' THEN 4
      WHEN lname rlike 'community health cent|\\bchc\\b|rural hospital|first referral unit|\\bfru\\b' THEN 3
      WHEN lname rlike 'primary health cent|\\bphc\\b|urban health cent|\\buphc\\b|\\bpmc\\b' THEN 2
      WHEN lname rlike 'sub ?-?cent(re|er)|health (and|&) wellness|ayushman arogya|\\bhwc\\b|\\bshc\\b' THEN 1
    END AS ext_tier,
  CASE WHEN ext_operator IN ('government','public') THEN 'public' WHEN ext_operator IN ('private','business') THEN 'private'
       WHEN ext_operator IN ('charity','ngo','religious','community') THEN 'non-profit' END AS ext_operator,
  beds, specialities, pincode, state, district, latitude, longitude
FROM osm WHERE name IS NOT NULL
UNION ALL
SELECT * FROM workspace.vf_silver.external_facilities_gov;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 3. Gold: best outside match for each facility
-- MAGIC A pair is a candidate when the two points are within 2 km. Names are compared as word sets after dropping generic words (hospital, clinic, centre, medical, ...) and the facility's own city, district and state names, so two places sharing only a town name do not match. A match needs name similarity of at least 0.34 within 300 m, 0.5 within 1 km, or 0.67 within 2 km. The best-scoring match per facility and source is kept.

-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.vf_gold.facility_external_match
COMMENT 'Best outside record per facility and source, with distance and name similarity'
AS
WITH f AS (
  SELECT facility_id, name, latitude, longitude,
    split(trim(regexp_replace(lower(concat_ws(' ', city, district, state_display)), '[^a-z0-9 ]', ' ')), ' +') AS place_toks,
    filter(array_distinct(split(trim(regexp_replace(lower(name), '[^a-z0-9 ]', ' ')), ' +')), x -> length(x) > 1 AND NOT array_contains(array('hospital','hospitals','clinic','clinics','centre','center','the','and','of','dr','multispeciality','multispecialty','multi','speciality','specialty','super','care','health','healthcare','medical','nursing','home','pvt','ltd','private','limited','research','institute','trust','memorial','sri','shri','shree','government','govt','india','clinik','polyclinic','hosp','nagar','road','dental','dentist','dentistry','eye','child','children','mother','women','diagnostic','diagnostics','scan','lab','laboratory','city','new','life','family','speciality','orthopaedic','maternity','general','civil','district','community','primary','centre','hosptial'), x)) AS toks0
  FROM workspace.vf_silver.facilities WHERE latitude IS NOT NULL AND name IS NOT NULL
),
e AS (
  SELECT *, filter(array_distinct(split(trim(regexp_replace(lower(name), '[^a-z0-9 ]', ' ')), ' +')), x -> length(x) > 1 AND NOT array_contains(array('hospital','hospitals','clinic','clinics','centre','center','the','and','of','dr','multispeciality','multispecialty','multi','speciality','specialty','super','care','health','healthcare','medical','nursing','home','pvt','ltd','private','limited','research','institute','trust','memorial','sri','shri','shree','government','govt','india','clinik','polyclinic','hosp','nagar','road','dental','dentist','dentistry','eye','child','children','mother','women','diagnostic','diagnostics','scan','lab','laboratory','city','new','life','family','speciality','orthopaedic','maternity','general','civil','district','community','primary','centre','hosptial'), x)) AS toks
  FROM workspace.vf_silver.external_facilities
),
pairs0 AS (
  SELECT f.facility_id, array_except(f.toks0, f.place_toks) AS ftoks, array_except(e.toks, f.place_toks) AS etoks, e.source, e.ext_id, e.name AS ext_name, e.ext_kind, e.ext_tier, e.ext_operator, e.beds AS ext_beds, e.specialities AS ext_specialities,
    111.0 * sqrt(pow(e.latitude - f.latitude, 2) + pow((e.longitude - f.longitude) * cos(radians(f.latitude)), 2)) AS km,
    111.0 * sqrt(pow(e.latitude - f.latitude, 2) + pow((e.longitude - f.longitude) * cos(radians(f.latitude)), 2)) AS km0
  FROM f JOIN e
    ON e.latitude BETWEEN f.latitude - 0.02 AND f.latitude + 0.02
   AND e.longitude BETWEEN f.longitude - 0.02 AND f.longitude + 0.02
),
pairs AS (
  SELECT * EXCEPT (ftoks, etoks, km0),
    CASE WHEN size(array_union(ftoks, etoks)) = 0 THEN 0.0
         ELSE size(array_intersect(ftoks, etoks)) / size(array_union(ftoks, etoks)) END AS name_sim
  FROM pairs0
)
SELECT * EXCEPT (rk) FROM (
  SELECT *, row_number() OVER (PARTITION BY facility_id, source ORDER BY name_sim DESC, km) AS rk
  FROM pairs
  WHERE (km <= 0.3 AND name_sim >= 0.34) OR (km <= 1 AND name_sim >= 0.5) OR (km <= 2 AND name_sim >= 0.67)
) WHERE rk = 1;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 4. Gold: does the outside record agree with our level?
-- MAGIC - **confirms**: the outside name states the same public tier, or the outside type fits our rung (a hospital for rung 3 to 5, a clinic or doctor for rung 1 to 2).
-- MAGIC - **conflicts**: the outside name states a tier more than one rung away.
-- MAGIC - **type differs**: the outside record calls it a hospital and we placed it at rung 1 to 2, or a doctor or dentist and we placed it at rung 4 to 5. Worth a look; OSM often tags small clinics as hospitals.
-- MAGIC - **matched, no tier info**: found, but the outside record says nothing about level.
-- MAGIC - **no outside record**: nothing nearby with a similar name.
-- MAGIC Operator (public or private) is checked the same way.

-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.vf_gold.facility_external_check
COMMENT 'One row per facility: outside matches and whether they confirm our care level and operator'
AS
WITH best AS (
  SELECT facility_id,
    max_by(struct(source, ext_name, ext_kind, ext_tier, ext_operator, ext_beds, km, name_sim), name_sim - km / 10) AS m,
    collect_set(source) AS matched_sources
  FROM workspace.vf_gold.facility_external_match GROUP BY facility_id
)
SELECT l.facility_id, l.care_level, l.operator_type,
  b.matched_sources,
  b.m.source AS ext_source, b.m.ext_name, b.m.ext_kind, b.m.ext_tier, b.m.ext_operator, b.m.ext_beds,
  round(b.m.km, 2) AS ext_distance_km, round(b.m.name_sim, 2) AS ext_name_similarity,
  CASE
    WHEN b.facility_id IS NULL THEN 'no outside record'
    WHEN l.care_level IS NULL THEN 'matched, no rung to check'
    WHEN b.m.ext_tier IS NOT NULL AND abs(b.m.ext_tier - l.care_level) = 0 THEN 'confirms'
    WHEN b.m.ext_tier IS NOT NULL AND abs(b.m.ext_tier - l.care_level) = 1 THEN 'close (1 rung apart)'
    WHEN b.m.ext_tier IS NOT NULL THEN 'conflicts'
    WHEN b.m.ext_kind = 'hospital' AND l.care_level >= 3 THEN 'confirms'
    WHEN b.m.ext_kind IN ('clinic or health centre', 'doctor', 'dentist') AND l.care_level <= 2 THEN 'confirms'
    WHEN b.m.ext_kind IN ('doctor', 'dentist') AND l.care_level >= 4 THEN 'type differs'
    WHEN b.m.ext_kind = 'hospital' AND l.care_level <= 2 THEN 'type differs'
    ELSE 'matched, no tier info'
  END AS level_check,
  CASE
    WHEN b.m.ext_operator IS NULL OR l.operator_type IS NULL THEN NULL
    WHEN b.m.ext_operator = l.operator_type THEN 'agrees'
    ELSE 'differs'
  END AS operator_check,
  CASE WHEN b.facility_id IS NULL THEN NULL
       ELSE concat(b.m.source, ': "', b.m.ext_name, '" (', coalesce(b.m.ext_kind, 'type unknown'),
                   CASE WHEN b.m.ext_tier IS NOT NULL THEN concat(', name implies rung ', b.m.ext_tier) ELSE '' END,
                   CASE WHEN b.m.ext_operator IS NOT NULL THEN concat(', ', b.m.ext_operator) ELSE '' END,
                   ') ', round(b.m.km * 1000), ' m away') END AS external_evidence
FROM workspace.vf_gold.facility_level l LEFT JOIN best b USING (facility_id);

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 5. Summary

-- COMMAND ----------

SELECT level_check, count(*) AS facilities FROM workspace.vf_gold.facility_external_check GROUP BY 1 ORDER BY 2 DESC;
