# Databricks notebook source
# MAGIC %md
# MAGIC # 11 - Geography
# MAGIC District polygons, population, a district crosswalk, district context, and a straight-line access surface.
# MAGIC
# MAGIC | Object | What it is |
# MAGIC |---|---|
# MAGIC | `vf_bronze.geo_sources` | Where each outside file came from, its vintage and licence |
# MAGIC | `vf_bronze.geo_district_polygons_raw` | geoBoundaries ADM2 districts as downloaded (geometry as GeoJSON text) |
# MAGIC | `vf_silver.district_polygons` | One row per district polygon with state, area, population and WKT geometry |
# MAGIC | `vf_gold.district_crosswalk` | Links polygon districts, the PIN-code districts used by `vf_silver.facilities`, and NFHS-5 districts |
# MAGIC | `vf_gold.district_crosswalk_issues` | Every name that could not be linked cleanly |
# MAGIC | `vf_gold.access_grid` | One row per 1 km land cell: population, district, straight-line km to the nearest facility per service |
# MAGIC | `vf_gold.access_evidence_gap` | Per 5 km block and service: nearest claimed facility vs nearest better-evidenced one |
# MAGIC | `vf_gold.district_service_access` | Per district and service: population-weighted and area-weighted distance and coverage |
# MAGIC | `vf_gold.district_context` | One row per district: NFHS indicators, population, facility supply, data gaps, coverage |
# MAGIC | `vf_gold.critical_zones` | 5 km blocks with many people, a long distance and a deprived district, with the criteria written down |
# MAGIC | `vf_gold.geography_checks` | Pass/fail tests |
# MAGIC
# MAGIC **Every distance in this notebook is straight-line (as the crow flies). It is not travel time.** It ignores roads, rivers, hills and traffic, so real journeys are longer. Section 8 has a routing function for small sets of real road times.
# MAGIC
# MAGIC **Distances are to facilities in this dataset only.** The Virtue Foundation data holds about 10,000 facilities, a fraction of India's real hospitals and clinics. A long distance here means "no facility *in this dataset* nearby", which is partly a real gap and partly missing data. Read the numbers as relative, between places and between evidence tiers, not as the true distance to care.
# MAGIC
# MAGIC Run after 07 and 09. Safe to rerun; downloads are kept in the volume and reused. Takes about 10 minutes.

# COMMAND ----------

# MAGIC %pip install rasterio shapely --quiet

# COMMAND ----------

import json, os, time, urllib.request, difflib, re, datetime
import numpy as np, pandas as pd
import rasterio
from rasterio import features
from shapely.geometry import shape
from scipy.spatial import cKDTree
import pyarrow as pa, pyarrow.parquet as pq

GEO_DIR = "/Volumes/workspace/vf_bronze/external/geo"
STAGE   = GEO_DIR + "/_staging"
os.makedirs(GEO_DIR, exist_ok=True); os.makedirs(STAGE, exist_ok=True)
SUMMARY = {}

SERVICES = ["emergency", "trauma", "obstetric_delivery", "c_section", "nicu", "dialysis", "icu", "ct", "mri",
            "cardiac_cath", "stroke", "blood_bank", "burns", "paediatrics"]
LEVELS   = {"level3plus": 3, "level4plus": 4, "level5": 5}
GAP_KM   = 30          # a planning threshold for "far", not a clinical rule
BLOCK    = 5           # 5 x 5 one-km cells per block

def fetch(url, path):
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return False
    req = urllib.request.Request(url, headers={"User-Agent": "python-urllib"})
    with urllib.request.urlopen(req, timeout=300) as r, open(path, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b: break
            f.write(b)
    return True

def write_table(df, name, comment, stage_name=None):
    """Write a pandas frame to a Delta table by way of parquet in the volume (safe for millions of rows)."""
    d = f"{STAGE}/{stage_name or name.split('.')[-1]}"
    if os.path.exists(d):
        for f in os.listdir(d): os.remove(os.path.join(d, f))
    os.makedirs(d, exist_ok=True)
    step = 500_000
    for i in range(0, max(len(df), 1), step):
        pq.write_table(pa.Table.from_pandas(df.iloc[i:i + step], preserve_index=False), f"{d}/part_{i // step:04d}.parquet", compression="zstd")
    spark.read.parquet(d).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(name)
    spark.sql(f"COMMENT ON TABLE {name} IS '{comment}'")
    for f in os.listdir(d): os.remove(os.path.join(d, f))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Download the boundary and population files
# MAGIC - **geoBoundaries** (gbOpen) India ADM2 = districts, ADM1 = states. Open licence, simplified geometry.
# MAGIC - **WorldPop** India 2020, about 1 km cells (30 arc-seconds), UN-adjusted population counts. A modelled estimate, not a census count.

# COMMAND ----------

meta = {}
for lvl in ("ADM1", "ADM2"):
    mp = f"{GEO_DIR}/geoBoundaries-IND-{lvl}-api.json"
    fetch(f"https://www.geoboundaries.org/api/current/gbOpen/IND/{lvl}/", mp)
    meta[lvl] = json.load(open(mp))
    fetch(meta[lvl]["simplifiedGeometryGeoJSON"], f"{GEO_DIR}/geoBoundaries-IND-{lvl}_simplified.geojson")

WP_URL = "https://data.worldpop.org/GIS/Population/Global_2000_2020_1km_UNadj/2020/IND/ind_ppp_2020_1km_Aggregated_UNadj.tif"
WP_TIF = f"{GEO_DIR}/ind_ppp_2020_1km_Aggregated_UNadj.tif"
fetch(WP_URL, WP_TIF)

now = datetime.datetime.utcnow().isoformat(timespec="seconds")
src_rows = []
for lvl in ("ADM1", "ADM2"):
    m = meta[lvl]
    src_rows.append(dict(source_name=f"geoBoundaries gbOpen India {lvl}", source_url=m.get("simplifiedGeometryGeoJSON"),
        vintage=str(m.get("boundaryYearRepresented")), publisher=str(m.get("boundarySource")), licence=str(m.get("boundaryLicense")),
        units=str(m.get("admUnitCount")), file=f"{GEO_DIR}/geoBoundaries-IND-{lvl}_simplified.geojson",
        build_date=str(m.get("buildDate")), downloaded_at=now, note="Simplified geometry. Boundary vintage can differ from NFHS-5 (2019-21) and from current districts."))
src_rows.append(dict(source_name="WorldPop India population counts, 1 km, UN-adjusted", source_url=WP_URL, vintage="2020",
    publisher="WorldPop, University of Southampton", licence="CC BY 4.0", units="30 arc-second cells", file=WP_TIF, build_date="2020",
    downloaded_at=now, note="Modelled population estimate for 2020, adjusted to UN national totals. Not a census count."))
write_table(pd.DataFrame(src_rows), "workspace.vf_bronze.geo_sources", "Outside geography files: source, vintage, licence (notebook 11). downloaded_at is a download time, not a verification date.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Rasterise districts onto the population grid
# MAGIC Each 1 km cell gets the district and state whose polygon covers its centre. Facilities and PIN-code centres are placed the same way, so "which district is this point in" is answered by position, not by name.

# COMMAND ----------

adm2 = json.load(open(f"{GEO_DIR}/geoBoundaries-IND-ADM2_simplified.geojson"))["features"]
adm1 = json.load(open(f"{GEO_DIR}/geoBoundaries-IND-ADM1_simplified.geojson"))["features"]

with rasterio.open(WP_TIF) as src:
    pop = src.read(1).astype("float32")
    T = src.transform
    nodata = src.nodata
H, W = pop.shape
pop[~np.isfinite(pop)] = 0
if nodata is not None: pop[pop == np.float32(nodata)] = 0
pop[pop < 0] = 0

d_r = features.rasterize(((f["geometry"], i + 1) for i, f in enumerate(adm2)), out_shape=(H, W), transform=T, fill=0, dtype="int32")
s_r = features.rasterize(((f["geometry"], i + 1) for i, f in enumerate(adm1)), out_shape=(H, W), transform=T, fill=0, dtype="int32")

def rc(lat, lon):
    col = np.floor((np.asarray(lon) - T.c) / T.a).astype(int)
    row = np.floor((np.asarray(lat) - T.f) / T.e).astype(int)
    ok = (row >= 0) & (row < H) & (col >= 0) & (col < W)
    return row, col, ok

def district_at(lat, lon):
    row, col, ok = rc(lat, lon)
    out = np.zeros(len(row), dtype=int)
    out[ok] = d_r[row[ok], col[ok]]
    return out            # 0 = outside every polygon

land = d_r > 0
rows, cols = np.nonzero(land)
lat = (T.f + (rows + 0.5) * T.e).astype("float64")
lon = (T.c + (cols + 0.5) * T.a).astype("float64")
cpop = pop[rows, cols].astype("float64")
cdist = d_r[rows, cols]
carea = (111.32 * np.cos(np.radians(lat)) * abs(T.a)) * (110.57 * abs(T.e))
N = len(rows)
SUMMARY.update(cells=int(N), population_in_districts=float(cpop.sum()), population_in_raster=float(pop.sum()), districts=len(adm2), states=len(adm1))

# district table: state by majority of cells, area, population, centre
dn = len(adm2)
d_pop  = np.bincount(cdist, weights=cpop, minlength=dn + 1)
d_area = np.bincount(cdist, weights=carea, minlength=dn + 1)
d_cnt  = np.bincount(cdist, minlength=dn + 1)
d_lat  = np.bincount(cdist, weights=lat, minlength=dn + 1) / np.maximum(d_cnt, 1)
d_lon  = np.bincount(cdist, weights=lon, minlength=dn + 1) / np.maximum(d_cnt, 1)
st_of = np.zeros(dn + 1, dtype=int)
pair = pd.DataFrame({"d": cdist, "s": s_r[rows, cols]})
pair = pair[pair.s > 0].value_counts().reset_index(name="n").sort_values("n", ascending=False).drop_duplicates("d")
st_of[pair.d.values] = pair.s.values
state_name = [""] + [f["properties"].get("shapeName") for f in adm1]

poly = pd.DataFrame({
    "district_idx": np.arange(1, dn + 1),
    "district_id": [f["properties"].get("shapeID") for f in adm2],
    "district_name": [f["properties"].get("shapeName") for f in adm2],
    "state_name": [state_name[st_of[i]] or None for i in range(1, dn + 1)],
    "area_km2": np.round(d_area[1:], 1), "population_2020": np.round(d_pop[1:]), "grid_cells": d_cnt[1:],
    "centre_lat": np.round(d_lat[1:], 4), "centre_lon": np.round(d_lon[1:], 4),
    "geometry_wkt": [shape(f["geometry"]).wkt for f in adm2],
})
poly["pop_per_km2"] = np.round(poly.population_2020 / poly.area_km2.where(poly.area_km2 > 0), 1)
poly["boundary_source"] = "geoBoundaries gbOpen ADM2, year " + str(meta["ADM2"].get("boundaryYearRepresented"))
raw = pd.DataFrame({"shape_id": poly.district_id, "shape_name": poly.district_name, "shape_group": [f["properties"].get("shapeGroup") for f in adm2],
                    "shape_type": [f["properties"].get("shapeType") for f in adm2], "geometry_geojson": [json.dumps(f["geometry"]) for f in adm2],
                    "source_url": meta["ADM2"].get("simplifiedGeometryGeoJSON"), "boundary_year": str(meta["ADM2"].get("boundaryYearRepresented")), "downloaded_at": now})
write_table(raw, "workspace.vf_bronze.geo_district_polygons_raw", "geoBoundaries ADM2 district polygons as downloaded, geometry as GeoJSON text (notebook 11)")
write_table(poly, "workspace.vf_silver.district_polygons", "One row per district polygon: state (by position), area, WorldPop 2020 population, centre and WKT geometry (notebook 11)")
SUMMARY.update(districts_without_cells=int((poly.grid_cells == 0).sum()), districts_without_state=int(poly.state_name.isna().sum()))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. District crosswalk
# MAGIC Three district lists have to line up: the **polygons**, the **PIN-code districts** that `vf_silver.facilities` uses, and the **NFHS-5 districts**.
# MAGIC
# MAGIC - **States** are linked by position: a state's PIN-code centres are dropped on the map and the polygon state holding most of them is its match. No state names are compared.
# MAGIC - A district (PIN-code or NFHS) is linked to a polygon when a polygon **in the same state** has the same name (similarity 0.9+, with no close runner-up). State plus name, never the name alone.
# MAGIC - If that fails, a PIN-code district is linked by position when 60%+ of its PIN-code centres (at least 5) fall in one polygon. PIN-code centres are rough, so position alone is the fallback, not the first choice.
# MAGIC - If that fails, an NFHS district may follow its PIN-code district of the same state and name, or a clear close spelling (0.85+) in the same state.
# MAGIC - Anything else goes to `district_crosswalk_issues` and is left unlinked.
# MAGIC
# MAGIC Several newer districts can sit inside one older polygon (districts get split over time). That is recorded, not treated as an error.

# COMMAND ----------

def nk(x):
    x = str(x or "").lower()
    for a, b in (("ā", "a"), ("ī", "i"), ("ū", "u"), ("ē", "e"), ("ō", "o")): x = x.replace(a, b)
    return re.sub(r"[^a-z]", "", x)
def sim(a, b):
    return difflib.SequenceMatcher(None, nk(a), nk(b)).ratio()

pins = spark.sql("SELECT pincode, state, district, district_key, centroid_lat, centroid_lon FROM workspace.vf_silver.pincode WHERE centroid_lat IS NOT NULL").toPandas()
pins["didx"] = district_at(pins.centroid_lat.values, pins.centroid_lon.values)
pins = pins.merge(poly[["district_idx", "state_name"]], left_on="didx", right_on="district_idx", how="left")
# state link, by position: the polygon state that holds most of a state's PIN-code centres
state_poly = pins[pins.didx > 0].groupby("state").state_name.agg(lambda x: x.value_counts().index[0]).to_dict()
state_back = {v: k for k, v in state_poly.items()}
polys_in = poly.groupby("state_name").apply(lambda g: list(zip(g.district_idx, g.district_name))).to_dict()
pname = dict(zip(poly.district_idx, poly.district_name)); pid = dict(zip(poly.district_idx, poly.district_id)); pstate = dict(zip(poly.district_idx, poly.state_name))
votes = pins[pins.didx > 0].groupby(["state", "district_key", "didx"]).size()
tot = votes.groupby(level=[0, 1]).sum()

def by_name(state, name):
    """Best polygon in the same state by name. Returns (district_idx, similarity, clear) where clear = no close runner-up."""
    c = sorted(((sim(name, n), idx) for idx, n in polys_in.get(state_poly.get(state), [])), reverse=True)
    if not c: return None, None, False
    return c[0][1], round(c[0][0], 2), (len(c) == 1 or c[0][0] - c[1][0] >= 0.05)

rows_x = []
pin_all = spark.sql("SELECT state, district_key, max(district) AS district, count(*) AS pins FROM workspace.vf_silver.pincode GROUP BY 1, 2").toPandas()
pin_link = {}
for r in pin_all.itertuples():
    n_loc = int(tot.get((r.state, r.district_key), 0))
    v = votes.get((r.state, r.district_key)) if n_loc else None
    pos_idx = int(v.idxmax()) if v is not None else None
    pos_share = float(v.max() / n_loc) if v is not None else None
    nm_idx, nm_sim, clear = by_name(r.state, r.district)
    if nm_idx is not None and nm_sim >= 0.9 and clear:
        idx, status, method, s_ = nm_idx, "linked", "same state (by position) + same district name", nm_sim
    elif pos_idx is not None and pos_share >= 0.6 and n_loc >= 5:
        idx, status, method, s_ = pos_idx, "linked", "position: most PIN-code centres fall in this polygon", round(sim(r.district, pname[pos_idx]), 2)
    elif pos_idx is not None and pos_share >= 0.6:
        idx, status, method, s_ = pos_idx, "linked_low_evidence", "position, under 5 PIN codes and a different name", round(sim(r.district, pname[pos_idx]), 2)
    else:
        idx, status, method, s_ = None, "unlinked", None, nm_sim
    share = float(v.get(idx, 0) / n_loc) if (idx is not None and v is not None) else None
    if status == "linked": pin_link[(r.state, r.district_key)] = idx
    rows_x.append(dict(source_list="pin_district", state=r.state, district_key=r.district_key, district_name=r.district,
        polygon_district_id=pid.get(idx), polygon_district_name=pname.get(idx), polygon_state_name=pstate.get(idx),
        link_status=status, link_method=method, name_similarity=s_, pin_codes=int(r.pins), pin_codes_located=n_loc,
        share_of_pin_codes_in_polygon=None if share is None else round(share, 3),
        note=None if status != "unlinked" else ("no polygon with this name in the state, and its PIN-code centres are spread over several polygons"
              + (f" (closest name: {pname.get(nm_idx)}, largest share: {pname.get(pos_idx)} {round(pos_share, 2)})" if pos_idx is not None else ""))))

nf = spark.sql("SELECT state, district_key, district_name FROM workspace.vf_silver.nfhs_district").toPandas()
pin_keys_by_state = pin_all.groupby("state").district_key.apply(list).to_dict()
for r in nf.itertuples():
    nm_idx, nm_sim, clear = by_name(r.state, r.district_name)
    idx, method, s_ = None, None, nm_sim
    if nm_idx is not None and nm_sim >= 0.9 and clear:
        idx, method = nm_idx, "same state (by position) + same district name"
    elif (r.state, r.district_key) in pin_link:
        idx, method, s_ = pin_link[(r.state, r.district_key)], "through the PIN-code district of the same state and name", round(sim(r.district_name, pname[pin_link[(r.state, r.district_key)]]), 2)
    elif nm_idx is not None and nm_sim >= 0.85 and clear:
        idx, method = nm_idx, "same state (by position) + close spelling"
    else:
        c = sorted(((sim(r.district_key, k), k) for k in pin_keys_by_state.get(r.state, []) if (r.state, k) in pin_link), reverse=True)
        if c and c[0][0] >= 0.85 and (len(c) == 1 or c[0][0] - c[1][0] >= 0.05):
            idx, method, s_ = pin_link[(r.state, c[0][1])], "through a PIN-code district with a close spelling in the same state", round(c[0][0], 2)
    rows_x.append(dict(source_list="nfhs_district", state=r.state, district_key=r.district_key, district_name=r.district_name,
        polygon_district_id=pid.get(idx), polygon_district_name=pname.get(idx), polygon_state_name=pstate.get(idx),
        link_status="linked" if idx is not None else "unlinked", link_method=method, name_similarity=s_, pin_codes=None, pin_codes_located=None,
        share_of_pin_codes_in_polygon=None,
        note=None if idx is not None else f"no district with this name in the same state (closest polygon name: {pname.get(nm_idx)}, similarity {nm_sim})"))
crosswalk = pd.DataFrame(rows_x)
for c in ("pin_codes", "pin_codes_located"): crosswalk[c] = pd.to_numeric(crosswalk[c]).astype("Int64")
write_table(crosswalk, "workspace.vf_gold.district_crosswalk", "Links PIN-code districts (used by vf_silver.facilities) and NFHS-5 districts to polygon districts. A link always needs the state (matched by position) plus the name, or a clear majority of PIN-code centres. Never the name alone (notebook 11).")

# the silver (current) state of each polygon: the state of most PIN-code centres inside it; polygons with none fall back to the state link
st_in = pins[pins.didx > 0].groupby("didx").state.agg(lambda x: x.value_counts().index[0])
poly["state"] = poly.district_idx.map(st_in).fillna(poly.state_name.map(state_back))   # state_name is the polygon file's own (older) state
issues = [(r.source_list, r.state, r.district_name, r.link_status, r.note or r.link_method, r.polygon_district_name)
          for r in crosswalk.itertuples() if r.link_status != "linked"]
lp = crosswalk[crosswalk.link_status == "linked"]
for src_list, label in (("pin_district", "PIN-code"), ("nfhs_district", "NFHS")):
    have = set(lp[lp.source_list == src_list].polygon_district_id)
    for r in poly.itertuples():
        if r.district_id not in have:
            issues.append(("polygon_district", r.state, r.district_name, f"polygon_without_{src_list}", f"no {label} district is linked to this polygon (boundary vintage differs, or a new or very small district)", r.district_name))
    per = lp[lp.source_list == src_list].groupby("polygon_district_id").district_key.nunique()
    for q, n in per[per > 1].items():
        rr = poly[poly.district_id == q].iloc[0]
        issues.append(("polygon_district", rr.state, rr.district_name, f"several_{src_list}s_in_one_polygon", f"{n} {label} districts are linked to this polygon", rr.district_name))
iss = pd.DataFrame(issues, columns=["list", "state", "name", "issue", "detail", "nearest_polygon_name"])
write_table(iss, "workspace.vf_gold.district_crosswalk_issues", "District names that could not be linked cleanly between polygons, PIN-code districts and NFHS-5 (notebook 11). Unlinked names are left unlinked, not guessed.")
write_table(poly.drop(columns=["district_idx"]).assign(district_idx=poly.district_idx), "workspace.vf_silver.district_polygons", "One row per district polygon: state (by position), area, WorldPop 2020 population, centre and WKT geometry (notebook 11)")
SUMMARY.update(pin_districts=int((crosswalk.source_list == "pin_district").sum()), pin_districts_linked=int(((crosswalk.source_list == "pin_district") & (crosswalk.link_status == "linked")).sum()),
               nfhs_districts=int(len(nf)), nfhs_linked=int(((crosswalk.source_list == "nfhs_district") & (crosswalk.link_status == "linked")).sum()),
               link_methods=crosswalk.groupby(["source_list", "link_status"]).size().rename(lambda x: x).to_frame("n").reset_index().values.tolist(),
               polygons_with_nfhs=int(lp[lp.source_list == "nfhs_district"].polygon_district_id.nunique()),
               crosswalk_issues=iss.issue.value_counts().to_dict(), states_linked=len(state_poly))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Straight-line access grid (`access_grid`)
# MAGIC For every 1 km land cell, the straight-line distance to the nearest facility, per service.
# MAGIC
# MAGIC Two evidence tiers per service:
# MAGIC - **claimed** (`km_<service>`): any facility whose own text claims the service (any strength).
# MAGIC - **better evidenced** (`kmb_<service>`): the claim is direct wording (strong or moderate), has no negating text and no plausibility flag, **and** the facility is matched in an outside source (or the service itself is externally supported or verified). This is still not verification of the service; it removes the weakest claims and unconfirmed facilities.
# MAGIC
# MAGIC Only facilities with a `good` location flag are used. Near-certain duplicates count once (`facility_service_canonical`). Hospital classes `km_level3plus / 4plus / 5` use `facility_level_canonical`.

# COMMAND ----------

fs = spark.sql("""
SELECT facility_id, service, claim_strength, plausibility_flag, negating_quote, facility_existence_status, verification_status, latitude, longitude, location_quality_flag
FROM workspace.vf_gold.facility_service_canonical WHERE claim_detected
""").toPandas()
lv = spark.sql("""
SELECT l.facility_id, l.care_level, l.latitude, l.longitude, q.geo_flag
FROM workspace.vf_gold.facility_level_canonical l JOIN workspace.vf_silver.facility_geo_quality q ON q.facility_id = l.facility_id
""").toPandas()
SUMMARY["claims_dropped_for_location"] = int((fs.location_quality_flag != "good").sum())
SUMMARY["claims_total"] = int(len(fs))
fs = fs[(fs.location_quality_flag == "good") & fs.latitude.notna()].copy()
fs["better"] = fs.claim_strength.isin(["strong", "moderate"]) & fs.plausibility_flag.isna() & fs.negating_quote.isna() & \
               ((fs.facility_existence_status == "externally_supported") | fs.verification_status.isin(["externally_supported", "verified"]))
fs["strict"] = fs.verification_status.isin(["externally_supported", "verified"])
lvg = lv[(lv.geo_flag == "good") & lv.latitude.notna()].copy()

def xyz(la, lo):
    la, lo = np.radians(np.asarray(la, dtype="float64")), np.radians(np.asarray(lo, dtype="float64"))
    return np.c_[np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)]
def nearest(points_xyz, fac):
    if len(fac) == 0:
        return np.full(len(points_xyz), np.nan, dtype="float32"), np.full(len(points_xyz), -1)
    d, i = cKDTree(xyz(fac.latitude.values, fac.longitude.values)).query(points_xyz, workers=-1)
    return (2 * 6371.0088 * np.arcsin(np.minimum(d, 2.0) / 2)).astype("float32"), i

cell_xyz = xyz(lat, lon)
grid = {"cell_id": (rows.astype("int64") * W + cols), "latitude": np.round(lat, 5), "longitude": np.round(lon, 5),
        "population": np.round(cpop, 2).astype("float32"), "area_km2": np.round(carea, 4).astype("float32"),
        "district_idx": cdist.astype("int32"), "block_id": ((rows // BLOCK).astype("int64") * 100000 + (cols // BLOCK))}
tier_counts = []
for s in SERVICES:
    a = fs[fs.service == s]; b = a[a.better]
    grid[f"km_{s}"]  = np.round(nearest(cell_xyz, a)[0], 2)
    grid[f"kmb_{s}"] = np.round(nearest(cell_xyz, b)[0], 2)
    tier_counts.append((s, len(a), len(b), int(a.strict.sum())))
for name, lvl in LEVELS.items():
    grid[f"km_{name}"] = np.round(nearest(cell_xyz, lvg[lvg.care_level >= lvl])[0], 2)
    tier_counts.append((name, int((lvg.care_level >= lvl).sum()), None, None))
G = pd.DataFrame(grid).merge(poly[["district_idx", "district_id", "district_name", "state_name", "state"]], on="district_idx", how="left").drop(columns=["district_idx"])
write_table(G, "workspace.vf_gold.access_grid",
            "One row per 1 km land cell (WorldPop 2020). km_* = STRAIGHT-LINE km to the nearest facility claiming the service; kmb_* = to the nearest better-evidenced one. Not travel time. Only facilities with a good location flag are used (notebook 11).")
SUMMARY["facilities_used_per_class"] = {s: {"claimed": a, "better_evidenced": b, "service_externally_supported": c} for s, a, b, c in tier_counts}

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Claimed vs better evidenced (`access_evidence_gap`) and district roll-up (`district_service_access`)
# MAGIC `access_evidence_gap` has one row per 5 km block and service. It compares the nearest facility that merely **claims** the service with the nearest **better-evidenced** one, measured from the block's population centre.
# MAGIC `differs` is true when they are different facilities; `gap_flag` when the better-evidenced one is more than 30 km further away.
# MAGIC `km_service_supported` is the distance to the nearest facility whose **service** has outside support. Very few facilities have that today, so this number is usually large.

# COMMAND ----------

w = G.population.values.astype("float64")
blk = G.block_id.values
ub, inv = np.unique(blk, return_inverse=True)
nb = len(ub)
b_pop = np.bincount(inv, weights=w, minlength=nb)
b_n   = np.bincount(inv, minlength=nb)
wz = np.where(b_pop[inv] > 0, w, 1.0)                       # empty blocks: plain centre
b_w  = np.bincount(inv, weights=wz, minlength=nb)
b_lat = np.bincount(inv, weights=wz * G.latitude.values, minlength=nb) / b_w
b_lon = np.bincount(inv, weights=wz * G.longitude.values, minlength=nb) / b_w
b_area = np.bincount(inv, weights=G.area_km2.values.astype("float64"), minlength=nb)
# block district = district of its most populated cell
order = np.lexsort((-wz, inv)); first = np.r_[True, inv[order][1:] != inv[order][:-1]]
b_did = G.district_id.values[order][first]; b_dn = G.district_name.values[order][first]; b_st = G.state_name.values[order][first]; b_sk = G.state.values[order][first]
bxyz = xyz(b_lat, b_lon)

gap_parts, dsa_parts = [], []
didx = pd.Categorical(G.district_id); dcode = didx.codes; nd = len(didx.categories)
d_pop_ = np.bincount(dcode, weights=w, minlength=nd); d_area_ = np.bincount(dcode, weights=G.area_km2.values.astype("float64"), minlength=nd)
area = G.area_km2.values.astype("float64")
for s in SERVICES + list(LEVELS):
    is_service = s in SERVICES
    a = fs[fs.service == s] if is_service else lvg[lvg.care_level >= LEVELS[s]]
    kc = G[f"km_{s}"].values.astype("float64")
    kb = G[f"kmb_{s}"].values.astype("float64") if is_service else None
    # district roll-up
    row = {"district_id": didx.categories, "service": s,
           "pop_weighted_km_claimed": np.bincount(dcode, weights=w * kc, minlength=nd) / np.where(d_pop_ > 0, d_pop_, np.nan),
           "area_weighted_km_claimed": np.bincount(dcode, weights=area * kc, minlength=nd) / d_area_,
           "pct_pop_within_30km_claimed": 100 * np.bincount(dcode, weights=w * (kc <= GAP_KM), minlength=nd) / np.where(d_pop_ > 0, d_pop_, np.nan),
           "pct_area_within_30km_claimed": 100 * np.bincount(dcode, weights=area * (kc <= GAP_KM), minlength=nd) / d_area_,
           "pop_beyond_30km_claimed": np.bincount(dcode, weights=w * (kc > GAP_KM), minlength=nd),
           "pop_beyond_60km_claimed": np.bincount(dcode, weights=w * (kc > 60), minlength=nd)}
    if is_service:
        row.update({"pop_weighted_km_better": np.bincount(dcode, weights=w * kb, minlength=nd) / np.where(d_pop_ > 0, d_pop_, np.nan),
                    "area_weighted_km_better": np.bincount(dcode, weights=area * kb, minlength=nd) / d_area_,
                    "pct_pop_within_30km_better": 100 * np.bincount(dcode, weights=w * (kb <= GAP_KM), minlength=nd) / np.where(d_pop_ > 0, d_pop_, np.nan),
                    "pct_area_within_30km_better": 100 * np.bincount(dcode, weights=area * (kb <= GAP_KM), minlength=nd) / d_area_,
                    "pop_beyond_30km_better": np.bincount(dcode, weights=w * (kb > GAP_KM), minlength=nd),
                    "pop_where_better_is_30km_further": np.bincount(dcode, weights=w * ((kb - kc) > GAP_KM), minlength=nd)})
    dsa_parts.append(pd.DataFrame(row))
    if not is_service: continue
    b = a[a.better]; c = a[a.strict]
    k1, i1 = nearest(bxyz, a); k2, i2 = nearest(bxyz, b); k3, _ = nearest(bxyz, c)
    f1 = a.facility_id.values[i1] if len(a) else np.full(nb, None); f2 = b.facility_id.values[i2] if len(b) else np.full(nb, None)
    gap_parts.append(pd.DataFrame({
        "block_id": ub, "service": s, "latitude": np.round(b_lat, 4), "longitude": np.round(b_lon, 4), "population": np.round(b_pop, 1),
        "district_id": b_did, "district_name": b_dn, "state_name": b_st, "state": b_sk,
        "km_claimed": np.round(k1, 2), "nearest_claimed_facility_id": f1,
        "km_better_evidenced": np.round(k2, 2), "nearest_better_evidenced_facility_id": f2,
        "km_service_supported": np.round(k3, 2),
        "differs": f1 != f2, "gap_km": np.round(k2 - k1, 2), "gap_flag": (k2 - k1) > GAP_KM,
        "pop_weighted_km_claimed": np.round(np.bincount(inv, weights=wz * kc, minlength=nb) / b_w, 2),
        "pop_weighted_km_better": np.round(np.bincount(inv, weights=wz * kb, minlength=nb) / b_w, 2)}))
gap = pd.concat(gap_parts, ignore_index=True)
write_table(gap, "workspace.vf_gold.access_evidence_gap",
            "Per 5 km block and service: nearest facility that claims the service vs nearest better-evidenced one (STRAIGHT-LINE km from the block population centre). differs = different facilities; gap_flag = better-evidenced is over 30 km further. Not travel time (notebook 11).")
dsa = pd.concat(dsa_parts, ignore_index=True).merge(poly[["district_id", "district_name", "state_name", "state", "population_2020", "area_km2"]], on="district_id", how="left")
for c in dsa.columns:
    if c.startswith(("pop_weighted", "area_weighted", "pct_")): dsa[c] = dsa[c].astype("float64").round(2)
    if c.startswith(("pop_beyond", "pop_where")): dsa[c] = dsa[c].round(0)
write_table(dsa, "workspace.vf_gold.district_service_access",
            "Per district and service: population-weighted vs area-weighted STRAIGHT-LINE km, and share of people and of land within 30 km, for claimed and better-evidenced facilities (notebook 11).")

nat = []
for s in SERVICES:
    kc = G[f"km_{s}"].values; kb = G[f"kmb_{s}"].values; g = gap[gap.service == s]
    nat.append({"service": s, "pop_weighted_km_claimed": round(float(np.average(kc, weights=w)), 1), "pop_weighted_km_better": round(float(np.average(kb, weights=w)), 1),
                "pct_pop_beyond_30km_claimed": round(float(100 * w[kc > GAP_KM].sum() / w.sum()), 1), "pct_pop_beyond_30km_better": round(float(100 * w[kb > GAP_KM].sum() / w.sum()), 1),
                "pct_blocks_differs": round(float(100 * g.differs.mean()), 1), "pct_pop_in_gap_flag_blocks": round(float(100 * g.population[g.gap_flag].sum() / g.population.sum()), 1)})
SUMMARY["national_by_service"] = nat
SUMMARY["blocks"] = int(nb)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. `district_context`
# MAGIC One row per polygon district. Facilities are counted by **position** (good locations only). Facilities with suspect or missing positions are counted separately through their address district, so they are visible but never placed on the map.
# MAGIC NFHS indicators are attached through the crosswalk; where several NFHS districts map to one polygon the plain average is shown and `nfhs_districts_linked` says how many.
# MAGIC No facility's beds or doctors are filled in from averages; the missing-data rates say how much is unknown.

# COMMAND ----------

NFHS = {"institutional_birth_5y_pct": "nfhs_institutional_births_pct", "births_delivered_by_csection_5y_pct": "nfhs_csection_births_pct",
        "mothers_who_had_at_least_4_anc_visits_lb5y_pct": "nfhs_4plus_anc_visits_pct", "births_attended_by_skilled_hp_5y_10_pct": "nfhs_skilled_birth_attendance_pct",
        "child_12_23m_fully_vaccinated_based_on_information_from_eit_pct": "nfhs_children_fully_vaccinated_pct", "child_u5_who_are_stunted_height_for_age_18_pct": "nfhs_children_stunted_pct",
        "women_age_15_49_who_are_literate_pct": "nfhs_women_literate_pct", "hh_member_covered_health_insurance_pct": "nfhs_health_insurance_pct",
        "hh_use_improved_sanitation_pct": "nfhs_improved_sanitation_pct", "hh_electricity_pct": "nfhs_electricity_pct",
        "households_using_clean_fuel_for_cooking_pct": "nfhs_clean_cooking_fuel_pct", "all_w15_49_who_are_anaemic_pct": "nfhs_women_anaemic_pct"}
nfv = spark.sql("SELECT state, district_key AS nfhs_district_key, " + ", ".join(NFHS) + " FROM workspace.vf_silver.nfhs_district").toPandas().rename(columns=NFHS)
link = lp[lp.source_list == "nfhs_district"][["state", "district_key", "polygon_district_id"]].rename(columns={"district_key": "nfhs_district_key"}).drop_duplicates()
nfp = link.merge(nfv, on=["state", "nfhs_district_key"]).groupby("polygon_district_id")
nf_agg = nfp[list(NFHS.values())].mean().round(1)
nf_agg["nfhs_districts_linked"] = nfp.nfhs_district_key.nunique()
# deprivation: average national percentile rank over six indicators (higher = more deprived)
dep_cols = {"nfhs_institutional_births_pct": -1, "nfhs_4plus_anc_visits_pct": -1, "nfhs_women_literate_pct": -1, "nfhs_improved_sanitation_pct": -1,
            "nfhs_health_insurance_pct": -1, "nfhs_children_stunted_pct": 1}
ranks = pd.concat([(nf_agg[c] * sgn).rank(pct=True) for c, sgn in dep_cols.items()], axis=1)
nf_agg["deprivation_score"] = ranks.mean(axis=1, skipna=True).round(3)
nf_agg["deprivation_indicators_used"] = ranks.notna().sum(axis=1)
nf_agg["deprivation_tercile"] = pd.qcut(nf_agg.deprivation_score, 3, labels=["least deprived third", "middle third", "most deprived third"]).astype(str)

fac = spark.sql("""
SELECT l.facility_id, l.care_level, l.level_confidence, l.capacity_beds, l.number_doctors, l.latitude, l.longitude, q.geo_flag, f.state, f.district_key
FROM workspace.vf_gold.facility_level_canonical l
JOIN workspace.vf_silver.facility_geo_quality q ON q.facility_id = l.facility_id
JOIN workspace.vf_silver.facilities f ON f.facility_id = l.facility_id
""").toPandas()
good = fac[(fac.geo_flag == "good") & fac.latitude.notna()].copy()
good["didx"] = district_at(good.latitude.values, good.longitude.values)
good = good.merge(poly[["district_idx", "district_id"]], left_on="didx", right_on="district_idx", how="left")
SUMMARY["good_location_facilities_outside_polygons"] = int(good.district_id.isna().sum())
g = good[good.district_id.notna()].groupby("district_id")
sup = pd.DataFrame({"facilities_located": g.size(),
                    "facilities_rung_1": g.care_level.apply(lambda s: int((s == 1).sum())), "facilities_rung_2": g.care_level.apply(lambda s: int((s == 2).sum())),
                    "facilities_rung_3": g.care_level.apply(lambda s: int((s == 3).sum())), "facilities_rung_4": g.care_level.apply(lambda s: int((s == 4).sum())),
                    "facilities_rung_5": g.care_level.apply(lambda s: int((s == 5).sum())), "facilities_no_rung": g.care_level.apply(lambda s: int(s.isna().sum())),
                    "pct_facilities_beds_missing": g.capacity_beds.apply(lambda s: round(100 * s.isna().mean(), 1)),
                    "pct_facilities_doctors_missing": g.number_doctors.apply(lambda s: round(100 * s.isna().mean(), 1)),
                    "pct_facilities_low_confidence_level": g.level_confidence.apply(lambda s: round(100 * (s == "low").mean(), 1))})
# claims per service by position
fsd = fs.copy(); fsd["didx"] = district_at(fsd.latitude.values, fsd.longitude.values)
fsd = fsd.merge(poly[["district_idx", "district_id"]], left_on="didx", right_on="district_idx", how="left")
cl = fsd.pivot_table(index="district_id", columns="service", values="facility_id", aggfunc="count", fill_value=0).add_prefix("claimed_")
cb = fsd[fsd.better].pivot_table(index="district_id", columns="service", values="facility_id", aggfunc="count", fill_value=0).add_prefix("better_evidenced_")
# suspect / missing positions, through the address district
addr = fac.merge(lp[lp.source_list == "pin_district"][["state", "district_key", "polygon_district_id"]], on=["state", "district_key"], how="left")
ga = addr[addr.polygon_district_id.notna()].groupby("polygon_district_id")
adr = pd.DataFrame({"facilities_by_address": ga.size(), "facilities_suspect_or_no_location": ga.geo_flag.apply(lambda s: int((s != "good").sum()))})
adr["pct_facilities_suspect_or_no_location"] = (100 * adr.facilities_suspect_or_no_location / adr.facilities_by_address).round(1)

head = dsa[dsa.service.isin(["emergency", "c_section", "icu", "level3plus"])]
cov = None
for s in ["emergency", "c_section", "icu", "level3plus"]:
    h = head[head.service == s].set_index("district_id")
    part = pd.DataFrame({f"straight_km_pop_weighted_{s}": h.pop_weighted_km_claimed, f"straight_km_area_weighted_{s}": h.area_weighted_km_claimed,
                         f"pct_pop_within_30km_{s}": h.pct_pop_within_30km_claimed, f"pct_area_within_30km_{s}": h.pct_area_within_30km_claimed})
    if s != "level3plus":
        part[f"straight_km_pop_weighted_{s}_better_evidenced"] = h.pop_weighted_km_better
        part[f"pct_pop_within_30km_{s}_better_evidenced"] = h.pct_pop_within_30km_better
    cov = part if cov is None else cov.join(part)

ctx = poly.drop(columns=["district_idx", "geometry_wkt", "grid_cells"]).set_index("district_id").join([nf_agg, sup, cl, cb, adr, cov]).reset_index()
count_cols = [c for c in ctx.columns if c.startswith(("facilities_", "claimed_", "better_evidenced_"))]
ctx[count_cols] = ctx[count_cols].fillna(0).astype("int64")
ctx["nfhs_districts_linked"] = ctx.nfhs_districts_linked.fillna(0).astype("int64")
ctx["nfhs_status"] = np.where(ctx.nfhs_districts_linked == 0, "no NFHS district linked (see district_crosswalk_issues)", np.where(ctx.nfhs_districts_linked > 1, "average of several NFHS districts", "one NFHS district"))
ctx.loc[ctx.deprivation_tercile.isin(["nan", "None"]) | ctx.deprivation_score.isna(), "deprivation_tercile"] = None
ctx["facilities_per_100k"] = (1e5 * ctx.facilities_located / ctx.population_2020.where(ctx.population_2020 > 0)).round(2)
ctx["hospitals_rung3plus_per_100k"] = (1e5 * (ctx.facilities_rung_3 + ctx.facilities_rung_4 + ctx.facilities_rung_5) / ctx.population_2020.where(ctx.population_2020 > 0)).round(2)
ctx["distance_kind"] = "straight-line, not travel time"
write_table(ctx, "workspace.vf_gold.district_context",
            "One row per district polygon: NFHS-5 indicators (through the crosswalk), WorldPop 2020 population, facility supply by position, missing-data rates, suspect-location share, and population- vs area-weighted STRAIGHT-LINE coverage (notebook 11).")
SUMMARY.update(context_rows=int(len(ctx)), context_with_nfhs=int((ctx.nfhs_districts_linked > 0).sum()), context_with_no_facility=int((ctx.facilities_located == 0).sum()))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. `critical_zones`
# MAGIC A 5 km block is a critical zone for a service when **all three** hold:
# MAGIC 1. **People:** at least 5,000 people live in the block (about 200 per km2 or more).
# MAGIC 2. **Distance:** the population-weighted straight-line distance to the nearest facility **claiming** the service is over 30 km.
# MAGIC 3. **Deprivation:** its district is in the most deprived third on the NFHS-based score (institutional births, 4+ antenatal visits, women's literacy, sanitation, health insurance, child stunting).
# MAGIC
# MAGIC `priority_rank` orders the zones of one service and type by people x distance (1 = most people furthest away), so a map or a shortlist can take the top N.
# MAGIC `zone_type = 'critical'` meets all three. `'far and populated, NFHS unknown'` meets 1 and 2 in a district with no NFHS link. `'far only on better evidence'` is within 30 km of a claimed facility but over 30 km from a better-evidenced one, in a most-deprived district.
# MAGIC These thresholds are planning choices written down so they can be changed; 30 km is not a clinical rule, and the distance is straight-line.

# COMMAND ----------

ZONE_SERVICES = ["emergency", "trauma", "obstetric_delivery", "c_section", "nicu", "icu", "dialysis", "blood_bank"]
MIN_POP = 5000
dctx = ctx.set_index("district_id")[["deprivation_tercile", "deprivation_score", "nfhs_districts_linked"]]
z = gap[gap.service.isin(ZONE_SERVICES) & (gap.population >= MIN_POP)].merge(dctx, left_on="district_id", right_index=True, how="left")
far_c = z.pop_weighted_km_claimed > GAP_KM
far_b = z.pop_weighted_km_better > GAP_KM
dep = z.deprivation_tercile == "most deprived third"
z["zone_type"] = np.select([far_c & dep, far_c & (z.nfhs_districts_linked.fillna(0) == 0), ~far_c & far_b & dep],
                           ["critical", "far and populated, NFHS unknown", "far only on better evidence"], default="")
z = z[z.zone_type != ""].copy()
z["people_km"] = (z.population * np.where(z.zone_type == "far only on better evidence", z.pop_weighted_km_better, z.pop_weighted_km_claimed)).round(0)
z["priority_rank"] = z.groupby(["service", "zone_type"]).people_km.rank(ascending=False, method="first").astype("int64")
z["criteria"] = f"block population >= {MIN_POP}; population-weighted straight-line km to nearest claimed facility > {GAP_KM} (or better-evidenced, for the third type); district in the most deprived NFHS third"
zones = z[["block_id", "service", "zone_type", "priority_rank", "people_km", "latitude", "longitude", "population", "district_id", "district_name", "state_name", "state",
           "pop_weighted_km_claimed", "pop_weighted_km_better", "nearest_claimed_facility_id", "nearest_better_evidenced_facility_id",
           "deprivation_score", "deprivation_tercile", "criteria"]].rename(columns={"pop_weighted_km_claimed": "straight_km_claimed", "pop_weighted_km_better": "straight_km_better_evidenced"})
write_table(zones, "workspace.vf_gold.critical_zones",
            "5 km blocks with many people, a long STRAIGHT-LINE distance to a service and a deprived district. Criteria are in the criteria column. Planning thresholds, not clinical rules (notebook 11).")
SUMMARY["critical_zones"] = {f"{a} | {b}": [int(n), int(p)] for (a, b), n, p in zip(zones.groupby(["service", "zone_type"]).size().index, zones.groupby(["service", "zone_type"]).size().values, zones.groupby(["service", "zone_type"]).population.sum().values)}

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Real road time for a small set of trips
# MAGIC `route_minutes(pairs)` returns driving minutes and km for a short list of origin -> destination pairs.
# MAGIC - Default provider: the public OSRM demo server (OpenStreetMap roads, free-flow speeds, no traffic, no key). It is rate-limited and meant for small tests.
# MAGIC - `provider="google"` uses the Google Distance Matrix API with a key you pass in. **Do not store Google results in tables** (terms of service); call it live for the trips on screen.
# MAGIC
# MAGIC Use it for the handful of facilities shown to a user, not for the national grid.

# COMMAND ----------

def route_minutes(pairs, provider="osrm", google_key=None, pause=1.0):
    """pairs: list of (origin_lat, origin_lon, dest_lat, dest_lon). Returns a list of dicts with raw minutes and km, or an error."""
    out = []
    for (olat, olon, dlat, dlon) in pairs:
        try:
            if provider == "osrm":
                u = f"https://router.project-osrm.org/route/v1/driving/{olon},{olat};{dlon},{dlat}?overview=false"
                j = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "python-urllib"}), timeout=30))
                r = j["routes"][0]
                out.append({"provider": "osrm (OpenStreetMap, no traffic)", "minutes": round(r["duration"] / 60, 1), "road_km": round(r["distance"] / 1000, 1)})
            elif provider == "google":
                u = ("https://maps.googleapis.com/maps/api/distancematrix/json?"
                     f"origins={olat},{olon}&destinations={dlat},{dlon}&departure_time=now&key={google_key}")
                e = json.load(urllib.request.urlopen(u, timeout=30))["rows"][0]["elements"][0]
                out.append({"provider": "google (with traffic)", "minutes": round(e.get("duration_in_traffic", e["duration"])["value"] / 60, 1), "road_km": round(e["distance"]["value"] / 1000, 1)})
            else:
                raise ValueError("provider must be 'osrm' or 'google'")
        except Exception as ex:
            out.append({"provider": provider, "minutes": None, "road_km": None, "error": str(ex)[:200]})
        time.sleep(pause)
    return out

# a small live check: six critical-zone blocks -> their nearest claimed emergency facility. Shown, not stored.
route_test = []
try:
    zs = zones[(zones.service == "emergency") & (zones.zone_type == "critical")].sort_values("population", ascending=False).head(6)
    fpos = fs[fs.service == "emergency"].drop_duplicates("facility_id").set_index("facility_id")[["latitude", "longitude"]]
    prs = [(r.latitude, r.longitude, float(fpos.loc[r.nearest_claimed_facility_id].latitude), float(fpos.loc[r.nearest_claimed_facility_id].longitude)) for r in zs.itertuples()]
    for r, t in zip(zs.itertuples(), route_minutes(prs)):
        route_test.append({"district": r.district_name, "state": r.state_name, "straight_km": float(r.straight_km_claimed), **t})
except Exception as ex:
    route_test.append({"error": str(ex)[:200]})
SUMMARY["route_test"] = route_test
display(pd.DataFrame(route_test))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9. Tests

# COMMAND ----------

km_cols  = [f"km_{s}" for s in SERVICES]
bad_order = " + ".join(f"sum(CAST(kmb_{s} < km_{s} - 0.02 AS INT))" for s in SERVICES)
neg = " + ".join(f"sum(CAST(km_{s} < 0 OR km_{s} IS NULL AS INT))" for s in SERVICES + list(LEVELS))
spark.sql(f"""
CREATE OR REPLACE TABLE workspace.vf_gold.geography_checks
COMMENT 'Pass/fail tests for the geography tables (notebook 11)'
AS
SELECT check_name, bad_rows, bad_rows = 0 AS passed, detail, current_timestamp() AS checked_at FROM (
  SELECT 1 AS o, 'population total is plausible for India 2020 (1.30 to 1.45 billion)' AS check_name,
         (SELECT CAST(NOT (sum(population) BETWEEN 1.30e9 AND 1.45e9) AS INT) FROM workspace.vf_gold.access_grid) AS bad_rows,
         (SELECT concat('grid total ', CAST(round(sum(population) / 1e6) AS STRING), ' million') FROM workspace.vf_gold.access_grid) AS detail
  UNION ALL SELECT 2, 'every grid cell has a district and is unique',
         (SELECT sum(CAST(district_id IS NULL AS INT)) + count(*) - count(DISTINCT cell_id) FROM workspace.vf_gold.access_grid), 'cells come from the district raster'
  UNION ALL SELECT 3, 'district populations add up to the grid total',
         (SELECT CAST(abs((SELECT sum(population_2020) FROM workspace.vf_silver.district_polygons) - sum(population)) > 1000 AS INT) FROM workspace.vf_gold.access_grid), 'no people lost between grid and districts'
  UNION ALL SELECT 4, 'distances are present and not negative',
         (SELECT {neg} FROM workspace.vf_gold.access_grid), 'all km columns'
  UNION ALL SELECT 5, 'better-evidenced distance is never shorter than claimed distance',
         (SELECT {bad_order} FROM workspace.vf_gold.access_grid), 'better-evidenced facilities are a subset of claiming facilities'
  UNION ALL SELECT 6, 'district_context has one row per polygon',
         (SELECT abs(count(*) - count(DISTINCT district_id)) + abs(count(*) - (SELECT count(*) FROM workspace.vf_silver.district_polygons)) FROM workspace.vf_gold.district_context), ''
  UNION ALL SELECT 7, 'facilities counted by position do not exceed good-location facilities',
         (SELECT CAST(sum(facilities_located) > (SELECT count(*) FROM workspace.vf_gold.facility_level_canonical l JOIN workspace.vf_silver.facility_geo_quality q ON q.facility_id = l.facility_id WHERE q.geo_flag = 'good') AS INT) FROM workspace.vf_gold.district_context), 'suspect and missing positions are never placed on the map'
  UNION ALL SELECT 8, 'no facility with a suspect or invalid location is used as a nearest facility',
         (SELECT count(*) FROM (SELECT nearest_claimed_facility_id AS f FROM workspace.vf_gold.access_evidence_gap UNION SELECT nearest_better_evidenced_facility_id FROM workspace.vf_gold.access_evidence_gap) x
            JOIN workspace.vf_silver.facility_geo_quality q ON q.facility_id = x.f WHERE q.geo_flag <> 'good'), 'precise distances use good locations only'
  UNION ALL SELECT 9, 'gap table: one row per block and service, gap never negative',
         (SELECT count(*) - count(DISTINCT block_id, service) + sum(CAST(gap_km < -0.05 AS INT)) FROM workspace.vf_gold.access_evidence_gap), ''
  UNION ALL SELECT 10, 'crosswalk links always carry a method and a state',
         (SELECT count(*) FROM workspace.vf_gold.district_crosswalk WHERE (polygon_district_id IS NOT NULL AND (link_method IS NULL OR polygon_state_name IS NULL)) OR (link_status = 'linked' AND polygon_district_id IS NULL)), 'every link has a method and a state; no name-only joins'
  UNION ALL SELECT 11, 'critical zones meet their own criteria',
         (SELECT count(*) FROM workspace.vf_gold.critical_zones WHERE population < {MIN_POP} OR (zone_type = 'critical' AND (straight_km_claimed <= {GAP_KM} OR deprivation_tercile <> 'most deprived third'))), ''
  UNION ALL SELECT 12, 'NFHS percentages in district_context are within 0 to 100',
         (SELECT count(*) FROM workspace.vf_gold.district_context WHERE nfhs_institutional_births_pct NOT BETWEEN 0 AND 100 OR nfhs_women_literate_pct NOT BETWEEN 0 AND 100), ''
) ORDER BY o
""")
chk = spark.sql("SELECT check_name, bad_rows, passed, detail FROM workspace.vf_gold.geography_checks").toPandas()
display(chk)
SUMMARY["checks"] = {r.check_name: [int(r.bad_rows), bool(r.passed), r.detail] for r in chk.itertuples()}
SUMMARY["sources"] = {r["source_name"]: {"vintage": r["vintage"], "units": r["units"], "licence": r["licence"]} for r in src_rows}

# COMMAND ----------

dbutils.notebook.exit(json.dumps(SUMMARY, default=str))
