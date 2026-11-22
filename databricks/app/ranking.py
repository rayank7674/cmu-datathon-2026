"""PLACEHOLDER ranking for the app shell (chunk F).

Everything the app shows in ranked order goes through one function, rank().
Swap the body of rank() for the real triage score later; nothing else in the
app needs to change as long as the returned columns stay the same.

The placeholder order is deliberately simple and explainable:
  1. care level meets the usual minimum for the service (yes before no)
  2. the facility states the service directly (strong or moderate claim)
     before label-only or indirect wording (weak claim)
  3. straight-line distance, nearest first
Facilities whose map location is suspect or invalid are never ranked by
distance; they are returned separately so the app can list them with a warning.

Feedback reports and the user's profile do NOT change the order.
"""
import numpy as np
import pandas as pd

PLACEHOLDER_NOTE = (
    "PLACEHOLDER ranking: facilities whose care level meets the usual minimum "
    "for this service come first, then those that state the service directly, "
    "then nearest by straight-line distance. "
    "It does not measure quality, travel time, cost or whether the service is "
    "working today."
)

# Usual minimum care rung at which a service is normally found (1 = sub-centre,
# 2 = PHC / clinic, 3 = CHC / small hospital, 4 = district / multispecialty,
# 5 = medical college / tertiary). A placeholder assumption, not a rule.
MIN_LEVEL = {
    "emergency": 2, "obstetric_delivery": 2, "paediatrics": 2,
    "c_section": 3, "blood_bank": 3, "icu": 3, "nicu": 3, "dialysis": 3,
    "ct": 3, "mri": 3, "trauma": 3, "burns": 3,
    "stroke": 4, "cardiac_cath": 4,
}


def haversine_km(lat1, lon1, lat2, lon2):
    """Straight-line (great-circle) distance in km. lat2/lon2 may be arrays."""
    r = 6371.0088
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = p2 - p1
    dl = np.radians(lon2) - np.radians(lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def rank(candidates: pd.DataFrame, context: dict):
    """Order candidate facilities for one service and one starting point.

    candidates: one row per facility that claims the service, with at least
        latitude, longitude, care_level, location_quality_flag.
    context: {"service": str, "lat": float, "lon": float,
              "radius_km": float | None, "profile": dict}
        profile is passed through for the future score; the placeholder
        ignores it on purpose.

    Returns (ranked, unplaced):
        ranked   - usable-location facilities inside the radius, best first,
                   with columns km, min_level, level_meets, rank, rank_reason
        unplaced - facilities in the same district/state whose location is
                   suspect or invalid, so distance cannot be trusted
    """
    df = candidates.copy()
    service = context["service"]
    min_level = MIN_LEVEL.get(service, 3)
    df["min_level"] = min_level
    df["level_meets"] = df["care_level"].fillna(0) >= min_level

    usable = df["location_quality_flag"].eq("good") & df["latitude"].notna() & df["longitude"].notna()
    placed = df[usable].copy()
    unplaced = df[~usable].copy()

    placed["km"] = haversine_km(context["lat"], context["lon"],
                                placed["latitude"].to_numpy(float),
                                placed["longitude"].to_numpy(float))
    radius = context.get("radius_km")
    if radius:
        placed = placed[placed["km"] <= radius]

    placed["direct_claim"] = placed["claim_strength"].isin(["strong", "moderate"])
    placed = placed.sort_values(["level_meets", "direct_claim", "km"],
                                ascending=[False, False, True]).reset_index(drop=True)
    placed["rank"] = placed.index + 1

    def reason(r):
        lvl = "unknown" if pd.isna(r["care_level"]) else int(r["care_level"])
        if r["level_meets"]:
            first = f"Its care level ({lvl}) meets the usual minimum for this service ({min_level})."
        else:
            first = f"Its care level ({lvl}) is below the usual minimum for this service ({min_level}), so it is listed after the others."
        second = ("It states the service directly." if r["direct_claim"] else
                  "It only has a label or indirect wording for the service, so it is listed after direct claims.")
        return f"{first} {second} It is about {r['km']:.0f} km away in a straight line."

    placed["rank_reason"] = placed.apply(reason, axis=1) if len(placed) else []

    # Suspect-location facilities: keep only those the dataset places nearby by
    # address (same district), since their coordinates cannot be used.
    if context.get("district") and len(unplaced):
        unplaced = unplaced[unplaced["district"].fillna("").str.lower() == str(context["district"]).lower()]
    else:
        unplaced = unplaced.iloc[0:0]
    return placed, unplaced
