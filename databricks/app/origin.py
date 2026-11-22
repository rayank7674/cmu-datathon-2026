"""Where a search starts from when the user picks a district.

The geometric centre of a district is often farmland far from its main town
(Patna's centre is about 17 km from the city), which made every result look far
away. The start point is now the most populated 1 km cell inside the district
(WorldPop grid in vf_gold.access_grid, saved in data/district_towns.csv and
joined on district_id), so it is always inside the district. In a district with
two towns it can be the second town. Falls back to the geometric centre when a
district has no row in the file.
"""
import os

import numpy as np
import pandas as pd

TOWNS_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "district_towns.csv")


def add_district_towns(districts: pd.DataFrame, towns: pd.DataFrame | None = None) -> pd.DataFrame:
    """Return `districts` with town_lat, town_lon (NaN where the centre is kept)."""
    out = districts.drop(columns=["town_lat", "town_lon"], errors="ignore").copy()
    if towns is None:
        towns = pd.read_csv(TOWNS_CSV, dtype={"district_id": str})
    t = towns[["district_id", "town_lat", "town_lon"]].copy()
    t["district_id"] = t["district_id"].astype(str)
    for c in ("town_lat", "town_lon"):
        t[c] = pd.to_numeric(t[c], errors="coerce")
    t = t[t["town_lat"].between(6, 37.5) & t["town_lon"].between(68, 97.5)].drop_duplicates("district_id")
    out["_k"] = out["district_id"].astype(str)
    out = out.merge(t.rename(columns={"district_id": "_k"}), on="_k", how="left").drop(columns="_k")
    out.index = districts.index
    return out
