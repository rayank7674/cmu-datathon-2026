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
# MAGIC | `vf_gold.critical_zones_govt_caveat` | one row per critical block and service | every critical block with the government counts for its district and the flag `govt_care_exists_not_in_our_data` |
# MAGIC | `vf_gold.district_critical_govt_caveat` | district x service | critical blocks and people per district, with the flag, for the map |
# MAGIC | `vf_gold.rhs_checks` | one row per check | measured validation |
# MAGIC
# MAGIC **Linking rule (same as notebook 11: never the name alone).** An RHS district links to a map district only when the state is the same and either the names are the same, or the RHS name is one the existing crosswalk already links (PIN-code or NFHS-5 district lists) in that state. No fuzzy matching. After that, 28 names were confirmed by hand on 2026-11-22 (clear spelling variants and renames, each recorded with its reason in `link_method`). Anything else is left unlinked and listed.
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
# Confirmed by hand on 2026-11-22 (team decision): clear spelling variants and renames within the same state only.
# (RHS state, RHS district): (map state, map district, reason)
SPELL = "same district, spelling variant"
MANUAL = {
    ("Chhattisgarh", "Garyaband"): ("Chhattisgarh", "Gariaband", SPELL),
    ("Chhattisgarh", "Korya"): ("Chhattisgarh", "Koriya", SPELL),
    ("Chhattisgarh", "Mungali"): ("Chhattisgarh", "Mungeli", SPELL),
    ("Chhattisgarh", "Kanker"): ("Chhattisgarh", "Uttar Bastar Kanker", "same district; the map uses the full official name"),
    ("Chhattisgarh", "Kawardha"): ("Chhattisgarh", "Kabeerdham", "rename: Kawardha district is officially Kabirdham"),
    ("Gujarat", "Chhotaudepur"): ("Gujarat", "Chhota Udaipur", SPELL),
    ("Jammu & Kashmir", "Budgam"): ("Jammu and Kashmir", "Badgam", SPELL),
    ("Jammu & Kashmir", "Poonch"): ("Jammu and Kashmir", "Punch", SPELL),
    ("Jammu & Kashmir", "Shopian"): ("Jammu and Kashmir", "Shupiyan", SPELL),
    ("Jharkhand", "Pashchmi Singhbhum"): ("Jharkhand", "Pashchimi Singhbhum", SPELL),
    ("Jharkhand", "Purbi Singhbum"): ("Jharkhand", "Purbi Singhbhum", SPELL),
    ("Karnataka", "Bagalkote"): ("Karnataka", "Bagalkot", SPELL),
    ("Madhya Pradesh", "Agar Malwa"): ("Madhya Pradesh", "Agar", "same district; the map uses the short name"),
    ("Tamil Nadu", "Kanyakumari"): ("Tamil Nadu", "Kanniyakumari", SPELL),
    ("Tamil Nadu", "Pudukottai"): ("Tamil Nadu", "Pudukkottai", SPELL),
    ("Tamil Nadu", "Thirupattur"): ("Tamil Nadu", "Tirupathur", SPELL),
    ("Tamil Nadu", "Thoothukudi"): ("Tamil Nadu", "Thoothukkudi", SPELL),
    ("Tamil Nadu", "Tiruchirapalli"): ("Tamil Nadu", "Tiruchirappalli", SPELL),
    ("Tamil Nadu", "Tirupur"): ("Tamil Nadu", "Tiruppur", SPELL),
    ("Tamil Nadu", "Tiruvallur"): ("Tamil Nadu", "Thiruvallur", SPELL),
    ("Tamil Nadu", "Tiruvarur"): ("Tamil Nadu", "Thiruvarur", SPELL),
    ("Uttar Pradesh", "Hathras"): ("Uttar Pradesh", "Mahamaya Nagar", "rename: Hathras district was called Mahamaya Nagar until 2012; the map keeps the old name"),
    ("West Bengal", "Malda"): ("West Bengal", "Maldah", SPELL),
    ("West Bengal", "North 24 Parganas"): ("West Bengal", "North Twenty Four Parganas", SPELL),
    ("West Bengal", "South 24 Parganas"): ("West Bengal", "South Twenty Four Parganas", SPELL),
    ("West Bengal", "Paschim Bardhhaman"): ("West Bengal", "Paschim Barddhaman", SPELL),
    ("West Bengal", "Purba Bardhhaman"): ("West Bengal", "Barddhaman", "Barddhaman was split in 2017; the map has Paschim Barddhaman as its own district, so the district it still calls Barddhaman is Purba Bardhaman"),
    ("Puducherry", "Yanam"): ("Andhra Pradesh", "Yanam", "same district; Yanam is a Puducherry enclave inside Andhra Pradesh and the map assigns states by position"),
}
# Looked at by hand and deliberately left unlinked.
LEFT = {
    ("Nagaland", "Noklak"): "created in 2021 out of Tuensang; the map has no separate district and the Tuensang count is already linked",
    ("Punjab", "Malerkotla"): "created in 2021 out of Sangrur; the map has no separate district and the Sangrur count is already linked",
    ("Telangana", "Hanumakonda"): "Warangal Urban was renamed Hanumakonda in 2021 and its boundary with Warangal changed at the same time, so the map district is not the same area",
}
links, issues = [], []
for _, r in raw.iterrows():
    ks, kd = norm(r["state"]), norm(r["district"])
    ids = by_name.get((ks, kd))
    method = "same state + same district name"
    if not ids and (ks, kd) in by_cw:
        ids, lists = by_cw[(ks, kd)]
        method = f"same state + name already linked by the crosswalk ({lists})"
    if not ids and (r["state"], r["district"]) in MANUAL:
        ms, md, reason = MANUAL[(r["state"], r["district"])]
        ids = by_name.get((norm(ms), norm(md)))
        method = f"confirmed by hand 2026-11-22: {reason} (map name: {md})"
    if ids and len(ids) == 1:
        links.append(dict(rhs_state=r["state"], rhs_district=r["district"], polygon_district_id=ids[0], link_status="linked", link_method=method))
    else:
        names = poly[poly["ks"] == ks]
        near = difflib.get_close_matches(kd, list(names["kd"]), n=1, cutoff=0.0)
        near_name = names[names["kd"] == near[0]]["district_name"].iloc[0] if near else None
        why = LEFT.get((r["state"], r["district"])) and ("checked by hand 2026-11-22, left unlinked: " + LEFT[(r["state"], r["district"])])
        why = why or ("more than one map district has this name in the state" if ids else
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

# MAGIC %md
# MAGIC ## Critical-area caveat: government care that our data cannot see
# MAGIC A critical block means "far from any facility **in this dataset**". Where the block's district has no facility in our data at all but the government counts community health centres or hospitals there, the block is flagged `govt_care_exists_not_in_our_data`. The flag does not remove the block and does not say the care is near: RHS gives district counts, not locations, so no distance or catchment is drawn for those facilities.
# MAGIC
# MAGIC `govt_hospitals_in_district` is filled for every block, flagged or not, because government health centres exist in almost every district and most are missing from our data.

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TABLE workspace.vf_gold.critical_zones_govt_caveat AS
WITH z AS (
  SELECT block_id, service, zone_type, latitude, longitude, population, district_id, district_name, state_name FROM workspace.vf_gold.critical_zones
  UNION ALL
  SELECT block_id, 'life_threatening_150km' AS service, 'critical' AS zone_type, latitude, longitude, population, district_id, district_name, state_name FROM workspace.vf_gold.critical_zones_emergency_150km)
SELECT z.*, p.our_facilities_located AS our_facilities_in_district, p.govt_hospitals_chc_and_above AS govt_hospitals_in_district,
       p.govt_chcs, p.govt_sub_divisional_hospitals, p.govt_district_hospitals, p.rhs_status,
       coalesce(p.our_facilities_located = 0 AND p.govt_hospitals_chc_and_above > 0, false) AS govt_care_exists_not_in_our_data,
       CASE WHEN p.our_facilities_located = 0 AND p.govt_hospitals_chc_and_above > 0
            THEN concat('Government care exists, not in our data: the district has ', p.govt_hospitals_chc_and_above, ' government community health centres or hospitals (March 2022) and no facility in this dataset. Locations unknown, so distance to them is unknown.')
            WHEN p.govt_hospitals_chc_and_above IS NULL THEN 'Government counts unknown for this district (not linked).'
            ELSE concat('The district also has ', p.govt_hospitals_chc_and_above, ' government community health centres or hospitals (March 2022); most are not in this dataset.') END AS govt_caveat,
       DATE'2022-03-31' AS govt_counts_as_of
FROM z LEFT JOIN workspace.vf_gold.district_public_facilities p ON p.district_id = z.district_id
""")
spark.sql("""
CREATE OR REPLACE TABLE workspace.vf_gold.district_critical_govt_caveat AS
SELECT c.district_id, c.district_name, c.state_name, d.centre_lat, d.centre_lon, c.service,
       count(*) AS critical_blocks, round(sum(c.population)) AS critical_people,
       max(c.govt_hospitals_in_district) AS govt_hospitals_in_district, max(c.govt_chcs) AS govt_chcs,
       max(c.govt_sub_divisional_hospitals) AS govt_sub_divisional_hospitals, max(c.govt_district_hospitals) AS govt_district_hospitals,
       max(c.our_facilities_in_district) AS our_facilities_in_district, max(c.govt_care_exists_not_in_our_data) AS govt_care_exists_not_in_our_data
FROM workspace.vf_gold.critical_zones_govt_caveat c JOIN workspace.vf_gold.district_context d ON d.district_id = c.district_id
WHERE c.zone_type = 'critical' GROUP BY 1, 2, 3, 4, 5, 6
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
UNION ALL SELECT 13, 'links confirmed by hand / map districts given two RHS districts by a hand link', concat((SELECT count(*) FROM workspace.vf_gold.district_rhs_crosswalk WHERE link_method LIKE 'confirmed by hand%'), ' / ',
       (SELECT count(*) FROM (SELECT polygon_district_id FROM workspace.vf_gold.district_rhs_crosswalk WHERE link_status = 'linked' GROUP BY 1 HAVING count(*) > 1 AND sum(CAST(link_method LIKE 'confirmed by hand%' AS INT)) > 0))),
       (SELECT count(*) FROM (SELECT polygon_district_id FROM workspace.vf_gold.district_rhs_crosswalk WHERE link_status = 'linked' GROUP BY 1 HAVING count(*) > 1 AND sum(CAST(link_method LIKE 'confirmed by hand%' AS INT)) > 0)) = 0
UNION ALL SELECT 14, 'caveat table rows = critical_zones + 150 km blocks (no block lost or doubled)', concat((SELECT count(*) FROM workspace.vf_gold.critical_zones_govt_caveat), ' vs ', (SELECT count(*) FROM workspace.vf_gold.critical_zones) + (SELECT count(*) FROM workspace.vf_gold.critical_zones_emergency_150km)),
       (SELECT count(*) FROM workspace.vf_gold.critical_zones_govt_caveat) = (SELECT count(*) FROM workspace.vf_gold.critical_zones) + (SELECT count(*) FROM workspace.vf_gold.critical_zones_emergency_150km)
UNION ALL SELECT 15, 'flagged blocks in a district that has a facility in our data (must be 0)', CAST(count(*) AS STRING), count(*) = 0 FROM workspace.vf_gold.critical_zones_govt_caveat WHERE govt_care_exists_not_in_our_data AND our_facilities_in_district > 0
UNION ALL SELECT 9, 'district_context unchanged', CAST(count(*) AS STRING), count(*) = 735 FROM workspace.vf_gold.district_context
""")
out = {"checks": [r.asDict() for r in spark.sql("SELECT * FROM workspace.vf_gold.rhs_checks ORDER BY n").collect()],
       "issues": [r.asDict() for r in spark.sql("SELECT state, name, detail, nearest_polygon_name FROM workspace.vf_gold.district_rhs_link_issues ORDER BY 1, 2").collect()],
       "state_top": [r.asDict() for r in spark.sql("SELECT state, chc_specialists_required, chc_specialists_in_position, chc_specialists_shortfall, chc_specialists_shortfall_pct FROM workspace.vf_gold.state_public_health_capacity WHERE chc_specialists_required >= 200 ORDER BY chc_specialists_shortfall_pct DESC LIMIT 8").collect()],
       "caveat": [r.asDict() for r in spark.sql("SELECT service, count(*) critical_blocks, round(sum(population)/1e6, 2) critical_people_m, sum(CAST(govt_care_exists_not_in_our_data AS INT)) flagged_blocks, round(sum(CASE WHEN govt_care_exists_not_in_our_data THEN population END)/1e6, 2) flagged_people_m, count(DISTINCT CASE WHEN govt_care_exists_not_in_our_data THEN district_id END) flagged_districts FROM workspace.vf_gold.critical_zones_govt_caveat WHERE zone_type = 'critical' GROUP BY 1 ORDER BY 1").collect()],
       "totals": [r.asDict() for r in spark.sql("""SELECT sum(govt_facilities_total) govt_total, sum(govt_hospitals_chc_and_above) govt_hosp, sum(our_facilities_located) ours_in_linked,
            sum(CAST(our_facilities_located = 0 AND govt_hospitals_chc_and_above > 0 AS INT)) districts_ours_zero_govt_has_hospital,
            sum(CASE WHEN our_facilities_located = 0 THEN govt_hospitals_chc_and_above END) govt_hospitals_in_those
            FROM workspace.vf_gold.district_public_facilities WHERE rhs_districts_linked > 0""").collect()]}
dbutils.notebook.exit(json.dumps(out, default=str))
