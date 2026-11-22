# Databricks notebook source
# MAGIC %md
# MAGIC # 18 - Rural Health Statistics 2021-22: government facility counts by district
# MAGIC Source: Rural Health Statistics 2021-22 (Ministry of Health and Family Welfare), Section II, district-wise availability of health centres, rural + urban, **as on 31 March 2022**. 737 districts with counts of functioning sub centres, primary health centres (PHC), community health centres (CHC), sub-divisional hospitals and district hospitals.
# MAGIC
# MAGIC | Object | Grain | What it is |
# MAGIC |---|---|---|
# MAGIC | `vf_bronze.ext_rhs_district_facilities_raw` | one row per RHS district (737) | the file as extracted, all strings, plus provenance |
# MAGIC | `vf_gold.district_rhs_crosswalk` | one row per RHS district | which map district it links to, and how |
# MAGIC | `vf_gold.district_rhs_link_issues` | one row per RHS district that could not be linked | left unlinked, with the closest name for a person to review |
# MAGIC | `vf_gold.district_public_facilities` | one row per map district (735) | government counts beside the facilities in our data |
# MAGIC | `vf_gold.district_context_with_rhs` | view, one row per map district | `district_context` plus the columns above |
# MAGIC | `vf_bronze.ext_rhs_state_beds_staff_raw` | state x table x measure x column (1,692) | state beds and staff tables, long format, blanks kept |
# MAGIC | `vf_gold.state_public_health_capacity` | one row per state (36) | government beds by facility type, PHC doctors and CHC specialists: required, in position, shortfall |
# MAGIC | `vf_gold.rhs_checks` | one row per check | measured validation |
# MAGIC
# MAGIC **Linking rule (same as notebook 11: never the name alone).** An RHS district links to a map district only when the state is the same and either the names are the same, or the RHS name is one the existing crosswalk already links (PIN-code or NFHS-5 district lists) in that state. No fuzzy matching. Anything else is left unlinked and listed.
# MAGIC
# MAGIC **Reading the comparison.** These are counts of government facilities. The Virtue Foundation data is mostly private facilities (8,835 private, 467 public, 687 unknown), so the two columns are not the same set and one is not a share of the other. The comparison shows how much of a district's care our data cannot see. Counts are for March 2022 and districts have changed since.

# COMMAND ----------

import re, json, unicodedata
import pandas as pd

CSV = "/Volumes/workspace/vf_bronze/external/rhs/rhs_2021_22_district_facilities.csv"
raw = pd.read_csv(CSV, dtype=str)
raw["source_url"] = "https://hmis.mohfw.gov.in/ (Rural Health Statistics 2021-22, Section II)"
raw["source_type"] = "external"
raw["source_ref"] = raw["source_name"] + ", " + raw["source_table"]
raw["confidence"] = "high: official government count, dated 2022-03-31"
raw["source_file"] = "dbfs:" + CSV
spark.createDataFrame(raw.astype(object).where(raw.notna(), None)).selectExpr("*", "current_timestamp() AS imported_at") \
    .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("workspace.vf_bronze.ext_rhs_district_facilities_raw")


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower().replace("&", " and ")
    s = re.sub(r"\bthe\b", " ", s)
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", s).split())


poly = spark.sql("SELECT district_id, district_name, state_name FROM workspace.vf_silver.district_polygons").toPandas()
poly["ks"], poly["kd"] = poly["state_name"].map(norm), poly["district_name"].map(norm)
by_name = poly.groupby(["ks", "kd"])["district_id"].agg(list).to_dict()

cw = spark.sql("""SELECT source_list, polygon_state_name, district_name, polygon_district_id FROM workspace.vf_gold.district_crosswalk
                  WHERE link_status = 'linked' AND polygon_district_id IS NOT NULL""").toPandas()
cw["ks"], cw["kd"] = cw["polygon_state_name"].map(norm), cw["district_name"].map(norm)
by_cw = {}
for (ks, kd), g in cw.groupby(["ks", "kd"]):
    ids = sorted(set(g["polygon_district_id"]))
    by_cw[(ks, kd)] = (ids, "/".join(sorted(set(g["source_list"]))))

import difflib
links, issues = [], []
for _, r in raw.iterrows():
    ks, kd = norm(r["state"]), norm(r["district"])
    ids = by_name.get((ks, kd))
    method = "same state + same district name"
    if not ids and (ks, kd) in by_cw:
        ids, lists = by_cw[(ks, kd)]
        method = f"same state + name already linked by the crosswalk ({lists})"
    if ids and len(ids) == 1:
        links.append(dict(rhs_state=r["state"], rhs_district=r["district"], polygon_district_id=ids[0], link_status="linked", link_method=method))
    else:
        names = poly[poly["ks"] == ks]
        near = difflib.get_close_matches(kd, list(names["kd"]), n=1, cutoff=0.0)
        near_name = names[names["kd"] == near[0]]["district_name"].iloc[0] if near else None
        why = ("more than one map district has this name in the state" if ids else
               ("no map district in this state" if names.empty else "no map district with this name in the state; not guessed"))
        links.append(dict(rhs_state=r["state"], rhs_district=r["district"], polygon_district_id=None, link_status="unlinked", link_method=None))
        issues.append(dict(list="rhs_2021_22", state=r["state"], name=r["district"], issue="unlinked", detail=why, nearest_polygon_name=near_name))

L = pd.DataFrame(links)
schema_l = "rhs_state string, rhs_district string, polygon_district_id string, link_status string, link_method string"
spark.createDataFrame(L.astype(object).where(L.notna(), None), schema=schema_l).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("workspace.vf_gold.district_rhs_crosswalk")
I = pd.DataFrame(issues, columns=["list", "state", "name", "issue", "detail", "nearest_polygon_name"])
spark.createDataFrame(I.astype(object).where(I.notna(), None), schema="list string, state string, name string, issue string, detail string, nearest_polygon_name string") \
    .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("workspace.vf_gold.district_rhs_link_issues")
print(L["link_status"].value_counts().to_dict())

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TABLE workspace.vf_gold.district_public_facilities AS
WITH r AS (
  SELECT x.polygon_district_id AS district_id, count(*) AS rhs_districts_linked, concat_ws('; ', sort_array(collect_list(x.rhs_district))) AS rhs_district_names,
         sum(CAST(p.sub_centres AS INT)) AS govt_sub_centres, sum(CAST(p.phcs AS INT)) AS govt_phcs, sum(CAST(p.chcs AS INT)) AS govt_chcs,
         sum(CAST(p.sub_divisional_hospitals AS INT)) AS govt_sub_divisional_hospitals, sum(CAST(p.district_hospitals AS INT)) AS govt_district_hospitals
  FROM workspace.vf_gold.district_rhs_crosswalk x
  JOIN workspace.vf_bronze.ext_rhs_district_facilities_raw p ON p.state = x.rhs_state AND p.district = x.rhs_district
  WHERE x.link_status = 'linked' GROUP BY 1)
SELECT c.district_id, c.district_name, c.state_name, c.population_2020,
       coalesce(r.rhs_districts_linked, 0) AS rhs_districts_linked, r.rhs_district_names,
       CASE WHEN r.district_id IS NULL THEN 'no RHS district linked (see district_rhs_link_issues); counts unknown, not zero'
            WHEN r.rhs_districts_linked > 1 THEN 'sum of several RHS districts' ELSE 'one RHS district' END AS rhs_status,
       r.govt_sub_centres, r.govt_phcs, r.govt_chcs, r.govt_sub_divisional_hospitals, r.govt_district_hospitals,
       r.govt_sub_centres + r.govt_phcs + r.govt_chcs + r.govt_sub_divisional_hospitals + r.govt_district_hospitals AS govt_facilities_total,
       r.govt_chcs + r.govt_sub_divisional_hospitals + r.govt_district_hospitals AS govt_hospitals_chc_and_above,
       c.facilities_located AS our_facilities_located,
       c.facilities_rung_3 + c.facilities_rung_4 + c.facilities_rung_5 AS our_facilities_rung3plus,
       c.facilities_located - (r.govt_sub_centres + r.govt_phcs + r.govt_chcs + r.govt_sub_divisional_hospitals + r.govt_district_hospitals) AS our_minus_govt_total,
       round(c.population_2020 / nullif(r.govt_chcs + r.govt_sub_divisional_hospitals + r.govt_district_hospitals, 0)) AS people_per_govt_hospital,
       DATE'2022-03-31' AS govt_counts_as_of,
       'Rural Health Statistics 2021-22 (MoHFW), Section II' AS govt_counts_source,
       'Government counts and our facilities are different sets (ours are mostly private). Not a share of each other.' AS comparison_note
FROM workspace.vf_gold.district_context c LEFT JOIN r ON r.district_id = c.district_id
""")
spark.sql("""
CREATE OR REPLACE VIEW workspace.vf_gold.district_context_with_rhs AS
SELECT c.*, p.rhs_status, p.govt_sub_centres, p.govt_phcs, p.govt_chcs, p.govt_sub_divisional_hospitals, p.govt_district_hospitals,
       p.govt_facilities_total, p.govt_hospitals_chc_and_above, p.people_per_govt_hospital, p.govt_counts_as_of, p.govt_counts_source
FROM workspace.vf_gold.district_context c LEFT JOIN workspace.vf_gold.district_public_facilities p USING (district_id)
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ## State level: government beds and staff (RHS 2021-22, rural areas, as on 31 March 2022)
# MAGIC Long format, one row per state, table, measure and column. A blank value with a `value_note` means surplus or not available. It is never zero and stays blank here.

# COMMAND ----------

CSV2 = "/Volumes/workspace/vf_bronze/external/rhs/rhs_2021_22_state_beds_staff.csv"
st = pd.read_csv(CSV2, dtype=str)
st["source_type"] = "external"
st["source_ref"] = st["source_name"] + ", " + st["rhs_table"]
st["confidence"] = "high: official government count, dated 2022-03-31"
st["source_file"] = "dbfs:" + CSV2
cols = ", ".join(f"{c} string" for c in st.columns)
spark.createDataFrame(st.astype(object).where(st.notna(), None), schema=cols).selectExpr("*", "current_timestamp() AS imported_at") \
    .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("workspace.vf_bronze.ext_rhs_state_beds_staff_raw")

spark.sql("""
CREATE OR REPLACE TABLE workspace.vf_gold.state_public_health_capacity AS
WITH r AS (SELECT state, rhs_table, `column` AS col, CAST(value AS INT) AS v, value_note FROM workspace.vf_bronze.ext_rhs_state_beds_staff_raw)
SELECT state,
       max(CASE WHEN rhs_table = 'Table 43' AND col = 'phc' THEN v END) AS govt_beds_phc,
       max(CASE WHEN rhs_table = 'Table 43' AND col = 'chc' THEN v END) AS govt_beds_chc,
       max(CASE WHEN rhs_table = 'Table 43' AND col = 'sdh' THEN v END) AS govt_beds_sub_divisional_hospital,
       max(CASE WHEN rhs_table = 'Table 43' AND col = 'dh' THEN v END) AS govt_beds_district_hospital,
       max(CASE WHEN rhs_table = 'Table 43' AND col = 'medical_college' THEN v END) AS govt_beds_medical_college,
       max(CASE WHEN rhs_table = 'Table 16' AND col = 'required' THEN v END) AS phc_doctors_required,
       max(CASE WHEN rhs_table = 'Table 16' AND col = 'in_position' THEN v END) AS phc_doctors_in_position,
       max(CASE WHEN rhs_table = 'Table 16' AND col = 'shortfall' THEN v END) AS phc_doctors_shortfall,
       max(CASE WHEN rhs_table = 'Table 16' AND col = 'shortfall' THEN value_note END) AS phc_doctors_shortfall_note,
       max(CASE WHEN rhs_table = 'Table 23' AND col = 'required' THEN v END) AS chc_specialists_required,
       max(CASE WHEN rhs_table = 'Table 23' AND col = 'in_position' THEN v END) AS chc_specialists_in_position,
       max(CASE WHEN rhs_table = 'Table 23' AND col = 'shortfall' THEN v END) AS chc_specialists_shortfall,
       max(CASE WHEN rhs_table = 'Table 23' AND col = 'shortfall' THEN value_note END) AS chc_specialists_shortfall_note,
       round(100.0 * max(CASE WHEN rhs_table = 'Table 23' AND col = 'shortfall' THEN v END) / nullif(max(CASE WHEN rhs_table = 'Table 23' AND col = 'required' THEN v END), 0), 1) AS chc_specialists_shortfall_pct,
       max(CASE WHEN rhs_table = 'Table 35' AND col = 'dh_sanctioned' THEN v END) AS dh_doctors_sanctioned,
       max(CASE WHEN rhs_table = 'Table 35' AND col = 'dh_in_position' THEN v END) AS dh_doctors_in_position,
       DATE'2022-03-31' AS as_of, 'Rural Health Statistics 2021-22 (MoHFW), rural areas; Tables 16, 23, 35, 43' AS source,
       'A blank means surplus or not available (see the note column), never zero.' AS reading_note
FROM r GROUP BY state
""")

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TABLE workspace.vf_gold.rhs_checks AS
SELECT 1 AS n, 'RHS rows loaded' AS check_name, CAST(count(*) AS STRING) AS measured, count(*) = 737 AS passed FROM workspace.vf_bronze.ext_rhs_district_facilities_raw
UNION ALL SELECT 2, 'all-India totals match the book (161829 / 31053 / 6064 / 1275 / 767)',
       concat(sum(CAST(sub_centres AS INT)), ' / ', sum(CAST(phcs AS INT)), ' / ', sum(CAST(chcs AS INT)), ' / ', sum(CAST(sub_divisional_hospitals AS INT)), ' / ', sum(CAST(district_hospitals AS INT))),
       sum(CAST(sub_centres AS INT)) = 161829 AND sum(CAST(phcs AS INT)) = 31053 AND sum(CAST(chcs AS INT)) = 6064 AND sum(CAST(sub_divisional_hospitals AS INT)) = 1275 AND sum(CAST(district_hospitals AS INT)) = 767
       FROM workspace.vf_bronze.ext_rhs_district_facilities_raw
UNION ALL SELECT 3, 'RHS districts linked / unlinked', concat(sum(CAST(link_status = 'linked' AS INT)), ' / ', sum(CAST(link_status <> 'linked' AS INT))), true FROM workspace.vf_gold.district_rhs_crosswalk
UNION ALL SELECT 4, 'every unlinked RHS district is in the issues table', concat((SELECT count(*) FROM workspace.vf_gold.district_rhs_crosswalk WHERE link_status <> 'linked'), ' vs ', (SELECT count(*) FROM workspace.vf_gold.district_rhs_link_issues)),
       (SELECT count(*) FROM workspace.vf_gold.district_rhs_crosswalk WHERE link_status <> 'linked') = (SELECT count(*) FROM workspace.vf_gold.district_rhs_link_issues)
UNION ALL SELECT 5, 'links without a method (name-only joins)', CAST(count(*) AS STRING), count(*) = 0 FROM workspace.vf_gold.district_rhs_crosswalk WHERE link_status = 'linked' AND link_method IS NULL
UNION ALL SELECT 6, 'one row per map district', concat(count(*), ' rows, ', count(DISTINCT district_id), ' ids'), count(*) = count(DISTINCT district_id) AND count(*) = (SELECT count(*) FROM workspace.vf_gold.district_context) FROM workspace.vf_gold.district_public_facilities
UNION ALL SELECT 7, 'govt facilities in linked districts = sum over the map table (nothing counted twice)',
       concat((SELECT sum(govt_facilities_total) FROM workspace.vf_gold.district_public_facilities), ' vs ',
              (SELECT sum(CAST(sub_centres AS INT) + CAST(phcs AS INT) + CAST(chcs AS INT) + CAST(sub_divisional_hospitals AS INT) + CAST(district_hospitals AS INT)) FROM workspace.vf_bronze.ext_rhs_district_facilities_raw p JOIN workspace.vf_gold.district_rhs_crosswalk x ON p.state = x.rhs_state AND p.district = x.rhs_district WHERE x.link_status = 'linked')),
       (SELECT sum(govt_facilities_total) FROM workspace.vf_gold.district_public_facilities) =
              (SELECT sum(CAST(sub_centres AS INT) + CAST(phcs AS INT) + CAST(chcs AS INT) + CAST(sub_divisional_hospitals AS INT) + CAST(district_hospitals AS INT)) FROM workspace.vf_bronze.ext_rhs_district_facilities_raw p JOIN workspace.vf_gold.district_rhs_crosswalk x ON p.state = x.rhs_state AND p.district = x.rhs_district WHERE x.link_status = 'linked')
UNION ALL SELECT 8, 'map districts with government counts / without', concat(sum(CAST(rhs_districts_linked > 0 AS INT)), ' / ', sum(CAST(rhs_districts_linked = 0 AS INT))), true FROM workspace.vf_gold.district_public_facilities
UNION ALL SELECT 10, 'state rows loaded / states', concat(count(*), ' / ', count(DISTINCT state)), count(*) = 1692 AND count(DISTINCT state) = 36 FROM workspace.vf_bronze.ext_rhs_state_beds_staff_raw
UNION ALL SELECT 11, 'blank values turned into zero (must be 0)', CAST(count(*) AS STRING), count(*) = 0 FROM workspace.vf_bronze.ext_rhs_state_beds_staff_raw WHERE value_note IS NOT NULL AND value IS NOT NULL
UNION ALL SELECT 12, 'all-India CHC specialists: required / in position / shortfall', concat(sum(chc_specialists_required), ' / ', sum(chc_specialists_in_position), ' / ', sum(chc_specialists_shortfall)), true FROM workspace.vf_gold.state_public_health_capacity
UNION ALL SELECT 9, 'district_context unchanged', CAST(count(*) AS STRING), count(*) = 735 FROM workspace.vf_gold.district_context
""")
out = {"checks": [r.asDict() for r in spark.sql("SELECT * FROM workspace.vf_gold.rhs_checks ORDER BY n").collect()],
       "issues": [r.asDict() for r in spark.sql("SELECT state, name, detail, nearest_polygon_name FROM workspace.vf_gold.district_rhs_link_issues ORDER BY 1, 2").collect()],
       "state_top": [r.asDict() for r in spark.sql("SELECT state, chc_specialists_required, chc_specialists_in_position, chc_specialists_shortfall, chc_specialists_shortfall_pct FROM workspace.vf_gold.state_public_health_capacity WHERE chc_specialists_required >= 200 ORDER BY chc_specialists_shortfall_pct DESC LIMIT 8").collect()],
       "totals": [r.asDict() for r in spark.sql("""SELECT sum(govt_facilities_total) govt_total, sum(govt_hospitals_chc_and_above) govt_hosp, sum(our_facilities_located) ours_in_linked,
            sum(CAST(our_facilities_located = 0 AND govt_hospitals_chc_and_above > 0 AS INT)) districts_ours_zero_govt_has_hospital,
            sum(CASE WHEN our_facilities_located = 0 THEN govt_hospitals_chc_and_above END) govt_hospitals_in_those
            FROM workspace.vf_gold.district_public_facilities WHERE rhs_districts_linked > 0""").collect()]}
dbutils.notebook.exit(json.dumps(out, default=str))
