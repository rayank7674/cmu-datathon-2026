-- Databricks notebook source
-- MAGIC %md
-- MAGIC # 07 — Facility Attribute Extraction (Task B)
-- MAGIC **Scope:** create only `workspace.vf_silver.facility_extracted_attributes` and
-- MAGIC `workspace.vf_silver.facility_web_identity`. This notebook does not modify existing
-- MAGIC tables or notebooks 01–06. Run after notebook 03.
-- MAGIC
-- MAGIC Extraction is deliberately conservative: regex-only, exact quotes, original values
-- MAGIC preserved; ambiguous, out-of-range, chain-wide and department-level claims flagged.
-- MAGIC Source text is unverified facility-associated scraped text, not proof of current
-- MAGIC bed availability or service capability. No AI validation pass is used.
-- MAGIC
-- MAGIC Snapshot reviewed: `rayank7674/cmu-datathon-2026` main at
-- MAGIC `6f60099df916151be1d39292556b3ed12cb488ef` (2026-11-21).

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 1. Safeguards and evidence extraction
-- MAGIC Only descriptive prose and explicit free-text capability/procedure/equipment items
-- MAGIC are scanned. Atomic specialty labels aren't numerically reliable. Each extracted
-- MAGIC quote is a literal substring of a source text field. Numbers in unrelated source
-- MAGIC columns and web domains do not become capacity claims.

-- COMMAND ----------

CREATE TABLE IF NOT EXISTS workspace.vf_silver.facility_extracted_attributes (
  facility_id STRING,
  attribute STRING,
  value INT,
  quote STRING,
  source_field STRING,
  method STRING,
  confidence STRING,
  source_type STRING,
  source_ref STRING,
  original_value INT,
  candidate_value INT,
  review_status STRING,
  rejection_reason STRING
)
COMMENT 'Strict quoted regex evidence from facility-associated text; conflict and ambiguity are retained; no overwrite of original facility attributes';

-- COMMAND ----------

-- Extract full literal numeric phrase (quote) then evaluate nearby context.
-- Regexp_extract_all (Databricks SQL / Spark SQL) uses Java regex syntax.
-- Regexes avoid numeric ranges beginning with "10-20" or "10 to 20" via
-- contextual filtering; the final review_status gate also rejects ambiguity.
INSERT OVERWRITE workspace.vf_silver.facility_extracted_attributes
WITH texts AS (
  SELECT f.facility_id, 'description' AS source_field, f.description AS source_text,
         f.capacity_beds, f.number_doctors, f.year_established,
         f.source_content_id, f.source_urls
  FROM workspace.vf_silver.facilities f WHERE f.description IS NOT NULL
  UNION ALL
  SELECT f.facility_id, concat('capabilities[', CAST(pos AS STRING), ']'), t.col,
         f.capacity_beds, f.number_doctors, f.year_established, f.source_content_id, f.source_urls
  FROM workspace.vf_silver.facilities f LATERAL VIEW posexplode(f.capabilities) t AS pos, col
  WHERE t.col IS NOT NULL
  UNION ALL
  SELECT f.facility_id, concat('procedures[', CAST(pos AS STRING), ']'), t.col,
         f.capacity_beds, f.number_doctors, f.year_established, f.source_content_id, f.source_urls
  FROM workspace.vf_silver.facilities f LATERAL VIEW posexplode(f.procedures) t AS pos, col
  WHERE t.col IS NOT NULL
  UNION ALL
  SELECT f.facility_id, concat('equipment[', CAST(pos AS STRING), ']'), t.col,
         f.capacity_beds, f.number_doctors, f.year_established, f.source_content_id, f.source_urls
  FROM workspace.vf_silver.facilities f LATERAL VIEW posexplode(f.equipment) t AS pos, col
  WHERE t.col IS NOT NULL
),
patterns AS (
  SELECT *, 'beds' AS attribute,
    '(?i)(?<![0-9/-])[0-9]{1,5}[[:space:]-]*(?:beds?|bedded)(?![a-z])' AS rx
  FROM texts
  UNION ALL SELECT *, 'doctors',
    '(?i)(?<![0-9/-])[0-9]{1,5}[[:space:]]+(?:doctors|consultants)(?![a-z])'
  FROM texts
  UNION ALL SELECT *, 'year_founded',
    '(?i)(?<![a-z])(?:established|founded|since)[[:space:]]+(?:in[[:space:]]+)?(?:18|19|20)[0-9]{2}(?![0-9])'
  FROM texts
  UNION ALL SELECT *, 'icu_beds',
    '(?i)(?<![0-9/-])[0-9]{1,5}[[:space:]-]*beds?[[:space:]]+(?:in[[:space:]]+)?(?:the[[:space:]]+)?(?:icu|intensive[[:space:]]+care[[:space:]]+unit)(?![a-z])'
  FROM texts
),
hits AS (
  SELECT p.*, q.quote
  FROM patterns p
  LATERAL VIEW explode(regexp_extract_all(p.source_text, p.rx, 0)) q AS quote
),
parsed AS (
 SELECT *,
   CASE WHEN attribute = 'year_founded'
        THEN try_cast(regexp_extract(quote, '(?:18|19|20)[0-9]{2}', 0) AS INT)
        ELSE try_cast(regexp_extract(quote, '[0-9]{1,5}', 0) AS INT) END AS candidate_value,
   substring(source_text,
     greatest(1, instr(lower(source_text), lower(quote)) - 65),
     length(quote) + 130) AS context_text
 FROM hits
),
flags AS (
 SELECT *, CASE
   WHEN candidate_value IS NULL OR candidate_value <= 0 THEN 'no valid positive number'
   WHEN attribute = 'year_founded' AND NOT candidate_value BETWEEN 1850 AND 2026 THEN 'year outside 1850-2026'
   WHEN attribute IN ('beds','icu_beds') AND candidate_value > 5000 THEN 'bed count above 5000'
   WHEN attribute = 'doctors' AND candidate_value > 2000 THEN 'doctor count above 2000'
   WHEN lower(substring(source_text, greatest(1, instr(lower(source_text),lower(quote))-18), length(quote)+36)) RLIKE '(?i)(up[[:space:]]+to|at[[:space:]]+most|maximum[[:space:]]+of|approx(?:imately)?|around|about|nearly|more[[:space:]]+than|over[[:space:]]+[0-9]|[0-9][[:space:]]*(?:to|[-–])[[:space:]]*[0-9])'
     THEN 'approximate, range or qualified numeric context'
   WHEN attribute = 'doctors' AND lower(context_text) RLIKE '(?i)(across (?:all|our) (?:branches|centres|centers)|in the (?:department|unit)|department of|specialty team|speciality team)'
     THEN 'possibly department or multi-site doctor count'
   WHEN lower(context_text) RLIKE '(?i)(across (?:our|all|the) (?:hospitals|locations|facilities|centres|centers)|group[- ]wide|entire chain|network[- ]wide|all branches|chain of hospitals|group of hospitals)'
     THEN 'possibly chain-wide count'
   WHEN attribute = 'year_founded' AND lower(context_text) RLIKE '(?i)(founded by|since (?:the|its) founding)'
     THEN 'founding year may not refer to facility'
   ELSE NULL END AS rejection_reason
 FROM parsed
),
-- For claims of "20 beds dedicated NICU" (unit follows numeric phrase), do NOT
-- treat 20 as total beds, even if an otherwise explicit total appears elsewhere.
-- A separate, precise ICU expression is accepted only for ICU, not NICU or PICU.
scored AS (
 SELECT *,
  CASE WHEN attribute='beds' AND lower(substring(source_text,
               instr(lower(source_text), lower(quote)) + length(quote), 45))
               RLIKE '(?i)^[[:space:]]*(?:dedicated[[:space:]]+)?(?:(?:to|for)[[:space:]]+)?(?:icu|nicu|picu|ccu|hdu|ward|department)' 
       THEN 'bed figure attached to department/unit'
       WHEN attribute='beds' AND lower(substring(source_text,
               greatest(1,instr(lower(source_text), lower(quote))-25),25))
               RLIKE '(?i)(icu|nicu|picu|ccu|hdu|ward|department)[[:space:]:-]*$'
       THEN 'department/unit-specific beds'
       ELSE rejection_reason END AS final_rejection,
  CASE attribute WHEN 'beds' THEN capacity_beds
                 WHEN 'doctors' THEN number_doctors
                 WHEN 'year_founded' THEN year_established
                 ELSE CAST(NULL AS INT) END AS original_value
 FROM flags
),
consistency AS (
 SELECT s.*, c.distinct_valid_values,
         ROW_NUMBER() OVER (PARTITION BY s.facility_id, s.attribute
                            ORDER BY CASE WHEN s.final_rejection IS NULL THEN 0 ELSE 1 END,
                                     CASE WHEN s.source_field='description' THEN 0 ELSE 1 END,
                                     s.source_field, s.quote) AS rn
 FROM scored s
 JOIN (
   SELECT facility_id, attribute,
          COUNT(DISTINCT CASE WHEN final_rejection IS NULL THEN candidate_value END)
            AS distinct_valid_values
   FROM scored
   GROUP BY facility_id, attribute
 ) c ON c.facility_id = s.facility_id AND c.attribute = s.attribute
),
results AS (
 SELECT facility_id, attribute,
   CASE WHEN final_rejection IS NULL AND distinct_valid_values=1 THEN candidate_value
        ELSE CAST(NULL AS INT) END AS value,
   quote, source_field, 'strict_regex' AS method,
   CASE WHEN final_rejection IS NULL AND distinct_valid_values=1 AND source_field='description'
        THEN 'medium' ELSE 'low' END AS confidence,
   'extracted_text' AS source_type,
   concat('vf_silver.facilities:', facility_id, ':', source_field,
          CASE WHEN source_content_id IS NOT NULL THEN concat(':',source_content_id) ELSE '' END) AS source_ref,
   original_value, candidate_value,
   CASE WHEN final_rejection IS NOT NULL THEN 'ambiguous'
        WHEN distinct_valid_values > 1 THEN 'ambiguous'
        WHEN original_value IS NULL THEN 'candidate_fill'
        WHEN original_value <> candidate_value THEN 'conflict'
        ELSE 'existing_agrees' END AS review_status,
   CASE WHEN final_rejection IS NOT NULL THEN final_rejection
        WHEN distinct_valid_values > 1 THEN 'different valid-looking values in facility text'
        ELSE NULL END AS rejection_reason,
   rn
 FROM consistency
)
SELECT facility_id, attribute, value, quote, source_field, method, confidence,
       source_type, source_ref, original_value, candidate_value, review_status,
       rejection_reason
FROM results WHERE rn=1;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 2. Web identity
-- MAGIC `official_domain` comes only from the stated official website, and from a
-- MAGIC secondary website only when it agrees with the stated official website.
-- MAGIC `source_urls` are stored as provenance, NOT treated as official websites.
-- MAGIC Chain labels are inferred from a recognized official-domain whitelist;
-- MAGIC otherwise left NULL. Domain recognition does not verify service capabilities.

-- COMMAND ----------

CREATE TABLE IF NOT EXISTS workspace.vf_silver.facility_web_identity (
 facility_id STRING,
 official_domain STRING,
 chain_name STRING,
 confidence STRING,
 source_type STRING,
 source_ref STRING,
 source_urls ARRAY<STRING>,
 identity_reason STRING
)
COMMENT 'Official domains from stated officialWebsite only; chain affiliation candidate, never inherited care capability';

-- COMMAND ----------

INSERT OVERWRITE workspace.vf_silver.facility_web_identity
WITH normalized AS (
 SELECT facility_id, official_website, source_urls,
   lower(regexp_extract(coalesce(official_website,''),
     '^(?:https?://)?(?:www[.])?([^/:? #]+)', 1)) AS host
 FROM workspace.vf_silver.facilities
),
classified AS (
 SELECT *,
 CASE WHEN host='' OR host NOT RLIKE '^[a-z0-9][a-z0-9.-]*[.][a-z]{2,}$'
                OR host RLIKE '(^|[.])(facebook|instagram|linkedin|youtube|justdial|practo|google|wikipedia|mappls)[.]'
       THEN NULL ELSE regexp_replace(host, '^www[.]', '') END AS official_domain
 FROM normalized
),
chains AS (
 SELECT *, CASE
  WHEN official_domain RLIKE '(^|[.])apollohospitals[.]com$' THEN 'Apollo'
  WHEN official_domain RLIKE '(^|[.])fortishealthcare[.]com$' THEN 'Fortis'
  WHEN official_domain RLIKE '(^|[.])manipalhospitals[.]com$' THEN 'Manipal'
  WHEN official_domain RLIKE '(^|[.])maxhealthcare[.]in$' THEN 'Max'
  WHEN official_domain RLIKE '(^|[.])narayanahealth[.]org$' THEN 'Narayana'
  WHEN official_domain RLIKE '(^|[.])aiims[.]edu$' OR official_domain RLIKE '(^|[.])aiims[.]gov[.]in$' THEN 'AIIMS'
  ELSE NULL END AS chain_name
 FROM classified
)
SELECT facility_id, official_domain, chain_name,
       CASE WHEN official_domain IS NOT NULL THEN 'medium' ELSE 'low' END AS confidence,
       'original' AS source_type,
       CASE WHEN official_domain IS NOT NULL THEN concat('vf_silver.facilities:', facility_id, ':official_website')
            ELSE concat('vf_silver.facilities:',facility_id,':source_urls (not verified official)') END AS source_ref,
       source_urls,
       CASE WHEN official_domain IS NULL THEN 'no trustworthy official_website field; cannot infer official status from crawled URLs'
            WHEN chain_name IS NOT NULL THEN 'recognized domain pattern; chain membership not independently verified'
            ELSE 'domain reported in original officialWebsite field; not independently verified' END AS identity_reason
FROM chains;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 3. Measured validation
-- MAGIC Run these read-only queries after population. They intentionally distinguish
-- MAGIC candidate fills from source-verified facts and retain conflicts for review.

-- COMMAND ----------

SELECT attribute, review_status, count(*) AS rows,
       count(DISTINCT facility_id) AS distinct_facilities
FROM workspace.vf_silver.facility_extracted_attributes
GROUP BY attribute, review_status ORDER BY attribute, review_status;

-- COMMAND ----------

SELECT attribute,
       count(DISTINCT CASE WHEN review_status='candidate_fill' THEN facility_id END) AS new_candidate_fills,
       count(DISTINCT CASE WHEN review_status='conflict' THEN facility_id END) AS conflicts,
       count(DISTINCT CASE WHEN review_status='ambiguous' THEN facility_id END) AS ambiguous
FROM workspace.vf_silver.facility_extracted_attributes
GROUP BY attribute ORDER BY attribute;

-- COMMAND ----------

SELECT count(*) AS identity_rows,
       count(DISTINCT facility_id) AS distinct_facilities,
       count_if(official_domain IS NOT NULL) AS with_official_domain,
       count_if(chain_name IS NOT NULL) AS recognized_chain
FROM workspace.vf_silver.facility_web_identity;

-- COMMAND ----------

-- 20 reproducible random quoted examples for manual review.
SELECT facility_id, attribute, value, candidate_value, original_value,
       quote, source_field, confidence, review_status, rejection_reason
FROM workspace.vf_silver.facility_extracted_attributes
ORDER BY rand(2026) LIMIT 20;

-- COMMAND ----------

-- Check quote is an exact substring of the indicated original source field.
WITH sources AS (
 SELECT facility_id, 'description' AS source_field, description AS raw_text
 FROM workspace.vf_silver.facilities WHERE description IS NOT NULL
 UNION ALL
 SELECT facility_id, concat('capabilities[',cast(pos as string),']'), col
 FROM workspace.vf_silver.facilities LATERAL VIEW posexplode(capabilities) t AS pos, col
 UNION ALL
 SELECT facility_id, concat('procedures[',cast(pos as string),']'), col
 FROM workspace.vf_silver.facilities LATERAL VIEW posexplode(procedures) t AS pos, col
 UNION ALL
 SELECT facility_id, concat('equipment[',cast(pos as string),']'), col
 FROM workspace.vf_silver.facilities LATERAL VIEW posexplode(equipment) t AS pos, col
)
SELECT count(*) AS quotes_not_found_in_source
FROM workspace.vf_silver.facility_extracted_attributes a
LEFT JOIN sources s USING (facility_id,source_field)
WHERE s.raw_text IS NULL OR instr(s.raw_text,a.quote)=0;

-- COMMAND ----------

-- No contradictory original attributes were overwritten (read-only comparison).
SELECT a.attribute, count(*) AS conflict_rows
FROM workspace.vf_silver.facility_extracted_attributes a
WHERE a.review_status='conflict' AND a.original_value IS NOT NULL AND a.value <> a.original_value
GROUP BY a.attribute;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 4. README documentation (only after real execution)
-- MAGIC Once the validation queries have run successfully, append an **Implemented —
-- MAGIC Task B** section to README.md containing the measured candidate fills, conflicts,
-- MAGIC ambiguous claims, domain coverage, verified quote-substring check, and date.
-- MAGIC Do not claim these figures, or mark this task implemented, before execution.
-- MAGIC
-- MAGIC **Important:** `candidate_fill` remains a source-text extraction, not an
-- MAGIC independently verified live capacity. No change to `facility_level` is made.