# Databricks notebook source
# MAGIC %md
# MAGIC # 07 - Gold Fixes and Tests
# MAGIC Corrections and quality flags that sit **beside** the existing tables. Nothing from notebooks 01-06 is edited, and no row is merged or deleted.
# MAGIC
# MAGIC | Object | What it is |
# MAGIC |---|---|
# MAGIC | `vf_gold.facility_level_v2` | `facility_level` with documented corrections. Same columns, plus `care_level_v1`, `v2_change`, `v2_change_reason` |
# MAGIC | `vf_silver.facility_geo_quality` | One row per facility: `geo_flag` good / suspect / invalid, with the reason |
# MAGIC | `vf_silver.facility_duplicate_groups` | Likely duplicate records, grouped. Flag only |
# MAGIC | `vf_silver.facility_duplicate_pairs` | The individual links behind the groups (working table) |
# MAGIC | `vf_silver.facility_canonical` | Maps each record to its centre; near-certain duplicates share one centre |
# MAGIC | `vf_gold.facility_level_canonical` | View: one row per centre, with the higher rung |
# MAGIC | `vf_gold.quality_tests` | Named assertions with pass/fail and counts |
# MAGIC
# MAGIC Safe to rerun. Run after 04 and 06; rerun 09 afterwards so it picks up the flags.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. `facility_level_v2`
# MAGIC **Why a diagnostic centre got rung 5.** In notebook 04 the rule "academic affiliation -> rung 5" is tested *before* the rule "pharmacy or standalone diagnostic centre -> no rung". *Anderson Diagnostics & Labs* has an academic affiliation, so it took rung 5 and kept the label "Diagnostic centre".
# MAGIC
# MAGIC Looking at the same branch showed two more ways a facility skips the documented single-specialty guard ("single-specialty hospitals are capped at rung 3 unless they have 100+ beds"):
# MAGIC - the academic rule also runs before that guard, so dental colleges and eye institutes with a teaching link took rung 5;
# MAGIC - the guard's pattern in notebook 04 has unescaped word boundaries (`\b` instead of `\\b`), so the words eye, skin, IVF, ENT and hair never matched.
# MAGIC
# MAGIC **Fixes applied here (v1 is left as it is):**
# MAGIC 1. Pharmacy or standalone diagnostic centre -> no rung, whatever its affiliation.
# MAGIC 2. Single-specialty test redone with working word boundaries.
# MAGIC 3. Single-specialty hospital with under 100 beds (or beds unknown) and no tier stated in its name -> rung 3, including teaching ones.
# MAGIC 4. Single-specialty teaching hospital with 100+ beds -> rung from the normal beds and services rule instead of an automatic 5.

# COMMAND ----------

spark.sql(r"""
CREATE OR REPLACE TABLE workspace.vf_gold.facility_level_v2
COMMENT 'facility_level with documented corrections (notebook 07). care_level_v1 keeps the original; v2_change says what changed and why.'
AS
WITH b AS (
  SELECT *,
    lower(coalesce(name, '')) AS lname
  FROM workspace.vf_gold.facility_level
),
s AS (
  SELECT *,
    lname rlike '\\beye\\b|netra|nethra|dental|dentist|\\bskin\\b|derma|\\bivf\\b|fertility|\\bent\\b|physiother|\\bhair\\b|cosmetic|psychiatr|de-?addiction|rehab|ortho(paedic|pedic)? cent'
      AND NOT lname rlike 'multi|general|medical college' AS ss2
  FROM b
),
r AS (
  SELECT *,
    CASE
      WHEN level_kind IN ('Diagnostic centre', 'Pharmacy') THEN NULL
      WHEN facility_type = 'hospital' AND ss2 AND level_basis <> 'name' AND coalesce(capacity_beds, 0) < 100 THEN least(care_level, 3)
      WHEN facility_type = 'hospital' AND ss2 AND level_basis = 'academic affiliation' THEN
        CASE WHEN (capacity_beds >= 300 AND tertiary_signals >= 1) OR (tertiary_signals >= 3 AND district_signals >= 3) THEN 5 ELSE 4 END
      ELSE care_level
    END AS new_level,
    CASE
      WHEN level_kind IN ('Diagnostic centre', 'Pharmacy') AND care_level IS NOT NULL THEN 'standalone diagnostic centre or pharmacy gets no rung, even with an academic affiliation'
      WHEN facility_type = 'hospital' AND ss2 AND level_basis <> 'name' AND coalesce(capacity_beds, 0) < 100 AND care_level > 3
        THEN concat('single-specialty hospital with under 100 beds or beds unknown is capped at rung 3 (v1 basis: ', level_basis, ')')
      WHEN facility_type = 'hospital' AND ss2 AND level_basis = 'academic affiliation'
           AND care_level <> CASE WHEN (capacity_beds >= 300 AND tertiary_signals >= 1) OR (tertiary_signals >= 3 AND district_signals >= 3) THEN 5 ELSE 4 END
        THEN 'single-specialty teaching hospital with 100+ beds: rung from beds and services, not automatic 5'
    END AS rung_reason
  FROM s
),
k AS (
  SELECT *,
    CASE
      WHEN level_kind IN ('Diagnostic centre', 'Pharmacy') THEN level_kind
      WHEN facility_type = 'hospital' AND ss2 AND level_basis <> 'name' AND new_level >= 3
           AND (rung_reason IS NOT NULL OR level_kind IN ('Nursing home / small hospital', 'Multispecialty hospital', 'Super-specialty / tertiary')) THEN 'Single-specialty hospital'
      ELSE level_kind
    END AS new_kind
  FROM r
)
SELECT
  facility_id, name, facility_type, operator_type, city, district, state, latitude, longitude,
  new_level AS care_level,
  CASE WHEN new_level IS NULL THEN new_kind ELSE concat('Rung ', new_level, ': ', new_kind) END AS care_level_label,
  new_kind AS level_kind,
  CASE WHEN rung_reason IS NOT NULL AND new_level IS NULL THEN 'kind only'
       WHEN rung_reason IS NOT NULL THEN 'single-specialty rule (v2)'
       ELSE level_basis END AS level_basis,
  CASE WHEN rung_reason IS NULL THEN level_confidence
       WHEN new_level IS NULL THEN 'medium'
       WHEN capacity_beds IS NOT NULL THEN 'medium'
       ELSE 'low' END AS level_confidence,
  newborn_level, obstetric_level, trauma_level, dialysis_level,
  nabh_claimed, jci_claimed, pmjay_claimed, cghs_claimed, is_academic, is_ayush,
  ss2 AS is_single_specialty,
  capacity_beds, number_doctors, tertiary_signals, district_signals, block_signals, signals, evidence,
  CASE WHEN rung_reason IS NOT NULL THEN concat(level_reason, '; v2 correction: ', rung_reason) ELSE level_reason END AS level_reason,
  quality_score,
  care_level AS care_level_v1,
  level_kind AS level_kind_v1,
  CASE WHEN rung_reason IS NOT NULL THEN 'rung' WHEN new_kind <> level_kind THEN 'kind only' END AS v2_change,
  coalesce(rung_reason, CASE WHEN new_kind <> level_kind THEN 'relabelled as single-specialty (pattern fixed); rung unchanged' END) AS v2_change_reason
FROM k
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Every facility whose rung changed

# COMMAND ----------

display(spark.sql("""
SELECT care_level_v1, care_level AS care_level_v2, count(*) AS facilities, v2_change_reason
FROM workspace.vf_gold.facility_level_v2 WHERE v2_change = 'rung' GROUP BY 1, 2, 4 ORDER BY 3 DESC
"""))
display(spark.sql("""
SELECT name, state, capacity_beds, care_level_v1, care_level AS care_level_v2, level_kind_v1, level_kind, v2_change_reason
FROM workspace.vf_gold.facility_level_v2 WHERE v2_change = 'rung' ORDER BY care_level_v1 DESC, name
"""))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. `facility_geo_quality`
# MAGIC | Flag | When |
# MAGIC |---|---|
# MAGIC | `invalid` | no coordinates, or coordinates outside India / not a real position |
# MAGIC | `suspect` | coordinates fall in a different state than the address; coordinates far from any known PIN code; the stated state disagrees with the PIN code's state; or the exact same point is shared with an unrelated facility |
# MAGIC | `good` | none of the above |
# MAGIC
# MAGIC `good` means no known problem was found. It does not mean the position was checked on a map. `suspect` records should not be used for precise routing without showing the warning.

# COMMAND ----------

spark.sql(r"""
CREATE OR REPLACE TABLE workspace.vf_silver.facility_geo_quality
COMMENT 'One row per facility: geo_flag (good / suspect / invalid) and why. Built by notebook 07 from the silver flags plus shared-coordinate detection.'
AS
WITH f AS (
  SELECT facility_id, name, latitude, longitude, official_phone, coords_invalid, coords_state_mismatch, state_mismatch, coords_far_from_any_pincode, no_district,
         array_distinct(filter(split(lower(trim(regexp_replace(coalesce(name, ''), '[^A-Za-z0-9]+', ' '))), ' '),
                x -> length(x) > 1 AND x NOT IN ('hospital','hospitals','clinic','centre','center','dr','the','and','of','pvt','ltd','care','health','medical','nursing','home','multispeciality','multispecialty','speciality','specialty','diagnostic','diagnostics','lab','labs','institute','research','super'))) AS toks
  FROM workspace.vf_silver.facilities
),
shared AS (  -- same exact point, different-looking name and different phone
  SELECT a.facility_id, count(*) AS n_unrelated, min(b.name) AS example
  FROM f a JOIN f b
    ON round(a.latitude, 5) = round(b.latitude, 5) AND round(a.longitude, 5) = round(b.longitude, 5) AND a.facility_id <> b.facility_id
  WHERE size(array_intersect(a.toks, b.toks)) / greatest(size(array_union(a.toks, b.toks)), 1) < 0.3
    AND NOT coalesce(a.official_phone = b.official_phone, false)
  GROUP BY a.facility_id
)
SELECT f.facility_id,
  CASE WHEN f.latitude IS NULL OR f.longitude IS NULL OR f.coords_invalid THEN 'invalid'
       WHEN f.coords_state_mismatch OR f.coords_far_from_any_pincode OR f.state_mismatch OR s.facility_id IS NOT NULL THEN 'suspect'
       ELSE 'good' END AS geo_flag,
  nullif(concat_ws('; ',
    CASE WHEN f.latitude IS NULL OR f.longitude IS NULL THEN 'no coordinates' END,
    CASE WHEN f.coords_invalid THEN 'coordinates are not a valid position in India' END,
    CASE WHEN f.coords_state_mismatch THEN 'coordinates fall in a different state than the address' END,
    CASE WHEN f.state_mismatch THEN 'stated state disagrees with the PIN code state' END,
    CASE WHEN f.coords_far_from_any_pincode THEN 'coordinates are far from any known PIN code' END,
    CASE WHEN s.facility_id IS NOT NULL THEN concat('exact same point as ', s.n_unrelated, ' unrelated facility record(s), e.g. "', s.example, '"') END,
    CASE WHEN f.no_district THEN 'no district could be assigned' END), '') AS reason,
  coalesce(f.coords_invalid, false) AS coords_invalid,
  coalesce(f.coords_state_mismatch, false) AS coords_state_mismatch,
  coalesce(f.state_mismatch, false) AS postcode_state_mismatch,
  coalesce(f.coords_far_from_any_pincode, false) AS far_from_any_pincode,
  s.facility_id IS NOT NULL AS shares_point_with_unrelated
FROM f LEFT JOIN shared s ON s.facility_id = f.facility_id
""")
display(spark.sql("SELECT geo_flag, count(*) AS facilities FROM workspace.vf_silver.facility_geo_quality GROUP BY 1 ORDER BY 2 DESC"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. `facility_duplicate_groups`
# MAGIC Two records are linked when their names look alike **and** something else agrees. Name likeness is word overlap (Jaccard) after dropping generic words such as hospital, clinic, centre.
# MAGIC
# MAGIC | Link | Confidence |
# MAGIC |---|---|
# MAGIC | same phone, names alike (0.5+), within 1 km or one has no coordinates | high |
# MAGIC | same website domain, names alike (0.5+), within 1 km | high |
# MAGIC | within 100 m, names alike (0.6+) | medium |
# MAGIC | same phone, names alike (0.5+), more than 1 km apart | low (often branches of one group) |
# MAGIC | same website domain, names nearly identical (0.8+), more than 1 km apart | low |
# MAGIC
# MAGIC Phones shared by more than 5 records and domains shared by more than 5 records (call centres, chains, practo, facebook, business.site ...) are ignored. Linked records are grouped; `group_id` is the smallest facility_id in the group.
# MAGIC **Nothing is merged or deleted.** A consumer that wants one result per real place can keep one row per `group_id` where confidence is high or medium.

# COMMAND ----------

spark.sql(r"""
CREATE OR REPLACE TABLE workspace.vf_silver.facility_duplicate_pairs
COMMENT 'Pairs of facility records that look like the same place, with the basis and confidence of each link. Working table for facility_duplicate_groups (notebook 07).'
AS
WITH f AS (
  SELECT facility_id, name, latitude, longitude,
         nullif(right(regexp_replace(coalesce(official_phone, ''), '[^0-9]', ''), 10), '') AS ph,
         nullif(regexp_extract(lower(coalesce(official_website, '')), '^(?:https?://)?(?:www\\.)?([^/:?#]+)', 1), '') AS dom,
         array_distinct(filter(split(lower(trim(regexp_replace(coalesce(name, ''), '[^A-Za-z0-9]+', ' '))), ' '),
                x -> length(x) > 1 AND x NOT IN ('hospital','hospitals','clinic','centre','center','dr','the','and','of','pvt','ltd','care','health','medical','nursing','home','multispeciality','multispecialty','speciality','specialty','diagnostic','diagnostics','lab','labs','institute','research','super'))) AS toks
  FROM workspace.vf_silver.facilities
),
okph AS (SELECT ph FROM f WHERE length(ph) = 10 GROUP BY ph HAVING count(*) BETWEEN 2 AND 5),
okdom AS (SELECT dom FROM f WHERE dom NOT RLIKE 'facebook|practo|justdial|google|g\\.page|business\\.site|youtube|bit\\.ly|instagram|linkedin|lybrate|eka\\.care|sulekha|wikipedia|\\.gov\\.in$|\\.nic\\.in$' GROUP BY dom HAVING count(*) BETWEEN 2 AND 5),
cand AS (
  SELECT a.facility_id AS a, b.facility_id AS b, 'phone' AS via FROM f a JOIN f b ON a.ph = b.ph AND a.facility_id < b.facility_id JOIN okph o ON o.ph = a.ph
  UNION ALL
  SELECT a.facility_id, b.facility_id, 'domain' FROM f a JOIN f b ON a.dom = b.dom AND a.facility_id < b.facility_id JOIN okdom o ON o.dom = a.dom
  UNION ALL
  SELECT a.facility_id, b.facility_id, 'near'
  FROM (SELECT facility_id, latitude, longitude, floor(latitude * 500) AS cy, floor(longitude * 500) AS cx FROM f WHERE latitude IS NOT NULL) a
  JOIN (SELECT facility_id, latitude, longitude, floor(latitude * 500) + dy AS cy, floor(longitude * 500) + dx AS cx
        FROM f LATERAL VIEW explode(array(-1, 0, 1)) t1 AS dy LATERAL VIEW explode(array(-1, 0, 1)) t2 AS dx WHERE latitude IS NOT NULL) b
    ON a.cy = b.cy AND a.cx = b.cx AND a.facility_id < b.facility_id
),
pairs AS (
  SELECT c.a, c.b,
         array_sort(collect_set(c.via)) AS vias,
         first(size(array_intersect(x.toks, y.toks)) / greatest(size(array_union(x.toks, y.toks)), 1)) AS name_sim,
         first(CASE WHEN x.latitude IS NOT NULL AND y.latitude IS NOT NULL THEN
           6371 * 2 * asin(sqrt(pow(sin(radians(y.latitude - x.latitude) / 2), 2) + cos(radians(x.latitude)) * cos(radians(y.latitude)) * pow(sin(radians(y.longitude - x.longitude) / 2), 2))) END) AS km
  FROM cand c JOIN f x ON x.facility_id = c.a JOIN f y ON y.facility_id = c.b
  GROUP BY c.a, c.b
),
edges AS (
  SELECT a, b, name_sim, km,
    CASE WHEN array_contains(vias, 'phone')  AND name_sim >= 0.5 AND coalesce(km, 0) < 1 THEN 'high'
         WHEN array_contains(vias, 'domain') AND name_sim >= 0.5 AND km < 1 THEN 'high'
         WHEN km < 0.1 AND name_sim >= 0.6 THEN 'medium'
         WHEN array_contains(vias, 'phone')  AND name_sim >= 0.5 THEN 'low'
         WHEN array_contains(vias, 'domain') AND name_sim >= 0.8 THEN 'low' END AS confidence,
    concat_ws(' + ',
      CASE WHEN array_contains(vias, 'phone') THEN 'same phone' END,
      CASE WHEN array_contains(vias, 'domain') THEN 'same website domain' END,
      CASE WHEN km < 0.1 THEN 'within 100 m' WHEN km < 1 THEN 'within 1 km' WHEN km >= 1 THEN 'over 1 km apart' END,
      concat('name overlap ', round(name_sim, 2))) AS basis
  FROM pairs
)
SELECT a, b, confidence, basis, name_sim, km FROM edges WHERE confidence IS NOT NULL
""")

# Group linked records: every record starts as its own group, then repeatedly takes the smallest group id among its links until nothing changes.
spark.sql(r"""
CREATE OR REPLACE TABLE workspace.vf_silver.facility_duplicate_groups
COMMENT 'Likely duplicate facility records, grouped (flag only, nothing merged). group_id = smallest facility_id in the group. Built by notebook 07.'
AS
WITH e AS (SELECT a AS x, b AS other, confidence, basis FROM workspace.vf_silver.facility_duplicate_pairs
           UNION ALL SELECT b, a, confidence, basis FROM workspace.vf_silver.facility_duplicate_pairs),
per AS (
  SELECT x AS facility_id,
         element_at(array('low', 'medium', 'high'), max(CASE confidence WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END)) AS confidence,
         max_by(basis, CASE confidence WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END) AS match_basis,
         max_by(other, CASE confidence WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END) AS best_match_facility_id,
         count(*) AS links
  FROM e GROUP BY x
)
SELECT p.facility_id, p.facility_id AS group_id, p.match_basis, p.confidence, CAST(1 AS BIGINT) AS group_size,
       p.best_match_facility_id, p.links, f.name, m.name AS best_match_name
FROM per p
JOIN workspace.vf_silver.facilities f ON f.facility_id = p.facility_id
LEFT JOIN workspace.vf_silver.facilities m ON m.facility_id = p.best_match_facility_id
""")

for round_no in range(1, 31):
    changed = int(spark.sql(r"""
    MERGE INTO workspace.vf_silver.facility_duplicate_groups t
    USING (
      SELECT u.x, min(n.group_id) AS mn
      FROM (SELECT a AS x, b AS y FROM workspace.vf_silver.facility_duplicate_pairs
            UNION ALL SELECT b, a FROM workspace.vf_silver.facility_duplicate_pairs) u
      JOIN workspace.vf_silver.facility_duplicate_groups n ON n.facility_id = u.y
      GROUP BY u.x
    ) s ON t.facility_id = s.x AND s.mn < t.group_id
    WHEN MATCHED THEN UPDATE SET t.group_id = s.mn
    """).collect()[0][0])
    print("grouping round", round_no, "records changed:", changed)
    if changed == 0:
        break

spark.sql(r"""
MERGE INTO workspace.vf_silver.facility_duplicate_groups t
USING (SELECT group_id, count(*) AS n FROM workspace.vf_silver.facility_duplicate_groups GROUP BY group_id) s
ON t.group_id = s.group_id
WHEN MATCHED THEN UPDATE SET t.group_size = s.n
""")
display(spark.sql("""
SELECT confidence, count(*) AS facilities, count(DISTINCT group_id) AS groups, max(group_size) AS largest_group
FROM workspace.vf_silver.facility_duplicate_groups GROUP BY 1 ORDER BY 1
"""))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3b. `facility_canonical`: treat near-certain duplicates as one centre
# MAGIC Team decision (2026-11-21): when the chance that two records are the same place is above 80%, treat them as **one centre whose rung is the higher of the two**.
# MAGIC
# MAGIC The duplicate confidence here is a label, not a measured probability. The mapping used: **`high` = above 80%**. A `high` link needs three things at once: the same phone number or the same website, names that overlap, and positions within 1 km. `medium` (close together with similar names but no shared phone or website) and `low` (same organisation, far apart) are **not** merged; those are often a hospital and its diagnostic unit, or branches.
# MAGIC
# MAGIC - Every original record stays in `vf_silver.facilities`, with its own evidence.
# MAGIC - `facility_canonical` maps each record to its centre. The centre's record is the one with the higher rung (then the higher quality score).
# MAGIC - `vf_gold.facility_level_canonical` is the one-row-per-centre view of `facility_level_v2`.

# COMMAND ----------

spark.sql(r"""
CREATE OR REPLACE TABLE workspace.vf_silver.facility_canonical
COMMENT 'Maps every facility record to its centre. Records linked with high duplicate confidence (treated as above 80%) share one canonical_facility_id. Nothing is deleted. Built by notebook 07.'
AS
SELECT facility_id, facility_id AS merge_group, facility_id AS canonical_facility_id, true AS is_canonical_record,
       CAST(1 AS BIGINT) AS merged_group_size, CAST(NULL AS STRING) AS merge_confidence, CAST(NULL AS STRING) AS merge_basis,
       CAST(NULL AS INT) AS care_level, CAST(NULL AS INT) AS canonical_care_level
FROM workspace.vf_silver.facilities
""")

for round_no in range(1, 31):
    changed = int(spark.sql(r"""
    MERGE INTO workspace.vf_silver.facility_canonical t
    USING (
      SELECT u.x, min(n.merge_group) AS mn
      FROM (SELECT a AS x, b AS y FROM workspace.vf_silver.facility_duplicate_pairs WHERE confidence = 'high'
            UNION ALL SELECT b, a FROM workspace.vf_silver.facility_duplicate_pairs WHERE confidence = 'high') u
      JOIN workspace.vf_silver.facility_canonical n ON n.facility_id = u.y
      GROUP BY u.x
    ) s ON t.facility_id = s.x AND s.mn < t.merge_group
    WHEN MATCHED THEN UPDATE SET t.merge_group = s.mn
    """).collect()[0][0])
    print("merge round", round_no, "records changed:", changed)
    if changed == 0:
        break

spark.sql(r"""
CREATE OR REPLACE TABLE workspace.vf_silver.facility_canonical
COMMENT 'Maps every facility record to its centre. Records linked with high duplicate confidence (treated as above 80%) share one canonical_facility_id. Nothing is deleted. Built by notebook 07.'
AS
WITH b AS (
  SELECT c.facility_id, c.merge_group, v.care_level, v.quality_score,
         (SELECT max_by(basis, name_sim) FROM workspace.vf_silver.facility_duplicate_pairs p WHERE p.confidence = 'high' AND (p.a = c.facility_id OR p.b = c.facility_id)) AS merge_basis
  FROM workspace.vf_silver.facility_canonical c JOIN workspace.vf_gold.facility_level_v2 v ON v.facility_id = c.facility_id
)
SELECT facility_id, merge_group,
  first_value(facility_id) OVER w AS canonical_facility_id,
  facility_id = first_value(facility_id) OVER w AS is_canonical_record,
  count(*) OVER (PARTITION BY merge_group) AS merged_group_size,
  CASE WHEN count(*) OVER (PARTITION BY merge_group) > 1 THEN 'high (treated as above 80%)' END AS merge_confidence,
  merge_basis,
  care_level,
  max(care_level) OVER (PARTITION BY merge_group) AS canonical_care_level
FROM b
WINDOW w AS (PARTITION BY merge_group ORDER BY coalesce(care_level, 0) DESC, coalesce(quality_score, 0) DESC, facility_id)
""")

spark.sql(r"""
CREATE OR REPLACE VIEW workspace.vf_gold.facility_level_canonical
COMMENT 'One row per centre: facility_level_v2 with near-certain duplicate records folded into one. care_level is the highest rung among the merged records.'
AS
SELECT v.*, c.merged_group_size AS merged_records, m.member_facility_ids, m.member_names, m.member_care_levels,
       CASE WHEN c.merged_group_size > 1 THEN concat('Merged from ', c.merged_group_size, ' records judged to be the same place (', c.merge_basis, '). Rung is the highest among them.') END AS merge_note
FROM workspace.vf_gold.facility_level_v2 v
JOIN workspace.vf_silver.facility_canonical c ON c.facility_id = v.facility_id AND c.is_canonical_record
JOIN (SELECT c2.canonical_facility_id, array_sort(collect_list(c2.facility_id)) AS member_facility_ids,
             collect_list(v2.name) AS member_names, collect_list(v2.care_level) AS member_care_levels
      FROM workspace.vf_silver.facility_canonical c2 JOIN workspace.vf_gold.facility_level_v2 v2 ON v2.facility_id = c2.facility_id
      GROUP BY c2.canonical_facility_id) m ON m.canonical_facility_id = v.facility_id
""")
display(spark.sql("""
SELECT c.canonical_facility_id, v.name, v.care_level AS record_rung, c.canonical_care_level AS centre_rung, c.is_canonical_record, c.merge_basis
FROM workspace.vf_silver.facility_canonical c JOIN workspace.vf_gold.facility_level_v2 v ON v.facility_id = c.facility_id
WHERE c.merged_group_size > 1 ORDER BY c.canonical_facility_id, c.is_canonical_record DESC
"""))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. `quality_tests`
# MAGIC Each row is one named assertion. `passed` is true when `bad_rows` is 0. The test on the original `facility_level` is expected to fail: it documents the known issue that v2 fixes.

# COMMAND ----------

HAS_FS = spark.catalog.tableExists("workspace.vf_gold.facility_service")
fs_tests = r"""
  UNION ALL SELECT 14, 'facility_service: one row per facility and service', 'vf_gold.facility_service',
         (SELECT count(*) - count(DISTINCT facility_id, service) FROM workspace.vf_gold.facility_service), (SELECT count(*) FROM workspace.vf_gold.facility_service), 'no join duplicated rows'
  UNION ALL SELECT 15, 'verification_date never set without a dated source', 'vf_gold.facility_service',
         (SELECT count(*) FROM workspace.vf_gold.facility_service WHERE verification_date IS NOT NULL AND verification_source IS NULL), (SELECT count(*) FROM workspace.vf_gold.facility_service), 'an import or match time is not a verification date'
  UNION ALL SELECT 16, 'source_date is never the day the row was computed', 'vf_gold.facility_verification',
         (SELECT count(*) FROM workspace.vf_gold.facility_verification WHERE source_date IS NOT NULL AND CAST(source_date AS DATE) = CAST(matched_at AS DATE)), (SELECT count(*) FROM workspace.vf_gold.facility_verification), 'matched_at is when we matched, not when anyone verified'
  UNION ALL SELECT 17, 'every service row carries a location quality flag', 'vf_gold.facility_service',
         (SELECT count(*) FROM workspace.vf_gold.facility_service WHERE location_quality_flag IS NULL), (SELECT count(*) FROM workspace.vf_gold.facility_service), 'no low-quality position can be used silently'
""" if HAS_FS else ""

spark.sql(r"""
CREATE OR REPLACE TABLE workspace.vf_gold.quality_tests
COMMENT 'Named SQL assertions over the silver and gold tables (notebook 07). passed = bad_rows is 0.'
AS
SELECT test_id, test_name, table_checked, bad_rows, total_rows, bad_rows = 0 AS passed, detail, current_timestamp() AS checked_at FROM (
  SELECT 1 AS test_id, 'one facility_level row per facility' AS test_name, 'vf_gold.facility_level' AS table_checked,
         (SELECT abs(count(*) - count(DISTINCT facility_id)) + abs(count(*) - (SELECT count(*) FROM workspace.vf_silver.facilities)) FROM workspace.vf_gold.facility_level) AS bad_rows,
         (SELECT count(*) FROM workspace.vf_gold.facility_level) AS total_rows, 'row count equals silver facilities, no duplicate ids' AS detail
  UNION ALL SELECT 2, 'one facility_level_v2 row per facility', 'vf_gold.facility_level_v2',
         (SELECT abs(count(*) - count(DISTINCT facility_id)) + abs(count(*) - (SELECT count(*) FROM workspace.vf_silver.facilities)) FROM workspace.vf_gold.facility_level_v2),
         (SELECT count(*) FROM workspace.vf_gold.facility_level_v2), 'row count equals silver facilities, no duplicate ids'
  UNION ALL SELECT 3, 'no standalone diagnostic centre or pharmacy with a rung (corrected table)', 'vf_gold.facility_level_v2',
         (SELECT count(*) FROM workspace.vf_gold.facility_level_v2 WHERE level_kind IN ('Diagnostic centre', 'Pharmacy') AND care_level IS NOT NULL),
         (SELECT count(*) FROM workspace.vf_gold.facility_level_v2 WHERE level_kind IN ('Diagnostic centre', 'Pharmacy')), 'policy: these get a kind, not a rung'
  UNION ALL SELECT 4, 'no standalone diagnostic centre or pharmacy with a rung (original table, known issue)', 'vf_gold.facility_level',
         (SELECT count(*) FROM workspace.vf_gold.facility_level WHERE level_kind IN ('Diagnostic centre', 'Pharmacy') AND care_level IS NOT NULL),
         (SELECT count(*) FROM workspace.vf_gold.facility_level WHERE level_kind IN ('Diagnostic centre', 'Pharmacy')), 'expected to fail: academic rule runs before the kind-only rule in notebook 04; use facility_level_v2'
  UNION ALL SELECT 5, 'no single-specialty hospital under 100 beds above rung 3 unless its name states the tier', 'vf_gold.facility_level_v2',
         (SELECT count(*) FROM workspace.vf_gold.facility_level_v2 WHERE facility_type = 'hospital' AND is_single_specialty AND level_basis <> 'name' AND coalesce(capacity_beds, 0) < 100 AND care_level > 3),
         (SELECT count(*) FROM workspace.vf_gold.facility_level_v2 WHERE is_single_specialty), 'documented single-specialty guard'
  UNION ALL SELECT 6, 'v2 only changes rows that carry a reason', 'vf_gold.facility_level_v2',
         (SELECT count(*) FROM workspace.vf_gold.facility_level_v2 WHERE (NOT (care_level <=> care_level_v1) OR level_kind <> level_kind_v1) AND v2_change_reason IS NULL),
         (SELECT count(*) FROM workspace.vf_gold.facility_level_v2 WHERE v2_change IS NOT NULL), 'every difference from v1 is explained'
  UNION ALL SELECT 7, 'rung is 1 to 5 or empty', 'vf_gold.facility_level_v2',
         (SELECT count(*) FROM workspace.vf_gold.facility_level_v2 WHERE care_level NOT BETWEEN 1 AND 5), (SELECT count(*) FROM workspace.vf_gold.facility_level_v2), ''
  UNION ALL SELECT 8, 'no evidence row with a blank quote', 'vf_gold.facility_level_evidence',
         (SELECT count(*) FROM workspace.vf_gold.facility_level_evidence WHERE quote IS NULL OR trim(quote) = ''), (SELECT count(*) FROM workspace.vf_gold.facility_level_evidence), 'every signal shows the words it came from'
  UNION ALL SELECT 9, 'one evidence row per facility and signal', 'vf_gold.facility_level_evidence',
         (SELECT count(*) - count(DISTINCT facility_id, signal) FROM workspace.vf_gold.facility_level_evidence), (SELECT count(*) FROM workspace.vf_gold.facility_level_evidence), ''
  UNION ALL SELECT 10, 'outside check did not duplicate facilities', 'vf_gold.facility_external_check',
         (SELECT abs(count(*) - count(DISTINCT facility_id)) + abs(count(*) - (SELECT count(*) FROM workspace.vf_silver.facilities)) FROM workspace.vf_gold.facility_external_check),
         (SELECT count(*) FROM workspace.vf_gold.facility_external_check), 'one row per facility after the external join'
  UNION ALL SELECT 11, 'one geo quality row per facility, valid flag', 'vf_silver.facility_geo_quality',
         (SELECT abs(count(*) - count(DISTINCT facility_id)) + abs(count(*) - (SELECT count(*) FROM workspace.vf_silver.facilities)) + sum(CAST(geo_flag NOT IN ('good', 'suspect', 'invalid') OR geo_flag IS NULL AS INT)) + sum(CAST(geo_flag <> 'good' AND reason IS NULL AS INT)) FROM workspace.vf_silver.facility_geo_quality),
         (SELECT count(*) FROM workspace.vf_silver.facility_geo_quality), 'every non-good flag has a reason'
  UNION ALL SELECT 12, 'duplicate groups: one row per facility, 2+ members, grouping settled', 'vf_silver.facility_duplicate_groups',
         (SELECT count(*) - count(DISTINCT facility_id) FROM workspace.vf_silver.facility_duplicate_groups)
         + (SELECT count(*) FROM workspace.vf_silver.facility_duplicate_groups WHERE group_size < 2)
         + (SELECT count(*) FROM workspace.vf_silver.facility_duplicate_groups a JOIN workspace.vf_silver.facility_duplicate_groups b ON b.facility_id = a.best_match_facility_id WHERE a.group_id <> b.group_id),
         (SELECT count(*) FROM workspace.vf_silver.facility_duplicate_groups), 'a record and its best match always share a group'
  UNION ALL SELECT 13, 'flagging removed no facility', 'vf_silver.facilities',
         (SELECT abs(count(*) - 9989) FROM workspace.vf_silver.facilities), (SELECT count(*) FROM workspace.vf_silver.facilities), 'duplicates and bad positions are flagged, never deleted (9,989 rows)'
  __FS_TESTS__
  UNION ALL SELECT 20, 'canonical map: one row per facility, every centre points at its own record', 'vf_silver.facility_canonical',
         (SELECT abs(count(*) - count(DISTINCT facility_id)) + abs(count(*) - (SELECT count(*) FROM workspace.vf_silver.facilities)) FROM workspace.vf_silver.facility_canonical)
         + (SELECT count(*) FROM workspace.vf_silver.facility_canonical c LEFT JOIN workspace.vf_silver.facility_canonical k ON k.facility_id = c.canonical_facility_id WHERE k.facility_id IS NULL OR NOT k.is_canonical_record),
         (SELECT count(*) FROM workspace.vf_silver.facility_canonical), 'no record is dropped by merging'
  UNION ALL SELECT 21, 'merged centre takes the highest rung of its records', 'vf_gold.facility_level_canonical',
         (SELECT count(*) FROM workspace.vf_gold.facility_level_canonical l JOIN workspace.vf_silver.facility_canonical c ON c.canonical_facility_id = l.facility_id WHERE coalesce(c.care_level, 0) > coalesce(l.care_level, 0)),
         (SELECT count(*) FROM workspace.vf_gold.facility_level_canonical), 'team rule: one centre, the higher rung'
  UNION ALL SELECT 22, 'only high-confidence links are merged', 'vf_silver.facility_canonical',
         (SELECT count(*) FROM workspace.vf_silver.facility_canonical c WHERE c.merged_group_size > 1 AND NOT EXISTS (SELECT 1 FROM workspace.vf_silver.facility_duplicate_pairs p WHERE p.confidence = 'high' AND (p.a = c.facility_id OR p.b = c.facility_id))),
         (SELECT count(*) FROM workspace.vf_silver.facility_canonical WHERE merged_group_size > 1), 'medium and low links stay separate records'
  UNION ALL SELECT 18, 'no stray verification-date columns', 'information_schema.columns',
         (SELECT count(*) FROM workspace.information_schema.columns WHERE table_schema IN ('vf_silver', 'vf_gold')
            AND lower(column_name) RLIKE 'verif.*(date|_at|time)|last_verified|verified_on'
            AND NOT (table_name = 'facility_service' AND column_name = 'verification_date')),
         (SELECT count(*) FROM workspace.information_schema.columns WHERE table_schema IN ('vf_silver', 'vf_gold')),
         'the only verification date column is facility_service.verification_date, which tests 15 and 16 guard'
  UNION ALL SELECT 19, 'no ingest timestamp exposed in gold', 'information_schema.columns',
         (SELECT count(*) FROM workspace.information_schema.columns WHERE table_schema = 'vf_gold' AND lower(column_name) RLIKE 'ingest|import|loaded_at'),
         (SELECT count(*) FROM workspace.information_schema.columns WHERE table_schema = 'vf_gold'), 'load times stay in bronze'
) ORDER BY test_id
""".replace("__FS_TESTS__", fs_tests))
display(spark.sql("SELECT test_id, test_name, table_checked, bad_rows, total_rows, passed, detail FROM workspace.vf_gold.quality_tests ORDER BY test_id"))
