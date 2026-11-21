# Databricks notebook source


# COMMAND ----------



# COMMAND ----------

# DBTITLE 1,Introduction
# MAGIC %md
# MAGIC # Virtue Foundation Dataset — Data Exploration & Overview
# MAGIC
# MAGIC This notebook performs an exploratory data analysis (EDA) of the Virtue Foundation dataset in Unity Catalog schema `databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset`. The dataset contains three tables:
# MAGIC
# MAGIC | Table | Rows | Description |
# MAGIC | --- | --- |
# MAGIC | `facilities` | ~10,088 | Global facility directory: name, organization type, address, capacity, specialties, equipment, geo-coordinates, and social/engagement metrics |
# MAGIC | `india_post_pincode_directory` | ~165,627 | India Post pincode lookup: pincode → post office, district, state |
# MAGIC | `nfhs_5_district_health_indicators` | ~706 | District-level health indicator percentages from India's NFHS-5 survey |
# MAGIC
# MAGIC **Notebook goals**
# MAGIC
# MAGIC 1. Profile each table: schema, row counts, null rates on key columns, and distribution summaries (facilities by country and state/region, pincode directory by state, NFHS by state/UT).
# MAGIC 2. Test join feasibility for the intended analysis path:
# MAGIC    `facilities.address_zipOrPostcode` → `india_post_pincode_directory.pincode` → `district` → `nfhs_5_district_health_indicators.district_name`
# MAGIC
# MAGIC This tells us how many facilities can be enriched with district-level health indicators.

# COMMAND ----------

# DBTITLE 1,Table overview: row and column counts
from pyspark.sql import functions as F

CATALOG = "databricks_virtue_foundation_dataset_dais_2026"
SCHEMA = "virtue_foundation_dataset"
TABLES = ["facilities", "india_post_pincode_directory", "nfhs_5_district_health_indicators"]

facilities = spark.table(f"{CATALOG}.{SCHEMA}.facilities")
pincode_dir = spark.table(f"{CATALOG}.{SCHEMA}.india_post_pincode_directory")
nfhs = spark.table(f"{CATALOG}.{SCHEMA}.nfhs_5_district_health_indicators")

# Table-level overview: row counts and column counts
overview = []
for t, df in [("facilities", facilities), ("india_post_pincode_directory", pincode_dir), ("nfhs_5_district_health_indicators", nfhs)]:
    overview.append((t, df.count(), len(df.columns)))
display(spark.createDataFrame(overview, ["table", "row_count", "column_count"]))

# COMMAND ----------

# DBTITLE 1,Facilities section header
# MAGIC %md
# MAGIC ## 1. `facilities` — global facility directory
# MAGIC
# MAGIC Schema overview, null rates on key columns, and distributions by country and state/region.

# COMMAND ----------

# DBTITLE 1,Facilities schema
print(f"facilities: {facilities.count()} rows, {len(facilities.columns)} columns")
display(spark.createDataFrame(facilities.dtypes, ["column", "data_type"]))

# COMMAND ----------

# DBTITLE 1,Facilities null rates on key columns
def null_rate_report(df, cols):
    """DataFrame report of null counts, null rates (%), and distinct-value counts for the given columns."""
    total = df.count()
    aggs = []
    for c in cols:
        aggs.append(F.sum(F.col(c).isNull().cast("int")).alias(c + "__nulls"))
        aggs.append(F.countDistinct(c).alias(c + "__distincts"))
    row = df.agg(*aggs).collect()[0].asDict()
    return spark.createDataFrame(
        [(c, row[c + "__nulls"], round(100.0 * row[c + "__nulls"] / total, 2), row[c + "__distincts"]) for c in cols],
        ["column", "null_count", "null_rate_pct", "distinct_values"],
    )

FACILITY_KEY_COLS = [
    "unique_id", "name", "organization_type", "address_city", "address_stateOrRegion",
    "address_zipOrPostcode", "address_country", "capacity", "numberDoctors",
    "specialties", "equipment", "latitude", "longitude", "cluster_id",
]
display(null_rate_report(facilities, FACILITY_KEY_COLS))

# COMMAND ----------

# DBTITLE 1,Facilities distributions: country and state
print("Facilities by address_country (top 20):")
display(
    facilities.groupBy(F.coalesce(F.col("address_country"), F.lit("(null)")).alias("address_country"))
    .count().orderBy(F.desc("count")).limit(20)
)

print("Facilities by address_stateOrRegion (top 20):")
display(
    facilities.groupBy(F.coalesce(F.col("address_stateOrRegion"), F.lit("(null)")).alias("address_stateOrRegion"))
    .count().orderBy(F.desc("count")).limit(20)
)

# COMMAND ----------

# DBTITLE 1,Pincode section header
# MAGIC %md
# MAGIC ## 2. `india_post_pincode_directory` — pincode → district/state lookup
# MAGIC
# MAGIC Schema overview, null rates on key columns, and distribution by state.

# COMMAND ----------

# DBTITLE 1,Pincode schema and null rates
print(f"india_post_pincode_directory: {pincode_dir.count()} rows, {len(pincode_dir.columns)} columns")
display(spark.createDataFrame(pincode_dir.dtypes, ["column", "data_type"]))

PINCODE_KEY_COLS = ["pincode", "officename", "officetype", "delivery", "district", "statename", "latitude", "longitude"]
display(null_rate_report(pincode_dir, PINCODE_KEY_COLS))

# COMMAND ----------

# DBTITLE 1,Pincode distribution by state
print("Post offices by statename (top 25, with distinct pincodes and districts):")
display(
    pincode_dir.groupBy(F.coalesce(F.col("statename"), F.lit("(null)")).alias("statename"))
    .agg(F.count("*").alias("post_offices"),
          F.countDistinct("pincode").alias("distinct_pincodes"),
          F.countDistinct("district").alias("distinct_districts"))
    .orderBy(F.desc("post_offices")).limit(25)
)

# COMMAND ----------

# DBTITLE 1,NFHS section header
# MAGIC %md
# MAGIC ## 3. `nfhs_5_district_health_indicators` — district-level health indicators
# MAGIC
# MAGIC Schema overview, a check on the STRING-typed percentage columns, null rates on key columns, and distribution by state/UT.

# COMMAND ----------

# DBTITLE 1,NFHS schema and STRING pct check
from collections import Counter

print(f"nfhs_5_district_health_indicators: {nfhs.count()} rows, {len(nfhs.columns)} columns")
type_counts = Counter(t for _, t in nfhs.dtypes)
display(spark.createDataFrame(sorted(type_counts.items()), ["data_type", "n_columns"]))

# Many *_pct columns hold percentages but are typed STRING — quantify and verify they are numeric-looking
pct_cols = [c for c, t in nfhs.dtypes if c.endswith("_pct")]
string_pct_cols = [c for c, t in nfhs.dtypes if c.endswith("_pct") and t == "string"]
print(f"{len(pct_cols)} columns end in '_pct'; {len(string_pct_cols)} of them are STRING despite holding percentages.")

example_col = "child_12_23m_fully_vaccinated_based_on_information_from_eit_pct"
non_numeric = nfhs.where(
    F.col(example_col).isNotNull() & F.expr(f"try_cast(`{example_col}` AS DOUBLE) IS NULL")
).count()
print(f"Sample check on STRING pct column `{example_col}`: {non_numeric} non-null values fail to cast to DOUBLE.")
display(nfhs.select("district_name", "state_ut", example_col, F.expr(f"try_cast(`{example_col}` AS DOUBLE)").alias(example_col + "_as_double")).limit(10))

# COMMAND ----------

# DBTITLE 1,NFHS null rates and distribution by state
NFHS_KEY_COLS = [
    "district_name", "state_ut", "households_surveyed", "women_15_49_interviewed",
    "hh_electricity_pct", "hh_improved_water_pct", "institutional_birth_5y_pct",
    "child_12_23m_fully_vaccinated_based_on_information_from_eit_pct",
    "fp_cm_w15_49_any_method_pct",
]
display(null_rate_report(nfhs, NFHS_KEY_COLS))

print("NFHS districts per state_ut (top 25):")
display(
    nfhs.groupBy(F.coalesce(F.col("state_ut"), F.lit("(null)")).alias("state_ut"))
    .agg(F.count("*").alias("district_rows"), F.countDistinct("district_name").alias("distinct_district_names"))
    .orderBy(F.desc("district_rows")).limit(25)
)

# COMMAND ----------

# DBTITLE 1,Join feasibility header
# MAGIC %md
# MAGIC ## 4. Join feasibility: facilities → pincode → district → NFHS
# MAGIC
# MAGIC Intended analysis path: `facilities.address_zipOrPostcode` → `india_post_pincode_directory.pincode` → `district` → `nfhs_5_district_health_indicators.district_name`.
# MAGIC
# MAGIC Normalizations applied:
# MAGIC
# MAGIC * Facility postcodes are stripped of non-digit characters (some contain spaces, e.g. `"201 301"`) and must be exactly 6 digits to match an Indian pincode.
# MAGIC * District names are compared on `lower(trim(...))` on both sides.
# MAGIC
# MAGIC The funnel below reports how many facilities match a pincode and how many resolve to an NFHS district, followed by a sample of joined rows.

# COMMAND ----------

# DBTITLE 1,Join feasibility funnel
# --- Clean facility postcodes: keep digits only (handles values like "201 301") ---
fac_loc = facilities.select(
    "unique_id", "name", "address_city", "address_stateOrRegion", "address_country",
    F.regexp_replace(F.trim(F.col("address_zipOrPostcode")), "[^0-9]", "").alias("zip_clean"),
)
fac_valid = fac_loc.filter(F.length("zip_clean") == 6)
fac_valid_india = fac_valid.filter(F.col("address_country") == "India")

# --- Pincode -> district lookup (one row per distinct pincode/district/state) ---
pin_map = pincode_dir.select(
    "pincode",
    F.lower(F.trim("district")).alias("district_norm"),
    "statename",
).distinct()
n_pincodes = pin_map.select("pincode").distinct().count()
n_multi_district_pincodes = pin_map.groupBy("pincode").count().filter("count > 1").count()
print(f"{n_pincodes} distinct pincodes; {n_multi_district_pincodes} map to more than one district.")

# --- Join facilities to pincode map ---
f_pin = fac_valid.join(pin_map, F.col("zip_clean").cast("long") == F.col("pincode"), "left")
fac_with_pin = f_pin.filter(F.col("pincode").isNotNull())

# --- Join resolved districts to NFHS district names ---
nfhs_districts = nfhs.select(F.lower(F.trim("district_name")).alias("district_norm")).distinct().withColumn("nfhs_match", F.lit(1))
f_resolved = fac_with_pin.join(nfhs_districts, "district_norm", "left")
fac_with_nfhs = f_resolved.filter(F.col("nfhs_match") == 1)

# --- Funnel ---
total_fac = facilities.count()
india_fac = facilities.filter(F.col("address_country") == "India").count()
valid_zip_india = fac_valid_india.count()
pin_match = fac_with_pin.select("unique_id").distinct().count()
nfhs_resolved = fac_with_nfhs.select("unique_id").distinct().count()

funnel = spark.createDataFrame(
    [(s, c, round(100.0 * c / total_fac, 2)) for s, c in [
        ("Total facilities", total_fac),
        ("Facilities in India", india_fac),
        ("India facilities with valid 6-digit postcode", valid_zip_india),
        ("Facilities matching a pincode in the directory", pin_match),
        ("Facilities resolving to an NFHS district", nfhs_resolved),
    ]],
    ["stage", "facilities", "pct_of_total"],
)
display(funnel)

n_districts_resolved = fac_with_nfhs.select("district_norm").distinct().count()
print(f"Distinct NFHS districts reachable from facilities: {n_districts_resolved}")

# COMMAND ----------

# DBTITLE 1,Sample joined rows
# --- Sample of fully joined rows: facility -> pincode -> district -> NFHS indicators ---
nfhs_norm = nfhs.withColumn("district_norm", F.lower(F.trim("district_name")))

# District names can repeat across states in NFHS — check for ambiguity before joining on district alone
ambiguous = nfhs_norm.groupBy("district_norm").agg(F.countDistinct("state_ut").alias("n_states")).filter("n_states > 1")
print(f"NFHS district names appearing in more than one state: {ambiguous.count()}")

sample_joined = (
    fac_with_nfhs
    .join(nfhs_norm, "district_norm")
    .select(
        "unique_id", "name", "address_city", "zip_clean", "district_norm",
        "statename", "state_ut",
        F.col("hh_electricity_pct"),
        F.col("institutional_birth_5y_pct"),
        F.col("child_12_23m_fully_vaccinated_based_on_information_from_eit_pct"),
        F.col("fp_cm_w15_49_any_method_pct"),
    )
    .distinct()
    .limit(12)
)
display(sample_joined)

# COMMAND ----------

# DBTITLE 1,Key findings
# MAGIC %md
# MAGIC ## Key findings
# MAGIC
# MAGIC **Table profiles**
# MAGIC
# MAGIC * `facilities`: 10,088 rows × 51 columns. Null rates on all key columns are low (≤ 1.17%); `unique_id` has 10,077 distinct values (a few duplicates). 10,000 of 10,088 facilities are in India; the 58 null-country rows and ~30 malformed values make up the rest. Top states: Maharashtra (1,575), Gujarat (981), Uttar Pradesh (919).
# MAGIC * `india_post_pincode_directory`: 165,627 rows × 11 columns, zero nulls on all key columns; 19,586 distinct pincodes across 750 districts and 37 states.
# MAGIC * `nfhs_5_district_health_indicators`: 706 rows × 109 columns (58 double, 51 string), zero nulls on key columns. 101 columns end in `_pct`, of which 47 are STRING despite holding percentages — values include parentheses (e.g. `(64.2)`), asterisks (`*`), and trailing spaces, so they need cleaning (`try_cast` + stripping) before numeric use.
# MAGIC
# MAGIC **Join feasibility (facilities → pincode → district → NFHS)**
# MAGIC
# MAGIC | Stage | Facilities | % of total |
# MAGIC | --- | --- | --- |
# MAGIC | Total facilities | 10,088 | 100% |
# MAGIC | Facilities in India | 10,000 | 99.13% |
# MAGIC | India facilities with valid 6-digit postcode | 9,929 | 98.42% |
# MAGIC | Facilities matching a pincode in the directory | 9,707 | 96.22% |
# MAGIC | Facilities resolving to an NFHS district | 8,195 | 81.24% |
# MAGIC
# MAGIC * 81% of all facilities can be enriched with district-level NFHS-5 health indicators, reaching 475 distinct NFHS districts.
# MAGIC * 1,478 of 19,586 pincodes map to more than one district — a pincode-to-district join fans out; downstream analysis should pick or aggregate districts deliberately.
# MAGIC * 8 NFHS district names appear in more than one state, so joining on district name alone is slightly ambiguous; adding a state match would remove the ambiguity.
# MAGIC * Some pincode districts are spelled differently in NFHS (e.g. `ahmadabad` vs NFHS `Ahmadabad` matched, but `howrah`, `raigad`, `pondicherry` variants did not) — ~597 of 750 pincode districts match NFHS after lower/trim normalization, and fuzzy/alias matching could raise the 81% resolution further.
# MAGIC * The sample joined rows show the mechanics work end-to-end (e.g. Patna facility → pincode 800004 → `patna` → Bihar NFHS indicators), but also data-quality quirks (e.g. a facility listed in Amritsar whose pincode resolves to a different Punjab district).