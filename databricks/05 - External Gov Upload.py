# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 05 External gov upload: load government facility lists
# MAGIC **How to use**
# MAGIC 1. In Catalog, open `workspace` > `vf_bronze` > Volumes > `external`, and upload each downloaded file (CSV or Excel) into its folder:
# MAGIC    - `gov_hospital_directory/`: National Hospital Directory with Geo Code (AIKosh / data.gov.in)
# MAGIC    - `gov_health_centres/`: All India Health Centres Directory (sub-centres, PHCs, CHCs, district hospitals)
# MAGIC    - `cghs/`: Hospitals empanelled under CGHS
# MAGIC    - any other folder you create also works; its name becomes the source name
# MAGIC 2. Click **Run all**. Each folder becomes a raw bronze table `vf_bronze.ext_<folder>_raw`, and the rows are standardised into `vf_silver.external_facilities_gov`.
# MAGIC 3. Run **06 - External Checks** to match them against our facilities.
# MAGIC
# MAGIC Column names differ between releases, so the notebook finds them by name patterns (for example any column containing "lat" becomes latitude). The cell output lists which column it used for each field. If one is wrong, set it in `OVERRIDES` below.

# COMMAND ----------

import re
from pyspark.sql import functions as F

VOLUME = "/Volumes/workspace/vf_bronze/external"
SKIP = {"osm"}  # loaded by notebook 06
# Optional manual mapping, e.g. {"gov_hospital_directory": {"name": "Hospital_Name", "latitude": "Location_Coordinates"}}
OVERRIDES = {}

PATTERNS = {  # first column whose normalised name matches wins
    "name": [r"^(hospital|facility|health_?facility|institution|hcf|hco)_?name$", r"name_of_(the_)?(hospital|facility|institution)", r"^name$", r"name"],
    "kind": [r"facility_?type", r"hospital_?category", r"category", r"type"],
    "operator": [r"ownership", r"owner", r"operator", r"management", r"sector"],
    "beds": [r"total_?(no_?of_?)?beds", r"beds", r"bed"],
    "specialities": [r"special", r"facilities", r"services"],
    "pincode": [r"pin_?code", r"pincode", r"postal", r"zip"],
    "state": [r"^state", r"state"],
    "district": [r"^district", r"district"],
    "latitude": [r"^lat", r"latitude"],
    "longitude": [r"^lon", r"^lng", r"longitude"],
    "coords": [r"location_?coordinates", r"coordinates", r"geo_?code", r"lat_?long"],
}

def norm(c):
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", c.lower())).strip("_")

def pick(cols, field, source):
    if field in OVERRIDES.get(source, {}):
        return OVERRIDES[source][field]
    for pat in PATTERNS[field]:
        for c in cols:
            if re.search(pat, norm(c)):
                return c
    return None

def read_folder(path):
    files = [f.path for f in dbutils.fs.ls(path) if not f.name.startswith(".")]
    csvs = [f for f in files if f.lower().endswith((".csv", ".txt"))]
    xls = [f for f in files if f.lower().endswith((".xlsx", ".xls"))]
    frames = []
    if csvs:
        frames.append(spark.read.option("header", True).option("multiLine", True).option("escape", '"').csv(csvs))
    for x in xls:
        import pandas as pd
        pdf = pd.read_excel(x.replace("dbfs:", ""), dtype=str)
        frames.append(spark.createDataFrame(pdf.fillna("")))
    if not frames:
        return None
    df = frames[0]
    for other in frames[1:]:
        df = df.unionByName(other, allowMissingColumns=True)
    # Delta column names cannot contain spaces or punctuation
    for c in df.columns:
        df = df.withColumnRenamed(c, norm(c) or "col")
    return df

# COMMAND ----------

TIER_SQL = r"""CASE
  WHEN lower(concat_ws(' ', name, kind)) rlike 'aiims|all india institute of medical|medical college|institute of medical sciences' AND NOT lower(name) rlike 'ayurved|homoeo|homeopath|unani|siddha|dental|nursing' THEN 5
  WHEN lower(concat_ws(' ', name, kind)) rlike 'district hospital|\\bdh\\b|civil hospital|sub ?-?district|sub ?divisional|\\bsdh\\b|taluka? hospital|area hospital|(government|govt|state) general hospital' THEN 4
  WHEN lower(concat_ws(' ', name, kind)) rlike 'community health cent|\\bchc\\b|rural hospital|first referral|\\bfru\\b' THEN 3
  WHEN lower(concat_ws(' ', name, kind)) rlike 'primary health cent|\\bphc\\b|urban health cent|\\buphc\\b|dispensary' THEN 2
  WHEN lower(concat_ws(' ', name, kind)) rlike 'sub ?-?cent(re|er)|\\bsc\\b|health (and|&) wellness|ayushman arogya|\\bhwc\\b|\\bshc\\b' THEN 1
END"""

standardised = []
for entry in dbutils.fs.ls(VOLUME):
    source = entry.name.strip("/")
    if not entry.isDir() or source in SKIP:
        continue
    raw = read_folder(entry.path)
    if raw is None:
        print(f"{source}: no files yet, skipped")
        continue
    raw_table = f"workspace.vf_bronze.ext_{norm(source)}_raw"
    raw.withColumn("_ingested_at", F.current_timestamp()).write.mode("overwrite").option("overwriteSchema", True).saveAsTable(raw_table)
    cols = raw.columns
    m = {f: pick(cols, f, source) for f in PATTERNS}
    if m["latitude"] == m["longitude"]:
        m["latitude"] = m["longitude"] = None
    print(f"{source}: {raw.count():,} rows -> {raw_table}; columns used: {m}")
    col = lambda f: F.col(m[f]).cast("string") if m[f] else F.lit(None).cast("string")
    lat, lon = col("latitude").cast("double"), col("longitude").cast("double")
    if m["coords"] and not m["latitude"]:
        parts = F.split(F.regexp_replace(col("coords"), r"[^0-9.,\-]", ""), ",")
        lat, lon = parts.getItem(0).cast("double"), parts.getItem(1).cast("double")
    std = raw.select(
        F.lit(source).alias("source"),
        F.monotonically_increasing_id().cast("string").alias("ext_id"),
        F.trim(col("name")).alias("name"),
        F.lower(F.trim(col("kind"))).alias("kind"),
        F.lower(F.trim(col("operator"))).alias("operator_raw"),
        F.regexp_extract(col("beds"), r"(\d+)", 1).cast("int").alias("beds"),
        col("specialities").alias("specialities"),
        F.regexp_extract(col("pincode"), r"([1-9][0-9]{5})", 1).alias("pincode"),
        F.trim(col("state")).alias("state"),
        F.trim(col("district")).alias("district"),
        lat.alias("latitude"), lon.alias("longitude"),
    ).where("name IS NOT NULL AND name <> ''")
    std = std.selectExpr(
        "source", "ext_id", "name",
        "CASE WHEN lower(concat_ws(' ', name, kind)) rlike 'hospital|\\\\bdh\\\\b|\\\\bchc\\\\b|medical college' THEN 'hospital' "
        "WHEN lower(concat_ws(' ', name, kind)) rlike 'cent(re|er)|dispensary|clinic|phc|\\\\bsc\\\\b|hwc' THEN 'clinic or health centre' END AS ext_kind",
        f"{TIER_SQL} AS ext_tier",
        "CASE WHEN concat_ws(' ', operator_raw, kind) rlike 'gov|public|state|central|municipal|psu|railway|army|esi' THEN 'public' "
        "WHEN concat_ws(' ', operator_raw, kind) rlike 'private|pvt|corporate' THEN 'private' "
        "WHEN concat_ws(' ', operator_raw, kind) rlike 'trust|charit|ngo|mission|society' THEN 'non-profit' "
        "WHEN source rlike 'health_centres' THEN 'public' END AS ext_operator",
        "beds", "specialities", "pincode", "state", "district",
        "CASE WHEN latitude BETWEEN 6 AND 37.5 AND longitude BETWEEN 68 AND 97.5 THEN latitude END AS latitude",
        "CASE WHEN latitude BETWEEN 6 AND 37.5 AND longitude BETWEEN 68 AND 97.5 THEN longitude END AS longitude",
    )
    standardised.append(std)

# COMMAND ----------

target = "workspace.vf_silver.external_facilities_gov"
if standardised:
    out = standardised[0]
    for s in standardised[1:]:
        out = out.unionByName(s)
    out.write.mode("overwrite").saveAsTable(target)
    display(spark.sql(f"""SELECT source, count(*) AS rows, count(latitude) AS with_coordinates, count(pincode) AS with_pincode,
                                 count(ext_tier) AS with_tier, count(ext_operator) AS with_operator
                          FROM {target} GROUP BY source"""))
    print("Done. Now run notebook 06 - External Checks.")
else:
    print("No government files uploaded yet: nothing to do. Upload files into the volume folders and run again.")