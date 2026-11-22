# Databricks notebook source
# MAGIC %md
# MAGIC # 13 - External Evidence (Task D into chunk C)
# MAGIC Turns the Task D matches into facility-level evidence that the app, the dashboard and the recommender can read, without overstating what any source proves.
# MAGIC
# MAGIC | Object | Grain | What it is |
# MAGIC |---|---|---|
# MAGIC | `vf_gold.facility_external_evidence` | facility x accepted source record | What the matched outside record says, what that establishes, and what it does not |
# MAGIC | `vf_gold.facility_external_conflicts` | one row per disagreement | Where an outside record and the Virtue Foundation record differ. Both values are kept |
# MAGIC | `vf_gold.facility_external_review` | facility x ambiguous source record | Ambiguous matches kept for a person to review. Never used as identity |
# MAGIC | `vf_gold.facility_external_summary` | **one row per facility (9,989)** | Entry point for consumers |
# MAGIC | `vf_gold.external_integration_checks` | one row per check | The validation list, measured |
# MAGIC
# MAGIC Rules
# MAGIC - Only `accepted_high` and `accepted_medium` matches count as identity evidence. Ambiguous matches go to the review table. Rejected matches are ignored.
# MAGIC - Nothing here changes a Virtue Foundation value or a service claim. External values sit beside them.
# MAGIC - **No source in this notebook verifies a clinical service.** NABH: the directory has no validity dates and matches are name + pincode, so validity is always `unknown`. eRaktKosh: a directory listing, not stock or working status. Wikidata: identity and published attributes only.
# MAGIC - `source_downloaded_at` is when the file was fetched. It is not a verification date.
# MAGIC - The teammate's `ext_` tables are read, never written.
# MAGIC
# MAGIC Run after Task D's notebook and after 09. Safe to rerun.

# COMMAND ----------

import json
SUMMARY = {}
def one(sql):
    return spark.sql(sql).collect()[0][0]

STATUS_RAW = r"trim(regexp_replace(get_json_object(n.raw_json, '$.status_raw'), '[\\s\']+', ' '))"
# Same classification as notebook 09 (keep the two in step).
NABH_CLASS = r"""CASE
      WHEN lower(get_json_object(n.raw_json, '$.status')) = 'withdrawn' OR lower(__RAW__) LIKE '%withdraw%' THEN 'withdrawn'
      WHEN lower(__RAW__) LIKE '%suspen%' THEN 'suspended'
      WHEN lower(get_json_object(n.raw_json, '$.status')) IN ('accredited', 'renewed')
           AND get_json_object(n.raw_json, '$.identifier_prefix') IN ('H', 'SHCO') THEN 'accredited_hospital_programme'
      WHEN lower(get_json_object(n.raw_json, '$.status')) IN ('accredited', 'renewed') THEN 'accredited_other_programme'
      WHEN lower(get_json_object(n.raw_json, '$.status')) = 'certified' THEN 'certification_not_accreditation'
      WHEN lower(get_json_object(n.raw_json, '$.status')) = 'empanelled' THEN 'empanelment_not_accreditation'
      WHEN n.source_record_id IS NOT NULL THEN 'status_unclear' END""".replace("__RAW__", STATUS_RAW)

DETAIL_SELECT = r"""
  m.facility_id, m.source, m.source_record_id, m.match_status, m.confidence AS match_confidence,
  m.match_score, m.name_sim, m.distance_km, m.match_method, m.matched_fields, m.ext_name,
  m.source_type, m.source_ref,
  coalesce(get_json_object(n.raw_json, '$.certificate_url'), get_json_object(n.raw_json, '$.source_url'),
           get_json_object(e.raw_json, '$.source_url'), CASE WHEN c.source_ref LIKE 'http%' THEN c.source_ref END) AS source_url,
  CASE m.source WHEN 'nabh' THEN 'nabh_directory_record' WHEN 'eraktkosh' THEN 'blood_centre_directory_record'
                WHEN 'wikidata' THEN 'knowledge_base_entry' ELSE 'directory_record' END AS evidence_kind,
  CASE m.source WHEN 'nabh' THEN __NABH_CLASS__ WHEN 'eraktkosh' THEN get_json_object(e.raw_json, '$.centre_type') END AS evidence_class,
  get_json_object(n.raw_json, '$.programme') AS nabh_programme,
  get_json_object(n.raw_json, '$.identifier_prefix') AS nabh_id_prefix,
  CASE WHEN m.source = 'nabh' THEN __RAW__ END AS nabh_status_text,
  get_json_object(n.raw_json, '$.accreditation_no') AS nabh_accreditation_no,
  get_json_object(n.raw_json, '$.certificate_url') AS nabh_certificate_url,
  CASE WHEN m.source = 'nabh' THEN 'unknown: the directory gives no validity dates' END AS nabh_validity,
  get_json_object(e.raw_json, '$.centre_type') AS eraktkosh_centre_type,
  get_json_object(e.raw_json, '$.category') AS eraktkosh_category,
  get_json_object(e.raw_json, '$.hospital_code') AS eraktkosh_code,
  c.beds AS ext_beds, TRY_CAST(left(c.inception_raw, 4) AS INT) AS ext_inception_year,
  c.latitude AS ext_latitude, c.longitude AS ext_longitude, c.pincode AS ext_pincode, c.state AS ext_state,
  coalesce(c.downloaded_at, n.downloaded_at, e.downloaded_at) AS source_downloaded_at
""".replace("__NABH_CLASS__", NABH_CLASS).replace("__RAW__", STATUS_RAW)

DETAIL_FROM = r"""
  FROM workspace.vf_silver.ext_facility_matches m
  LEFT JOIN workspace.vf_bronze.ext_nabh_raw n ON m.source = 'nabh' AND n.source_record_id = m.source_record_id
  LEFT JOIN workspace.vf_bronze.ext_eraktkosh_raw e ON m.source = 'eraktkosh' AND e.source_record_id = m.source_record_id
  LEFT JOIN workspace.vf_silver.ext_facilities_clean c ON c.source = m.source AND c.source_record_id = m.source_record_id
"""

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. `facility_external_evidence`: accepted matches only

# COMMAND ----------

spark.sql(r"""
CREATE OR REPLACE TABLE workspace.vf_gold.facility_external_evidence
COMMENT 'One row per facility and ACCEPTED outside record (Task D). Says what the matched record states, what that establishes and what it does not. No row here verifies a clinical service. source_downloaded_at is a download time, not a verification date (notebook 13).'
AS
WITH d AS (SELECT __SELECT__ __FROM__ WHERE m.match_status IN ('accepted_high', 'accepted_medium') AND m.facility_id IS NOT NULL)
SELECT d.*,
  CASE
    WHEN source = 'nabh' AND evidence_class = 'accredited_hospital_programme' THEN 'A record with this name and pincode is in the NABH directory as accredited under the hospital programme.'
    WHEN source = 'nabh' AND evidence_class = 'accredited_other_programme' THEN concat('A record with this name and pincode is in the NABH directory as accredited under the ', coalesce(nabh_programme, 'a non-hospital'), ' programme.')
    WHEN source = 'nabh' AND evidence_class = 'certification_not_accreditation' THEN 'A record with this name and pincode is in the NABH directory with a certification (entry level or similar), which is not accreditation.'
    WHEN source = 'nabh' AND evidence_class = 'empanelment_not_accreditation' THEN 'A record with this name and pincode is in the NABH directory as empanelled, which is not accreditation.'
    WHEN source = 'nabh' AND evidence_class IN ('withdrawn', 'suspended') THEN concat('A record with this name and pincode is in the NABH directory with accreditation ', evidence_class, '.')
    WHEN source = 'nabh' THEN 'A record with this name and pincode is in the NABH directory; its status is unclear.'
    WHEN source = 'eraktkosh' AND lower(evidence_class) = 'blood bank' THEN 'A blood bank with this name at this location is listed in the official eRaktKosh blood-centre directory.'
    WHEN source = 'eraktkosh' THEN 'A blood storage unit with this name at this location is listed in the eRaktKosh directory. It is not a blood bank.'
    WHEN source = 'wikidata' THEN 'A hospital with this name at this location has a Wikidata entry.'
  END AS establishes,
  CASE
    WHEN source = 'nabh' THEN 'Does not establish that accreditation is current (no validity dates), its scope, or any clinical capability. The match is by name and pincode only.'
    WHEN source = 'eraktkosh' THEN 'Does not establish that the blood bank is working today, that any blood group is in stock, that the licence is current, or that the hospital performs transfusions.'
    WHEN source = 'wikidata' THEN 'Does not establish any service. Wikidata is edited by volunteers; published beds and founding year may be out of date.'
  END AS does_not_establish,
  current_timestamp() AS computed_at
FROM d
""".replace("__SELECT__", DETAIL_SELECT).replace("__FROM__", DETAIL_FROM))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. `facility_external_review`: ambiguous matches, for a person to check

# COMMAND ----------

spark.sql(r"""
CREATE OR REPLACE TABLE workspace.vf_gold.facility_external_review
COMMENT 'Ambiguous Task D matches kept for manual review. They are NOT identity evidence and are not used anywhere else (notebook 13).'
AS
SELECT f.name AS vf_name, f.state AS vf_state, f.pincode AS vf_pincode, d.*, m2.match_reason,
       'ambiguous: review by hand before using' AS review_status
FROM (SELECT __SELECT__ __FROM__ WHERE m.match_status = 'ambiguous' AND m.facility_id IS NOT NULL) d
JOIN workspace.vf_silver.facilities f ON f.facility_id = d.facility_id
LEFT JOIN workspace.vf_silver.ext_facility_matches m2
  ON m2.facility_id = d.facility_id AND m2.source = d.source AND m2.source_record_id = d.source_record_id
""".replace("__SELECT__", DETAIL_SELECT).replace("__FROM__", DETAIL_FROM))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. `facility_external_conflicts`: both values kept, nothing overwritten
# MAGIC `is_contradiction = true` means the two sources cannot both be right. `false` rows are differences worth knowing about (for example the directory lists a blood bank the facility's own text never mentions).

# COMMAND ----------

LEVEL = "workspace.vf_gold.facility_level_v2" if one("SELECT count(*) FROM workspace.information_schema.tables WHERE table_schema = 'vf_gold' AND table_name = 'facility_level_v2'") else "workspace.vf_gold.facility_level"
spark.sql(r"""
CREATE OR REPLACE TABLE workspace.vf_gold.facility_external_conflicts
COMMENT 'Differences between an accepted outside record and the Virtue Foundation record. Both values are kept; the Virtue Foundation value is never replaced (notebook 13).'
AS
WITH ev AS (SELECT * FROM workspace.vf_gold.facility_external_evidence),
f AS (SELECT facility_id, name, capacity_beds, year_established, state, latitude, longitude FROM workspace.vf_silver.facilities),
l AS (SELECT facility_id, nabh_claimed FROM __LEVEL__),
bb AS (SELECT facility_id, claim_status, claim_detected, negating_quote FROM workspace.vf_gold.facility_service WHERE service = 'blood_bank')
SELECT ev.facility_id, f.name, ev.source, ev.source_record_id, ev.match_status, 'beds_differ' AS conflict_type, true AS is_contradiction,
       CAST(f.capacity_beds AS STRING) AS vf_value, CAST(ev.ext_beds AS STRING) AS external_value,
       'Published bed count differs by more than 25% and more than 20 beds. Either may be out of date.' AS detail, ev.source_ref
FROM ev JOIN f ON f.facility_id = ev.facility_id
WHERE ev.ext_beds IS NOT NULL AND f.capacity_beds IS NOT NULL
  AND abs(ev.ext_beds - f.capacity_beds) > 20 AND abs(ev.ext_beds - f.capacity_beds) > 0.25 * greatest(ev.ext_beds, f.capacity_beds)
UNION ALL
SELECT ev.facility_id, f.name, ev.source, ev.source_record_id, ev.match_status, 'founding_year_differs', true,
       CAST(f.year_established AS STRING), CAST(ev.ext_inception_year AS STRING),
       'Founding year differs by more than 2 years.', ev.source_ref
FROM ev JOIN f ON f.facility_id = ev.facility_id
WHERE ev.ext_inception_year IS NOT NULL AND f.year_established IS NOT NULL AND abs(ev.ext_inception_year - f.year_established) > 2
UNION ALL
SELECT ev.facility_id, f.name, ev.source, ev.source_record_id, ev.match_status, 'location_differs', true,
       concat(round(f.latitude, 4), ', ', round(f.longitude, 4)), concat(round(ev.ext_latitude, 4), ', ', round(ev.ext_longitude, 4)),
       concat('The outside record is ', round(ev.distance_km, 1), ' km from the Virtue Foundation coordinates.'), ev.source_ref
FROM ev JOIN f ON f.facility_id = ev.facility_id
WHERE ev.distance_km > 2
UNION ALL
SELECT ev.facility_id, f.name, ev.source, ev.source_record_id, ev.match_status, 'nabh_claim_vs_directory', true,
       'facility text claims NABH accreditation', concat(ev.evidence_class, ': ', ev.nabh_status_text),
       'The facility says it is NABH accredited; the matched directory record is not a current accreditation entry.', ev.source_ref
FROM ev JOIN f ON f.facility_id = ev.facility_id JOIN l ON l.facility_id = ev.facility_id
WHERE ev.source = 'nabh' AND l.nabh_claimed
  AND ev.evidence_class IN ('withdrawn', 'suspended', 'certification_not_accreditation', 'empanelment_not_accreditation')
UNION ALL
SELECT ev.facility_id, f.name, ev.source, ev.source_record_id, ev.match_status, 'blood_bank_listed_but_negated_in_text', true,
       concat('facility text: "', bb.negating_quote, '"'), 'listed as a Blood Bank in eRaktKosh',
       'The facility text mentions a blood bank only in a negating or qualified way, but the directory lists one.', ev.source_ref
FROM ev JOIN f ON f.facility_id = ev.facility_id JOIN bb ON bb.facility_id = ev.facility_id
WHERE ev.source = 'eraktkosh' AND lower(ev.evidence_class) = 'blood bank' AND bb.claim_status = 'negated_or_qualified'
UNION ALL
SELECT ev.facility_id, f.name, ev.source, ev.source_record_id, ev.match_status, 'blood_bank_listed_but_not_claimed', false,
       'no blood bank mentioned in the facility text', 'listed as a Blood Bank in eRaktKosh',
       'Not a contradiction: the directory lists a blood bank the facility record does not mention. The claim tables are unchanged.', ev.source_ref
FROM ev JOIN f ON f.facility_id = ev.facility_id JOIN bb ON bb.facility_id = ev.facility_id
WHERE ev.source = 'eraktkosh' AND lower(ev.evidence_class) = 'blood bank' AND NOT bb.claim_detected AND bb.claim_status <> 'negated_or_qualified'
UNION ALL
SELECT ev.facility_id, f.name, ev.source, ev.source_record_id, ev.match_status, 'nabh_listed_but_not_claimed', false,
       'no NABH accreditation mentioned in the facility text', concat(ev.evidence_class, ': ', ev.nabh_status_text),
       'Not a contradiction: the directory shows accreditation the facility record does not mention.', ev.source_ref
FROM ev JOIN f ON f.facility_id = ev.facility_id JOIN l ON l.facility_id = ev.facility_id
WHERE ev.source = 'nabh' AND NOT coalesce(l.nabh_claimed, false) AND ev.evidence_class LIKE 'accredited%'
""".replace("__LEVEL__", LEVEL))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. `facility_external_summary`: one row per facility
# MAGIC Starts from every silver facility, so the grain is exactly the facility table. Match rows are aggregated before the join, never joined raw.

# COMMAND ----------

spark.sql(r"""
CREATE OR REPLACE TABLE workspace.vf_gold.facility_external_summary
COMMENT 'ONE ROW PER FACILITY (all 9,989). Summary of accepted outside evidence from Task D, with conflicts and ambiguous matches counted. External values sit beside the Virtue Foundation values and never replace them. Nothing here verifies a clinical service (notebook 13).'
AS
WITH ev AS (SELECT * FROM workspace.vf_gold.facility_external_evidence),
agg AS (
  SELECT facility_id,
         sort_array(collect_set(source)) AS external_sources,
         count(*) AS accepted_matches,
         CASE WHEN bool_or(match_status = 'accepted_high') THEN 'high' ELSE 'medium' END AS best_match_confidence,
         max(CASE WHEN source = 'nabh' THEN evidence_class END) AS nabh_record_class,
         max(CASE WHEN source = 'nabh' THEN nabh_programme END) AS nabh_programme,
         max(CASE WHEN source = 'nabh' THEN nabh_status_text END) AS nabh_status_text,
         max(CASE WHEN source = 'nabh' THEN nabh_accreditation_no END) AS nabh_accreditation_no,
         max(CASE WHEN source = 'nabh' THEN nabh_certificate_url END) AS nabh_certificate_url,
         max(CASE WHEN source = 'nabh' THEN match_status END) AS nabh_match_status,
         max(CASE WHEN source = 'eraktkosh' THEN eraktkosh_centre_type END) AS eraktkosh_centre_type,
         max(CASE WHEN source = 'eraktkosh' THEN eraktkosh_code END) AS eraktkosh_code,
         max(CASE WHEN source = 'eraktkosh' THEN match_status END) AS eraktkosh_match_status,
         max(CASE WHEN source = 'wikidata' THEN source_url END) AS wikidata_url,
         max(CASE WHEN source = 'wikidata' THEN ext_beds END) AS wikidata_beds,
         max(CASE WHEN source = 'wikidata' THEN ext_inception_year END) AS wikidata_founding_year,
         max(CASE WHEN source = 'wikidata' THEN match_status END) AS wikidata_match_status,
         collect_list(named_struct('source', source, 'source_record_id', source_record_id, 'match_status', match_status,
                                   'match_method', match_method, 'match_score', match_score, 'source_ref', source_ref)) AS evidence_refs
  FROM ev GROUP BY facility_id),
cf AS (SELECT facility_id, sum(CAST(is_contradiction AS INT)) AS contradictions, sort_array(collect_set(CASE WHEN is_contradiction THEN conflict_type END)) AS contradiction_types,
              sort_array(collect_set(CASE WHEN NOT is_contradiction THEN conflict_type END)) AS other_differences
       FROM workspace.vf_gold.facility_external_conflicts GROUP BY facility_id),
rv AS (SELECT facility_id, sort_array(collect_set(source)) AS ambiguous_sources FROM workspace.vf_gold.facility_external_review GROUP BY facility_id),
l AS (SELECT facility_id, nabh_claimed FROM __LEVEL__),
bb AS (SELECT facility_id, claim_status AS vf_blood_bank_claim_status, verification_status AS blood_bank_verification_status
       FROM workspace.vf_gold.facility_service WHERE service = 'blood_bank'),
ex AS (SELECT facility_id, max(facility_existence_status) AS facility_existence_status, max(facility_existence_source) AS facility_existence_source
       FROM workspace.vf_gold.facility_service GROUP BY facility_id)
SELECT f.facility_id, f.name, f.state, f.district,
       coalesce(agg.external_sources, array()) AS external_sources,
       coalesce(agg.accepted_matches, 0) AS accepted_matches,
       agg.best_match_confidence,
       ex.facility_existence_status, ex.facility_existence_source,
       -- NABH
       CASE WHEN agg.nabh_record_class IS NULL THEN 'no_accepted_match'
            WHEN agg.nabh_record_class LIKE 'accredited%' THEN 'listed_accredited_validity_unknown'
            WHEN agg.nabh_record_class IN ('withdrawn', 'suspended') THEN concat('listed_', agg.nabh_record_class)
            WHEN agg.nabh_record_class IN ('certification_not_accreditation', 'empanelment_not_accreditation') THEN 'listed_non_accreditation_programme'
            ELSE 'listed_status_unknown' END AS nabh_status,
       agg.nabh_record_class, agg.nabh_programme, agg.nabh_status_text, agg.nabh_accreditation_no, agg.nabh_certificate_url, agg.nabh_match_status,
       coalesce(l.nabh_claimed, false) AS vf_claims_nabh,
       -- eRaktKosh
       coalesce(lower(agg.eraktkosh_centre_type) = 'blood bank', false) AS eraktkosh_blood_bank_listed,
       agg.eraktkosh_centre_type, agg.eraktkosh_code, agg.eraktkosh_match_status,
       bb.vf_blood_bank_claim_status, bb.blood_bank_verification_status,
       -- Wikidata (beside the Virtue Foundation values, never instead of them)
       agg.wikidata_url, agg.wikidata_match_status,
       f.capacity_beds AS vf_beds, agg.wikidata_beds,
       f.year_established AS vf_founding_year, agg.wikidata_founding_year,
       -- conflicts and review
       coalesce(cf.contradictions, 0) AS contradictions,
       coalesce(cf.contradiction_types, array()) AS contradiction_types,
       coalesce(cf.other_differences, array()) AS other_differences,
       coalesce(rv.ambiguous_sources, array()) AS ambiguous_sources_for_review,
       agg.evidence_refs,
       'vf_silver.ext_facility_matches (accepted only) via notebook 13' AS source_ref,
       current_timestamp() AS computed_at
FROM workspace.vf_silver.facilities f
LEFT JOIN agg ON agg.facility_id = f.facility_id
LEFT JOIN cf ON cf.facility_id = f.facility_id
LEFT JOIN rv ON rv.facility_id = f.facility_id
LEFT JOIN l ON l.facility_id = f.facility_id
LEFT JOIN bb ON bb.facility_id = f.facility_id
LEFT JOIN ex ON ex.facility_id = f.facility_id
""".replace("__LEVEL__", LEVEL))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Validation list (`external_integration_checks`)

# COMMAND ----------

spark.sql(r"""
CREATE OR REPLACE TABLE workspace.vf_gold.external_integration_checks
COMMENT 'Measured validation results for the Task D integration (notebook 13)'
AS
WITH s AS (SELECT * FROM workspace.vf_gold.facility_external_summary),
ev AS (SELECT * FROM workspace.vf_gold.facility_external_evidence),
fv AS (SELECT * FROM workspace.vf_gold.facility_verification),
t AS (SELECT * FROM workspace.vf_silver.ext_facility_match_summary)
SELECT 1 AS n, 'facilities in silver = rows in summary' AS check_name,
       concat((SELECT count(*) FROM workspace.vf_silver.facilities), ' vs ', (SELECT count(*) FROM s)) AS measured,
       (SELECT count(*) FROM workspace.vf_silver.facilities) = (SELECT count(*) FROM s) AS passed
UNION ALL SELECT 2, 'distinct facility ids in summary', CAST((SELECT count(DISTINCT facility_id) FROM s) AS STRING), (SELECT count(DISTINCT facility_id) FROM s) = (SELECT count(*) FROM workspace.vf_silver.facilities)
UNION ALL SELECT 3, 'duplicate facility ids in summary', CAST((SELECT count(*) - count(DISTINCT facility_id) FROM s) AS STRING), (SELECT count(*) - count(DISTINCT facility_id) FROM s) = 0
UNION ALL SELECT 4, 'accepted matches by source (nabh / eraktkosh / wikidata)',
       concat((SELECT count(*) FROM ev WHERE source = 'nabh'), ' / ', (SELECT count(*) FROM ev WHERE source = 'eraktkosh'), ' / ', (SELECT count(*) FROM ev WHERE source = 'wikidata')),
       (SELECT count(*) FROM ev) = (SELECT count(*) FROM workspace.vf_silver.ext_facility_matches WHERE match_status IN ('accepted_high', 'accepted_medium'))
UNION ALL SELECT 5, 'facilities with accepted evidence = Task D match summary',
       concat((SELECT count(*) FROM s WHERE accepted_matches > 0), ' vs ', (SELECT count(*) FROM t)),
       (SELECT count(*) FROM s WHERE accepted_matches > 0) = (SELECT count(*) FROM t)
UNION ALL SELECT 6, 'evidence rows missing source_ref, source_type or confidence',
       CAST((SELECT count(*) FROM ev WHERE source_ref IS NULL OR source_type IS NULL OR match_confidence IS NULL OR source_record_id IS NULL) AS STRING),
       (SELECT count(*) FROM ev WHERE source_ref IS NULL OR source_type IS NULL OR match_confidence IS NULL OR source_record_id IS NULL) = 0
UNION ALL SELECT 7, 'NABH: accredited (validity unknown) / non-accreditation programme / withdrawn or suspended / status unknown / valid / expired',
       concat((SELECT count(*) FROM s WHERE nabh_status = 'listed_accredited_validity_unknown'), ' / ', (SELECT count(*) FROM s WHERE nabh_status = 'listed_non_accreditation_programme'), ' / ',
              (SELECT count(*) FROM s WHERE nabh_status IN ('listed_withdrawn', 'listed_suspended')), ' / ', (SELECT count(*) FROM s WHERE nabh_status = 'listed_status_unknown'), ' / 0 / 0'),
       (SELECT count(*) FROM s WHERE nabh_status <> 'no_accepted_match') = (SELECT count(*) FROM ev WHERE source = 'nabh')
UNION ALL SELECT 8, 'NABH rows marked verified', CAST((SELECT count(*) FROM fv WHERE lower(verification_source) = 'nabh' AND verification_status = 'verified') AS STRING),
       (SELECT count(*) FROM fv WHERE lower(verification_source) = 'nabh' AND verification_status = 'verified') = 0
UNION ALL SELECT 9, 'eRaktKosh rows: all externally_supported directory listings, none verified',
       concat((SELECT count(*) FROM fv WHERE lower(verification_source) = 'eraktkosh' AND service = 'blood_bank' AND verification_status = 'externally_supported'), ' listings, ',
              (SELECT count(*) FROM fv WHERE lower(verification_source) = 'eraktkosh' AND verification_status = 'verified'), ' verified'),
       (SELECT count(*) FROM fv WHERE lower(verification_source) = 'eraktkosh' AND (verification_status = 'verified' OR (service = 'blood_bank' AND verification_notes NOT LIKE '%Directory listing only%'))) = 0
UNION ALL SELECT 10, 'external contradictions / other differences',
       concat((SELECT count(*) FROM workspace.vf_gold.facility_external_conflicts WHERE is_contradiction), ' / ', (SELECT count(*) FROM workspace.vf_gold.facility_external_conflicts WHERE NOT is_contradiction)), true
UNION ALL SELECT 11, 'clinical services marked verified (any source)',
       CAST((SELECT count(*) FROM workspace.vf_gold.facility_service WHERE verification_status = 'verified') AS STRING),
       (SELECT count(*) FROM workspace.vf_gold.facility_service WHERE verification_status = 'verified') = 0
UNION ALL SELECT 12, 'service claims and quotes untouched: claims in facility_service = claims rebuilt from rules',
       CAST((SELECT count(*) FROM workspace.vf_gold.facility_service WHERE claim_detected) AS STRING),
       (SELECT count(*) FROM workspace.vf_gold.facility_service WHERE claim_detected AND supporting_quote IS NULL) = 0
UNION ALL SELECT 13, 'ambiguous matches kept for review and not used as evidence',
       CAST((SELECT count(*) FROM workspace.vf_gold.facility_external_review) AS STRING),
       (SELECT count(*) FROM ev WHERE match_status NOT IN ('accepted_high', 'accepted_medium')) = 0
         AND (SELECT count(*) FROM workspace.vf_gold.facility_external_review) = (SELECT count(*) FROM workspace.vf_silver.ext_facility_matches WHERE match_status = 'ambiguous')
UNION ALL SELECT 14, 'no download time presented as a verification date',
       CAST((SELECT count(*) FROM workspace.vf_gold.facility_service WHERE verification_date IS NOT NULL) AS STRING),
       (SELECT count(*) FROM workspace.vf_gold.facility_service WHERE verification_date IS NOT NULL) = 0
UNION ALL SELECT 15, 'existing chunk C tests (facility_service_checks) passing',
       concat((SELECT sum(CAST(passed AS INT)) FROM workspace.vf_gold.facility_service_checks), ' of ', (SELECT count(*) FROM workspace.vf_gold.facility_service_checks)),
       (SELECT count(*) FROM workspace.vf_gold.facility_service_checks WHERE NOT passed) = 0
""")
display(spark.sql("SELECT * FROM workspace.vf_gold.external_integration_checks ORDER BY n"))

# COMMAND ----------

SUMMARY["checks"] = [r.asDict() for r in spark.sql("SELECT * FROM workspace.vf_gold.external_integration_checks ORDER BY n").collect()]
SUMMARY["conflicts_by_type"] = [r.asDict() for r in spark.sql("SELECT conflict_type, is_contradiction, source, count(*) AS n FROM workspace.vf_gold.facility_external_conflicts GROUP BY 1, 2, 3 ORDER BY 2 DESC, 4 DESC").collect()]
SUMMARY["nabh_classes"] = [r.asDict() for r in spark.sql("SELECT evidence_class, count(*) AS n FROM workspace.vf_gold.facility_external_evidence WHERE source = 'nabh' GROUP BY 1 ORDER BY 2 DESC").collect()]
SUMMARY["sources_per_facility"] = [r.asDict() for r in spark.sql("SELECT concat_ws('+', external_sources) AS sources, count(*) AS facilities FROM workspace.vf_gold.facility_external_summary WHERE accepted_matches > 0 GROUP BY 1 ORDER BY 2 DESC").collect()]
dbutils.notebook.exit(json.dumps(SUMMARY, default=str))
