# Databricks notebook source
# MAGIC %md
# MAGIC # 09 - Gold Facility Service
# MAGIC Builds the table the recommender reads, and a separate verification layer.
# MAGIC
# MAGIC | Object | What it is |
# MAGIC |---|---|
# MAGIC | `vf_gold.facility_service_rules` | Editable keyword rules per service (direct and indirect wording) |
# MAGIC | `vf_gold.facility_service` | One row per facility and service: is it claimed, the quote that supports it, how strong the claim is, and what is still unverified |
# MAGIC | `vf_gold.facility_verification` | One row per facility, service and outside source: what an outside source does and does not establish |
# MAGIC | `vf_gold.facility_service_checks` | Pass/fail tests for the two tables |
# MAGIC
# MAGIC **Ladder used everywhere:** claimed in the facility's own text -> `inferred` -> `externally_supported` -> `verified` -> currently available (always `unknown`).
# MAGIC A map or directory match supports that the facility exists and its type. It never verifies a service.
# MAGIC
# MAGIC **Rerun:** the whole notebook is safe to rerun. After notebook 10 (external sources) or 07 (fixes) lands, rerun all cells; step 2 picks up `vf_silver.ext_facility_matches`, and step 1 picks up `facility_level_v2`, `facility_geo_quality` and `facility_duplicate_groups`, if they exist.
# MAGIC This notebook does not change any table from notebooks 01-08 or 10.

# COMMAND ----------

def exists(table):
    return spark.catalog.tableExists(table)

HAS_GEOQ = exists("workspace.vf_silver.facility_geo_quality")       # chunk A
HAS_DUPS = exists("workspace.vf_silver.facility_duplicate_groups")  # chunk A
HAS_D    = exists("workspace.vf_silver.ext_facility_matches")       # chunk D
HAS_NABH_RAW  = exists("workspace.vf_bronze.ext_nabh_raw")          # Task D source detail (NABH programme and status)
HAS_ERAKT_RAW = exists("workspace.vf_bronze.ext_eraktkosh_raw")     # Task D source detail (blood centre type)
HAS_EXT_CLEAN = exists("workspace.vf_silver.ext_facilities_clean")  # Task D cleaned external records
LEVEL_TABLE = "workspace.vf_gold.facility_level_v2" if exists("workspace.vf_gold.facility_level_v2") else "workspace.vf_gold.facility_level"  # chunk A
print("care levels read from:", LEVEL_TABLE)
print("geo quality table:", HAS_GEOQ, "| duplicate groups:", HAS_DUPS, "| external matches from notebook 10:", HAS_D)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0. Service rules
# MAGIC Patterns run on normalised text (lowercase, punctuation mostly removed).
# MAGIC `direct` = wording that states the service itself. `indirect` = wording that only hints at it (a specialty name, a related device). Indirect hits are always weak.

# COMMAND ----------

spark.sql(r"""
CREATE OR REPLACE TABLE workspace.vf_gold.facility_service_rules
  (service STRING, service_label STRING, strength STRING, pattern STRING, note STRING)
COMMENT 'Keyword rules that turn facility text into service claims. Edit and rerun notebook 09.'
""")

spark.sql(r"""
INSERT INTO workspace.vf_gold.facility_service_rules VALUES
 ('emergency','Emergency care','direct', r'24 ?(/|x)? ?7 emergency|24 ?h(ou)?rs? emergency|round the clock emergency|emergency (care|services?|department|room|ward|unit|cases|trauma|surg)|\bcasualty\b|accident and emergency|trauma and emergency', 'An emergency department or emergency service is stated'),
 ('emergency','Emergency care','indirect', r'emergency', 'Only the word emergency, e.g. the specialty tag emergency medicine'),
 ('trauma','Trauma care','direct', r'\btrauma (care|cent(re|er)|unit|surg|team|services?|ward|icu|emergenc|management)|polytrauma|accident and emergency|(emergency|advanced) trauma|level (i|ii|iii|1|2|3) trauma', 'A trauma unit or trauma service is stated'),
 ('trauma','Trauma care','indirect', r'\btrauma', 'Only the word trauma, e.g. orthopaedics and trauma'),
 ('obstetric_delivery','Childbirth / delivery','direct', r'labou?r room|normal deliver|(?<!instrument |drug |home |medicine |medicines |care |service |healthcare |oxygen |report |doorstep |free |food |anesthesia |anaesthesia |gas |dose )deliver(y|ies)(?! system| of (care|service|health|medic|drug|report))|maternity (ward|home|care|service|hospital|unit)|childbirth|obstetric (care|service|ward|unit|emergenc)', 'Deliveries or a labour room are stated'),
 ('obstetric_delivery','Childbirth / delivery','indirect', r'obstetric|maternity', 'Only an obstetrics or maternity label'),
 ('c_section','C-section','direct', r'\bc section|c-section|caesarean|cesarean|\blscs\b', 'Caesarean section is stated'),
 ('nicu','Newborn intensive care (NICU)','direct', r'\bnicu\b|neonatal intensive|neonatal icu', 'A NICU is stated'),
 ('nicu','Newborn intensive care (NICU)','indirect', r'\bsncu\b|special newborn care|neonatolog|newborn (care|stabili)|\bnbsu\b', 'Newborn care below NICU level, or a neonatology label'),
 ('dialysis','Dialysis','direct', r'dialysis|\bcrrt\b', 'Dialysis is stated'),
 ('icu','Intensive care (ICU)','direct', r'(?<!neonatal |pediatric |paediatric |newborn )(\bicu\b|intensive care)|\biccu\b|\bmicu\b|\bsicu\b|\bccu\b|\bhdu\b|critical care', 'An adult ICU or critical care is stated (neonatal and paediatric ICU are separate services)'),
 ('icu','Intensive care (ICU)','indirect', r'ventilator', 'A ventilator is listed, no ICU wording'),
 ('ct','CT scan','direct', r'(?<!dental |pet |pet/)\bct\b(?! university| simulator)|(?<!cone beam )computed tomograph|\bcect\b|\bhrct\b|cat scan', 'CT is stated (cone beam / dental CBCT, PET-CT and CT simulators are not counted)'),
 ('mri','MRI','direct', r'\bmri\b|magnetic resonance', 'MRI is stated'),
 ('cardiac_cath','Cardiac cath lab','direct', r'cath ?lab|catheteri[sz]ation lab|angioplasty|(?<!ct )coronary angiograph|\bptca\b|\bpci\b', 'A cath lab or angioplasty is stated'),
 ('cardiac_cath','Cardiac cath lab','indirect', r'interventional cardiolog|cardiac catheteri', 'Interventional cardiology label only'),
 ('stroke','Stroke care','direct', r'stroke (unit|cent(re|er)|care|ward|management|treatment|team|ready|program|thromboly)|thrombectomy.{0,30}stroke|stroke.{0,30}thrombectomy|acute stroke|brain attack', 'A stroke unit or acute stroke treatment is stated'),
 ('stroke','Stroke care','indirect', r'(?<!heat )\bstroke\b|thrombolysis|thrombectomy|neuro ?intervention', 'Stroke or thrombolysis mentioned without a unit or service'),
 ('blood_bank','Blood bank','direct', r'blood bank|blood cent(re|er)|blood component', 'A blood bank is stated'),
 ('blood_bank','Blood bank','indirect', r'blood storage|blood transfusion', 'Blood storage or transfusion only, not a blood bank'),
 ('burns','Burns care','direct', r'burns? (unit|ward|care|cent(re|er)|icu|management|surg|treatment|injur)', 'A burns unit or burns treatment is stated'),
 ('burns','Burns care','indirect', r'\bburns?\b', 'Only the word burn(s)'),
 ('paediatrics','Paediatrics','direct', r'pa?ediatric (ward|icu|intensive|emergenc|surg|care|unit|department|opd|services?|clinic|hospital)|\bpicu\b|children s hospital|child ?care|pa?ediatrician', 'A paediatric service or paediatrician is stated'),
 ('paediatrics','Paediatrics','indirect', r'pa?ediatric|child (health|specialist)', 'Only a paediatrics label')
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. `facility_service`
# MAGIC Text read: the four list fields in `vf_silver.facility_claims`, each sentence of the description, and the facility name.
# MAGIC
# MAGIC Rules that keep it conservative:
# MAGIC - A claim with **negating wording** ("no MRI facility on site", "CT is outsourced", "does not provide emergency services") is never counted as a claim. It is kept and shown as `negating_quote`.
# MAGIC - `claim_strength`: **strong** = direct wording in 2+ different fields; **moderate** = direct wording in one field; **weak** = only a specialty tag or indirect wording. It drops one step when the record's quality score is under 0.7 or when the same record also has negating text.
# MAGIC - `hours_claim` is `24x7` only when a sentence that directly states the service also says 24x7 / 24 hours / round the clock. That sentence is kept in `hours_quote`.
# MAGIC - `availability_status` is always `unknown`. Nothing here knows whether a bed, machine or doctor is available now.
# MAGIC - The verification columns are filled in step 2.

# COMMAND ----------

NEG_PRE  = r"\b(no(?! ?\d)|not(?! only)|without|lacks?|cannot|non functional|doesn t|don t)\b.{0,40}"
NEG_POST = r".{0,40}\b(not available|unavailable|outsourced|not explicit|not listed|not provided|not offered|is not|are not|not functional)\b"
H24      = r"24 ?(/|x|\*)? ?7|24 ?h(ou)?rs?|round the clock|day and night"
DAYTIME  = r"day ?time only|opd hours only|day ?care only"

geo_join = "LEFT JOIN workspace.vf_silver.facility_geo_quality gq ON gq.facility_id = f.facility_id" if HAS_GEOQ else ""
geo_flag = "gq.geo_flag" if HAS_GEOQ else """CASE WHEN f.latitude IS NULL OR f.longitude IS NULL THEN 'missing'
            WHEN f.coords_invalid THEN 'invalid'
            WHEN f.coords_state_mismatch OR f.coords_far_from_any_pincode THEN 'suspect'
            ELSE 'good' END"""
geo_src  = "'vf_silver.facility_geo_quality (notebook 07)'" if HAS_GEOQ else "'vf_silver.facilities flags (notebook 07 table not present)'"
dup_join = """LEFT JOIN (SELECT facility_id, min(group_id) AS group_id, min(confidence) AS confidence FROM workspace.vf_silver.facility_duplicate_groups GROUP BY facility_id) dg
              ON dg.facility_id = f.facility_id""" if HAS_DUPS else ""
dup_col  = "CAST(dg.group_id AS STRING)" if HAS_DUPS else "CAST(NULL AS STRING)"
dup_conf = "dg.confidence" if HAS_DUPS else "CAST(NULL AS STRING)"

spark.sql(r"""
CREATE OR REPLACE TABLE workspace.vf_gold.facility_service
COMMENT 'One row per facility and service: claim, supporting quote, claim strength, verification status and what is unknown. Primary input for ranking and explanation cards.'
AS
WITH rules AS (SELECT * FROM workspace.vf_gold.facility_service_rules),
claims AS (
  SELECT facility_id, source_field, norm_text AS norm, raw_text AS raw FROM workspace.vf_silver.facility_claims
  UNION ALL
  SELECT facility_id, 'description', lower(trim(regexp_replace(s, '[^A-Za-z0-9/]+', ' '))), left(trim(s), 400)
  FROM (SELECT facility_id, explode(split(description, '(?<=[.!?;])\\s+')) AS s
        FROM workspace.vf_silver.facilities WHERE description IS NOT NULL)
  WHERE length(trim(s)) > 2
  UNION ALL
  SELECT facility_id, 'name', lower(trim(regexp_replace(name, '[^A-Za-z0-9/]+', ' '))), name
  FROM workspace.vf_silver.facilities WHERE name IS NOT NULL
),
hits0 AS (
  SELECT c.facility_id, r.service, r.strength, c.source_field, c.raw, c.norm,
         regexp_extract(c.norm, concat('(?:', r.pattern, ')'), 0) AS matched,
         c.norm rlike concat('__NEG_PRE__(?:', r.pattern, ')|(?:', r.pattern, ')__NEG_POST__') AS neg,
         c.norm rlike '__H24__' AS h24,
         c.norm rlike '__DAYTIME__' AS daytime,
         CASE c.source_field WHEN 'equipment' THEN 1 WHEN 'procedure' THEN 2 WHEN 'capability' THEN 3
                             WHEN 'description' THEN 4 WHEN 'name' THEN 5 ELSE 6 END AS field_rank
  FROM claims c JOIN rules r ON c.norm rlike r.pattern
),
hits AS (
  SELECT *, CASE WHEN max(neg) OVER (PARTITION BY facility_id, service, source_field, raw) THEN 9
                 WHEN strength = 'direct' AND source_field <> 'specialty' THEN 1
                 WHEN strength = 'direct' THEN 2
                 ELSE 3 END AS tier
  FROM hits0
),
agg AS (
  SELECT facility_id, service,
    min(tier) FILTER (WHERE tier < 9) AS min_tier,
    min_by(named_struct('f', source_field, 'q', raw, 'm', matched), tier * 10 + field_rank) FILTER (WHERE tier < 9) AS best,
    count(DISTINCT CASE WHEN tier = 1 THEN source_field END) AS n_direct_fields,
    count(DISTINCT CASE WHEN tier < 9 THEN raw END) AS n_claims,
    array_sort(collect_set(CASE WHEN tier < 9 THEN source_field END)) AS fields,
    slice(sort_array(collect_set(CASE WHEN tier < 9 THEN named_struct('rank', tier * 10 + field_rank, 'source_field', source_field, 'quote', raw) END)), 1, 5) AS quotes,
    min_by(raw, field_rank) FILTER (WHERE tier = 9) AS negating_quote,
    min_by(raw, field_rank) FILTER (WHERE h24 AND tier <= 2) AS h24_quote,
    min_by(raw, field_rank) FILTER (WHERE daytime AND tier <= 2) AS daytime_quote
  FROM hits GROUP BY facility_id, service
),
grid AS (
  SELECT f.facility_id, s.service, s.service_label
  FROM workspace.vf_silver.facilities f CROSS JOIN (SELECT DISTINCT service, service_label FROM rules) s
),
base AS (
  SELECT g.*, a.* EXCEPT (facility_id, service),
    CASE WHEN a.min_tier = 1 AND a.n_direct_fields >= 2 THEN 3 WHEN a.min_tier = 1 THEN 2 WHEN a.min_tier IN (2, 3) THEN 1 ELSE 0 END AS s0
  FROM grid g LEFT JOIN agg a ON a.facility_id = g.facility_id AND a.service = g.service
)
SELECT
  b.facility_id, f.name, f.state, f.district, f.facility_type, fl.level_kind,
  b.service, b.service_label,
  fl.care_level,
  CASE WHEN b.min_tier IS NULL THEN NULL
       WHEN b.service IN ('obstetric_delivery', 'c_section') THEN fl.obstetric_level
       WHEN b.service = 'nicu' THEN fl.newborn_level
       WHEN b.service IN ('trauma', 'emergency') THEN fl.trauma_level
       WHEN b.service = 'dialysis' THEN fl.dialysis_level
       WHEN b.service = 'blood_bank' THEN CASE WHEN b.min_tier <= 2 THEN 'Blood bank stated' ELSE 'Blood storage or transfusion only' END
  END AS service_specific_level,
  b.min_tier IS NOT NULL AS claim_detected,
  CASE WHEN b.min_tier = 1 THEN 'claimed'
       WHEN b.min_tier = 2 THEN 'claimed_tag_only'
       WHEN b.min_tier = 3 THEN 'claimed_indirect'
       WHEN b.negating_quote IS NOT NULL THEN 'negated_or_qualified'
       ELSE 'not_found' END AS claim_status,
  CASE WHEN b.s0 = 0 THEN 'none'
       ELSE element_at(array('weak', 'moderate', 'strong'),
            greatest(1, b.s0 - CAST(coalesce(f.quality_score, 1) < 0.7 AS INT) - CAST(b.negating_quote IS NOT NULL AS INT))) END AS claim_strength,
  CASE WHEN b.min_tier = 1 AND b.n_direct_fields >= 2 THEN 'direct wording in 2+ fields'
       WHEN b.min_tier = 1 THEN 'direct wording in one field'
       WHEN b.min_tier = 2 THEN 'specialty tag only'
       WHEN b.min_tier = 3 THEN 'indirect wording only'
       WHEN b.negating_quote IS NOT NULL THEN 'only negating or qualified text found'
       ELSE 'no text found' END AS claim_basis,
  b.best.m AS supporting_signal,
  b.best.q AS supporting_quote,
  b.best.f AS source_field,
  CASE WHEN b.min_tier IS NULL THEN NULL WHEN b.best.f IN ('description', 'name') THEN 'extracted_text' ELSE 'original' END AS service_evidence_source,
  CASE WHEN b.min_tier IS NULL THEN NULL WHEN b.best.f IN ('description', 'name') THEN concat('vf_silver.facilities.', b.best.f)
       ELSE concat('vf_silver.facility_claims.', b.best.f) END AS source_ref,
  coalesce(b.n_claims, 0) AS supporting_claims,
  b.fields AS supporting_fields,
  b.quotes AS all_quotes,
  b.negating_quote,
  CASE WHEN b.min_tier IS NULL THEN NULL
       WHEN b.service IN ('icu', 'nicu', 'c_section', 'trauma', 'cardiac_cath', 'burns', 'stroke')
            AND (f.facility_type IN ('dentist', 'pharmacy', 'doctor') OR fl.level_kind IN ('Diagnostic centre', 'Pharmacy', 'Dental clinic')
                 OR (f.facility_type = 'clinic' AND coalesce(fl.care_level, 0) <= 2))
         THEN 'unusual for this facility type'
       WHEN f.specialty_doctor_mismatch THEN 'record lists 30+ specialties for one doctor'
  END AS plausibility_flag,
  fl.level_confidence,
  f.quality_score,
  -- verification columns: placeholders here, filled from facility_verification in step 2
  CASE WHEN b.min_tier IS NOT NULL THEN 'inferred' ELSE 'unknown' END AS verification_status,
  CAST(NULL AS STRING) AS verification_source,
  CAST(NULL AS DATE)   AS verification_date,
  false AS contradiction_flag,
  'unknown' AS facility_existence_status,
  CAST(NULL AS STRING) AS facility_existence_source,
  f.latitude, f.longitude,
  __GEO_FLAG__ AS location_quality_flag,
  __GEO_SRC__  AS location_quality_source,
  __DUP_COL__  AS duplicate_group_id,
  __DUP_CONF__ AS duplicate_confidence,
  '__LEVEL_TABLE__' AS care_level_source,
  CASE WHEN f.official_phone IS NOT NULL OR size(f.phone_numbers) > 0 THEN 'listed_unverified' ELSE 'missing' END AS contact_verification_status,
  f.official_phone AS contact_phone,
  'unknown' AS availability_status,
  CASE WHEN b.h24_quote IS NOT NULL THEN '24x7' WHEN b.daytime_quote IS NOT NULL THEN 'daytime' ELSE 'unknown' END AS hours_claim,
  coalesce(b.h24_quote, b.daytime_quote) AS hours_quote
FROM base b
JOIN workspace.vf_silver.facilities f ON f.facility_id = b.facility_id
LEFT JOIN __LEVEL_TABLE__ fl ON fl.facility_id = b.facility_id
__GEO_JOIN__
__DUP_JOIN__
""".replace("__NEG_PRE__", NEG_PRE.replace("\\", "\\\\")).replace("__NEG_POST__", NEG_POST.replace("\\", "\\\\"))
   .replace("__H24__", H24.replace("\\", "\\\\")).replace("__DAYTIME__", DAYTIME)
   .replace("__GEO_FLAG__", geo_flag).replace("__GEO_SRC__", geo_src).replace("__DUP_COL__", dup_col).replace("__DUP_CONF__", dup_conf)
   .replace("__LEVEL_TABLE__", LEVEL_TABLE).replace("__GEO_JOIN__", geo_join).replace("__DUP_JOIN__", dup_join))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. `facility_verification` (rerun from here when new outside sources land)
# MAGIC One row per facility, service and source. `is_primary` marks the single row per facility and service that `facility_service` shows.
# MAGIC
# MAGIC | Status | Meaning | What can grant it |
# MAGIC |---|---|---|
# MAGIC | `unknown` | No claim in the facility's text and no outside source | default |
# MAGIC | `inferred` | Claimed in the facility's own text only | step 1 |
# MAGIC | `externally_supported` | An outside source independently lists this service or speciality | OSM / directory speciality fields |
# MAGIC | `verified` | A source that actually establishes this specific thing | only the sources in the table below |
# MAGIC | `contradicted` | An outside source disagrees | type or tier mismatch |
# MAGIC
# MAGIC Sources allowed to grant `verified`, and only for the thing named: official CGHS list -> `_cghs_empanelment`; official PM-JAY list -> `_pmjay_empanelment` (neither is loaded yet).
# MAGIC
# MAGIC **Task D sources (2026-11-22) grant `externally_supported` at most, never `verified`:** an accepted eRaktKosh match whose record is a Blood Bank supports `blood_bank` as a directory listing only (not stock, not working today); an accepted NABH match whose record shows accreditation supports `_nabh_accreditation`, with validity unknown because the directory has no dates and the match is name + pincode. NABH certification, empanelment and unclear entries go to `_nabh_listing`. A withdrawn or suspended NABH record against a facility that claims NABH is `contradicted`. Wikidata supports existence only.
# MAGIC None of these verify a clinical capability other than the one named, and none say anything about availability today.
# MAGIC
# MAGIC Rows whose service starts with `_` are about the facility, not a clinical service: `_facility_existence`, `_facility_type`, and the scheme / accreditation badges.
# MAGIC
# MAGIC `matched_at` is when this row was computed. It is **not** a verification date. `source_date` stays null unless the source itself gives a date.

# COMMAND ----------

# Notebook 10 marks each match accepted / ambiguous / rejected. Only accepted matches are used.
d_cols = [r[0] for r in spark.sql("DESCRIBE TABLE workspace.vf_silver.ext_facility_matches").collect()] if HAS_D else []
d_filter = "lower(CAST(match_status AS STRING)) LIKE 'accepted%'" if "match_status" in d_cols else "lower(coalesce(CAST(match_method AS STRING), '')) NOT LIKE '%ambig%'"
d_km  = "CAST(distance_km AS DOUBLE)" if "distance_km" in d_cols else "CAST(NULL AS DOUBLE)"
d_ref = "CAST(source_ref AS STRING)" if "source_ref" in d_cols else "CAST(NULL AS STRING)"
# What the matched outside record actually says. A match only tells us WHICH record; these fields say what that
# record establishes. Read from the Task D bronze tables when they exist; otherwise left null (treated as "details unknown").
NABH_STATUS_RAW = r"trim(regexp_replace(get_json_object(n.raw_json, '$.status_raw'), '[\\s'']+', ' '))"
nabh_kind = r"""CASE
      WHEN lower(get_json_object(n.raw_json, '$.status')) = 'withdrawn' OR lower(__RAW__) LIKE '%withdraw%' THEN 'withdrawn'
      WHEN lower(__RAW__) LIKE '%suspen%' THEN 'suspended'
      WHEN lower(get_json_object(n.raw_json, '$.status')) IN ('accredited', 'renewed')
           AND get_json_object(n.raw_json, '$.identifier_prefix') IN ('H', 'SHCO') THEN 'accredited_hospital_programme'
      WHEN lower(get_json_object(n.raw_json, '$.status')) IN ('accredited', 'renewed') THEN 'accredited_other_programme'
      WHEN lower(get_json_object(n.raw_json, '$.status')) = 'certified' THEN 'certification_not_accreditation'
      WHEN lower(get_json_object(n.raw_json, '$.status')) = 'empanelled' THEN 'empanelment_not_accreditation'
      WHEN n.source_record_id IS NOT NULL THEN 'status_unclear' END""".replace("__RAW__", NABH_STATUS_RAW)
nabh_label = r"""concat_ws('; ', concat('programme: ', coalesce(get_json_object(n.raw_json, '$.programme'), concat('not stated (id prefix ', get_json_object(n.raw_json, '$.identifier_prefix'), ')'))),
                 concat('directory status: ', __RAW__), concat('no. ', get_json_object(n.raw_json, '$.accreditation_no')))""".replace("__RAW__", NABH_STATUS_RAW)
d_kind  = "CASE WHEN m.source = 'nabh' THEN %s WHEN m.source = 'eraktkosh' THEN %s END" % (
    nabh_kind if HAS_NABH_RAW else "CAST(NULL AS STRING)",
    "get_json_object(e.raw_json, '$.centre_type')" if HAS_ERAKT_RAW else "CAST(NULL AS STRING)")
d_label = "CASE WHEN m.source = 'nabh' THEN %s WHEN m.source = 'eraktkosh' THEN %s END" % (
    nabh_label if HAS_NABH_RAW else "CAST(NULL AS STRING)",
    "concat_ws(', ', get_json_object(e.raw_json, '$.centre_type'), get_json_object(e.raw_json, '$.category'), concat('directory code ', get_json_object(e.raw_json, '$.hospital_code')))" if HAS_ERAKT_RAW else "CAST(NULL AS STRING)")
d_ref_parts = []
if HAS_NABH_RAW:  d_ref_parts += ["get_json_object(n.raw_json, '$.certificate_url')", "get_json_object(n.raw_json, '$.source_url')"]
if HAS_ERAKT_RAW: d_ref_parts += ["get_json_object(e.raw_json, '$.source_url')"]
if HAS_EXT_CLEAN: d_ref_parts += ["CASE WHEN c.source_ref LIKE 'http%' THEN c.source_ref END"]
d_ref_parts += [d_ref]
d_joins = ""
if HAS_NABH_RAW:  d_joins += "\n  LEFT JOIN workspace.vf_bronze.ext_nabh_raw n ON m.source = 'nabh' AND n.source_record_id = m.source_record_id"
if HAS_ERAKT_RAW: d_joins += "\n  LEFT JOIN workspace.vf_bronze.ext_eraktkosh_raw e ON m.source = 'eraktkosh' AND e.source_record_id = m.source_record_id"
if HAS_EXT_CLEAN: d_joins += "\n  LEFT JOIN workspace.vf_silver.ext_facilities_clean c ON c.source = m.source AND c.source_record_id = m.source_record_id"
d_status = "CAST(m.match_status AS STRING)" if "match_status" in d_cols else "'accepted'"
d_union = r"""
  UNION ALL
  SELECT m.facility_id, CAST(m.source AS STRING), CAST(m.source_record_id AS STRING), __D_KIND__, CAST(NULL AS INT), __D_LABEL__,
         CAST(NULL AS STRING), __D_KM__, CAST(m.match_score AS DOUBLE),
         concat('notebook 10 match: ', CAST(m.match_method AS STRING), ' (', __D_STATUS__, ')'), 'vf_silver.ext_facility_matches', coalesce(__D_REF__)
  FROM workspace.vf_silver.ext_facility_matches m__D_JOINS__
  WHERE m.facility_id IS NOT NULL AND __D_FILTER__
""".replace("__D_KIND__", d_kind).replace("__D_LABEL__", d_label).replace("__D_KM__", d_km.replace("distance_km", "m.distance_km")) \
   .replace("__D_STATUS__", d_status).replace("__D_REF__", ", ".join(d_ref_parts).replace("CAST(source_ref", "CAST(m.source_ref")) \
   .replace("__D_JOINS__", d_joins).replace("__D_FILTER__", d_filter.replace("match_status", "m.match_status").replace("match_method", "m.match_method")) if HAS_D else ""

spark.sql(r"""
CREATE OR REPLACE TABLE workspace.vf_gold.facility_verification
COMMENT 'One row per facility, service and outside source. matched_at is the time the row was computed, not a verification date.'
AS
WITH m0 AS (
  SELECT facility_id, source, ext_id, ext_kind, ext_tier, ext_operator, ext_specialities, km, name_sim,
         'name word overlap + distance (notebook 06)' AS method, 'vf_gold.facility_external_match' AS via, CAST(NULL AS STRING) AS ref
  FROM workspace.vf_gold.facility_external_match
  __D_UNION__
),
m AS (  -- one row per facility and source: the best-scoring match
  SELECT facility_id, source, max_by(named_struct('ext_id', ext_id, 'ext_kind', ext_kind, 'ext_tier', ext_tier, 'ext_label', ext_operator, 'ext_specialities', ext_specialities,
                                                  'km', km, 'score', name_sim, 'method', method, 'via', via, 'ref', ref), coalesce(name_sim, 0)) AS x
  FROM m0 GROUP BY facility_id, source
),
src AS (
  SELECT facility_id, source, x.*,
    coalesce(CASE WHEN lower(source) LIKE '%openstreetmap%' OR lower(source) = 'osm' THEN concat('https://www.openstreetmap.org/', x.ext_id) END, CASE WHEN x.ref LIKE 'http%' THEN x.ref END) AS source_url,
    CASE WHEN lower(source) LIKE '%openstreetmap%' OR lower(source) = 'osm' THEN 'crowd-sourced map'
         WHEN lower(source) RLIKE 'wikidata' THEN 'crowd-sourced knowledge base'
         WHEN lower(source) RLIKE 'erakt' THEN 'eraktkosh'
         WHEN lower(source) RLIKE 'cghs' THEN 'cghs'
         WHEN lower(source) RLIKE 'pm ?-?_?jay|ayushman' THEN 'pmjay'
         WHEN lower(source) RLIKE 'nabh' THEN 'nabh'
         ELSE 'directory' END AS source_class
  FROM m
),
chk AS (SELECT facility_id, level_check, operator_check, external_evidence FROM workspace.vf_gold.facility_external_check),
fs AS (SELECT facility_id, service, claim_status, claim_detected FROM workspace.vf_gold.facility_service),
fl AS (SELECT facility_id, nabh_claimed, pmjay_claimed, cghs_claimed FROM __LEVEL_TABLE__),
ext_rows AS (
  -- a) the facility exists in an outside source (existence only, never a service)
  SELECT s.facility_id, '_facility_existence' AS service, CAST(NULL AS STRING) AS claim_status, 'externally_supported' AS verification_status,
         s.source, s.ext_id, s.source_url, s.score, s.method, s.km,
         concat('Matched to a ', s.source_class, ' record', coalesce(concat(' ', round(s.km, 2), ' km away'), ''), ' (', s.method, '). Supports that the facility exists; says nothing about any service.') AS notes,
         false AS contradiction_flag
  FROM src s
  UNION ALL
  -- b) the outside type or tier agrees or disagrees (notebook 06 result)
  SELECT s.facility_id, '_facility_type', NULL,
         CASE WHEN c.level_check IN ('conflicts', 'type differs') THEN 'contradicted' ELSE 'externally_supported' END,
         s.source, s.ext_id, s.source_url, s.score, s.method, s.km,
         concat('Outside check: ', c.level_check, coalesce(concat('; operator ', c.operator_check), ''), '. ', coalesce(c.external_evidence, '')),
         c.level_check IN ('conflicts', 'type differs') OR coalesce(c.operator_check = 'differs', false)
  FROM src s JOIN chk c ON c.facility_id = s.facility_id
  WHERE s.via = 'vf_gold.facility_external_match'
    AND (c.level_check IN ('confirms', 'close (1 rung apart)', 'conflicts', 'type differs') OR c.operator_check = 'differs')
  UNION ALL
  -- c) the outside record's own speciality text names the service: support, never verification
  SELECT s.facility_id, r.service, f.claim_status, 'externally_supported',
         s.source, s.ext_id, s.source_url, s.score, s.method, s.km,
         concat('Outside record lists speciality "', left(s.ext_specialities, 120), '". Independent mention, not an official designation.'),
         false
  FROM src s
  JOIN (SELECT service, concat_ws('|', collect_list(pattern)) AS pattern FROM workspace.vf_gold.facility_service_rules WHERE strength = 'direct' GROUP BY service) r
    ON lower(regexp_replace(s.ext_specialities, '[^A-Za-z0-9/]+', ' ')) RLIKE r.pattern
  JOIN fs f ON f.facility_id = s.facility_id AND f.service = r.service
  WHERE s.ext_specialities IS NOT NULL AND s.source_class <> 'eraktkosh'
  UNION ALL
  -- d) sources that are about one specific thing.
  --    A match says which outside record this is; what it establishes comes from that record's own fields.
  --    NABH and eRaktKosh never grant 'verified' here: the NABH file has no validity dates and its matches are
  --    name + pincode identity candidates; an eRaktKosh row is a directory listing, not proof the blood bank works today.
  SELECT s.facility_id,
         CASE s.source_class
              WHEN 'eraktkosh' THEN CASE WHEN lower(s.ext_kind) = 'blood bank' THEN 'blood_bank'
                                         WHEN s.ext_kind IS NULL THEN '_blood_centre_listing' ELSE '_blood_storage_listing' END
              WHEN 'cghs' THEN '_cghs_empanelment' WHEN 'pmjay' THEN '_pmjay_empanelment'
              WHEN 'nabh' THEN CASE WHEN s.ext_kind LIKE 'accredited%' THEN '_nabh_accreditation'
                                    WHEN s.ext_kind IN ('withdrawn', 'suspended') AND coalesce(l.nabh_claimed, false) THEN '_nabh_accreditation'
                                    ELSE '_nabh_listing' END END,
         CASE s.source_class WHEN 'eraktkosh' THEN CASE WHEN lower(s.ext_kind) = 'blood bank' THEN f.claim_status END
              WHEN 'cghs' THEN CASE WHEN l.cghs_claimed THEN 'claimed' ELSE 'not_found' END
              WHEN 'pmjay' THEN CASE WHEN l.pmjay_claimed THEN 'claimed' ELSE 'not_found' END
              WHEN 'nabh' THEN CASE WHEN l.nabh_claimed THEN 'claimed' ELSE 'not_found' END END,
         CASE WHEN s.source_class IN ('cghs', 'pmjay') THEN 'verified'
              WHEN s.source_class = 'nabh' AND s.ext_kind IN ('withdrawn', 'suspended') AND coalesce(l.nabh_claimed, false) THEN 'contradicted'
              ELSE 'externally_supported' END,
         s.source, s.ext_id, s.source_url, s.score, s.method, s.km,
         CASE
           WHEN s.source_class = 'eraktkosh' AND lower(s.ext_kind) = 'blood bank' THEN
             concat('Listed as a Blood Bank in the official eRaktKosh blood-centre directory (', coalesce(s.ext_label, 'details not available'), '). Directory listing only: it does not show that the blood bank is working today, what is in stock, or that its licence is current.')
           WHEN s.source_class = 'eraktkosh' AND s.ext_kind IS NOT NULL THEN
             concat('Listed in the eRaktKosh directory as "', s.ext_kind, '", which is a blood storage unit, not a blood bank (', coalesce(s.ext_label, ''), '). Does not support a blood bank claim.')
           WHEN s.source_class = 'eraktkosh' THEN 'Matched to an eRaktKosh directory record, but the centre type is not available, so it is not counted as blood bank evidence.'
           WHEN s.source_class = 'cghs' THEN 'Listed in the official CGHS empanelled hospital file. Does not show which specialties or cities are covered, or that it is current.'
           WHEN s.source_class = 'pmjay' THEN 'Listed in the official PM-JAY empanelment file. Does not guarantee free treatment or a given package.'
           WHEN s.source_class = 'nabh' AND s.ext_kind = 'accredited_hospital_programme' THEN
             concat('The matched NABH directory record shows hospital accreditation (', s.ext_label, '). The directory gives no validity dates, so whether it is current is unknown, and the match is by name and pincode only. Accreditation is not proof of any specific clinical capability.')
           WHEN s.source_class = 'nabh' AND s.ext_kind = 'accredited_other_programme' THEN
             concat('The matched NABH directory record shows accreditation under a programme that is not the general hospital programme (', s.ext_label, '). No validity dates; match by name and pincode only. Not proof of any specific clinical capability.')
           WHEN s.source_class = 'nabh' AND s.ext_kind IN ('withdrawn', 'suspended') THEN
             concat('The matched NABH directory record shows accreditation ', s.ext_kind, ' (', s.ext_label, ').', CASE WHEN coalesce(l.nabh_claimed, false) THEN ' The facility''s own text claims NABH accreditation, so the two disagree.' ELSE '' END, ' Match by name and pincode only.')
           WHEN s.source_class = 'nabh' AND s.ext_kind = 'certification_not_accreditation' THEN
             concat('The matched NABH directory record is a certification, not accreditation (', s.ext_label, '). No validity dates; match by name and pincode only.')
           WHEN s.source_class = 'nabh' AND s.ext_kind = 'empanelment_not_accreditation' THEN
             concat('The matched NABH directory record is an empanelment entry, not accreditation (', s.ext_label, '). No validity dates; match by name and pincode only.')
           WHEN s.source_class = 'nabh' THEN concat('Matched to an NABH directory record whose status is unclear or not available (', coalesce(s.ext_label, 'no details'), '). Accreditation status: unknown.') END,
         s.source_class = 'nabh' AND s.ext_kind IN ('withdrawn', 'suspended') AND coalesce(l.nabh_claimed, false)
  FROM src s
  LEFT JOIN fs f ON f.facility_id = s.facility_id AND f.service = 'blood_bank'
  LEFT JOIN fl l ON l.facility_id = s.facility_id
  WHERE s.source_class IN ('eraktkosh', 'cghs', 'pmjay', 'nabh')
),
baseline AS (
  -- every facility and clinical service, from the facility's own text
  SELECT facility_id, service, claim_status,
         CASE WHEN claim_detected THEN 'inferred' ELSE 'unknown' END AS verification_status,
         CAST(NULL AS STRING) AS source, CAST(NULL AS STRING) AS ext_id, CAST(NULL AS STRING) AS source_url,
         CAST(NULL AS DOUBLE) AS score, CAST(NULL AS STRING) AS method, CAST(NULL AS DOUBLE) AS km,
         CASE WHEN claim_detected THEN 'Rests only on the facility''s own listed text. No outside source has checked this service.'
              WHEN claim_status = 'negated_or_qualified' THEN 'The facility''s text mentions this service only in a negating or qualified way.'
              ELSE 'No mention found and no outside source.' END AS notes,
         false AS contradiction_flag
  FROM fs
  UNION ALL
  -- scheme and accreditation badges the facility claims for itself
  SELECT facility_id, svc, 'claimed', 'inferred', NULL, NULL, NULL, NULL, NULL, NULL,
         'Self-claimed in the facility''s text. Not checked against the official list.', false
  FROM (SELECT facility_id, explode(filter(array(CASE WHEN nabh_claimed THEN '_nabh_accreditation' END,
                                                 CASE WHEN pmjay_claimed THEN '_pmjay_empanelment' END,
                                                 CASE WHEN cghs_claimed THEN '_cghs_empanelment' END), x -> x IS NOT NULL)) AS svc FROM fl)
),
allrows AS (SELECT * FROM baseline UNION ALL SELECT * FROM ext_rows WHERE service IS NOT NULL)
SELECT
  facility_id, service, claim_status, verification_status,
  source AS verification_source,
  ext_id AS source_record_id,
  source_url,
  CAST(NULL AS DATE) AS source_date,          -- no loaded source carries a per-record date yet
  CASE WHEN source IS NOT NULL THEN current_timestamp() END AS matched_at,
  score AS match_score,
  CASE WHEN source IS NOT NULL THEN concat(method, coalesce(concat(', ', round(km, 2), ' km'), '')) END AS match_method,
  notes AS verification_notes,
  contradiction_flag,
  row_number() OVER (PARTITION BY facility_id, service
     ORDER BY CASE verification_status WHEN 'verified' THEN 1 WHEN 'contradicted' THEN 2 WHEN 'externally_supported' THEN 3 WHEN 'inferred' THEN 4 ELSE 5 END,
              coalesce(score, 0) DESC, source) = 1 AS is_primary
FROM allrows
""".replace("__D_UNION__", d_union).replace("__LEVEL_TABLE__", LEVEL_TABLE))

# COMMAND ----------

# MAGIC %md
# MAGIC ### 2b. Copy the primary verification row back onto `facility_service`

# COMMAND ----------

spark.sql(r"""
MERGE INTO workspace.vf_gold.facility_service t
USING (
  SELECT v.facility_id, v.service, v.verification_status, v.verification_source, v.source_date,
         c.any_contra, e.verification_source AS ex_source
  FROM workspace.vf_gold.facility_verification v
  JOIN (SELECT facility_id, service, bool_or(contradiction_flag) AS any_contra FROM workspace.vf_gold.facility_verification GROUP BY 1, 2) c
    ON c.facility_id = v.facility_id AND c.service = v.service
  LEFT JOIN (SELECT facility_id, verification_source FROM workspace.vf_gold.facility_verification WHERE service = '_facility_existence' AND is_primary) e
    ON e.facility_id = v.facility_id
  WHERE v.is_primary AND v.service NOT LIKE '\\_%'
) s
ON t.facility_id = s.facility_id AND t.service = s.service
WHEN MATCHED THEN UPDATE SET
  t.verification_status = s.verification_status,
  t.verification_source = s.verification_source,
  t.verification_date = s.source_date,
  t.contradiction_flag = s.any_contra,
  t.facility_existence_status = CASE WHEN s.ex_source IS NOT NULL THEN 'externally_supported' ELSE 'unknown' END,
  t.facility_existence_source = s.ex_source
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ### 2c. `facility_service_canonical`: one row per centre and service
# MAGIC Uses `vf_silver.facility_canonical` (notebook 07). Records judged to be the same place are folded into one centre.
# MAGIC The centre keeps the **strongest** claim among its records and lists every record's claim in `claims_by_record`, so the source of each quote stays visible. `evidence_facility_id` says which original record the shown quote came from. Original rows stay in `facility_service`.

# COMMAND ----------

if exists("workspace.vf_silver.facility_canonical"):
    spark.sql(r"""
    CREATE OR REPLACE VIEW workspace.vf_gold.facility_service_canonical
    COMMENT 'One row per centre and service. Near-certain duplicate records are folded into one centre; the strongest claim is shown and every record claim is kept in claims_by_record.'
    AS
    WITH m AS (
      SELECT c.canonical_facility_id, c.merged_group_size, fs.*,
        row_number() OVER (PARTITION BY c.canonical_facility_id, fs.service ORDER BY
            CASE fs.claim_strength WHEN 'strong' THEN 3 WHEN 'moderate' THEN 2 WHEN 'weak' THEN 1 ELSE 0 END DESC,
            CAST(fs.facility_id = c.canonical_facility_id AS INT) DESC, fs.facility_id) AS rn,
        count(CASE WHEN fs.claim_detected THEN 1 END) OVER (PARTITION BY c.canonical_facility_id, fs.service) AS records_claiming,
        max(CASE fs.verification_status WHEN 'verified' THEN 5 WHEN 'contradicted' THEN 4 WHEN 'externally_supported' THEN 3 WHEN 'inferred' THEN 2 ELSE 1 END)
            OVER (PARTITION BY c.canonical_facility_id, fs.service) AS vrank,
        max(CASE WHEN fs.facility_existence_status = 'externally_supported' THEN 1 ELSE 0 END) OVER (PARTITION BY c.canonical_facility_id, fs.service) AS any_exist,
        max(CAST(fs.contradiction_flag AS INT)) OVER (PARTITION BY c.canonical_facility_id, fs.service) AS any_contra,
        max(CASE WHEN fs.hours_claim = '24x7' THEN 1 ELSE 0 END) OVER (PARTITION BY c.canonical_facility_id, fs.service) AS any_24x7,
        collect_list(CASE WHEN fs.claim_detected OR fs.negating_quote IS NOT NULL THEN named_struct('facility_id', fs.facility_id, 'name', fs.name, 'claim_status', fs.claim_status,
            'claim_strength', fs.claim_strength, 'source_field', fs.source_field, 'quote', fs.supporting_quote, 'negating_quote', fs.negating_quote) END)
            OVER (PARTITION BY c.canonical_facility_id, fs.service) AS claims_by_record
      FROM workspace.vf_gold.facility_service fs JOIN workspace.vf_silver.facility_canonical c ON c.facility_id = fs.facility_id
    )
    SELECT
      k.facility_id, k.name, k.state, k.district, k.facility_type, k.level_kind, e.service, e.service_label,
      k.care_level, coalesce(e.service_specific_level, k.service_specific_level) AS service_specific_level,
      e.claim_detected, e.claim_status, e.claim_strength, e.claim_basis, e.supporting_signal, e.supporting_quote, e.source_field,
      e.service_evidence_source, e.source_ref, e.supporting_claims, e.supporting_fields, e.all_quotes, e.negating_quote, e.plausibility_flag,
      k.level_confidence, k.quality_score,
      element_at(array('unknown', 'inferred', 'externally_supported', 'contradicted', 'verified'), e.vrank) AS verification_status,
      CASE WHEN e.vrank > 2 THEN coalesce(e.verification_source, k.verification_source) END AS verification_source,
      e.verification_date,
      e.any_contra = 1 AS contradiction_flag,
      CASE WHEN e.any_exist = 1 THEN 'externally_supported' ELSE 'unknown' END AS facility_existence_status,
      coalesce(k.facility_existence_source, e.facility_existence_source) AS facility_existence_source,
      k.latitude, k.longitude, k.location_quality_flag, k.location_quality_source, k.care_level_source,
      k.contact_verification_status, k.contact_phone, 'unknown' AS availability_status,
      CASE WHEN e.any_24x7 = 1 THEN '24x7' ELSE e.hours_claim END AS hours_claim, e.hours_quote,
      e.facility_id AS evidence_facility_id,
      e.merged_group_size AS merged_records,
      e.records_claiming,
      e.claims_by_record
    FROM m e JOIN workspace.vf_gold.facility_service k ON k.facility_id = e.canonical_facility_id AND k.service = e.service
    WHERE e.rn = 1
    """)
    display(spark.sql("""
    SELECT (SELECT count(DISTINCT facility_id) FROM workspace.vf_gold.facility_service) AS records,
           count(DISTINCT facility_id) AS centres, sum(CAST(claim_detected AS INT)) AS claims_per_centre,
           (SELECT sum(CAST(claim_detected AS INT)) FROM workspace.vf_gold.facility_service) AS claims_per_record
    FROM workspace.vf_gold.facility_service_canonical"""))
else:
    print("vf_silver.facility_canonical not found (run notebook 07 first); facility_service_canonical not built")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Tests

# COMMAND ----------

canon_checks = r"""
  UNION ALL SELECT 15, 'canonical view: one row per centre x service',
         (SELECT abs(count(*) - count(DISTINCT facility_id, service)) + abs(count(DISTINCT facility_id) - (SELECT count(DISTINCT canonical_facility_id) FROM workspace.vf_silver.facility_canonical)) FROM workspace.vf_gold.facility_service_canonical), 'every centre present once per service'
  UNION ALL SELECT 16, 'canonical view: a centre claims a service exactly when one of its records does',
         (SELECT count(*) FROM workspace.vf_gold.facility_service_canonical v
            JOIN (SELECT c.canonical_facility_id, fs.service, bool_or(fs.claim_detected) AS any_claim FROM workspace.vf_gold.facility_service fs
                  JOIN workspace.vf_silver.facility_canonical c ON c.facility_id = fs.facility_id GROUP BY 1, 2) a
              ON a.canonical_facility_id = v.facility_id AND a.service = v.service
          WHERE v.claim_detected <> a.any_claim OR (v.claim_detected AND v.supporting_quote IS NULL)), 'merging never invents or loses a claim'
""" if exists("workspace.vf_gold.facility_service_canonical") else ""

spark.sql(r"""
CREATE OR REPLACE TABLE workspace.vf_gold.facility_service_checks
COMMENT 'Pass/fail tests for facility_service and facility_verification (notebook 09)'
AS
WITH fs AS (SELECT * FROM workspace.vf_gold.facility_service),
     fv AS (SELECT * FROM workspace.vf_gold.facility_verification),
     n  AS (SELECT (SELECT count(*) FROM workspace.vf_silver.facilities) AS fac, (SELECT count(DISTINCT service) FROM workspace.vf_gold.facility_service_rules) AS svc)
SELECT check_name, bad_rows, bad_rows = 0 AS passed, detail, current_timestamp() AS checked_at FROM (
  SELECT 1 AS o, 'one row per facility x service' AS check_name,
         (SELECT abs(count(*) - count(DISTINCT facility_id, service)) + abs(count(*) - (SELECT fac * svc FROM n)) FROM fs) AS bad_rows,
         'rows = facilities x services and no duplicate pairs' AS detail
  UNION ALL SELECT 2, 'every silver facility present, none extra',
         (SELECT abs(count(DISTINCT facility_id) - (SELECT fac FROM n)) FROM fs), 'join did not drop or duplicate facilities'
  UNION ALL SELECT 3, 'no claim without a quote and a matched rule',
         (SELECT count(*) FROM fs WHERE claim_detected AND (supporting_quote IS NULL OR trim(supporting_quote) = '' OR supporting_signal IS NULL OR trim(supporting_signal) = '' OR source_field IS NULL)), 'claim_detected rows carry quote, matched wording and field'
  UNION ALL SELECT 4, 'no quote on rows without a claim',
         (SELECT count(*) FROM fs WHERE NOT claim_detected AND supporting_quote IS NOT NULL), 'no evidence shown for a claim that was not detected'
  UNION ALL SELECT 5, 'negated-only text is never a claim',
         (SELECT count(*) FROM fs WHERE claim_status = 'negated_or_qualified' AND (claim_detected OR claim_strength <> 'none')), 'e.g. "no MRI facility on site"'
  UNION ALL SELECT 6, 'no verified from a map or generic directory',
         (SELECT count(*) FROM fv WHERE verification_status = 'verified' AND lower(coalesce(verification_source, '')) NOT RLIKE 'erakt|cghs|pm ?-?_?jay|ayushman|nabh'), 'OSM and directory matches support existence or type only'
  UNION ALL SELECT 7, 'verified only for the one thing the source establishes',
         (SELECT count(*) FROM fv WHERE verification_status = 'verified' AND service NOT IN ('blood_bank', '_cghs_empanelment', '_pmjay_empanelment', '_nabh_accreditation')), 'no clinical capability verified by a scheme or accreditation list'
  UNION ALL SELECT 8, 'verified rows have a source and record id',
         (SELECT count(*) FROM fv WHERE verification_status IN ('verified', 'externally_supported', 'contradicted') AND (verification_source IS NULL OR source_record_id IS NULL)), 'every outside status is traceable'
  UNION ALL SELECT 9, 'no import time used as a verification date',
         (SELECT count(*) FROM fs WHERE verification_date IS NOT NULL AND verification_source IS NULL) + (SELECT count(*) FROM fv WHERE source_date IS NOT NULL AND CAST(source_date AS DATE) = CAST(matched_at AS DATE)), 'verification_date comes only from a source date'
  UNION ALL SELECT 10, 'exactly one primary verification row per facility x service',
         (SELECT count(*) FROM (SELECT facility_id, service FROM fv GROUP BY 1, 2 HAVING sum(CAST(is_primary AS INT)) <> 1)), ''
  UNION ALL SELECT 11, 'facility_service status equals the primary verification row',
         (SELECT count(*) FROM fs JOIN fv ON fv.facility_id = fs.facility_id AND fv.service = fs.service AND fv.is_primary WHERE fs.verification_status <> fv.verification_status), ''
  UNION ALL SELECT 12, 'availability is always unknown',
         (SELECT count(*) FROM fs WHERE availability_status <> 'unknown'), 'no current-availability source exists'
  UNION ALL SELECT 13, '24x7 only with a claim, hours values valid',
         (SELECT count(*) FROM fs WHERE hours_claim NOT IN ('24x7', 'daytime', 'unknown') OR (hours_claim <> 'unknown' AND NOT claim_detected)), ''
  UNION ALL SELECT 14, 'inferred means a claim, unknown means none',
         (SELECT count(*) FROM fs WHERE (verification_status = 'inferred' AND NOT claim_detected) OR (verification_status = 'unknown' AND claim_detected)), ''
  __CANON_CHECKS__
) ORDER BY o
""".replace("__CANON_CHECKS__", canon_checks))
display(spark.sql("SELECT * FROM workspace.vf_gold.facility_service_checks"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Report

# COMMAND ----------

display(spark.sql(r"""
SELECT service,
  count(*) AS facilities,
  sum(CAST(claim_detected AS INT)) AS claimed,
  sum(CAST(claim_strength = 'strong' AS INT)) AS strong,
  sum(CAST(claim_strength = 'moderate' AS INT)) AS moderate,
  sum(CAST(claim_strength = 'weak' AS INT)) AS weak,
  round(100 * sum(CAST(claim_strength = 'weak' AS INT)) / nullif(sum(CAST(claim_detected AS INT)), 0), 1) AS pct_of_claims_weak,
  sum(CAST(claim_status = 'negated_or_qualified' AS INT)) AS negated_only,
  sum(CAST(claim_detected AND verification_status = 'inferred' AS INT)) AS inferred,
  sum(CAST(claim_detected AND verification_status = 'externally_supported' AS INT)) AS externally_supported,
  sum(CAST(verification_status = 'verified' AS INT)) AS verified,
  sum(CAST(claim_detected AND facility_existence_status = 'externally_supported' AS INT)) AS claimed_and_facility_seen_outside,
  sum(CAST(hours_claim = '24x7' AS INT)) AS says_24x7,
  sum(CAST(plausibility_flag = 'unusual for this facility type' AS INT)) AS unusual_for_type
FROM workspace.vf_gold.facility_service GROUP BY service ORDER BY claimed DESC
"""))

# COMMAND ----------

display(spark.sql(r"""
SELECT service, verification_status, count(*) AS rows, sum(CAST(contradiction_flag AS INT)) AS contradictions
FROM workspace.vf_gold.facility_verification WHERE is_primary GROUP BY 1, 2 ORDER BY 1, 2
"""))
