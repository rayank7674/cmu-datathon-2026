# Databricks notebook source
# MAGIC %md
# MAGIC # 15 - Resolved values (team decision: trust the external source)
# MAGIC **Decision (Ishaan, 2026-11-22):** where an accepted outside record and the Virtue Foundation record conflict, trust the outside source.
# MAGIC
# MAGIC This notebook applies that decision additively. `vf_gold.facility_resolved_values` has one row per facility with the original Virtue Foundation value, the resolved value, and where the resolved value came from. No original is changed anywhere.
# MAGIC
# MAGIC Scope, exactly:
# MAGIC - beds: the 4 Wikidata contradictions from notebook 13 and the 3 Rural Hospital Network differences from notebook 14
# MAGIC - founding year: the 17 Wikidata contradictions from notebook 13
# MAGIC - NABH: the 2 facilities whose own text claims accreditation while the matched directory record is withdrawn; the directory wins
# MAGIC
# MAGIC - gap fill (second decision, same day): where the Virtue Foundation value is missing, beds, founding year and full-time doctors are filled from an accepted (high or medium) match, marked `external_gap_fill`. NMC college founding years are never used as a hospital's founding year.
# MAGIC
# MAGIC - text gap fill: where there is still no value, an audited plain statement from the facility's own text (notebook 16) is used, marked `text_gap_fill`, with its quote. This is the facility's own claim, not an outside source.
# MAGIC
# MAGIC Not in scope: ambiguous matches (still held for review) and any clinical service. A resolved value is a team decision, not a verification.

# COMMAND ----------

BY = "Ishaan manual decision 2026-11-22: trust the external source"
spark.sql(f"""
CREATE OR REPLACE TABLE workspace.vf_gold.facility_resolved_values AS
WITH c AS (
  SELECT facility_id, source, conflict_type, external_value, source_ref FROM workspace.vf_gold.facility_external_conflicts
  WHERE is_contradiction AND conflict_type IN ('beds_differ', 'founding_year_differs')
  UNION ALL
  SELECT facility_id, source, conflict_type, external_value, source_ref FROM workspace.vf_gold.facility_rhn_conflicts
  WHERE conflict_type IN ('beds_differ', 'founding_year_differs')
),
b AS (SELECT facility_id, max(CAST(external_value AS INT)) AS v, max(source) AS src, max(source_ref) AS ref, count(*) AS n FROM c WHERE conflict_type = 'beds_differ' GROUP BY 1),
y AS (SELECT facility_id, max(CAST(external_value AS INT)) AS v, max(source) AS src, max(source_ref) AS ref, count(*) AS n FROM c WHERE conflict_type = 'founding_year_differs' GROUP BY 1),
nb AS (SELECT facility_id, max(external_value) AS v, max(source_ref) AS ref FROM workspace.vf_gold.facility_external_conflicts
       WHERE conflict_type = 'nabh_claim_vs_directory' AND is_contradiction GROUP BY 1),
l AS (SELECT facility_id, nabh_claimed FROM workspace.vf_gold.facility_level_v2),
-- gap fill: explicit values on accepted (high or medium) matches only
g AS (
  SELECT facility_id, source, 'beds' AS field, CAST(ext_beds AS INT) AS v, source_ref AS ref, match_status FROM workspace.vf_gold.facility_external_evidence
  WHERE ext_beds IS NOT NULL AND match_status IN ('accepted_high', 'accepted_medium')
  UNION ALL SELECT facility_id, source, 'year', CAST(ext_inception_year AS INT), source_ref, match_status FROM workspace.vf_gold.facility_external_evidence
  WHERE ext_inception_year IS NOT NULL AND match_status IN ('accepted_high', 'accepted_medium')
  UNION ALL SELECT facility_id, source, 'beds', rhn_beds, source_ref, match_status FROM workspace.vf_gold.facility_rhn_evidence WHERE rhn_beds IS NOT NULL
  UNION ALL SELECT facility_id, source, 'year', rhn_founding_year, source_ref, match_status FROM workspace.vf_gold.facility_rhn_evidence WHERE rhn_founding_year IS NOT NULL
  UNION ALL SELECT facility_id, source, 'doctors', rhn_doctors_fulltime, source_ref, match_status FROM workspace.vf_gold.facility_rhn_evidence WHERE rhn_doctors_fulltime IS NOT NULL
),
gg AS (SELECT facility_id, field, max_by(v, match_status = 'accepted_high') AS v, max_by(source, match_status = 'accepted_high') AS src,
              max_by(ref, match_status = 'accepted_high') AS ref, max_by(match_status, match_status = 'accepted_high') AS ms,
              count(DISTINCT v) AS n_values FROM g WHERE v > 0 GROUP BY 1, 2),
-- audited values from the facility's own text (notebook 16); used only when no value exists anywhere else
t AS (SELECT facility_id, attribute, max(value) AS v, max(quote) AS quote, max(source_ref) AS ref FROM workspace.vf_silver.facility_extracted_attributes_audit WHERE audit_pass GROUP BY 1, 2),
tb AS (SELECT * FROM t WHERE attribute = 'beds'), ty AS (SELECT * FROM t WHERE attribute = 'year_founded'), td AS (SELECT * FROM t WHERE attribute = 'doctors'),
gb AS (SELECT * FROM gg WHERE field = 'beds'), gy AS (SELECT * FROM gg WHERE field = 'year'), gd AS (SELECT * FROM gg WHERE field = 'doctors')
SELECT f.facility_id, f.name,
       f.capacity_beds AS vf_beds,
       coalesce(b.v, f.capacity_beds, gb.v, tb.v) AS beds_resolved,
       CASE WHEN b.v IS NOT NULL THEN 'external' WHEN f.capacity_beds IS NOT NULL THEN 'virtue_foundation' WHEN gb.v IS NOT NULL THEN 'external_gap_fill' WHEN tb.v IS NOT NULL THEN 'text_gap_fill' END AS beds_resolution_source,
       CASE WHEN b.v IS NOT NULL THEN b.src WHEN f.capacity_beds IS NULL THEN gb.src END AS beds_external_source,
       CASE WHEN b.v IS NOT NULL THEN b.ref WHEN f.capacity_beds IS NULL THEN coalesce(gb.ref, tb.ref) END AS beds_external_ref,
       CASE WHEN b.v IS NULL AND f.capacity_beds IS NULL AND gb.v IS NULL THEN tb.quote END AS beds_text_quote,
       CASE WHEN b.v IS NULL AND f.capacity_beds IS NULL THEN gb.ms END AS beds_fill_match_confidence,
       f.year_established AS vf_year_founded,
       coalesce(y.v, f.year_established, gy.v, ty.v) AS year_founded_resolved,
       CASE WHEN y.v IS NOT NULL THEN 'external' WHEN f.year_established IS NOT NULL THEN 'virtue_foundation' WHEN gy.v IS NOT NULL THEN 'external_gap_fill' WHEN ty.v IS NOT NULL THEN 'text_gap_fill' END AS year_founded_resolution_source,
       CASE WHEN y.v IS NOT NULL THEN y.src WHEN f.year_established IS NULL THEN gy.src END AS year_founded_external_source,
       CASE WHEN y.v IS NOT NULL THEN y.ref WHEN f.year_established IS NULL THEN coalesce(gy.ref, ty.ref) END AS year_founded_external_ref,
       CASE WHEN y.v IS NULL AND f.year_established IS NULL AND gy.v IS NULL THEN ty.quote END AS year_founded_text_quote,
       CASE WHEN y.v IS NULL AND f.year_established IS NULL THEN gy.ms END AS year_founded_fill_match_confidence,
       f.number_doctors AS vf_doctors,
       coalesce(f.number_doctors, gd.v, td.v) AS doctors_resolved,
       CASE WHEN f.number_doctors IS NOT NULL THEN 'virtue_foundation' WHEN gd.v IS NOT NULL THEN 'external_gap_fill' WHEN td.v IS NOT NULL THEN 'text_gap_fill' END AS doctors_resolution_source,
       CASE WHEN f.number_doctors IS NULL THEN gd.src END AS doctors_external_source,
       CASE WHEN f.number_doctors IS NULL THEN coalesce(gd.ref, td.ref) END AS doctors_external_ref,
       CASE WHEN f.number_doctors IS NULL AND gd.v IS NULL THEN td.quote END AS doctors_text_quote,
       CASE WHEN f.number_doctors IS NULL AND gd.v IS NOT NULL THEN 'full-time doctors as stated on the profile' END AS doctors_note,
       greatest(CASE WHEN f.capacity_beds IS NULL THEN coalesce(gb.n_values, 0) ELSE 0 END, CASE WHEN f.year_established IS NULL THEN coalesce(gy.n_values, 0) ELSE 0 END) AS fill_competing_values,
       l.nabh_claimed AS vf_nabh_claimed,
       CASE WHEN nb.v IS NOT NULL THEN 'withdrawn' END AS nabh_status_resolved,
       CASE WHEN nb.v IS NOT NULL THEN 'external' END AS nabh_resolution_source,
       nb.v AS nabh_external_value, nb.ref AS nabh_external_ref,
       (b.v IS NOT NULL OR y.v IS NOT NULL OR nb.v IS NOT NULL) AS any_value_resolved_from_external,
       ((f.capacity_beds IS NULL AND gb.v IS NOT NULL) OR (f.year_established IS NULL AND gy.v IS NOT NULL) OR (f.number_doctors IS NULL AND gd.v IS NOT NULL)) AS any_gap_filled_from_external,
       ((f.capacity_beds IS NULL AND gb.v IS NULL AND tb.v IS NOT NULL) OR (f.year_established IS NULL AND gy.v IS NULL AND ty.v IS NOT NULL) OR (f.number_doctors IS NULL AND gd.v IS NULL AND td.v IS NOT NULL)) AS any_gap_filled_from_text,
       CASE WHEN b.v IS NOT NULL OR y.v IS NOT NULL OR nb.v IS NOT NULL OR (f.capacity_beds IS NULL AND gb.v IS NOT NULL) OR (f.year_established IS NULL AND gy.v IS NOT NULL) OR (f.number_doctors IS NULL AND gd.v IS NOT NULL) OR ((f.capacity_beds IS NULL AND tb.v IS NOT NULL) OR (f.year_established IS NULL AND ty.v IS NOT NULL) OR (f.number_doctors IS NULL AND td.v IS NOT NULL)) THEN '{BY}' END AS resolution_by,
       coalesce(b.n, 0) AS beds_conflict_rows, coalesce(y.n, 0) AS year_conflict_rows
FROM workspace.vf_silver.facilities f
LEFT JOIN b ON b.facility_id = f.facility_id
LEFT JOIN y ON y.facility_id = f.facility_id
LEFT JOIN nb ON nb.facility_id = f.facility_id
LEFT JOIN l ON l.facility_id = f.facility_id
LEFT JOIN gb ON gb.facility_id = f.facility_id
LEFT JOIN gy ON gy.facility_id = f.facility_id
LEFT JOIN gd ON gd.facility_id = f.facility_id
LEFT JOIN tb ON tb.facility_id = f.facility_id
LEFT JOIN ty ON ty.facility_id = f.facility_id
LEFT JOIN td ON td.facility_id = f.facility_id
""")

# COMMAND ----------

import json
spark.sql("""
CREATE OR REPLACE TABLE workspace.vf_gold.resolved_values_checks AS
SELECT 1 AS n, 'one row per facility' AS check_name, concat(count(*), ' rows, ', count(DISTINCT facility_id), ' ids') AS measured, count(*) = 9989 AND count(DISTINCT facility_id) = 9989 AS passed FROM workspace.vf_gold.facility_resolved_values
UNION ALL SELECT 2, 'beds resolved from external / founding year / NABH', concat(sum(CAST(beds_resolution_source = 'external' AS INT)), ' / ', sum(CAST(year_founded_resolution_source = 'external' AS INT)), ' / ', sum(CAST(nabh_resolution_source = 'external' AS INT))),
       sum(CAST(beds_resolution_source = 'external' AS INT)) = 7 AND sum(CAST(year_founded_resolution_source = 'external' AS INT)) = 17 AND sum(CAST(nabh_resolution_source = 'external' AS INT)) = 2 FROM workspace.vf_gold.facility_resolved_values
UNION ALL SELECT 3, 'facilities with two different external values for one field', CAST(count(*) AS STRING), count(*) = 0 FROM workspace.vf_gold.facility_resolved_values WHERE beds_conflict_rows > 1 OR year_conflict_rows > 1
UNION ALL SELECT 4, 'original values still equal silver', CAST(count(*) AS STRING), count(*) = 0 FROM workspace.vf_gold.facility_resolved_values r JOIN workspace.vf_silver.facilities f USING (facility_id) WHERE NOT (r.vf_beds <=> f.capacity_beds AND r.vf_year_founded <=> f.year_established)
UNION ALL SELECT 5, 'resolved differs from original only where an external value was applied', CAST(count(*) AS STRING), count(*) = 0 FROM workspace.vf_gold.facility_resolved_values WHERE (NOT beds_resolved <=> vf_beds AND beds_resolution_source NOT IN ('external', 'external_gap_fill', 'text_gap_fill')) OR (NOT year_founded_resolved <=> vf_year_founded AND year_founded_resolution_source NOT IN ('external', 'external_gap_fill', 'text_gap_fill')) OR (vf_beds IS NOT NULL AND beds_resolution_source LIKE '%gap_fill') OR (vf_year_founded IS NOT NULL AND year_founded_resolution_source LIKE '%gap_fill') OR (vf_doctors IS NOT NULL AND NOT doctors_resolved <=> vf_doctors)
UNION ALL SELECT 6, 'externally resolved rows missing source ref or decision note', CAST(count(*) AS STRING), count(*) = 0 FROM workspace.vf_gold.facility_resolved_values WHERE (any_value_resolved_from_external OR any_gap_filled_from_external OR any_gap_filled_from_text) AND (resolution_by IS NULL OR coalesce(beds_external_ref, year_founded_external_ref, nabh_external_ref, doctors_external_ref) IS NULL)
UNION ALL SELECT 7, 'gaps filled from accepted matches: beds / founding year / doctors', concat(sum(CAST(beds_resolution_source = 'external_gap_fill' AS INT)), ' / ', sum(CAST(year_founded_resolution_source = 'external_gap_fill' AS INT)), ' / ', sum(CAST(doctors_resolution_source = 'external_gap_fill' AS INT))), true FROM workspace.vf_gold.facility_resolved_values
UNION ALL SELECT 11, 'gaps filled from audited facility text: beds / founding year / doctors', concat(sum(CAST(beds_resolution_source = 'text_gap_fill' AS INT)), ' / ', sum(CAST(year_founded_resolution_source = 'text_gap_fill' AS INT)), ' / ', sum(CAST(doctors_resolution_source = 'text_gap_fill' AS INT))), true FROM workspace.vf_gold.facility_resolved_values
UNION ALL SELECT 12, 'text gap fills without their quote', CAST(count(*) AS STRING), count(*) = 0 FROM workspace.vf_gold.facility_resolved_values WHERE (beds_resolution_source = 'text_gap_fill' AND beds_text_quote IS NULL) OR (year_founded_resolution_source = 'text_gap_fill' AND year_founded_text_quote IS NULL) OR (doctors_resolution_source = 'text_gap_fill' AND doctors_text_quote IS NULL)
UNION ALL SELECT 13, 'coverage after resolution: beds / founding year / doctors (of 9,989)', concat(count(beds_resolved), ' / ', count(year_founded_resolved), ' / ', count(doctors_resolved), ' (was ', count(vf_beds), ' / ', count(vf_year_founded), ' / ', count(vf_doctors), ')'), true FROM workspace.vf_gold.facility_resolved_values
UNION ALL SELECT 8, 'gap fills where two outside sources give different values', CAST(count(*) AS STRING), count(*) = 0 FROM workspace.vf_gold.facility_resolved_values WHERE fill_competing_values > 1 AND any_gap_filled_from_external
UNION ALL SELECT 9, 'gap fills from a non-accepted match or from NMC (must be 0)', CAST(count(*) AS STRING), count(*) = 0 FROM workspace.vf_gold.facility_resolved_values
          WHERE coalesce(beds_fill_match_confidence, 'accepted_medium') NOT IN ('accepted_high', 'accepted_medium') OR coalesce(year_founded_fill_match_confidence, 'accepted_medium') NOT IN ('accepted_high', 'accepted_medium')
             OR (year_founded_resolution_source = 'external_gap_fill' AND year_founded_external_source NOT IN ('wikidata', 'rural_hospital_network'))
UNION ALL SELECT 10, 'filled founding years outside 1700 to 2026', CAST(count(*) AS STRING), count(*) = 0 FROM workspace.vf_gold.facility_resolved_values WHERE year_founded_resolution_source = 'external_gap_fill' AND NOT year_founded_resolved BETWEEN 1700 AND 2026
""")
out = {"checks": [r.asDict() for r in spark.sql("SELECT * FROM workspace.vf_gold.resolved_values_checks ORDER BY n").collect()],
       "changed": [r.asDict() for r in spark.sql("SELECT name, vf_beds, beds_resolved, beds_external_source, vf_year_founded, year_founded_resolved, year_founded_external_source, nabh_status_resolved FROM workspace.vf_gold.facility_resolved_values WHERE any_value_resolved_from_external ORDER BY name").collect()],
       "filled": [r.asDict() for r in spark.sql("SELECT beds_external_source, year_founded_external_source, doctors_external_source, beds_resolution_source b, year_founded_resolution_source y, doctors_resolution_source d, count(*) n FROM workspace.vf_gold.facility_resolved_values WHERE any_gap_filled_from_external GROUP BY ALL").collect()],
       "filled_examples": [r.asDict() for r in spark.sql("SELECT name, vf_beds, beds_resolved, beds_external_source, vf_year_founded, year_founded_resolved, year_founded_external_source, vf_doctors, doctors_resolved FROM workspace.vf_gold.facility_resolved_values WHERE any_gap_filled_from_external ORDER BY beds_resolution_source = 'external_gap_fill' DESC, name LIMIT 12").collect()]}
dbutils.notebook.exit(json.dumps(out))
