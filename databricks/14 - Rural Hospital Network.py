# Databricks notebook source
# MAGIC %md
# MAGIC # 14 - Rural Hospital Network profiles
# MAGIC Loads 42 self-reported hospital profiles from ruralhospitalnetwork.org and matches them to Virtue Foundation facilities, conservatively.
# MAGIC
# MAGIC | Object | Grain | What it is |
# MAGIC |---|---|---|
# MAGIC | `vf_bronze.ext_rhn_profiles_raw` | one row per profile (42) | The file as extracted, every column a string, plus provenance |
# MAGIC | `vf_silver.ext_rhn_facility_matches` | profile x candidate facility | Same columns as Task D's `ext_facility_matches`, source `rural_hospital_network`. Kept in its own table so Task D's tables are never overwritten |
# MAGIC | `vf_gold.facility_rhn_evidence` | one row per accepted match | Profile values beside the Virtue Foundation values, with the profile's quotes and URL |
# MAGIC | `vf_gold.facility_rhn_conflicts` | one row per difference | Both values kept. Nothing is replaced |
# MAGIC | `vf_gold.rhn_checks` | one row per check | Measured validation |
# MAGIC
# MAGIC **Rules**
# MAGIC - The profiles have no coordinates, so matching uses name + state + location text only. The best possible status is therefore `accepted_medium`, never high.
# MAGIC - Accepted needs all three: the distinctive name words are the same on both sides, the state is the same, and a word from the profile's location text appears in the facility's district, city or address.
# MAGIC - A similar name (similarity 0.80 or more) in the same state and place whose distinctive words differ (for example "Dental Care" against "Dental College") is `ambiguous` and is for hand review only.
# MAGIC - Everything else is `rejected`. An unmatched profile says nothing about any facility.
# MAGIC - Profile values are self-reported and undated. They sit beside Virtue Foundation values and never replace them. `downloaded_at` is when the page was read, not a verification date.
# MAGIC - A match supports that the facility exists. It does not establish any clinical service.

# COMMAND ----------

import re, difflib, json
import pandas as pd

CSV = "/Volumes/workspace/vf_bronze/external/rural_hospital_network/rural_hospital_network_profiles.csv"
SOURCE = "rural_hospital_network"
raw = pd.read_csv(CSV, dtype=str)
raw["source"] = SOURCE
raw["source_record_id"] = raw["rhn_slug"]
raw["source_type"] = "external"
raw["source_ref"] = raw["source_url"]
raw["confidence"] = "low: self-reported profile, undated"
raw["source_file"] = "dbfs:" + CSV
sdf = spark.createDataFrame(raw.astype(object).where(raw.notna(), None).astype(str).where(raw.notna(), None))
sdf = sdf.selectExpr("*", "current_timestamp() AS imported_at")
sdf.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("workspace.vf_bronze.ext_rhn_profiles_raw")
print(len(raw), "profiles loaded")

# COMMAND ----------

# MAGIC %md ## Matching

# COMMAND ----------

STOP = {"hospital", "hospitals", "the", "and", "of", "centre", "center", "health", "clinic", "clinics", "trust", "dr",
        "pvt", "ltd", "research", "memorial", "care", "community", "kendra", "rural", "for", "society"}


GENERIC_PLACE = {"road", "west", "east", "north", "south", "village", "block", "district", "hills", "tehsil",
                 "mandal", "near", "nagar", "town", "city", "post", "taluk", "taluka"}


def norm(s):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", re.sub(r"\(.*?\)", " ", str(s).lower())).split())


def words(s):
    return {t for t in norm(s).split() if t not in STOP and len(t) > 1}


fac = spark.sql("""
  SELECT facility_id, name, state, district, city,
         concat_ws(' ', address_line1, address_line2, address_line3) AS addr,
         capacity_beds, year_established, number_doctors
  FROM workspace.vf_silver.facilities""").toPandas()
fac["nn"] = fac["name"].map(norm)
fac["tk"] = fac["name"].map(words)
fac["loc"] = (fac["district"].fillna("") + " " + fac["city"].fillna("") + " " + fac["addr"].fillna("")).str.lower()

rows = []
for _, x in raw.iterrows():
    nn = norm(x["name"])
    loc_words = [t for t in re.split(r"[^a-z]+", str(x["location_text"] if pd.notna(x["location_text"]) else "").lower()) if len(t) > 3 and t not in GENERIC_PLACE]
    tk = words(x["name"])
    # words in the profile name that are just its place (e.g. "..., Mastichak") are not part of the name
    tk_core = {t for t in tk if t not in loc_words} or tk
    state = re.sub(r"\(.*?\)", "", str(x["state_listed"])).strip().lower()
    sim = fac["nn"].map(lambda n: difflib.SequenceMatcher(None, nn, n).ratio())
    cand = fac[sim >= 0.70].copy()
    cand["name_sim"] = sim[sim >= 0.70]
    for _, y in cand.iterrows():
        same_state = str(y["state"]).lower() == state
        loc_hit = any(t in y["loc"] for t in loc_words)
        same_words = (y["tk"] - set(loc_words)) == tk_core or y["tk"] == tk
        fields = ["name"] + (["state"] if same_state else []) + (["location_text"] if loc_hit else [])
        if same_words and same_state and loc_hit:
            status, reason, conf = "accepted_medium", "same distinctive name words, same state, place named in the facility address; no coordinates to confirm", "medium"
        elif same_state and loc_hit and y["name_sim"] >= 0.80:
            status, reason, conf = "ambiguous", "similar name in the same state and place, but the distinctive name words differ", "low"
        elif same_words and same_state:
            status, reason, conf = "ambiguous", "same name and state, but the place could not be confirmed from the address", "low"
        else:
            status, reason, conf = "rejected", "name is similar but state or place does not agree", "low"
        rows.append(dict(facility_id=y["facility_id"], source=SOURCE, source_record_id=x["rhn_slug"], ext_name=x["name"],
                         match_score=round(0.5 * y["name_sim"] + 0.25 * same_state + 0.25 * loc_hit, 3),
                         name_sim=round(float(y["name_sim"]), 3), distance_km=None, match_method="name_state_location_text",
                         matched_fields=fields, match_status=status, match_reason=reason, source_type="external",
                         source_ref=x["source_url"], confidence=conf))

m = pd.DataFrame(rows)
# one profile may be accepted for one facility only, and one facility for one profile; otherwise all become ambiguous
acc = m[m["match_status"] == "accepted_medium"]
dup_p = acc["source_record_id"].duplicated(keep=False)
dup_f = acc["facility_id"].duplicated(keep=False)
bad = acc[dup_p | dup_f].index
m.loc[bad, ["match_status", "match_reason", "confidence"]] = ["ambiguous", "more than one equally good candidate", "low"]

schema = ("facility_id string, source string, source_record_id string, ext_name string, match_score double, name_sim double, "
          "distance_km double, match_method string, matched_fields array<string>, match_status string, match_reason string, "
          "source_type string, source_ref string, confidence string")
spark.createDataFrame(m.astype(object).where(m.notna(), None), schema=schema) \
    .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("workspace.vf_silver.ext_rhn_facility_matches")
print(m["match_status"].value_counts().to_dict())

# COMMAND ----------

# MAGIC %md ## Evidence and differences (accepted matches only)

# COMMAND ----------

spark.sql(r"""
CREATE OR REPLACE TABLE workspace.vf_gold.facility_rhn_evidence AS
SELECT m.facility_id, f.name AS vf_name, f.state AS vf_state, f.district AS vf_district,
       m.source, m.source_record_id, m.ext_name, m.match_status, m.match_method, m.matched_fields, m.match_score, m.match_reason,
       p.state_listed AS rhn_state_listed, p.location_text AS rhn_location_text,
       f.capacity_beds AS vf_beds, CAST(p.beds AS INT) AS rhn_beds, p.beds_quote AS rhn_beds_quote,
       f.number_doctors AS vf_doctors, CAST(p.doctors_fulltime AS INT) AS rhn_doctors_fulltime,
       CAST(p.doctors_parttime AS INT) AS rhn_doctors_parttime, p.doctors_quote AS rhn_doctors_quote,
       f.year_established AS vf_founding_year, CAST(p.founding_year AS INT) AS rhn_founding_year, p.founding_quote AS rhn_founding_quote,
       p.notes AS rhn_notes, p.source_url, p.downloaded_at AS source_downloaded_at,
       'external' AS source_type, p.source_url AS source_ref, 'medium identity match; values are self-reported and undated' AS confidence,
       'That a hospital with this name, in this state and place, has a profile on the Rural Hospital Network site, and what that profile says about itself.' AS establishes,
       'Any clinical service, that the values are current, or that they are more correct than the Virtue Foundation values.' AS does_not_establish
FROM workspace.vf_silver.ext_rhn_facility_matches m
JOIN workspace.vf_bronze.ext_rhn_profiles_raw p ON p.rhn_slug = m.source_record_id
JOIN workspace.vf_silver.facilities f ON f.facility_id = m.facility_id
WHERE m.match_status IN ('accepted_high', 'accepted_medium')
""")

spark.sql(r"""
CREATE OR REPLACE TABLE workspace.vf_gold.facility_rhn_conflicts AS
SELECT facility_id, vf_name AS name, source, source_record_id, match_status, 'beds_differ' AS conflict_type,
       (abs(rhn_beds - vf_beds) > 20 AND abs(rhn_beds - vf_beds) > 0.25 * greatest(rhn_beds, vf_beds)) AS is_contradiction,
       CAST(vf_beds AS STRING) AS vf_value, CAST(rhn_beds AS STRING) AS external_value,
       concat('Bed counts differ. Profile quote: "', rhn_beds_quote, '". Counted as a contradiction only above 25% and 20 beds. Either may be out of date.') AS detail,
       source_ref
FROM workspace.vf_gold.facility_rhn_evidence WHERE rhn_beds IS NOT NULL AND vf_beds IS NOT NULL AND rhn_beds <> vf_beds
UNION ALL
SELECT facility_id, vf_name, source, source_record_id, match_status, 'founding_year_differs',
       abs(rhn_founding_year - vf_founding_year) > 2,
       CAST(vf_founding_year AS STRING), CAST(rhn_founding_year AS STRING),
       concat('Founding years differ. Profile quote: "', rhn_founding_quote, '". Counted as a contradiction only above 2 years.'), source_ref
FROM workspace.vf_gold.facility_rhn_evidence WHERE rhn_founding_year IS NOT NULL AND vf_founding_year IS NOT NULL AND rhn_founding_year <> vf_founding_year
UNION ALL
SELECT facility_id, vf_name, source, source_record_id, match_status, 'doctors_differ', false,
       CAST(vf_doctors AS STRING), CAST(rhn_doctors_fulltime AS STRING),
       concat('Doctor counts differ. Profile quote: "', rhn_doctors_quote, '". Not a contradiction: the profile counts full-time doctors and the Virtue Foundation figure is not defined the same way.'), source_ref
FROM workspace.vf_gold.facility_rhn_evidence WHERE rhn_doctors_fulltime IS NOT NULL AND vf_doctors IS NOT NULL AND rhn_doctors_fulltime <> vf_doctors
""")

# COMMAND ----------

# MAGIC %md ## Checks

# COMMAND ----------

spark.sql(r"""
CREATE OR REPLACE TABLE workspace.vf_gold.rhn_checks AS
SELECT 1 AS n, 'profiles loaded = rows in file' AS check_name, CAST(count(*) AS STRING) AS measured, count(*) = 42 AS passed FROM workspace.vf_bronze.ext_rhn_profiles_raw
UNION ALL SELECT 2, 'profiles missing source URL', CAST(count(*) AS STRING), count(*) = 0 FROM workspace.vf_bronze.ext_rhn_profiles_raw WHERE source_url IS NULL
UNION ALL SELECT 3, 'values without their quote (beds, doctors, founding year)', CAST(count(*) AS STRING), count(*) = 0 FROM workspace.vf_bronze.ext_rhn_profiles_raw
          WHERE (beds IS NOT NULL AND beds_quote IS NULL) OR (doctors_fulltime IS NOT NULL AND doctors_quote IS NULL) OR (founding_year IS NOT NULL AND founding_quote IS NULL)
UNION ALL SELECT 4, 'accepted matches rated high (must be 0: no coordinates)', CAST(count(*) AS STRING), count(*) = 0 FROM workspace.vf_silver.ext_rhn_facility_matches WHERE match_status = 'accepted_high'
UNION ALL SELECT 5, 'facilities accepted for more than one profile, or profiles for more than one facility', CAST(count(*) AS STRING), count(*) = 0 FROM (
            SELECT facility_id k FROM workspace.vf_gold.facility_rhn_evidence GROUP BY 1 HAVING count(*) > 1
            UNION ALL SELECT source_record_id FROM workspace.vf_gold.facility_rhn_evidence GROUP BY 1 HAVING count(*) > 1)
UNION ALL SELECT 6, 'accepted matches where the state differs', CAST(count(*) AS STRING), count(*) = 0 FROM workspace.vf_gold.facility_rhn_evidence WHERE lower(vf_state) <> lower(trim(regexp_replace(rhn_state_listed, '\\(.*?\\)', '')))
UNION ALL SELECT 7, 'silver facilities unchanged', CAST(count(*) AS STRING), count(*) = 9989 FROM workspace.vf_silver.facilities
UNION ALL SELECT 8, 'Task D match table has no rural_hospital_network rows (kept separate)', CAST(count(*) AS STRING), count(*) = 0 FROM workspace.vf_silver.ext_facility_matches WHERE source = 'rural_hospital_network'
""")

out = {
    "checks": [r.asDict() for r in spark.sql("SELECT * FROM workspace.vf_gold.rhn_checks ORDER BY n").collect()],
    "match_status": [r.asDict() for r in spark.sql("SELECT match_status, count(*) n, count(DISTINCT source_record_id) profiles FROM workspace.vf_silver.ext_rhn_facility_matches GROUP BY 1").collect()],
    "accepted": [r.asDict() for r in spark.sql("SELECT ext_name, vf_name, vf_state, vf_district, vf_beds, rhn_beds, vf_founding_year, rhn_founding_year, vf_doctors, rhn_doctors_fulltime FROM workspace.vf_gold.facility_rhn_evidence ORDER BY 1").collect()],
    "ambiguous": [r.asDict() for r in spark.sql("SELECT m.ext_name, f.name vf_name, f.state, m.match_reason FROM workspace.vf_silver.ext_rhn_facility_matches m JOIN workspace.vf_silver.facilities f USING (facility_id) WHERE m.match_status = 'ambiguous'").collect()],
    "conflicts": [r.asDict() for r in spark.sql("SELECT name, conflict_type, is_contradiction, vf_value, external_value FROM workspace.vf_gold.facility_rhn_conflicts ORDER BY 1, 2").collect()],
}
dbutils.notebook.exit(json.dumps(out))
