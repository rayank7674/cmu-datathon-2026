-- Databricks notebook source
-- MAGIC %md
-- MAGIC # 04 Gold: facility care levels
-- MAGIC Assigns every facility a **care level** on India's five-rung referral ladder, plus service-specific levels (newborn, obstetric, trauma, dialysis), each with the evidence that drove it and a confidence.
-- MAGIC
-- MAGIC Framework: *India Facility Care Levels for Routing* (project doc). Reads `workspace.vf_silver`, writes `workspace.vf_gold`. Safe to rerun.
-- MAGIC
-- MAGIC | Rung | Public | Private equivalent |
-- MAGIC | --- | --- | --- |
-- MAGIC | 1 | Sub-centre / Ayushman Arogya Mandir | (none) |
-- MAGIC | 2 | Primary Health Centre | Clinic / polyclinic |
-- MAGIC | 3 | Community Health Centre / FRU | Nursing home / small hospital |
-- MAGIC | 4 | Sub-district / District Hospital | Multispecialty hospital |
-- MAGIC | 5 | Medical college / AIIMS | Super-specialty / tertiary |
-- MAGIC
-- MAGIC Tables: `level_signal_rules` (editable keyword rules), `facility_level_evidence` (one quote per facility and signal), `facility_level` (one row per facility).

-- COMMAND ----------

CREATE SCHEMA IF NOT EXISTS workspace.vf_gold COMMENT 'Search-ready Virtue Foundation tables';

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 1. Signal rules
-- MAGIC Edit this table to tune the levels, then rerun the notebook. Regexes run on the normalised claim text (lowercase, punctuation removed) and on the facility name and description.

-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.vf_gold.level_signal_rules (signal STRING, signal_group STRING, pattern STRING)
COMMENT 'Keyword rules that turn facility text into care-level signals';

-- COMMAND ----------

INSERT INTO workspace.vf_gold.level_signal_rules VALUES
  ('neurosurgery', 'tertiary', 'neuro ?surg|neurosurgeon|spine surg'),
  ('cardiac_surgery', 'tertiary', 'cardiac surg|cardiothoracic|\\bcabg\\b|bypass surg|open heart|valve replace'),
  ('cath_lab', 'tertiary', 'cath ?lab|catheteri[sz]ation lab|angioplasty|coronary angiograph|\\bptca\\b'),
  ('transplant', 'tertiary', '(kidney|renal|liver|heart|cardiac|bone marrow|lung|organ|stem cell|pancrea) transplant|transplant (surg|unit|cent)'),
  ('mri', 'tertiary', '\\bmri\\b|magnetic resonance'),
  ('nicu', 'tertiary', '\\bnicu\\b|neonatal intensive|neonatal icu|level iii nicu'),
  ('radiation_oncology', 'tertiary', 'radiotherap|radiation oncolog|radiation therap|linear accelerator|\\blinac\\b|brachytherap'),
  ('icu', 'district', '\\bicu\\b|\\biccu\\b|intensive care|\\bccu\\b|\\bhdu\\b|critical care'),
  ('ct', 'district', '\\bct\\b|computed tomograph|\\bcect\\b'),
  ('dialysis', 'district', 'dialysis'),
  ('blood_bank', 'district', 'blood bank|blood storage|blood transfusion'),
  ('emergency_24x7', 'district', '24 ?/ ?7 emergency|24x7 emergency|24 hours? emergency|round the clock emergency|emergency (care|services|department|medicine|room|ward)|casualty|accident and emergency'),
  ('sncu', 'district', '\\bsncu\\b|special newborn care'),
  ('ventilator', 'district', 'ventilator'),
  ('c_section', 'block', '\\bc section|caesarean|cesarean|\\blscs\\b'),
  ('operation_theatre', 'block', 'operation theat|operating room|operating theat|\\bot\\b|modular ot|operation room'),
  ('xray', 'block', 'x ?ray|radiograph'),
  ('ultrasound', 'block', 'ultrasound|ultrasonograph|sonograph|\\busg\\b'),
  ('inpatient', 'block', 'inpatient|in patient|\\bipd\\b|admission|bedded|\\bbeds\\b|ward'),
  ('delivery', 'block', 'labou?r room|normal deliver|deliver(y|ies)|maternity (ward|home|care|service|hospital|unit)|childbirth|obstetric (care|service|ward|unit|emergenc)'),
  ('anaesthesia', 'block', 'ana?esthe'),
  ('trauma', 'service', 'trauma'),
  ('newborn_stabilisation', 'service', 'newborn stabili|\\bnbsu\\b'),
  ('burns_unit', 'service', 'burns? (unit|ward|care|centre|center|icu)'),
  ('nabh', 'accreditation', '\\bnabh\\b'),
  ('jci', 'accreditation', '\\bjci\\b|joint commission'),
  ('nabl', 'accreditation', '\\bnabl\\b'),
  ('pmjay', 'scheme', 'pmjay|pm jay|ayushman bharat|ayushman card|ab pmjay'),
  ('cghs', 'scheme', '\\bcghs\\b');

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 2. Evidence: the best quote for each facility and signal
-- MAGIC Prefers equipment, then procedure, capability, specialty, and finally the description, so the quote shown is the most concrete one.

-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.vf_gold.facility_level_evidence
COMMENT 'One row per facility and care-level signal, with the quote that supports it'
AS
WITH claims AS (
  SELECT facility_id, source_field, norm_text, raw_text FROM workspace.vf_silver.facility_claims
  UNION ALL
  SELECT facility_id, 'description', lower(description), left(description, 300) FROM workspace.vf_silver.facilities WHERE description IS NOT NULL
),
hits AS (
  SELECT c.facility_id, r.signal, r.signal_group, c.source_field, c.raw_text,
         CASE c.source_field WHEN 'equipment' THEN 1 WHEN 'procedure' THEN 2 WHEN 'capability' THEN 3 WHEN 'specialty' THEN 4 ELSE 5 END AS field_rank
  FROM claims c JOIN workspace.vf_gold.level_signal_rules r ON c.norm_text rlike r.pattern
)
SELECT facility_id, signal, signal_group,
  min_by(source_field, field_rank) AS source_field,
  min_by(raw_text, field_rank) AS quote,
  count(*) AS supporting_claims,
  count(DISTINCT source_field) AS fields_mentioning
FROM hits GROUP BY facility_id, signal, signal_group;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 3. `facility_level`
-- MAGIC Order of rules:
-- MAGIC 1. **Name says the public tier** (e.g. "District Hospital", "Government Medical College", "PHC") and the operator is public or unknown: use it. High confidence.
-- MAGIC 2. **Academic affiliation** or an allopathic medical college name: rung 5.
-- MAGIC 3. Otherwise build from type, beds and services: start at rung 2 for clinics and 3 for hospitals, then raise to 4 or 5 when the record lists district-level services (ICU, CT, dialysis, blood bank, 24x7 emergency) or tertiary ones (neurosurgery, cath lab, MRI, NICU, transplant, radiotherapy), or has 100+ / 300+ beds.
-- MAGIC 4. Standalone diagnostic centres and pharmacies get no rung, only a kind, as in the framework.
-- MAGIC
-- MAGIC Confidence: **high** when the name or academic link states the tier, or beds agree with the services; **medium** when 2+ independent signal types agree; **low** when it rests on facility type alone. It drops one step when `quality_score` is under 0.7.

-- COMMAND ----------

CREATE OR REPLACE TABLE workspace.vf_gold.facility_level
COMMENT 'One row per facility: inferred care level (rung 1-5), service levels, evidence and confidence'
AS
WITH sig AS (
  SELECT facility_id,
    collect_set(signal) AS signals,
    count(DISTINCT CASE WHEN signal_group = 'tertiary' THEN signal END) AS n_tertiary,
    count(DISTINCT CASE WHEN signal_group = 'district' THEN signal END) AS n_district,
    count(DISTINCT CASE WHEN signal_group = 'block' THEN signal END) AS n_block,
    map_from_entries(collect_list(struct(signal, concat(source_field, ': "', left(quote, 160), '"')))) AS quotes
  FROM workspace.vf_gold.facility_level_evidence GROUP BY facility_id
),
f AS (
  SELECT f.facility_id, f.name, f.facility_type, f.operator_type, f.capacity_beds, f.number_doctors, f.affiliation_types,
    f.city, f.district, f.state_display AS state, f.latitude, f.longitude, f.quality_score, f.quality_notes,
    lower(coalesce(f.name, '')) AS lname,
    coalesce(s.signals, array()) AS signals, coalesce(s.n_tertiary, 0) AS n_tertiary, coalesce(s.n_district, 0) AS n_district, coalesce(s.n_block, 0) AS n_block,
    coalesce(s.quotes, map()) AS quotes
  FROM workspace.vf_silver.facilities f LEFT JOIN sig s USING (facility_id)
),
named AS (
  SELECT *,
    lname rlike 'ayurved|homoeo|homeopath|unani|siddha|naturopath|panchakarma|\\bayush\\b' AS is_ayush,
    lname rlike 'diagnostic|imaging|scan cent|scans\\b|pathology|\\blabs?\\b|laborator|x ?ray cent' AND coalesce(capacity_beds, 0) = 0 AND n_block - CAST(array_contains(signals, 'xray') AS INT) - CAST(array_contains(signals, 'ultrasound') AS INT) = 0
      AND NOT lname rlike 'hospital|medical college|aiims|health cent' AS is_diagnostic_only,
    lname rlike '\beye\b|netra|nethra|dental|dentist|\bskin\b|derma|\bivf\b|fertility|\bent\b|physiother|\bhair\b|cosmetic|psychiatr|de-?addiction|rehab|ortho(paedic|pedic)? cent' AND NOT lname rlike 'multi|general|medical college' AS is_single_specialty,
    CASE
      WHEN lname rlike 'laborator|pathology|diagnostic|pharmacy|chemist' THEN NULL
      WHEN lname rlike 'aiims|all india institute of medical|medical college|institute of medical sciences|\\bgmch\\b|pgimer|post ?graduate institute of medical' AND NOT lname rlike 'ayurved|homoeo|homeopath|unani|siddha|naturopath|dental college|nursing college' THEN 5
      WHEN lname rlike 'district hospital|district headquarters? hospital|civil hospital|zilla hospital|sub ?-?district hospital|sub ?divisional hospital|taluka? (government |headquarters? )?hospital|area hospital|general hospital' AND coalesce(operator_type, 'public') = 'public' THEN 4
      WHEN lname rlike 'community health cent|\\bchc\\b|rural hospital|first referral unit' AND coalesce(operator_type, 'public') = 'public' THEN 3
      WHEN lname rlike 'primary health cent|\\bphc\\b|urban health cent|\\buphc\\b' AND coalesce(operator_type, 'public') = 'public' THEN 2
      WHEN lname rlike 'sub ?-?cent(re|er)|health (and|&) wellness|ayushman arogya|\\bhwc\\b' AND coalesce(operator_type, 'public') = 'public' THEN 1
    END AS name_rung
  FROM f
),
scored AS (
  SELECT *,
    array_contains(affiliation_types, 'academic') AND NOT is_ayush AS is_academic,
    CASE
      WHEN name_rung IS NOT NULL THEN name_rung
      WHEN array_contains(affiliation_types, 'academic') AND NOT is_ayush AND facility_type = 'hospital' THEN 5
      WHEN facility_type = 'pharmacy' OR is_diagnostic_only THEN NULL
      WHEN facility_type = 'hospital' AND is_single_specialty AND coalesce(capacity_beds, 0) < 100 THEN 3
      WHEN facility_type = 'hospital' AND (coalesce(capacity_beds, 0) >= 300 AND n_tertiary >= 1 OR n_tertiary >= 3 AND n_district >= 3) THEN 5
      WHEN facility_type = 'hospital' AND (coalesce(capacity_beds, 0) >= 100 OR n_district >= 3 OR (n_tertiary >= 1 AND n_district >= 2)) THEN 4
      WHEN facility_type = 'hospital' THEN 3
      WHEN facility_type IN ('clinic', 'doctor', 'dentist') AND (coalesce(capacity_beds, 0) >= 10 OR (array_contains(signals, 'inpatient') AND n_block >= 3)) THEN 3
      WHEN facility_type IN ('clinic', 'doctor', 'dentist') THEN 2
      WHEN n_district >= 3 THEN 4
      WHEN n_block >= 3 THEN 3
      ELSE 2
    END AS care_level
  FROM named
),
labelled AS (
  SELECT *,
    CASE
      WHEN facility_type = 'pharmacy' THEN 'Pharmacy'
      WHEN is_diagnostic_only THEN 'Diagnostic centre'
      WHEN name_rung IS NOT NULL THEN
        CASE care_level WHEN 1 THEN 'Sub-centre / Arogya Mandir' WHEN 2 THEN 'Primary Health Centre' WHEN 3 THEN 'Community Health Centre'
                        WHEN 4 THEN 'District / sub-district hospital' WHEN 5 THEN 'Medical college / apex' END
      WHEN operator_type = 'public' THEN
        CASE care_level WHEN 2 THEN 'Public clinic / dispensary' WHEN 3 THEN 'Public hospital, CHC-level'
                        WHEN 4 THEN 'Public hospital, district-level' WHEN 5 THEN 'Public tertiary hospital' END
      WHEN facility_type = 'dentist' THEN 'Dental clinic'
      WHEN is_single_specialty AND care_level >= 3 THEN 'Single-specialty hospital'
      ELSE CASE care_level WHEN 2 THEN 'Clinic / polyclinic' WHEN 3 THEN 'Nursing home / small hospital'
                           WHEN 4 THEN 'Multispecialty hospital' WHEN 5 THEN 'Super-specialty / tertiary' END
    END AS level_kind,
    CASE
      WHEN name_rung IS NOT NULL THEN 'name'
      WHEN is_academic AND facility_type = 'hospital' AND care_level = 5 THEN 'academic affiliation'
      WHEN care_level IS NULL THEN 'kind only'
      WHEN capacity_beds IS NOT NULL AND n_district + n_tertiary > 0 THEN 'beds + services'
      WHEN capacity_beds IS NOT NULL THEN 'beds'
      WHEN n_district + n_tertiary + n_block > 0 THEN 'services'
      ELSE 'facility type only'
    END AS level_basis
  FROM scored
),
conf AS (
  SELECT *,
    CASE
      WHEN level_basis IN ('name', 'academic affiliation', 'beds + services') THEN 3
      WHEN level_basis IN ('beds', 'kind only') OR (level_basis = 'services' AND CAST(n_tertiary > 0 AS INT) + CAST(n_district > 0 AS INT) + CAST(n_block > 0 AS INT) >= 2) THEN 2
      ELSE 1
    END - CASE WHEN quality_score < 0.7 THEN 1 ELSE 0 END AS conf_n
  FROM labelled
)
SELECT facility_id, name, facility_type, operator_type, city, district, state, latitude, longitude,
  care_level,
  CASE WHEN care_level IS NULL THEN level_kind ELSE concat('Rung ', care_level, ': ', level_kind) END AS care_level_label,
  level_kind, level_basis,
  CASE WHEN conf_n >= 3 THEN 'high' WHEN conf_n = 2 THEN 'medium' ELSE 'low' END AS level_confidence,
  -- service-specific levels (from the framework's service schemes)
  CASE WHEN array_contains(signals, 'nicu') THEN 'NICU (Level III)'
       WHEN array_contains(signals, 'sncu') THEN 'SNCU (Level II)'
       WHEN array_contains(signals, 'newborn_stabilisation') THEN 'Newborn stabilisation unit'
       WHEN array_contains(signals, 'delivery') THEN 'Newborn care corner (delivery point)' END AS newborn_level,
  CASE WHEN array_contains(signals, 'c_section') AND array_contains(signals, 'blood_bank') THEN 'CEmONC (C-section + blood)'
       WHEN array_contains(signals, 'c_section') THEN 'C-section, no blood bank listed'
       WHEN array_contains(signals, 'delivery') THEN 'BEmONC (deliveries)' END AS obstetric_level,
  CASE WHEN array_contains(signals, 'trauma') AND array_contains(signals, 'neurosurgery') AND array_contains(signals, 'ct') AND array_contains(signals, 'icu') THEN 'Level I/II-like (neurosurgery, CT, ICU)'
       WHEN (array_contains(signals, 'trauma') OR array_contains(signals, 'emergency_24x7')) AND (array_contains(signals, 'ct') OR array_contains(signals, 'icu')) THEN 'Level III-like (stabilise, CT or ICU)'
       WHEN array_contains(signals, 'trauma') OR array_contains(signals, 'emergency_24x7') THEN 'Emergency, stabilise and transfer' END AS trauma_level,
  CASE WHEN array_contains(signals, 'dialysis') AND array_contains(signals, 'icu') AND array_contains(signals, 'emergency_24x7') THEN 'Emergency + maintenance dialysis'
       WHEN array_contains(signals, 'dialysis') THEN 'Maintenance dialysis' END AS dialysis_level,
  array_contains(signals, 'nabh') AS nabh_claimed,
  array_contains(signals, 'jci') AS jci_claimed,
  array_contains(signals, 'pmjay') AS pmjay_claimed,
  array_contains(signals, 'cghs') AS cghs_claimed,
  is_academic, is_ayush, is_single_specialty,
  capacity_beds, number_doctors, n_tertiary AS tertiary_signals, n_district AS district_signals, n_block AS block_signals,
  array_sort(signals) AS signals,
  quotes AS evidence,
  concat_ws('; ',
    CASE level_basis WHEN 'name' THEN concat('name "', name, '" states the public tier')
                     WHEN 'academic affiliation' THEN 'academic (teaching) affiliation' END,
    CASE WHEN capacity_beds IS NOT NULL THEN concat(capacity_beds, ' beds') END,
    CASE WHEN n_tertiary > 0 THEN concat('tertiary services: ', array_join(array_sort(filter(signals, x -> x IN ('neurosurgery','cardiac_surgery','cath_lab','transplant','mri','nicu','radiation_oncology'))), ', ')) END,
    CASE WHEN n_district > 0 THEN concat('district-level services: ', array_join(array_sort(filter(signals, x -> x IN ('icu','ct','dialysis','blood_bank','emergency_24x7','sncu','ventilator'))), ', ')) END,
    CASE WHEN level_basis = 'facility type only' THEN concat('only the facility type (', coalesce(facility_type, 'unknown'), ') is known') END,
    CASE WHEN quality_score < 0.7 THEN concat('record quality is low: ', quality_notes) END
  ) AS level_reason,
  quality_score
FROM conf;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 4. Checks and summary

-- COMMAND ----------

SELECT care_level_label, level_confidence, count(*) AS facilities
FROM workspace.vf_gold.facility_level GROUP BY ALL ORDER BY care_level_label, level_confidence;
