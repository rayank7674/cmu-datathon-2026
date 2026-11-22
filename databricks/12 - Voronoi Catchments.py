# Databricks notebook source
# MAGIC %md
# MAGIC # 12 - Voronoi Catchments
# MAGIC For each service, India is divided into **catchments**: the area that is closer (in a straight line) to one facility claiming the service than to any other. This is a Voronoi diagram.
# MAGIC
# MAGIC | Object | What it is |
# MAGIC |---|---|
# MAGIC | `vf_gold.service_catchments` | One row per catchment polygon piece: service, evidence tier, facility, area, people inside, polygon as JSON |
# MAGIC | `vf_gold.critical_zones_emergency_150km` | 5 km blocks over 150 km from life-threatening-capable care (section 5) |
# MAGIC | `vf_gold.life_threatening_capable_facilities` | The facilities that rule uses |
# MAGIC | `vf_gold.catchment_checks` | Pass/fail tests |
# MAGIC
# MAGIC Rules
# MAGIC - **Located facilities only.** A facility is used only when its location flag is `good` (notebook 07). Suspect and invalid locations are left out, the same rule as notebook 11.
# MAGIC - **Two tiers**, same definitions as notebook 11: `claimed` (any detected claim) and `better_evidenced` (direct wording, no negating text, no plausibility flag, and the facility is matched in an outside source). Neither is verification of the service.
# MAGIC - **Clipped to India** using the union of `vf_silver.district_polygons`.
# MAGIC - **Straight-line only.** A catchment says which listed facility is nearest as the crow flies. It is not travel time and not a referral area, and it only knows the roughly 10,000 facilities in this dataset.
# MAGIC - Facilities at exactly the same coordinates share one catchment (`n_facilities_at_point`).
# MAGIC - `population` is the 2020 WorldPop count of the 5 km blocks whose nearest facility is this one (from `vf_gold.access_evidence_gap`). It is null when that table is missing.
# MAGIC
# MAGIC Run after 09 and 11. Safe to rerun. Takes about 3 minutes.

# COMMAND ----------

# MAGIC %pip install shapely pyproj scipy --quiet

# COMMAND ----------

import json
import numpy as np, pandas as pd
from scipy.spatial import Voronoi
from shapely import wkt
from shapely.geometry import Polygon
from shapely.ops import unary_union, transform as shp_transform
from shapely.prepared import prep
from pyproj import Transformer

SUMMARY = {}
# WGS 84 / India NSF LCC: a projection made for all-India maps, so straight lines and areas are close to true.
FWD = Transformer.from_crs("EPSG:4326", "EPSG:7755", always_xy=True)
INV = Transformer.from_crs("EPSG:7755", "EPSG:4326", always_xy=True)

def table_exists(name):
    try:
        spark.sql(f"SELECT 1 FROM {name} LIMIT 1").collect(); return True
    except Exception:
        return False

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. India outline

# COMMAND ----------

dp = spark.sql("SELECT geometry_wkt FROM workspace.vf_silver.district_polygons").toPandas()
india_ll = unary_union([wkt.loads(w).buffer(0) for w in dp.geometry_wkt])
india = shp_transform(FWD.transform, india_ll).buffer(0).simplify(1000)   # metres; 1 km tolerance
india_p = prep(india)
INDIA_KM2 = india.area / 1e6
SUMMARY["india_outline_km2"] = round(INDIA_KM2)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Facilities used (same tiers as notebook 11)

# COMMAND ----------

fs = spark.sql("""
SELECT facility_id, name, service, claim_strength, plausibility_flag, negating_quote,
       facility_existence_status, verification_status, latitude, longitude, location_quality_flag
FROM workspace.vf_gold.facility_service_canonical WHERE claim_detected
""").toPandas()
SUMMARY["claims_total"] = int(len(fs))
SUMMARY["claims_left_out_for_location"] = int(((fs.location_quality_flag != "good") | fs.latitude.isna()).sum())
fs = fs[(fs.location_quality_flag == "good") & fs.latitude.notna() & fs.longitude.notna()].copy()
fs["better"] = fs.claim_strength.isin(["strong", "moderate"]) & fs.plausibility_flag.isna() & fs.negating_quote.isna() & \
               ((fs.facility_existence_status == "externally_supported") | fs.verification_status.isin(["externally_supported", "verified"]))
fs["x"], fs["y"] = FWD.transform(fs.longitude.to_numpy(float), fs.latitude.to_numpy(float))

pop = None
if table_exists("workspace.vf_gold.access_evidence_gap"):
    pop = spark.sql("""
      SELECT service, 'claimed' AS tier, nearest_claimed_facility_id AS facility_id, SUM(population) AS population
      FROM workspace.vf_gold.access_evidence_gap WHERE nearest_claimed_facility_id IS NOT NULL GROUP BY 1, 3
      UNION ALL
      SELECT service, 'better_evidenced', nearest_better_evidenced_facility_id, SUM(population)
      FROM workspace.vf_gold.access_evidence_gap WHERE nearest_better_evidenced_facility_id IS NOT NULL GROUP BY 1, 3
    """).toPandas()
    pop["population"] = pop.population.astype(float)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Voronoi cells, clipped to India

# COMMAND ----------

def catchments(sub, service, tier):
    # one point per distinct coordinate (rounded to 1 m)
    sub = sub.assign(kx=sub.x.round(0), ky=sub.y.round(0)).sort_values("facility_id")
    g = sub.groupby(["kx", "ky"], sort=False)
    pts = g.agg(facility_id=("facility_id", "first"), name=("name", "first"), n=("facility_id", "size"),
                ids=("facility_id", list), lat=("latitude", "first"), lon=("longitude", "first")).reset_index()
    xy = pts[["kx", "ky"]].to_numpy(float)
    # eight far-away helper points make every real cell a closed polygon
    cx, cy = xy.mean(0); R = 2.0e7
    ring = np.array([[cx + R * np.cos(a), cy + R * np.sin(a)] for a in np.linspace(0, 2 * np.pi, 8, endpoint=False)])
    vor = Voronoi(np.vstack([xy, ring]))
    popmap = {}
    if pop is not None:
        p = pop[(pop.service == service) & (pop.tier == tier)]
        popmap = dict(zip(p.facility_id, p.population))
    rows = []
    for i in range(len(pts)):
        region = vor.regions[vor.point_region[i]]
        if -1 in region or len(region) < 3:
            continue
        cell = Polygon(vor.vertices[region])
        if not india_p.contains(cell):
            cell = cell.intersection(india)
        if cell.is_empty:
            continue
        area = cell.area / 1e6
        parts = [q for q in (cell.geoms if hasattr(cell, "geoms") else [cell]) if q.geom_type == "Polygon"]
        keep = [q for q in parts if q.area / 1e6 >= 1.0] or parts[:1]
        r = pts.iloc[i]
        people = sum(popmap.get(f, 0.0) for f in r.ids) if pop is not None else None
        for k, q in enumerate(sorted(keep, key=lambda q: -q.area)):
            q = q.simplify(300)
            lon, lat = INV.transform(*np.asarray(q.exterior.coords).T)
            rows.append({"service": service, "tier": tier, "facility_id": r.facility_id, "facility_name": r["name"],
                         "n_facilities_at_point": int(r.n), "facility_latitude": float(r.lat), "facility_longitude": float(r.lon),
                         "part": k, "parts": len(keep), "area_km2": round(area, 1),
                         "population": None if people is None else float(round(people)),
                         "polygon_json": json.dumps([[round(a, 4), round(b, 4)] for a, b in zip(lon, lat)])})
    return rows, len(pts)

rows, counts = [], []
for s in sorted(fs.service.unique()):
    a = fs[fs.service == s]
    for tier, sub in (("claimed", a), ("better_evidenced", a[a.better])):
        if len(sub) < 3:
            continue
        r, n = catchments(sub, s, tier)
        rows += r
        counts.append({"service": s, "tier": tier, "facilities": int(len(sub)), "points": n})
out = pd.DataFrame(rows)
cnt = pd.DataFrame(counts)
SUMMARY["polygon_rows"] = int(len(out))

# COMMAND ----------

spark.createDataFrame(out).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("workspace.vf_gold.service_catchments")
spark.sql("""COMMENT ON TABLE workspace.vf_gold.service_catchments IS
'Voronoi catchments per service and evidence tier: the area closer in a STRAIGHT LINE to one facility claiming the service than to any other, clipped to India. Good-location facilities only. Not travel time, not a referral area. polygon_json = outer ring as [lon, lat] pairs. One row per polygon piece (part); area_km2 and population are for the whole catchment (notebook 12).'""")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Tests

# COMMAND ----------

one = out[out.part == 0]
agg = one.groupby(["service", "tier"]).agg(catchments=("facility_id", "size"), area=("area_km2", "sum"),
                                           population=("population", "sum")).reset_index().merge(cnt, on=["service", "tier"])
agg["area_share"] = agg.area / INDIA_KM2
bad_loc = spark.sql("""
SELECT COUNT(*) n FROM workspace.vf_gold.service_catchments c
JOIN workspace.vf_gold.facility_service_canonical f ON f.facility_id = c.facility_id AND f.service = c.service
WHERE f.location_quality_flag <> 'good' OR NOT f.claim_detected""").collect()[0].n
checks = [
    ("every service and tier covers India (catchment areas sum to 98-102% of the outline)",
     bool(((agg.area_share > 0.98) & (agg.area_share < 1.02)).all()), f"min {agg.area_share.min():.4f}, max {agg.area_share.max():.4f}"),
    ("one catchment per distinct facility point", bool((agg.catchments == agg.points).all()),
     f"{int(agg.catchments.sum())} catchments, {int(agg.points.sum())} points"),
    ("no catchment belongs to a suspect or invalid location, or to a facility with no claim", bad_loc == 0, f"{bad_loc} rows"),
    ("every polygon has at least 4 coordinates", bool(out.polygon_json.map(lambda j: len(json.loads(j)) >= 4).all()), ""),
    ("better-evidenced tier never has more catchments than claimed",
     bool(agg.pivot(index="service", columns="tier", values="catchments").pipe(lambda p: (p.get("better_evidenced", 0).fillna(0) <= p["claimed"]).all())), ""),
]
ck = pd.DataFrame(checks, columns=["test", "passed", "detail"])
spark.createDataFrame(ck).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("workspace.vf_gold.catchment_checks")
spark.sql("COMMENT ON TABLE workspace.vf_gold.catchment_checks IS 'Pass/fail tests for the Voronoi catchments (notebook 12)'")
display(spark.createDataFrame(ck))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Critical areas for life-threatening emergencies (150 km rule)
# MAGIC Team rule (2026-11-22): **any area more than 150 km in a straight line from the nearest facility that could handle a life-threatening emergency is a critical area**, whatever its population or deprivation.
# MAGIC
# MAGIC "Could handle a life-threatening emergency" is defined here as a facility that
# MAGIC - has a `good` location flag,
# MAGIC - states **emergency care or trauma care** in direct wording (strong or moderate claim, no negating text),
# MAGIC - **and** states an **ICU** in direct wording,
# MAGIC - **and** has care level 3 or higher (a hospital, not a clinic).
# MAGIC
# MAGIC These are the facility's own claims. Nothing here is verified, and availability today is unknown.
# MAGIC `vf_gold.critical_zones_emergency_150km` has one row per 5 km block that meets the rule; `vf_gold.life_threatening_capable_facilities` lists the facilities used.

# COMMAND ----------

from scipy.spatial import cKDTree
LT_KM = 150.0
cap = spark.sql("""
WITH f AS (
  SELECT facility_id, MAX(name) AS name, MAX(care_level) AS care_level, MAX(latitude) AS latitude, MAX(longitude) AS longitude,
         MAX(CASE WHEN service IN ('emergency', 'trauma') AND claim_strength IN ('strong', 'moderate') THEN 1 ELSE 0 END) AS em,
         MAX(CASE WHEN service = 'icu' AND claim_strength IN ('strong', 'moderate') THEN 1 ELSE 0 END) AS icu
  FROM workspace.vf_gold.facility_service_canonical
  WHERE claim_detected AND location_quality_flag = 'good' AND negating_quote IS NULL AND latitude IS NOT NULL
  GROUP BY facility_id)
SELECT facility_id, name, care_level, latitude, longitude FROM f WHERE em = 1 AND icu = 1 AND care_level >= 3
""").toPandas()
SUMMARY["life_threatening_capable_facilities"] = int(len(cap))
SUMMARY["critical_150km"] = None
if table_exists("workspace.vf_gold.access_evidence_gap") and len(cap):
    blk = spark.sql("""SELECT block_id, latitude, longitude, population, district_id, district_name, state_name, state
                       FROM workspace.vf_gold.access_evidence_gap WHERE service = 'emergency'""").toPandas()
    def unit(lat, lon):
        la, lo = np.radians(np.asarray(lat, float)), np.radians(np.asarray(lon, float))
        return np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])
    chord, idx = cKDTree(unit(cap.latitude, cap.longitude)).query(unit(blk.latitude, blk.longitude))
    blk["km_to_capable_facility"] = np.round(2 * 6371.0088 * np.arcsin(np.clip(chord / 2, 0, 1)), 1)
    blk["nearest_capable_facility_id"] = cap.facility_id.to_numpy()[idx]
    blk["nearest_capable_facility_name"] = cap.name.to_numpy()[idx]
    blk["population"] = blk.population.astype(float)
    crit = blk[blk.km_to_capable_facility > LT_KM].copy()
    crit["zone_type"] = "over 150 km from life-threatening-capable care"
    crit["criteria"] = ("straight-line km from the block to the nearest facility with a good location that states emergency or trauma care "
                        "AND an ICU in direct wording and has care level 3+ is over 150; no population or deprivation condition")
    spark.createDataFrame(crit).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("workspace.vf_gold.critical_zones_emergency_150km")
    spark.sql("""COMMENT ON TABLE workspace.vf_gold.critical_zones_emergency_150km IS
'5 km blocks more than 150 STRAIGHT-LINE km from the nearest facility that claims emergency or trauma care plus an ICU in direct wording at care level 3+. Self-reported claims, not verified; only facilities in this dataset. An extra way to be a critical area, alongside vf_gold.critical_zones (notebook 12).'""")
    spark.createDataFrame(cap).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("workspace.vf_gold.life_threatening_capable_facilities")
    spark.sql("COMMENT ON TABLE workspace.vf_gold.life_threatening_capable_facilities IS 'Facilities used for the 150 km critical-area rule: good location, direct emergency or trauma claim, direct ICU claim, care level 3+. Claims only, not verified (notebook 12).'")
    by_state = crit.groupby("state_name").agg(blocks=("block_id", "size"), people=("population", "sum")).sort_values("people", ascending=False).head(12).round(0).reset_index()
    SUMMARY["critical_150km"] = {"blocks": int(len(crit)), "blocks_total": int(len(blk)), "people": float(round(crit.population.sum())),
                                 "people_total": float(round(blk.population.sum())), "area_km2_approx": int(len(crit) * 25),
                                 "districts_touched": int(crit.district_id.nunique()),
                                 "max_km": float(crit.km_to_capable_facility.max()) if len(crit) else None,
                                 "top_states": by_state.to_dict("records")}
    display(spark.createDataFrame(by_state))

# COMMAND ----------

SUMMARY["checks_passed"] = f"{int(ck.passed.sum())} of {len(ck)}"
SUMMARY["checks"] = ck.to_dict("records")
SUMMARY["per_service"] = agg.assign(median_km2=lambda d: [float(one[(one.service == s) & (one.tier == t)].area_km2.median()) for s, t in zip(d.service, d.tier)],
                                    largest_km2=lambda d: [float(one[(one.service == s) & (one.tier == t)].area_km2.max()) for s, t in zip(d.service, d.tier)]
                                    )[["service", "tier", "facilities", "catchments", "area_share", "median_km2", "largest_km2", "population"]].round(3).to_dict("records")
display(spark.createDataFrame(pd.DataFrame(SUMMARY["per_service"])))
dbutils.notebook.exit(json.dumps(SUMMARY, default=str))
