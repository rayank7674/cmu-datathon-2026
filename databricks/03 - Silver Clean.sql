-- Databricks notebook source
-- MAGIC %md
-- MAGIC # 03 Silver: clean, typed, flagged
-- MAGIC Reads only `workspace.vf_bronze`, writes `workspace.vf_silver`. Safe to rerun (`CREATE OR REPLACE`).
-- MAGIC
-- MAGIC Rules: **flag, don't delete**, and keep raw values next to cleaned ones so every claim stays traceable.
-- MAGIC
-- MAGIC Tables built here:
-- MAGIC - `facilities` one row per facility, typed and canonicalised, with quality flags and `quality_score`
-- MAGIC - `facilities_rejected` the 88 broken-import fragment rows (pieces of a multi-line description, not facilities)
-- MAGIC - `pincode` one row per 6-digit pincode
-- MAGIC - `facility_geo` facility to pincode, district and state, with a coordinates fallback
-- MAGIC - `facility_claims` one row per specialty / procedure / equipment / capability item, with the raw text as evidence
-- MAGIC - `nfhs_district` NFHS-5 indicators as numbers, with low-sample flags
-- MAGIC - `silver_checks` pass/fail results for this layer

-- COMMAND ----------

CREATE SCHEMA IF NOT EXISTS workspace.vf_silver COMMENT 'Cleaned, typed and flagged Virtue Foundation data';

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## Helper functions
-- MAGIC Saved in the schema so teammates can reuse them in their own queries.

-- COMMAND ----------

CREATE OR REPLACE FUNCTION workspace.vf_silver.clean_str(s STRING) RETURNS STRING
COMMENT 'Trim, collapse whitespace, and turn text placeholders like "null" or "" into a real NULL'
RETURN CASE WHEN lower(trim(regexp_replace(s, '\\s+', ' '))) IN ('', 'null', 'none', 'nan', 'n/a', 'na', '[]', '{}', '""', '"null"') THEN NULL
            ELSE trim(regexp_replace(s, '\\s+', ' ')) END;

-- COMMAND ----------

CREATE OR REPLACE FUNCTION workspace.vf_silver.parse_list(s STRING) RETURNS ARRAY<STRING>
COMMENT 'Parse a JSON array string, clean each item, drop blank items'
RETURN filter(transform(from_json(workspace.vf_silver.clean_str(s), 'array<string>'),
  x -> CASE WHEN lower(trim(regexp_replace(x, '\\s+', ' '))) IN ('', 'null', 'none', 'nan', 'n/a', 'na') THEN NULL ELSE trim(regexp_replace(x, '\\s+', ' ')) END),
  y -> y IS NOT NULL);

-- COMMAND ----------

CREATE OR REPLACE FUNCTION workspace.vf_silver.state_key(s STRING) RETURNS STRING
COMMENT 'Uppercase letters-only key of the last comma-separated part of a state string'
RETURN trim(regexp_replace(regexp_replace(upper(regexp_replace(element_at(split(s, ','), -1), '&', ' AND ')), '[^A-Z ]', ''), ' +', ' '));

-- COMMAND ----------

CREATE OR REPLACE FUNCTION workspace.vf_silver.canon_state(s STRING) RETURNS STRING
COMMENT 'Map a free-text state name to the official India Post state name, or NULL if it is not recognisable'
RETURN CASE WHEN (CASE workspace.vf_silver.state_key(s)
    WHEN 'TAMILNADU' THEN 'TAMIL NADU'
    WHEN 'MAHARASTRA' THEN 'MAHARASHTRA'
    WHEN 'ORISSA' THEN 'ODISHA'
    WHEN 'UP' THEN 'UTTAR PRADESH'
    WHEN 'UTTARPRADESH' THEN 'UTTAR PRADESH'
    WHEN 'UTTAR PRADES H' THEN 'UTTAR PRADESH'
    WHEN 'MP' THEN 'MADHYA PRADESH'
    WHEN 'MADHYAPRADESH' THEN 'MADHYA PRADESH'
    WHEN 'TS' THEN 'TELANGANA'
    WHEN 'TELENGANA' THEN 'TELANGANA'
    WHEN 'GJ' THEN 'GUJARAT'
    WHEN 'MH' THEN 'MAHARASHTRA'
    WHEN 'DL' THEN 'DELHI'
    WHEN 'NCT' THEN 'DELHI'
    WHEN 'NCT OF DELHI' THEN 'DELHI'
    WHEN 'NEW DELHI' THEN 'DELHI'
    WHEN 'DELHI NCR' THEN 'DELHI'
    WHEN 'EAST DELHI' THEN 'DELHI'
    WHEN 'WEST DELHI' THEN 'DELHI'
    WHEN 'NORTH WEST DELHI' THEN 'DELHI'
    WHEN 'SOUTH EAST DELHI AREA' THEN 'DELHI'
    WHEN 'CG' THEN 'CHHATTISGARH'
    WHEN 'CHATTISGARH' THEN 'CHHATTISGARH'
    WHEN 'UK' THEN 'UTTARAKHAND'
    WHEN 'UTTARANCHAL' THEN 'UTTARAKHAND'
    WHEN 'PONDICHERRY' THEN 'PUDUCHERRY'
    WHEN 'UT OF PUDUCHERRY' THEN 'PUDUCHERRY'
    WHEN 'KASHMIR' THEN 'JAMMU AND KASHMIR'
    WHEN 'J AND K' THEN 'JAMMU AND KASHMIR'
    WHEN 'SRINAGAR KASHMIR' THEN 'JAMMU AND KASHMIR'
    WHEN 'PUNJAB REGION' THEN 'PUNJAB'
    WHEN 'CHANDIGARH UT' THEN 'CHANDIGARH'
    WHEN 'DADRA AND NAGAR HAVELI AND DAMAN AND DIU' THEN 'THE DADRA AND NAGAR HAVELI AND DAMAN AND DIU'
    WHEN 'DAMAN AND DIU' THEN 'THE DADRA AND NAGAR HAVELI AND DAMAN AND DIU'
    WHEN 'DADRA AND NAGAR HAVELI' THEN 'THE DADRA AND NAGAR HAVELI AND DAMAN AND DIU'
    WHEN 'ANDAMAN AND NICOBAR' THEN 'ANDAMAN AND NICOBAR ISLANDS'
    WHEN 'ANDAMAN NICOBAR ISLANDS' THEN 'ANDAMAN AND NICOBAR ISLANDS'
    WHEN 'NCT OF DELHI ' THEN 'DELHI'
    ELSE workspace.vf_silver.state_key(s) END) IN ('ANDAMAN AND NICOBAR ISLANDS','ANDHRA PRADESH','ARUNACHAL PRADESH','ASSAM','BIHAR','CHANDIGARH','CHHATTISGARH','DELHI','GOA','GUJARAT','HARYANA','HIMACHAL PRADESH','JAMMU AND KASHMIR','JHARKHAND','KARNATAKA','KERALA','LADAKH','LAKSHADWEEP','MADHYA PRADESH','MAHARASHTRA','MANIPUR','MEGHALAYA','MIZORAM','NAGALAND','ODISHA','PUDUCHERRY','PUNJAB','RAJASTHAN','SIKKIM','TAMIL NADU','TELANGANA','THE DADRA AND NAGAR HAVELI AND DAMAN AND DIU','TRIPURA','UTTAR PRADESH','UTTARAKHAND','WEST BENGAL')
  THEN (CASE workspace.vf_silver.state_key(s)
    WHEN 'TAMILNADU' THEN 'TAMIL NADU'
    WHEN 'MAHARASTRA' THEN 'MAHARASHTRA'
    WHEN 'ORISSA' THEN 'ODISHA'
    WHEN 'UP' THEN 'UTTAR PRADESH'
    WHEN 'UTTARPRADESH' THEN 'UTTAR PRADESH'
    WHEN 'UTTAR PRADES H' THEN 'UTTAR PRADESH'
    WHEN 'MP' THEN 'MADHYA PRADESH'
    WHEN 'MADHYAPRADESH' THEN 'MADHYA PRADESH'
    WHEN 'TS' THEN 'TELANGANA'
    WHEN 'TELENGANA' THEN 'TELANGANA'
    WHEN 'GJ' THEN 'GUJARAT'
    WHEN 'MH' THEN 'MAHARASHTRA'
    WHEN 'DL' THEN 'DELHI'
    WHEN 'NCT' THEN 'DELHI'
    WHEN 'NCT OF DELHI' THEN 'DELHI'
    WHEN 'NEW DELHI' THEN 'DELHI'
    WHEN 'DELHI NCR' THEN 'DELHI'
    WHEN 'EAST DELHI' THEN 'DELHI'
    WHEN 'WEST DELHI' THEN 'DELHI'
    WHEN 'NORTH WEST DELHI' THEN 'DELHI'
    WHEN 'SOUTH EAST DELHI AREA' THEN 'DELHI'
    WHEN 'CG' THEN 'CHHATTISGARH'
    WHEN 'CHATTISGARH' THEN 'CHHATTISGARH'
    WHEN 'UK' THEN 'UTTARAKHAND'
    WHEN 'UTTARANCHAL' THEN 'UTTARAKHAND'
    WHEN 'PONDICHERRY' THEN 'PUDUCHERRY'
    WHEN 'UT OF PUDUCHERRY' THEN 'PUDUCHERRY'
    WHEN 'KASHMIR' THEN 'JAMMU AND KASHMIR'
    WHEN 'J AND K' THEN 'JAMMU AND KASHMIR'
    WHEN 'SRINAGAR KASHMIR' THEN 'JAMMU AND KASHMIR'
    WHEN 'PUNJAB REGION' THEN 'PUNJAB'
    WHEN 'CHANDIGARH UT' THEN 'CHANDIGARH'
    WHEN 'DADRA AND NAGAR HAVELI AND DAMAN AND DIU' THEN 'THE DADRA AND NAGAR HAVELI AND DAMAN AND DIU'
    WHEN 'DAMAN AND DIU' THEN 'THE DADRA AND NAGAR HAVELI AND DAMAN AND DIU'
    WHEN 'DADRA AND NAGAR HAVELI' THEN 'THE DADRA AND NAGAR HAVELI AND DAMAN AND DIU'
    WHEN 'ANDAMAN AND NICOBAR' THEN 'ANDAMAN AND NICOBAR ISLANDS'
    WHEN 'ANDAMAN NICOBAR ISLANDS' THEN 'ANDAMAN AND NICOBAR ISLANDS'
    WHEN 'NCT OF DELHI ' THEN 'DELHI'
    ELSE workspace.vf_silver.state_key(s) END)
  ELSE NULL END;

-- COMMAND ----------

CREATE OR REPLACE FUNCTION workspace.vf_silver.norm_claim(s STRING) RETURNS STRING
COMMENT 'Normalise a claim for matching: split camelCase, lowercase, drop filler verbs and punctuation'
RETURN nullif(trim(regexp_replace(
  regexp_replace(
    regexp_replace(lower(regexp_replace(s, '([a-z])([A-Z])', '$1 $2')), '[^a-z0-9+/ ]', ' '),
    '^ *(provides|provide|offers|offer|performs|perform|has|have|manages|manage|available|availability of|facility for|facilities for|services for) +', ''),
  ' +', ' ')), '');

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 1. Split bronze rows: real facilities vs broken fragments
-- MAGIC Bronze has 10,088 rows: 9,970 complete records, 30 records cut short by a line break inside their description (`source` is empty, no coordinates), and 88 fragment rows that are just the leftover pieces of those descriptions. Fragments are not facilities, so they go to `facilities_rejected` with a reason. The 30 truncated records stay in, flagged.

-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.vf_silver.facilities_rejected
COMMENT 'Bronze rows that are not facilities: fragments of multi-line descriptions from a broken CSV import'
AS SELECT 'fragment of a multi-line description (no valid unique_id)' AS reject_reason, *
FROM workspace.vf_bronze.facilities_raw
WHERE NOT coalesce(unique_id rlike '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', false);

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 2. `facilities`: typed, canonical, flagged
-- MAGIC - Text placeholders become real NULLs; lists become arrays; numbers become INT/DOUBLE.
-- MAGIC - Impossible numbers are nulled, with the original kept in `*_raw`: year outside 1800 to 2026, doctors above 5,000, beds above 10,000, coordinates outside India.
-- MAGIC - 11 duplicate `unique_id`s: keep the most complete row; the others' row numbers go in `merged_row_nums`.
-- MAGIC - State and district are filled in from `facility_geo` in step 5.

-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.vf_silver.facilities
COMMENT 'One row per facility: cleaned, typed, canonicalised, with quality flags'
AS
WITH base AS (
  SELECT r.*,
    -- completeness score used to pick the survivor among duplicate ids
    (CASE WHEN workspace.vf_silver.clean_str(r.description) IS NOT NULL THEN 1 ELSE 0 END + CASE WHEN r.latitude IS NOT NULL THEN 1 ELSE 0 END
     + CASE WHEN workspace.vf_silver.clean_str(r.numberDoctors) IS NOT NULL THEN 1 ELSE 0 END + CASE WHEN workspace.vf_silver.clean_str(r.capacity) IS NOT NULL THEN 1 ELSE 0 END
     + coalesce(size(workspace.vf_silver.parse_list(r.procedure)), 0) + coalesce(size(workspace.vf_silver.parse_list(r.equipment)), 0)) AS _completeness
  FROM workspace.vf_bronze.facilities_raw r
  WHERE coalesce(r.unique_id rlike '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', false)
),
ranked AS (
  SELECT *,
    row_number() OVER (PARTITION BY unique_id ORDER BY _completeness DESC, _row_num) AS _rk,
    count(*) OVER (PARTITION BY unique_id) AS _dup_n,
    collect_list(_row_num) OVER (PARTITION BY unique_id) AS _all_rows
  FROM base
),
typed AS (
  SELECT
    unique_id AS facility_id,
    _row_num AS bronze_row_num,
    CASE WHEN _dup_n > 1 THEN filter(_all_rows, x -> x <> _row_num) END AS merged_row_nums,
    -- identity
    workspace.vf_silver.clean_str(name) AS name,
    CASE lower(workspace.vf_silver.clean_str(facilityTypeId)) WHEN 'farmacy' THEN 'pharmacy' ELSE lower(workspace.vf_silver.clean_str(facilityTypeId)) END AS facility_type,
    CASE lower(workspace.vf_silver.clean_str(operatorTypeId)) WHEN 'government' THEN 'public' ELSE lower(workspace.vf_silver.clean_str(operatorTypeId)) END AS operator_type,
    lower(workspace.vf_silver.clean_str(organization_type)) AS organization_type,
    array_distinct(transform(workspace.vf_silver.parse_list(affiliationTypeIds), x -> lower(x))) AS affiliation_types,
    CASE WHEN trim(description) = '' OR lower(trim(description)) = 'null' THEN NULL ELSE trim(description) END AS description,
    -- location
    workspace.vf_silver.clean_str(address_line1) AS address_line1,
    workspace.vf_silver.clean_str(address_line2) AS address_line2,
    workspace.vf_silver.clean_str(address_line3) AS address_line3,
    initcap(workspace.vf_silver.clean_str(address_city)) AS city,
    workspace.vf_silver.clean_str(address_stateOrRegion) AS state_stated_raw,
    workspace.vf_silver.canon_state(address_stateOrRegion) AS state_stated,
    workspace.vf_silver.clean_str(address_zipOrPostcode) AS zip_raw,
    CASE WHEN regexp_replace(address_zipOrPostcode, '[^0-9]', '') rlike '^[1-9][0-9]{5}$' THEN regexp_replace(address_zipOrPostcode, '[^0-9]', '') END AS zip_clean,
    workspace.vf_silver.clean_str(address_countryCode) AS country_code,
    latitude AS latitude_raw, longitude AS longitude_raw,
    CASE WHEN latitude BETWEEN 6 AND 37.5 AND longitude BETWEEN 68 AND 97.5 THEN latitude END AS latitude,
    CASE WHEN latitude BETWEEN 6 AND 37.5 AND longitude BETWEEN 68 AND 97.5 THEN longitude END AS longitude,
    -- scale
    workspace.vf_silver.clean_str(yearEstablished) AS year_established_raw,
    CASE WHEN try_cast(workspace.vf_silver.clean_str(yearEstablished) AS DOUBLE) BETWEEN 1800 AND 2026 THEN CAST(try_cast(workspace.vf_silver.clean_str(yearEstablished) AS DOUBLE) AS INT) END AS year_established,
    workspace.vf_silver.clean_str(numberDoctors) AS number_doctors_raw,
    CASE WHEN try_cast(workspace.vf_silver.clean_str(numberDoctors) AS DOUBLE) BETWEEN 1 AND 5000 THEN CAST(try_cast(workspace.vf_silver.clean_str(numberDoctors) AS DOUBLE) AS INT) END AS number_doctors,
    workspace.vf_silver.clean_str(capacity) AS capacity_raw,
    CASE WHEN try_cast(workspace.vf_silver.clean_str(capacity) AS DOUBLE) BETWEEN 1 AND 10000 THEN CAST(try_cast(workspace.vf_silver.clean_str(capacity) AS DOUBLE) AS INT) END AS capacity_beds,
    CASE WHEN try_cast(workspace.vf_silver.clean_str(area) AS DOUBLE) > 0 THEN try_cast(workspace.vf_silver.clean_str(area) AS DOUBLE) END AS area,
    -- service claims (kept as arrays here; exploded with evidence in facility_claims)
    array_distinct(workspace.vf_silver.parse_list(specialties)) AS specialties,
    array_distinct(workspace.vf_silver.parse_list(procedure)) AS procedures,
    array_distinct(workspace.vf_silver.parse_list(equipment)) AS equipment,
    array_distinct(workspace.vf_silver.parse_list(capability)) AS capabilities,
    -- contact
    array_distinct(workspace.vf_silver.parse_list(phone_numbers)) AS phone_numbers,
    workspace.vf_silver.clean_str(officialPhone) AS official_phone,
    CASE WHEN lower(workspace.vf_silver.clean_str(email)) rlike '^[a-z0-9._%+-]+@[a-z0-9.-]+\\.[a-z]{2,}$' THEN lower(workspace.vf_silver.clean_str(email)) END AS email,
    array_distinct(workspace.vf_silver.parse_list(websites)) AS websites,
    lower(regexp_replace(regexp_replace(workspace.vf_silver.clean_str(officialWebsite), '^https?://', ''), '^www\\.|/.*$', '')) AS official_website,
    workspace.vf_silver.clean_str(facebookLink) AS facebook_link,
    try_cast(workspace.vf_silver.clean_str(acceptsVolunteers) AS BOOLEAN) AS accepts_volunteers,
    -- web and social signals
    try_cast(workspace.vf_silver.clean_str(recency_of_page_update) AS DATE) AS page_last_updated,
    try_cast(workspace.vf_silver.clean_str(post_metrics_most_recent_social_media_post_date) AS DATE) AS last_social_post_date,
    CAST(try_cast(workspace.vf_silver.clean_str(post_metrics_post_count) AS DOUBLE) AS BIGINT) AS social_post_count,
    CAST(try_cast(workspace.vf_silver.clean_str(engagement_metrics_n_followers) AS DOUBLE) AS BIGINT) AS social_followers,
    CAST(try_cast(workspace.vf_silver.clean_str(engagement_metrics_n_likes) AS DOUBLE) AS BIGINT) AS social_likes,
    CAST(try_cast(workspace.vf_silver.clean_str(engagement_metrics_n_engagements) AS DOUBLE) AS BIGINT) AS social_engagements,
    try_cast(workspace.vf_silver.clean_str(distinct_social_media_presence_count) AS DOUBLE) AS social_presence_count,
    try_cast(workspace.vf_silver.clean_str(affiliated_staff_presence) AS BOOLEAN) AS affiliated_staff_presence,
    try_cast(workspace.vf_silver.clean_str(custom_logo_presence) AS BOOLEAN) AS custom_logo_presence,
    CAST(try_cast(workspace.vf_silver.clean_str(number_of_facts_about_the_organization) AS DOUBLE) AS INT) AS number_of_facts,
    -- provenance
    coalesce(size(workspace.vf_silver.parse_list(source_types)), 0) AS source_count,
    size(array_distinct(workspace.vf_silver.parse_list(source_ids))) AS distinct_source_ids,
    array_distinct(workspace.vf_silver.parse_list(source_types)) AS source_types,
    array_distinct(workspace.vf_silver.parse_list(source_urls)) AS source_urls,
    workspace.vf_silver.clean_str(source_content_id) AS source_content_id,
    workspace.vf_silver.clean_str(cluster_id) AS cluster_id,
    -- raw flags
    source IS NULL AS is_truncated_record,
    _dup_n > 1 AS is_duplicate_id
  FROM ranked WHERE _rk = 1
)
SELECT *,
  source_count >= 20 AS high_source_merge,
  source_count >= 50 AS at_source_cap,
  (number_doctors = 1 AND size(specialties) >= 30) AS specialty_doctor_mismatch,
  (latitude_raw IS NOT NULL AND latitude IS NULL) AS coords_invalid,
  ((year_established_raw IS NOT NULL AND year_established IS NULL AND try_cast(year_established_raw AS DOUBLE) IS NOT NULL)
   OR (number_doctors_raw IS NOT NULL AND number_doctors IS NULL AND try_cast(number_doctors_raw AS DOUBLE) > 0)
   OR (capacity_raw IS NOT NULL AND capacity_beds IS NULL AND try_cast(capacity_raw AS DOUBLE) > 0)) AS implausible_numbers
FROM typed;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 3. `pincode`: one row per 6-digit pincode
-- MAGIC The directory lists every post office, so a pincode appears several times. Take the most common district and state, and average the valid coordinates as a centroid.

-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.vf_silver.pincode
COMMENT 'One row per pincode: modal district and state, centroid of post-office coordinates'
AS
WITH po AS (
  SELECT lpad(CAST(pincode AS STRING), 6, '0') AS pincode,
         initcap(trim(district)) AS district, upper(trim(statename)) AS state,
         CASE WHEN try_cast(latitude AS DOUBLE) BETWEEN 6 AND 37.5 AND try_cast(longitude AS DOUBLE) BETWEEN 68 AND 97.5 THEN try_cast(latitude AS DOUBLE) END AS lat,
         CASE WHEN try_cast(latitude AS DOUBLE) BETWEEN 6 AND 37.5 AND try_cast(longitude AS DOUBLE) BETWEEN 68 AND 97.5 THEN try_cast(longitude AS DOUBLE) END AS lon
  FROM workspace.vf_bronze.pincode_raw
  WHERE pincode IS NOT NULL AND upper(trim(statename)) <> 'NA'
)
SELECT pincode,
  mode(district) AS district,
  lower(trim(mode(district))) AS district_key,
  mode(state) AS state,
  count(DISTINCT district) AS n_districts,
  count(*) AS n_post_offices,
  avg(lat) AS centroid_lat, avg(lon) AS centroid_lon
FROM po GROUP BY pincode;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 4. `facility_geo`: where each facility is
-- MAGIC 1. Zip match: cleaned 6-digit zip to `pincode`.
-- MAGIC 2. Fallback: nearest pincode centroid to the facility's coordinates (within about 50 km).
-- MAGIC 3. Cross-checks: the stated state vs the pincode's state, and the coordinates' nearest-pincode state vs the final state.

-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.vf_silver.facility_geo
COMMENT 'Facility to pincode, district and state, with method and consistency checks'
AS
WITH near AS (
  SELECT f.facility_id, p.pincode, p.district, p.district_key, p.state,
         111.0 * sqrt(pow(p.centroid_lat - f.latitude, 2) + pow((p.centroid_lon - f.longitude) * cos(radians(f.latitude)), 2)) AS km,
         row_number() OVER (PARTITION BY f.facility_id ORDER BY pow(p.centroid_lat - f.latitude, 2) + pow((p.centroid_lon - f.longitude) * cos(radians(f.latitude)), 2)) AS rk
  FROM workspace.vf_silver.facilities f
  JOIN workspace.vf_silver.pincode p
    ON p.centroid_lat BETWEEN f.latitude - 0.5 AND f.latitude + 0.5
   AND p.centroid_lon BETWEEN f.longitude - 0.5 AND f.longitude + 0.5
  WHERE f.latitude IS NOT NULL
)
SELECT f.facility_id,
  f.zip_raw, f.zip_clean,
  coalesce(z.pincode, CASE WHEN n.km <= 50 THEN n.pincode END) AS pincode,
  coalesce(z.district, CASE WHEN n.km <= 50 THEN n.district END) AS district,
  coalesce(z.district_key, CASE WHEN n.km <= 50 THEN n.district_key END) AS district_key,
  coalesce(z.state, CASE WHEN n.km <= 50 THEN n.state END, f.state_stated) AS state,
  CASE WHEN z.pincode IS NOT NULL THEN 'zip'
       WHEN n.km <= 50 THEN 'coords'
       WHEN f.state_stated IS NOT NULL THEN 'stated_state_only'
       ELSE 'none' END AS geo_method,
  f.state_stated,
  n.pincode AS nearest_pincode, n.state AS nearest_pincode_state, round(n.km, 1) AS nearest_pincode_km,
  (z.state IS NOT NULL AND f.state_stated IS NOT NULL AND z.state <> f.state_stated) AS state_mismatch,
  (n.state IS NOT NULL AND n.km <= 50 AND coalesce(z.state, f.state_stated) IS NOT NULL AND n.state <> coalesce(z.state, f.state_stated)) AS coords_state_mismatch,
  (f.latitude IS NOT NULL AND (n.km IS NULL OR n.km > 50)) AS coords_far_from_any_pincode
FROM workspace.vf_silver.facilities f
LEFT JOIN workspace.vf_silver.pincode z ON z.pincode = f.zip_clean
LEFT JOIN near n ON n.facility_id = f.facility_id AND n.rk = 1;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 5. Add location and the quality score to `facilities`
-- MAGIC `quality_score` starts at 1 and loses points for each flag. It is a rough trust signal for ranking and for the uncertainty text, not a verdict.
-- MAGIC
-- MAGIC | Flag | Penalty |
-- MAGIC | --- | --- |
-- MAGIC | is_truncated_record | 0.40 |
-- MAGIC | specialty_doctor_mismatch | 0.20 |
-- MAGIC | state_mismatch, coords_state_mismatch | 0.15 each |
-- MAGIC | high_source_merge | 0.10 (plus 0.10 more at the 50-source cap) |
-- MAGIC | coords_invalid, coords_far_from_any_pincode, implausible_numbers, no district | 0.10 each |
-- MAGIC | is_duplicate_id | 0.05 |

-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.vf_silver.facilities
COMMENT 'One row per facility: cleaned, typed, canonicalised, located, with quality flags and quality_score'
AS
WITH j AS (
  SELECT f.*,
    g.pincode, g.district, g.district_key, g.state, initcap(g.state) AS state_display, g.geo_method,
    g.state_mismatch, g.coords_state_mismatch, g.coords_far_from_any_pincode,
    g.district IS NULL AS no_district
  FROM workspace.vf_silver.facilities f JOIN workspace.vf_silver.facility_geo g USING (facility_id)
)
SELECT *,
  greatest(0.0, round(1.0
    - 0.40 * CAST(is_truncated_record AS INT)
    - 0.20 * CAST(coalesce(specialty_doctor_mismatch, false) AS INT)
    - 0.15 * CAST(state_mismatch AS INT)
    - 0.15 * CAST(coords_state_mismatch AS INT)
    - 0.10 * CAST(high_source_merge AS INT)
    - 0.10 * CAST(at_source_cap AS INT)
    - 0.10 * CAST(coords_invalid AS INT)
    - 0.10 * CAST(coords_far_from_any_pincode AS INT)
    - 0.10 * CAST(implausible_numbers AS INT)
    - 0.10 * CAST(no_district AS INT)
    - 0.05 * CAST(is_duplicate_id AS INT), 2)) AS quality_score,
  concat_ws('; ',
    CASE WHEN is_truncated_record THEN 'record cut short by a broken import' END,
    CASE WHEN specialty_doctor_mismatch THEN 'lists 30+ specialties with 1 doctor' END,
    CASE WHEN state_mismatch THEN 'postcode is in a different state than stated' END,
    CASE WHEN coords_state_mismatch THEN 'map location is in a different state' END,
    CASE WHEN at_source_cap THEN 'merged from 50+ web sources' WHEN high_source_merge THEN 'merged from 20+ web sources' END,
    CASE WHEN coords_invalid THEN 'coordinates outside India' END,
    CASE WHEN coords_far_from_any_pincode THEN 'coordinates far from any postcode' END,
    CASE WHEN implausible_numbers THEN 'implausible year, doctor or bed count removed' END,
    CASE WHEN no_district THEN 'district unknown' END,
    CASE WHEN is_duplicate_id THEN 'duplicate record merged' END) AS quality_notes
FROM j;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 6. `facility_claims`: one row per claim, with its evidence text
-- MAGIC Explodes specialties, procedures, equipment and capabilities. `raw_text` is the exact wording to quote; `norm_text` is for matching. `mentions` counts how many times the same claim appeared before de-duplication (merged records repeat claims).

-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.vf_silver.facility_claims
COMMENT 'One row per facility claim (specialty, procedure, equipment, capability) with raw evidence text'
AS
WITH parsed AS (
  SELECT unique_id AS facility_id,
    workspace.vf_silver.parse_list(specialties) AS sp, workspace.vf_silver.parse_list(procedure) AS pr,
    workspace.vf_silver.parse_list(equipment) AS eq, workspace.vf_silver.parse_list(capability) AS ca
  FROM workspace.vf_bronze.facilities_raw
),
raw AS (
  SELECT facility_id, 'specialty' AS source_field, e.pos, e.col AS raw_text FROM parsed LATERAL VIEW posexplode(sp) e AS pos, col
  UNION ALL SELECT facility_id, 'procedure', e.pos, e.col FROM parsed LATERAL VIEW posexplode(pr) e AS pos, col
  UNION ALL SELECT facility_id, 'equipment', e.pos, e.col FROM parsed LATERAL VIEW posexplode(eq) e AS pos, col
  UNION ALL SELECT facility_id, 'capability', e.pos, e.col FROM parsed LATERAL VIEW posexplode(ca) e AS pos, col
),
normed AS (
  SELECT *, workspace.vf_silver.norm_claim(raw_text) AS norm_text FROM raw
  WHERE facility_id IN (SELECT facility_id FROM workspace.vf_silver.facilities)
)
SELECT facility_id, source_field, norm_text,
  min_by(raw_text, pos) AS raw_text,
  min(pos) AS first_position,
  count(*) AS mentions
FROM normed
WHERE norm_text IS NOT NULL
GROUP BY facility_id, source_field, norm_text;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 7. `nfhs_district`: NFHS-5 indicators as numbers
-- MAGIC 49 indicator columns arrive as text. In NFHS, a value in parentheses is based on 25 to 49 cases (use with caution) and `*` means fewer than 25 cases (suppressed). So: parentheses stripped and `<col>__low_sample = true`; `*` becomes NULL; all cast to DOUBLE.

-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.vf_silver.nfhs_district
COMMENT 'NFHS-5 district indicators, all numeric; __low_sample marks values NFHS printed in parentheses'
AS SELECT
  trim(district_name) AS district_name,
  lower(trim(district_name)) AS district_key,
  trim(state_ut) AS state_ut,
  workspace.vf_silver.canon_state(state_ut) AS state,
  households_surveyed,
  women_15_49_interviewed,
  men_15_54_interviewed,
  female_population_age_6_years_and_above_ever_schooled_pct,
  population_below_age_15_years_pct,
  sex_ratio_total_f_per_1000_m,
  try_cast(nullif(regexp_replace(trim(sex_ratio_at_birth_5y_f_per_1000_m), '[()*]', ''), '') AS DOUBLE) AS sex_ratio_at_birth_5y_f_per_1000_m,
  (trim(sex_ratio_at_birth_5y_f_per_1000_m) rlike '^\\(.*\\)$') AS sex_ratio_at_birth_5y_f_per_1000_m__low_sample,
  child_u5_whose_birth_was_civil_reg_pct,
  try_cast(nullif(regexp_replace(trim(deaths_in_the_last_3_years_civil_reg_pct), '[()*]', ''), '') AS DOUBLE) AS deaths_in_the_last_3_years_civil_reg_pct,
  (trim(deaths_in_the_last_3_years_civil_reg_pct) rlike '^\\(.*\\)$') AS deaths_in_the_last_3_years_civil_reg_pct__low_sample,
  hh_electricity_pct,
  hh_improved_water_pct,
  hh_use_improved_sanitation_pct,
  households_using_clean_fuel_for_cooking_pct,
  households_using_iodized_salt_pct,
  hh_member_covered_health_insurance_pct,
  try_cast(nullif(regexp_replace(trim(child_5y_who_attended_pre_primary_school_during_the_school_pct), '[()*]', ''), '') AS DOUBLE) AS child_5y_who_attended_pre_primary_school_during_the_school_pct,
  (trim(child_5y_who_attended_pre_primary_school_during_the_school_pct) rlike '^\\(.*\\)$') AS child_5y_who_attended_pre_primary_school_during_the_school_pct__low_sample,
  women_age_15_49_who_are_literate_pct,
  women_age_15_49_with_10_or_more_years_of_schooling_pct,
  try_cast(nullif(regexp_replace(trim(w20_24_married_before_age_18_years_pct), '[()*]', ''), '') AS DOUBLE) AS w20_24_married_before_age_18_years_pct,
  (trim(w20_24_married_before_age_18_years_pct) rlike '^\\(.*\\)$') AS w20_24_married_before_age_18_years_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(births_in_the_5_years_preceding_the_survey_that_are_birth_3_pct), '[()*]', ''), '') AS DOUBLE) AS births_in_the_5_years_preceding_the_survey_that_are_birth_3_pct,
  (trim(births_in_the_5_years_preceding_the_survey_that_are_birth_3_pct) rlike '^\\(.*\\)$') AS births_in_the_5_years_preceding_the_survey_that_are_birth_3_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(w15_19_who_were_already_mothers_or_pregnant_at_the_time_of_pct), '[()*]', ''), '') AS DOUBLE) AS w15_19_who_were_already_mothers_or_pregnant_at_the_time_of_pct,
  (trim(w15_19_who_were_already_mothers_or_pregnant_at_the_time_of_pct) rlike '^\\(.*\\)$') AS w15_19_who_were_already_mothers_or_pregnant_at_the_time_of_pct__low_sample,
  w15_24_who_use_menstrual_hygiene_pct,
  fp_cm_w15_49_any_method_pct,
  fp_cm_w15_49_modern_method_pct,
  fp_cm_w15_49_f_steril_pct,
  fp_cm_w15_49_m_steril_pct,
  fp_cm_w15_49_iud_pct,
  fp_cm_w15_49_pill_pct,
  fp_cm_w15_49_condom_pct,
  fp_cm_w15_49_injectables_pct,
  fp_unmet_total_cm_w15_49_7_pct,
  fp_unmet_spacing_cm_w15_49_7_pct,
  health_worker_ever_talked_to_female_non_users_about_family_pct,
  try_cast(nullif(regexp_replace(trim(current_users_ever_told_about_side_effects_of_current_metho_pct), '[()*]', ''), '') AS DOUBLE) AS current_users_ever_told_about_side_effects_of_current_metho_pct,
  (trim(current_users_ever_told_about_side_effects_of_current_metho_pct) rlike '^\\(.*\\)$') AS current_users_ever_told_about_side_effects_of_current_metho_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(mothers_who_had_an_anc_visit_in_the_first_trimester_lb5y_pct), '[()*]', ''), '') AS DOUBLE) AS mothers_who_had_an_anc_visit_in_the_first_trimester_lb5y_pct,
  (trim(mothers_who_had_an_anc_visit_in_the_first_trimester_lb5y_pct) rlike '^\\(.*\\)$') AS mothers_who_had_an_anc_visit_in_the_first_trimester_lb5y_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(mothers_who_had_at_least_4_anc_visits_lb5y_pct), '[()*]', ''), '') AS DOUBLE) AS mothers_who_had_at_least_4_anc_visits_lb5y_pct,
  (trim(mothers_who_had_at_least_4_anc_visits_lb5y_pct) rlike '^\\(.*\\)$') AS mothers_who_had_at_least_4_anc_visits_lb5y_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(mothers_whose_last_birth_was_protected_against_neo_tetanus_pct), '[()*]', ''), '') AS DOUBLE) AS mothers_whose_last_birth_was_protected_against_neo_tetanus_pct,
  (trim(mothers_whose_last_birth_was_protected_against_neo_tetanus_pct) rlike '^\\(.*\\)$') AS mothers_whose_last_birth_was_protected_against_neo_tetanus_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(mothers_who_consumed_ifa_for_100_days_or_more_when_they_wer_pct), '[()*]', ''), '') AS DOUBLE) AS mothers_who_consumed_ifa_for_100_days_or_more_when_they_wer_pct,
  (trim(mothers_who_consumed_ifa_for_100_days_or_more_when_they_wer_pct) rlike '^\\(.*\\)$') AS mothers_who_consumed_ifa_for_100_days_or_more_when_they_wer_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(mothers_who_consumed_ifa_for_180_days_or_more_when_they_wer_pct), '[()*]', ''), '') AS DOUBLE) AS mothers_who_consumed_ifa_for_180_days_or_more_when_they_wer_pct,
  (trim(mothers_who_consumed_ifa_for_180_days_or_more_when_they_wer_pct) rlike '^\\(.*\\)$') AS mothers_who_consumed_ifa_for_180_days_or_more_when_they_wer_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(registered_pregnancies_for_which_the_mother_received_a_mcp_pct), '[()*]', ''), '') AS DOUBLE) AS registered_pregnancies_for_which_the_mother_received_a_mcp_pct,
  (trim(registered_pregnancies_for_which_the_mother_received_a_mcp_pct) rlike '^\\(.*\\)$') AS registered_pregnancies_for_which_the_mother_received_a_mcp_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(mothers_who_received_pnc_from_a_doctor_nurse_lhv_anm_midwif_pct), '[()*]', ''), '') AS DOUBLE) AS mothers_who_received_pnc_from_a_doctor_nurse_lhv_anm_midwif_pct,
  (trim(mothers_who_received_pnc_from_a_doctor_nurse_lhv_anm_midwif_pct) rlike '^\\(.*\\)$') AS mothers_who_received_pnc_from_a_doctor_nurse_lhv_anm_midwif_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(average_out_of_pocket_expenditure_per_delivery_in_a_public_fac), '[()*]', ''), '') AS DOUBLE) AS average_out_of_pocket_expenditure_per_delivery_in_a_public_fac,
  (trim(average_out_of_pocket_expenditure_per_delivery_in_a_public_fac) rlike '^\\(.*\\)$') AS average_out_of_pocket_expenditure_per_delivery_in_a_public_fac__low_sample,
  try_cast(nullif(regexp_replace(trim(children_born_at_home_who_were_taken_to_a_health_facility_f_pct), '[()*]', ''), '') AS DOUBLE) AS children_born_at_home_who_were_taken_to_a_health_facility_f_pct,
  (trim(children_born_at_home_who_were_taken_to_a_health_facility_f_pct) rlike '^\\(.*\\)$') AS children_born_at_home_who_were_taken_to_a_health_facility_f_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(children_who_received_pnc_from_a_doctor_nurse_lhv_anm_midwi_pct), '[()*]', ''), '') AS DOUBLE) AS children_who_received_pnc_from_a_doctor_nurse_lhv_anm_midwi_pct,
  (trim(children_who_received_pnc_from_a_doctor_nurse_lhv_anm_midwi_pct) rlike '^\\(.*\\)$') AS children_who_received_pnc_from_a_doctor_nurse_lhv_anm_midwi_pct__low_sample,
  institutional_birth_5y_pct,
  institutional_birth_in_public_facility_5y_pct,
  home_birth_that_were_conducted_by_skilled_hp_5y_10_pct,
  births_attended_by_skilled_hp_5y_10_pct,
  births_delivered_by_csection_5y_pct,
  try_cast(nullif(regexp_replace(trim(births_in_a_private_fac_that_were_delivered_by_csection_5y_pct), '[()*]', ''), '') AS DOUBLE) AS births_in_a_private_fac_that_were_delivered_by_csection_5y_pct,
  (trim(births_in_a_private_fac_that_were_delivered_by_csection_5y_pct) rlike '^\\(.*\\)$') AS births_in_a_private_fac_that_were_delivered_by_csection_5y_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(births_in_a_public_fac_that_were_delivered_by_csection_5y_pct), '[()*]', ''), '') AS DOUBLE) AS births_in_a_public_fac_that_were_delivered_by_csection_5y_pct,
  (trim(births_in_a_public_fac_that_were_delivered_by_csection_5y_pct) rlike '^\\(.*\\)$') AS births_in_a_public_fac_that_were_delivered_by_csection_5y_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(child_12_23m_fully_vaccinated_based_on_information_from_eit_pct), '[()*]', ''), '') AS DOUBLE) AS child_12_23m_fully_vaccinated_based_on_information_from_eit_pct,
  (trim(child_12_23m_fully_vaccinated_based_on_information_from_eit_pct) rlike '^\\(.*\\)$') AS child_12_23m_fully_vaccinated_based_on_information_from_eit_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(child_12_23m_fully_vaccinated_based_on_information_from_vax_pct), '[()*]', ''), '') AS DOUBLE) AS child_12_23m_fully_vaccinated_based_on_information_from_vax_pct,
  (trim(child_12_23m_fully_vaccinated_based_on_information_from_vax_pct) rlike '^\\(.*\\)$') AS child_12_23m_fully_vaccinated_based_on_information_from_vax_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(child_12_23m_who_have_received_bcg_pct), '[()*]', ''), '') AS DOUBLE) AS child_12_23m_who_have_received_bcg_pct,
  (trim(child_12_23m_who_have_received_bcg_pct) rlike '^\\(.*\\)$') AS child_12_23m_who_have_received_bcg_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(child_12_23m_who_have_received_3_doses_of_polio_vaccine_pct), '[()*]', ''), '') AS DOUBLE) AS child_12_23m_who_have_received_3_doses_of_polio_vaccine_pct,
  (trim(child_12_23m_who_have_received_3_doses_of_polio_vaccine_pct) rlike '^\\(.*\\)$') AS child_12_23m_who_have_received_3_doses_of_polio_vaccine_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(child_12_23m_who_have_received_3_doses_of_penta_or_dpt_vacc_pct), '[()*]', ''), '') AS DOUBLE) AS child_12_23m_who_have_received_3_doses_of_penta_or_dpt_vacc_pct,
  (trim(child_12_23m_who_have_received_3_doses_of_penta_or_dpt_vacc_pct) rlike '^\\(.*\\)$') AS child_12_23m_who_have_received_3_doses_of_penta_or_dpt_vacc_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(child_12_23m_who_have_received_the_first_dose_of_mcv_mcv_pct), '[()*]', ''), '') AS DOUBLE) AS child_12_23m_who_have_received_the_first_dose_of_mcv_mcv_pct,
  (trim(child_12_23m_who_have_received_the_first_dose_of_mcv_mcv_pct) rlike '^\\(.*\\)$') AS child_12_23m_who_have_received_the_first_dose_of_mcv_mcv_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(child_24_35m_who_have_received_a_second_dose_of_mcv_mcv_pct), '[()*]', ''), '') AS DOUBLE) AS child_24_35m_who_have_received_a_second_dose_of_mcv_mcv_pct,
  (trim(child_24_35m_who_have_received_a_second_dose_of_mcv_mcv_pct) rlike '^\\(.*\\)$') AS child_24_35m_who_have_received_a_second_dose_of_mcv_mcv_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(child_12_23m_who_have_received_3_doses_of_rotavirus_vaccine_pct), '[()*]', ''), '') AS DOUBLE) AS child_12_23m_who_have_received_3_doses_of_rotavirus_vaccine_pct,
  (trim(child_12_23m_who_have_received_3_doses_of_rotavirus_vaccine_pct) rlike '^\\(.*\\)$') AS child_12_23m_who_have_received_3_doses_of_rotavirus_vaccine_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(child_12_23m_who_have_received_3_doses_of_penta_or_hepatiti_pct), '[()*]', ''), '') AS DOUBLE) AS child_12_23m_who_have_received_3_doses_of_penta_or_hepatiti_pct,
  (trim(child_12_23m_who_have_received_3_doses_of_penta_or_hepatiti_pct) rlike '^\\(.*\\)$') AS child_12_23m_who_have_received_3_doses_of_penta_or_hepatiti_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(child_9_35m_who_received_a_vit_a_in_the_last_6_months_pct), '[()*]', ''), '') AS DOUBLE) AS child_9_35m_who_received_a_vit_a_in_the_last_6_months_pct,
  (trim(child_9_35m_who_received_a_vit_a_in_the_last_6_months_pct) rlike '^\\(.*\\)$') AS child_9_35m_who_received_a_vit_a_in_the_last_6_months_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(child_12_23m_who_received_most_of_their_vaccinations_in_a_p_pct), '[()*]', ''), '') AS DOUBLE) AS child_12_23m_who_received_most_of_their_vaccinations_in_a_p_pct,
  (trim(child_12_23m_who_received_most_of_their_vaccinations_in_a_p_pct) rlike '^\\(.*\\)$') AS child_12_23m_who_received_most_of_their_vaccinations_in_a_p_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(child_12_23m_who_received_most_of_their_vaccinations_in_a_2_pct), '[()*]', ''), '') AS DOUBLE) AS child_12_23m_who_received_most_of_their_vaccinations_in_a_2_pct,
  (trim(child_12_23m_who_received_most_of_their_vaccinations_in_a_2_pct) rlike '^\\(.*\\)$') AS child_12_23m_who_received_most_of_their_vaccinations_in_a_2_pct__low_sample,
  prev_diarrhoea_2wk_child_u5_pct,
  try_cast(nullif(regexp_replace(trim(children_with_diarrhoea_2wk_who_received_oral_rehydration_s_pct), '[()*]', ''), '') AS DOUBLE) AS children_with_diarrhoea_2wk_who_received_oral_rehydration_s_pct,
  (trim(children_with_diarrhoea_2wk_who_received_oral_rehydration_s_pct) rlike '^\\(.*\\)$') AS children_with_diarrhoea_2wk_who_received_oral_rehydration_s_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(children_with_diarrhoea_2wk_who_received_zinc_child_u5_pct), '[()*]', ''), '') AS DOUBLE) AS children_with_diarrhoea_2wk_who_received_zinc_child_u5_pct,
  (trim(children_with_diarrhoea_2wk_who_received_zinc_child_u5_pct) rlike '^\\(.*\\)$') AS children_with_diarrhoea_2wk_who_received_zinc_child_u5_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(children_with_diarrhoea_2wk_taken_to_a_health_facility_or_h_pct), '[()*]', ''), '') AS DOUBLE) AS children_with_diarrhoea_2wk_taken_to_a_health_facility_or_h_pct,
  (trim(children_with_diarrhoea_2wk_taken_to_a_health_facility_or_h_pct) rlike '^\\(.*\\)$') AS children_with_diarrhoea_2wk_taken_to_a_health_facility_or_h_pct__low_sample,
  children_prev_symptoms_of_acute_respiratory_infection_ari_2_pct,
  try_cast(nullif(regexp_replace(trim(children_with_fever_or_symptoms_of_ari_2wk_taken_to_a_healt_pct), '[()*]', ''), '') AS DOUBLE) AS children_with_fever_or_symptoms_of_ari_2wk_taken_to_a_healt_pct,
  (trim(children_with_fever_or_symptoms_of_ari_2wk_taken_to_a_healt_pct) rlike '^\\(.*\\)$') AS children_with_fever_or_symptoms_of_ari_2wk_taken_to_a_healt_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(children_under_age_3_years_breastfed_within_one_hour_of_bir_pct), '[()*]', ''), '') AS DOUBLE) AS children_under_age_3_years_breastfed_within_one_hour_of_bir_pct,
  (trim(children_under_age_3_years_breastfed_within_one_hour_of_bir_pct) rlike '^\\(.*\\)$') AS children_under_age_3_years_breastfed_within_one_hour_of_bir_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(child_u6m_exclusively_breastfed_pct), '[()*]', ''), '') AS DOUBLE) AS child_u6m_exclusively_breastfed_pct,
  (trim(child_u6m_exclusively_breastfed_pct) rlike '^\\(.*\\)$') AS child_u6m_exclusively_breastfed_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(child_6_8m_receiving_solid_or_semi_solid_food_and_breastmil_pct), '[()*]', ''), '') AS DOUBLE) AS child_6_8m_receiving_solid_or_semi_solid_food_and_breastmil_pct,
  (trim(child_6_8m_receiving_solid_or_semi_solid_food_and_breastmil_pct) rlike '^\\(.*\\)$') AS child_6_8m_receiving_solid_or_semi_solid_food_and_breastmil_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(breastfeeding_child_6_23m_receiving_an_adequate_diet16_17_pct), '[()*]', ''), '') AS DOUBLE) AS breastfeeding_child_6_23m_receiving_an_adequate_diet16_17_pct,
  (trim(breastfeeding_child_6_23m_receiving_an_adequate_diet16_17_pct) rlike '^\\(.*\\)$') AS breastfeeding_child_6_23m_receiving_an_adequate_diet16_17_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(non_breastfeeding_child_6_23m_receiving_an_adequate_diet16_pct), '[()*]', ''), '') AS DOUBLE) AS non_breastfeeding_child_6_23m_receiving_an_adequate_diet16_pct,
  (trim(non_breastfeeding_child_6_23m_receiving_an_adequate_diet16_pct) rlike '^\\(.*\\)$') AS non_breastfeeding_child_6_23m_receiving_an_adequate_diet16_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(total_child_6_23m_receiving_an_adequate_diet16_17_pct), '[()*]', ''), '') AS DOUBLE) AS total_child_6_23m_receiving_an_adequate_diet16_17_pct,
  (trim(total_child_6_23m_receiving_an_adequate_diet16_17_pct) rlike '^\\(.*\\)$') AS total_child_6_23m_receiving_an_adequate_diet16_17_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(child_u5_who_are_stunted_height_for_age_18_pct), '[()*]', ''), '') AS DOUBLE) AS child_u5_who_are_stunted_height_for_age_18_pct,
  (trim(child_u5_who_are_stunted_height_for_age_18_pct) rlike '^\\(.*\\)$') AS child_u5_who_are_stunted_height_for_age_18_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(child_u5_who_are_wasted_weight_for_height_18_pct), '[()*]', ''), '') AS DOUBLE) AS child_u5_who_are_wasted_weight_for_height_18_pct,
  (trim(child_u5_who_are_wasted_weight_for_height_18_pct) rlike '^\\(.*\\)$') AS child_u5_who_are_wasted_weight_for_height_18_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(child_u5_who_are_severe_wasted_weight_for_height_19_pct), '[()*]', ''), '') AS DOUBLE) AS child_u5_who_are_severe_wasted_weight_for_height_19_pct,
  (trim(child_u5_who_are_severe_wasted_weight_for_height_19_pct) rlike '^\\(.*\\)$') AS child_u5_who_are_severe_wasted_weight_for_height_19_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(child_u5_who_are_underweight_weight_for_age_18_pct), '[()*]', ''), '') AS DOUBLE) AS child_u5_who_are_underweight_weight_for_age_18_pct,
  (trim(child_u5_who_are_underweight_weight_for_age_18_pct) rlike '^\\(.*\\)$') AS child_u5_who_are_underweight_weight_for_age_18_pct__low_sample,
  try_cast(nullif(regexp_replace(trim(child_u5_who_are_overweight_weight_for_height_20_pct), '[()*]', ''), '') AS DOUBLE) AS child_u5_who_are_overweight_weight_for_height_20_pct,
  (trim(child_u5_who_are_overweight_weight_for_height_20_pct) rlike '^\\(.*\\)$') AS child_u5_who_are_overweight_weight_for_height_20_pct__low_sample,
  women_age_15_49_years_whose_bmi_bmi_is_underweight_bmi_lt_1_pct,
  women_age_15_49_years_who_are_overweight_obese_bmi_gte_25_0_pct,
  women_age_15_49_years_who_have_high_risk_whr_gte_0_85_pct,
  try_cast(nullif(regexp_replace(trim(child_6_59m_who_are_anaemic_lt_11_0_g_dl_22_pct), '[()*]', ''), '') AS DOUBLE) AS child_6_59m_who_are_anaemic_lt_11_0_g_dl_22_pct,
  (trim(child_6_59m_who_are_anaemic_lt_11_0_g_dl_22_pct) rlike '^\\(.*\\)$') AS child_6_59m_who_are_anaemic_lt_11_0_g_dl_22_pct__low_sample,
  non_pregnant_w15_49_who_are_anaemic_lt_12_0_g_dl_22_pct,
  try_cast(nullif(regexp_replace(trim(pregnant_w15_49_who_are_anaemic_lt_11_0_g_dl_22_pct), '[()*]', ''), '') AS DOUBLE) AS pregnant_w15_49_who_are_anaemic_lt_11_0_g_dl_22_pct,
  (trim(pregnant_w15_49_who_are_anaemic_lt_11_0_g_dl_22_pct) rlike '^\\(.*\\)$') AS pregnant_w15_49_who_are_anaemic_lt_11_0_g_dl_22_pct__low_sample,
  all_w15_49_who_are_anaemic_pct,
  try_cast(nullif(regexp_replace(trim(all_w15_19_who_are_anaemic_pct), '[()*]', ''), '') AS DOUBLE) AS all_w15_19_who_are_anaemic_pct,
  (trim(all_w15_19_who_are_anaemic_pct) rlike '^\\(.*\\)$') AS all_w15_19_who_are_anaemic_pct__low_sample,
  women_age_15_years_and_above_with_high_141_160_mg_dl_blood_pct,
  w15_plus_with_very_high_gt_160_mg_dl_blood_sugar_pct,
  w15_plus_with_high_or_very_high_gt_140_mg_dl_blood_sugar_or_pct,
  m15_plus_with_high_141_160_mg_dl_blood_sugar_pct,
  men_age_15_years_and_above_with_very_high_gt_160_mg_dl_bloo_pct,
  m15_plus_with_high_or_very_high_gt_140_mg_dl_blood_sugar_or_pct,
  w15_plus_with_mildly_high_bp_sys_140_159_mmhg_and_or_dia_90_pct,
  w15_plus_with_moderately_or_severely_high_bp_sys_gte_160_mm_pct,
  w15_plus_with_high_bp_sys_gte_140_mmhg_and_or_dia_gte_90_mm_pct,
  m15_plus_with_mildly_high_bp_sys_140_159_mmhg_and_or_dia_90_pct,
  m15_plus_with_moderately_or_severely_high_bp_sys_gte_160_mm_pct,
  m15_plus_with_high_bp_sys_gte_140_mmhg_and_or_dia_gte_90_mm_pct,
  women_age_30_49_years_ever_undergone_a_cervical_screen_pct,
  women_age_30_49_years_ever_undergone_a_breast_exam_pct,
  women_age_30_49_years_ever_undergone_an_oral_cancer_exam_pct,
  w15_plus_who_use_any_kind_of_tobacco_pct,
  m15_plus_who_use_any_kind_of_tobacco_pct,
  w15_plus_who_consume_alcohol_pct,
  m15_plus_who_consume_alcohol_pct
FROM workspace.vf_bronze.nfhs5_district_raw;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 8. Checks
-- MAGIC Every row in `silver_checks` should say `passed = true`.

-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.vf_silver.silver_checks AS
WITH c AS (
  SELECT 'no facility lost: facilities + rejected + merged duplicates = bronze' AS check_name,
         (SELECT count(*) FROM workspace.vf_silver.facilities) + (SELECT count(*) FROM workspace.vf_silver.facilities_rejected) + (SELECT coalesce(sum(size(merged_row_nums)),0) FROM workspace.vf_silver.facilities) AS actual,
         (SELECT count(*) FROM workspace.vf_bronze.facilities_raw) AS expected
  UNION ALL SELECT 'facility_id is unique', (SELECT count(*) - count(DISTINCT facility_id) FROM workspace.vf_silver.facilities), 0
  UNION ALL SELECT 'no "null" text left in name/type/operator', (SELECT count(*) FROM workspace.vf_silver.facilities WHERE lower(name) = 'null' OR facility_type = 'null' OR operator_type = 'null'), 0
  UNION ALL SELECT 'all coordinates inside India box', (SELECT count(*) FROM workspace.vf_silver.facilities WHERE latitude IS NOT NULL AND NOT (latitude BETWEEN 6 AND 37.5 AND longitude BETWEEN 68 AND 97.5)), 0
  UNION ALL SELECT 'states are canonical (36 or fewer)', (SELECT CASE WHEN count(DISTINCT state) <= 36 THEN 0 ELSE count(DISTINCT state) END FROM workspace.vf_silver.facilities), 0
  UNION ALL SELECT 'district coverage at least 95% (count below)', (SELECT CASE WHEN avg(CAST(district IS NOT NULL AS INT)) >= 0.95 THEN 0 ELSE count_if(district IS NULL) END FROM workspace.vf_silver.facilities), 0
  UNION ALL SELECT 'pincode is unique', (SELECT count(*) - count(DISTINCT pincode) FROM workspace.vf_silver.pincode), 0
  UNION ALL SELECT 'every claim has evidence text', (SELECT count(*) FROM workspace.vf_silver.facility_claims WHERE raw_text IS NULL), 0
  UNION ALL SELECT 'NFHS percentages within 0 to 100', (SELECT count(*) FROM workspace.vf_silver.nfhs_district WHERE births_delivered_by_csection_5y_pct NOT BETWEEN 0 AND 100 OR institutional_birth_5y_pct NOT BETWEEN 0 AND 100 OR child_u5_who_are_stunted_height_for_age_18_pct NOT BETWEEN 0 AND 100), 0
  UNION ALL SELECT 'NFHS state names recognised', (SELECT count(*) FROM workspace.vf_silver.nfhs_district WHERE state IS NULL), 0
)
SELECT check_name, actual, expected, actual = expected AS passed, current_timestamp() AS checked_at FROM c;

-- COMMAND ----------

SELECT * FROM workspace.vf_silver.silver_checks;
