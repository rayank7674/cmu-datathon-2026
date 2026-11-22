"""Facility Finder - app shell (chunk F).

A Databricks App (Streamlit). Service + location -> candidate facilities, each
with the claim, the quoted evidence, a source link, confidence, verification
status and what is still unknown.

This is facility discovery, NOT medical triage. The service search uses the
labelled placeholder in ranking.rank(); the "Describe symptoms" search uses the
modular recommender in recommender/ (see recommender/README.md).
"""
import base64
import io
import json
import os
import uuid
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st
from databricks import sql
from databricks.sdk.core import Config

from ranking import MIN_LEVEL, PLACEHOLDER_NOTE, haversine_km, rank
from symptom_ui import run_symptom_search, show_result, symptom_form

CATALOG = os.getenv("VF_CATALOG", "workspace")
GOLD = f"{CATALOG}.vf_gold"
SILVER = f"{CATALOG}.vf_silver"
FEEDBACK_TABLE = f"{GOLD}.feedback_reports"

st.set_page_config(page_title="Facility Finder (India)", page_icon="🏥", layout="wide")


# --------------------------------------------------------------------------
# Data access
# --------------------------------------------------------------------------
@st.cache_resource
def _cfg():
    return Config()


def _connect():
    cfg = _cfg()
    wh = os.environ["DATABRICKS_WAREHOUSE_ID"]
    return sql.connect(
        server_hostname=cfg.host.replace("https://", "").rstrip("/"),
        http_path=f"/sql/1.0/warehouses/{wh}",
        credentials_provider=lambda: cfg.authenticate,
        use_cloud_fetch=False,
    )


def query(q: str, params: dict | None = None) -> pd.DataFrame:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(q, params or {})
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
    return pd.DataFrame([list(r) for r in rows], columns=cols)


def execute(q: str, params: dict | None = None) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(q, params or {})


def optional_value(fn):
    """Like optional(), for loaders that return something other than a table."""
    try:
        return fn()
    except Exception:
        return None


def optional(fn):
    """Run a loader; return None when its table is missing or unreadable."""
    try:
        df = fn()
        return df if df is not None and len(df) else None
    except Exception:  # table not built yet, or no permission
        return None


@st.cache_data(ttl=3600, show_spinner="Loading service claims...")
def load_claims() -> pd.DataFrame:
    df = _claims_query("c.claim_detected")
    df["directory_only"] = False
    return df


def _claims_query(where: str) -> pd.DataFrame:
    df = query(f"""
        SELECT c.facility_id, c.name, c.state, c.district, c.facility_type, c.level_kind,
               c.service, c.service_label, c.care_level, c.service_specific_level,
               c.claim_status, c.claim_strength, c.claim_basis,
               c.supporting_quote, c.source_field, c.negating_quote, c.plausibility_flag,
               c.level_confidence, CAST(c.quality_score AS DOUBLE) AS quality_score,
               c.verification_status, c.verification_source,
               CAST(c.verification_date AS STRING) AS verification_date,
               c.contradiction_flag, c.facility_existence_status, c.facility_existence_source,
               c.latitude, c.longitude, c.location_quality_flag,
               c.contact_verification_status, c.contact_phone, c.availability_status,
               c.hours_claim, c.hours_quote, c.evidence_facility_id, c.merged_records,
               f.official_website, f.source_urls[0] AS source_url,
               l.city, l.operator_type, coalesce(rv.beds_resolved, l.capacity_beds) AS capacity_beds,
               rv.vf_beds, rv.beds_resolution_source, rv.beds_external_source,
               rv.nabh_status_resolved, l.pmjay_claimed, l.cghs_claimed
        FROM {GOLD}.facility_service_canonical c
        LEFT JOIN {SILVER}.facilities f ON f.facility_id = c.evidence_facility_id
        LEFT JOIN {GOLD}.facility_level_canonical l ON l.facility_id = c.facility_id
        LEFT JOIN {GOLD}.facility_resolved_values rv ON rv.facility_id = c.facility_id
        WHERE {where}
    """)
    for col in ("latitude", "longitude", "care_level", "quality_score"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


DIRECTORY_LABEL = "listed in the national blood centre directory"


@st.cache_data(ttl=3600, show_spinner=False)
def load_directory_blood_banks() -> pd.DataFrame:
    """Facilities with no blood bank claim of their own that the eRaktKosh
    directory lists as a Blood Bank. Directory evidence only: not verified,
    nothing about stock or whether it is working today."""
    df = _claims_query("NOT c.claim_detected AND c.service = 'blood_bank' "
                       "AND c.verification_source = 'eraktkosh' "
                       "AND c.verification_status = 'externally_supported'")
    df["claim_strength"] = "directory listing"
    df["claim_basis"] = DIRECTORY_LABEL
    df["supporting_quote"] = None
    df["directory_only"] = True
    return df


@st.cache_data(ttl=3600, show_spinner="Loading facilities...")
def load_facilities() -> pd.DataFrame:
    df = query(f"""
        SELECT facility_id, name, facility_type, district, state, latitude, longitude,
               care_level, level_kind
        FROM {GOLD}.facility_level_canonical
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
    """)
    for col in ("latitude", "longitude", "care_level"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(ttl=3600, show_spinner="Loading districts...")
def load_districts() -> pd.DataFrame:
    df = query(f"""
        SELECT district_id, district_name, state_name, state, centre_lat, centre_lon,
               population_2020, area_km2, facilities_located,
               pct_facilities_suspect_or_no_location, deprivation_score, deprivation_tercile,
               nfhs_status, nfhs_institutional_births_pct, nfhs_health_insurance_pct,
               facilities_per_100k, hospitals_rung3plus_per_100k
        FROM {GOLD}.district_context
    """)
    for col in df.columns:
        if col not in ("district_id", "district_name", "state_name", "state",
                       "deprivation_tercile", "nfhs_status"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(ttl=3600, show_spinner="Loading district access figures...")
def load_district_access() -> pd.DataFrame:
    df = query(f"""
        SELECT district_id, service, pop_weighted_km_claimed, pop_weighted_km_better,
               pct_pop_within_30km_claimed, pct_pop_within_30km_better,
               pop_beyond_30km_claimed, pop_beyond_60km_claimed
        FROM {GOLD}.district_service_access
    """)
    for col in df.columns[2:]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(ttl=3600, show_spinner="Loading map blocks...")
def load_blocks(district_id: str, service: str) -> pd.DataFrame:
    df = query(f"""
        SELECT latitude, longitude, population, km_claimed, km_better_evidenced, gap_flag
        FROM {GOLD}.access_evidence_gap
        WHERE district_id = :d AND service = :s
    """, {"d": district_id, "s": service})
    for col in ("latitude", "longitude", "population", "km_claimed", "km_better_evidenced"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(ttl=3600)
def load_zones(district_id: str, service: str) -> pd.DataFrame:
    df = query(f"""
        SELECT latitude, longitude, population, zone_type, priority_rank,
               straight_km_claimed, criteria
        FROM {GOLD}.critical_zones
        WHERE district_id = :d AND service = :s
    """, {"d": district_id, "s": service})
    for col in ("latitude", "longitude", "population", "straight_km_claimed"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(ttl=3600, show_spinner="Loading catchments...")
def load_catchments(service: str, tier: str) -> pd.DataFrame:
    df = query(f"""
        SELECT facility_id, facility_name, n_facilities_at_point, facility_latitude, facility_longitude,
               area_km2, population, polygon_json
        FROM {GOLD}.service_catchments
        WHERE service = :s AND tier = :t
    """, {"s": service, "t": tier})
    for col in ("facility_latitude", "facility_longitude", "area_km2", "population"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["polygon"] = df["polygon_json"].map(json.loads)
    return df.drop(columns=["polygon_json"])


@st.cache_data(ttl=3600)
def lookup_pin(pin: str) -> pd.DataFrame:
    return query(f"""
        SELECT pincode, district, state, centroid_lat, centroid_lon
        FROM {SILVER}.pincode WHERE pincode = :p
    """, {"p": pin})


@st.cache_resource
def ensure_feedback_table() -> bool:
    try:
        query(f"SELECT 1 FROM {FEEDBACK_TABLE} LIMIT 1")
        return True
    except Exception:
        pass
    try:
        execute(f"""
            CREATE TABLE IF NOT EXISTS {FEEDBACK_TABLE} (
              report_id STRING, created_at TIMESTAMP, facility_id STRING, facility_name STRING,
              service STRING, report_type STRING, label STRING, source_tab STRING)
        """)
        return True
    except Exception:
        return False


def save_feedback(facility_id, facility_name, service, report_type, tab):
    """Append one unverified report. Never stores profile data. Never affects ranking."""
    execute(f"""
        INSERT INTO {FEEDBACK_TABLE}
          (report_id, created_at, facility_id, facility_name, service, report_type, label, source_tab)
        VALUES (:id, current_timestamp(), :f, :n, :s, :t, 'unverified report', :tab)
    """, {"id": str(uuid.uuid4()), "f": facility_id, "n": facility_name, "s": service,
          "t": report_type, "tab": tab})


# --------------------------------------------------------------------------
# Wording
# --------------------------------------------------------------------------
STRENGTH_PLAIN = {
    "strong": "Stronger: the facility says this directly in two or more places.",
    "moderate": "Medium: the facility says this directly in one place.",
    "weak": "Weak: only a specialty label or indirect wording, not a direct statement.",
}
VERIFY_PLAIN = {
    "inferred": "Not checked. This rests only on what the facility says about itself.",
    "externally_supported": "An outside source supports this, but no one has confirmed the service itself.",
    "verified": "Confirmed by an official source for this service.",
    "claimed": "Not checked. This rests only on what the facility says about itself.",
}
FIELD_PLAIN = {"capability": "its capabilities list", "equipment": "its equipment list",
               "procedure": "its procedures list", "specialty": "its specialty tags", "specialties": "its specialty tags",
               "description": "its description"}
FEEDBACK_TYPES = [("couldnt_treat", "Couldn't treat"), ("service_down", "Service down"),
                  ("treated_ok", "Treated OK"), ("wrong_number", "Wrong number")]


def safety_block():
    st.error(
        "**In an emergency, call 108** (102 for pregnancy or a newborn). Do not wait for this app.\n\n"
        "This is a facility discovery tool, not medical triage. It cannot tell you what care you "
        "need, and it does not know whether a service is open or working right now. "
        "Phone the facility before you travel.",
        icon="🚨",
    )


def clean(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) or str(v) in ("None", "nan", "") else v


def source_link(r):
    url = clean(r.get("source_url"))
    site = clean(r.get("official_website"))
    parts = []
    if url:
        parts.append(f"[page this record was collected from]({url})")
    if site:
        href = site if str(site).startswith("http") else f"https://{site}"
        parts.append(f"[facility website]({href})")
    return " · ".join(parts) if parts else "no source link in the dataset"


def resolved_notes(r):
    """Lines for values where the team chose the outside source over the facility's own record."""
    out = []
    if clean(r.get("nabh_status_resolved")) == "withdrawn":
        out.append("⚠️ **NABH accreditation withdrawn.** The NABH directory lists this facility's accreditation as "
                   "withdrawn, although the facility's own text says it is accredited. The directory is used "
                   "(team decision, 22 Nov 2026).")
    if clean(r.get("beds_resolution_source")) == "external" and clean(r.get("capacity_beds")) is not None:
        out.append(f"Beds: {int(float(r['capacity_beds']))} according to {r['beds_external_source']} "
                   f"(the facility's record said {int(float(r['vf_beds']))}; the outside source is used by team decision, "
                   "it is not a verification).")
    return out


def unknowns(r):
    out = ["Whether the service is available or working today: **unknown**."]
    if r["verification_status"] != "verified":
        out.append("Whether the facility really provides this service: not confirmed by any official source.")
    if r["hours_claim"] != "24x7":
        out.append("Opening hours: unknown.")
    else:
        out.append("It says it is open 24x7, but that is its own claim.")
    if clean(r["contact_phone"]) is None:
        out.append("Phone number: none in the dataset.")
    else:
        out.append("Whether the phone number still works: not checked.")
    if r["facility_existence_status"] != "externally_supported":
        out.append("Whether the facility exists at this location: not confirmed by an outside source.")
    if r["location_quality_flag"] != "good":
        out.append("Its map location looks wrong, so distance is not shown.")
    out.append("Cost, beds free and waiting time: unknown.")
    return out


# --------------------------------------------------------------------------
# Shared search UI
# --------------------------------------------------------------------------
def location_input(key, districts):
    mode = st.radio("Where are you starting from?", ["District", "PIN code", "Coordinates"],
                    horizontal=True, key=f"{key}_mode")
    if mode == "District":
        if districts is None:
            st.info("District list is not available yet. Use a PIN code or coordinates.")
            return None
        c1, c2 = st.columns(2)
        states = sorted(districts["state_name"].dropna().unique())
        state = c1.selectbox("State", states, key=f"{key}_state",
                             index=states.index("Bihār") if "Bihār" in states else 0)
        sub = districts[districts["state_name"] == state].sort_values("district_name")
        names = sub["district_name"].tolist()
        name = c2.selectbox("District", names, key=f"{key}_district",
                            index=names.index("Patna") if "Patna" in names else 0)
        row = sub[sub["district_name"] == name].iloc[0]
        return {"lat": float(row["centre_lat"]), "lon": float(row["centre_lon"]),
                "district": name, "label": f"centre of {name} district, {state}",
                "approx": "Distances are measured from the centre of the district, so they are rough."}
    if mode == "PIN code":
        pin = st.text_input("6-digit PIN code", key=f"{key}_pin", max_chars=6, placeholder="e.g. 800001")
        if not pin:
            return None
        if not (pin.isdigit() and len(pin) == 6):
            st.warning("A PIN code has 6 digits.")
            return None
        hit = lookup_pin(pin)
        if hit.empty or clean(hit.iloc[0]["centroid_lat"]) is None:
            st.warning("That PIN code is not in the dataset's PIN list. Try the district or coordinates.")
            return None
        row = hit.iloc[0]
        return {"lat": float(row["centroid_lat"]), "lon": float(row["centroid_lon"]),
                "district": row["district"], "label": f"PIN {pin} ({row['district']}, {row['state']})",
                "approx": "Distances are measured from the middle of the PIN code area, so they are rough."}
    c1, c2 = st.columns(2)
    lat = c1.number_input("Latitude", min_value=6.0, max_value=37.5, value=25.61, format="%.5f", key=f"{key}_lat")
    lon = c2.number_input("Longitude", min_value=68.0, max_value=97.5, value=85.14, format="%.5f", key=f"{key}_lon")
    return {"lat": float(lat), "lon": float(lon), "district": None,
            "label": f"{lat:.4f}, {lon:.4f}", "approx": None}


def search_form(key, claims, districts, default_radius=50):
    services = (claims[["service", "service_label"]].drop_duplicates()
                .sort_values("service_label"))
    labels = dict(zip(services["service"], services["service_label"]))
    c1, c2 = st.columns([2, 1])
    opts = list(labels)
    service = c1.selectbox("Service needed", opts, format_func=labels.get, key=f"{key}_service",
                           index=opts.index("emergency") if "emergency" in opts else 0)
    radius = c2.slider("Search within (km, straight line)", 10, 300, default_radius, 10, key=f"{key}_radius")
    origin = location_input(key, districts)
    if st.button("Find facilities", type="primary", key=f"{key}_go", disabled=origin is None):
        st.session_state[f"{key}_search"] = {"service": service, "radius_km": radius, **origin}
    return st.session_state.get(f"{key}_search"), labels


def run_search(claims, search, profile):
    cand = claims[claims["service"] == search["service"]]
    if search["service"] == "blood_bank":
        extra = optional(load_directory_blood_banks)
        if extra is not None and len(extra):
            cand = pd.concat([cand, extra], ignore_index=True)
    return rank(cand, {**search, "profile": profile})


def feedback_buttons(r, tab, key):
    st.caption("Tell us what happened. Reports are stored as **unverified reports** and do not change the ranking.")
    cols = st.columns(len(FEEDBACK_TYPES))
    for col, (code, label) in zip(cols, FEEDBACK_TYPES):
        if col.button(label, key=f"{key}_{code}", use_container_width=True):
            try:
                save_feedback(r["facility_id"], r["name"], r["service"], code, tab)
                st.toast(f"Saved as an unverified report: {label}")
            except Exception as e:
                st.warning(f"Could not save the report ({type(e).__name__}).")


def results_map(ranked, search, height=380):
    pts = ranked.head(200)[["name", "latitude", "longitude", "km", "rank"]].copy()
    pts["tip"] = pts.apply(lambda r: f"#{int(r['rank'])} {r['name']} · {r['km']:.0f} km", axis=1)
    start = pd.DataFrame([{"latitude": search["lat"], "longitude": search["lon"], "tip": "Starting point"}])
    layers = [
        pdk.Layer("ScatterplotLayer", pts, get_position="[longitude, latitude]", get_fill_color=[20, 116, 107, 230],
                  get_line_color=[255, 255, 255], stroked=True, line_width_min_pixels=1,
                  radius_min_pixels=5, radius_max_pixels=9, get_radius=400, pickable=True),
        pdk.Layer("ScatterplotLayer", start, get_position="[longitude, latitude]", get_fill_color=[30, 30, 30, 255],
                  get_line_color=[255, 255, 255], stroked=True, line_width_min_pixels=2,
                  radius_min_pixels=8, radius_max_pixels=10, get_radius=600, pickable=True),
    ]
    zoom = 9 if search["radius_km"] <= 30 else 8 if search["radius_km"] <= 80 else 6.5 if search["radius_km"] <= 160 else 5.5
    st.pydeck_chart(pdk.Deck(layers=layers, tooltip={"text": "{tip}"},
                             initial_view_state=pdk.ViewState(latitude=search["lat"], longitude=search["lon"], zoom=zoom)),
                    height=height)
    st.caption("Teal dots: facilities that claim the service. Black dot: your starting point. Hover a dot for its name.")


def results_header(search, labels, ranked, unplaced):
    safety_block()
    st.subheader(f"{labels[search['service']]} near {search['label']}")
    st.warning(PLACEHOLDER_NOTE, icon="⚠️")
    n_dir = int(ranked["directory_only"].fillna(False).astype(bool).sum()) if "directory_only" in ranked else 0
    notes = [f"{len(ranked) - n_dir} facilities in this dataset say they offer this within {search['radius_km']} km (straight line)."]
    if n_dir:
        notes.append(f"{n_dir} more {'is' if n_dir == 1 else 'are'} {DIRECTORY_LABEL} without the facility text saying so "
                     "themselves (directory evidence only, not verified, no information on current stock).")
    if search.get("approx"):
        notes.append(search["approx"])
    notes.append("The dataset covers about 10,000 facilities, so real facilities nearby may be missing.")
    st.caption(" ".join(notes))


# --------------------------------------------------------------------------
# Patient view
# --------------------------------------------------------------------------
def patient_card(r, profile, key):
    with st.container(border=True):
        top = st.columns([4, 1])
        top[0].markdown(f"**{int(r['rank'])}. {r['name']}**  \n"
                        f"{clean(r['level_kind']) or clean(r['facility_type']) or 'Facility'} · "
                        f"{clean(r['district']) or 'district unknown'}, {str(r['state']).title()}")
        top[1].metric("Straight line", f"{r['km']:.0f} km")

        quote = clean(r["supporting_quote"])
        field = FIELD_PLAIN.get(r["source_field"], f"its {r['source_field']} field")
        directory_only = bool(r.get("directory_only"))
        if directory_only:
            st.markdown(f"**Blood bank: {DIRECTORY_LABEL}** (eRaktKosh). The facility's own text does not mention "
                        "one. This is a directory listing only: it is not verified and says nothing about "
                        "blood stock or whether the bank is working today.")
        else:
            st.markdown(f"**What it says:** it offers **{r['service_label'].lower()}**.")
        if quote and not directory_only:
            st.markdown(f"> “{quote}”\n>\n> from {field} · {source_link(r)}")
        c1, c2 = st.columns(2)
        c1.markdown(f"**How sure are we?**  \n{'Directory listing only: the facility itself does not say this.' if directory_only else STRENGTH_PLAIN.get(r['claim_strength'], 'Unknown.')}")
        c2.markdown(f"**Has anyone checked?**  \n{VERIFY_PLAIN.get(r['verification_status'], 'Not checked.')}")
        if clean(r["plausibility_flag"]):
            st.markdown(f"⚠️ **Treat with care:** {r['plausibility_flag']}.")
        for note in resolved_notes(r):
            st.markdown(note)

        phone = clean(r["contact_phone"])
        if phone:
            st.markdown(f"📞 **Call before you go:** [{phone}](tel:{phone}) (number not checked)")
        else:
            st.markdown("📞 No phone number in the dataset.")

        scheme = profile.get("insurance")
        if scheme == "PM-JAY (Ayushman Bharat)":
            st.markdown("🪪 It says it accepts PM-JAY (its own claim, not checked against the official list)."
                        if r.get("pmjay_claimed") is True else "🪪 PM-JAY: unknown for this facility.")
        elif scheme == "CGHS":
            st.markdown("🪪 It says it is CGHS empanelled (its own claim, not checked)."
                        if r.get("cghs_claimed") is True else "🪪 CGHS: unknown for this facility.")
        elif scheme and scheme != "Prefer not to say":
            st.markdown(f"🪪 {scheme}: unknown for this facility.")

        with st.expander("Why this hospital?"):
            st.markdown(r["rank_reason"])
            st.markdown("**What we don't know**\n" + "\n".join(f"- {u}" for u in unknowns(r)))
            if r["facility_existence_status"] == "externally_supported":
                st.markdown(f"An outside source ({r['facility_existence_source']}) lists a facility with this "
                            "name here. That supports that it exists, not that it offers this service.")
            if clean(r["merged_records"]) and int(r["merged_records"]) > 1:
                st.markdown(f"This entry combines {int(r['merged_records'])} records that look like the same centre.")
            st.caption(PLACEHOLDER_NOTE)
        feedback_buttons(r, "patients", key)


def tab_patients(claims, districts):
    st.markdown("Find facilities that say they offer a service near you. Always phone first.")
    with st.expander("About you (optional, kept only for this visit and never saved)"):
        c = st.columns(4)
        profile = {
            "age_band": c[0].selectbox("Age band", ["Prefer not to say", "0-4", "5-17", "18-39", "40-59", "60+"], key="p_age"),
            "conditions": c[1].multiselect("Conditions", ["Pregnancy", "Diabetes", "Heart disease", "Kidney disease",
                                                          "Cancer", "Disability", "Other"], key="p_cond"),
            "insurance": c[2].selectbox("Insurance scheme", ["Prefer not to say", "None", "PM-JAY (Ayushman Bharat)",
                                                             "CGHS", "ESI", "State scheme", "Private"], key="p_ins"),
            "income_band": c[3].selectbox("Household income band", ["Prefer not to say", "Low", "Middle", "High"], key="p_inc"),
        }
        st.caption("These answers stay in this browser session. They are not written to any table. "
                   "The placeholder ranking does not use them yet; the insurance answer only adds a note to each card.")
    how = st.radio("How do you want to search?", ["Describe symptoms", "I know the service I need"],
                   horizontal=True, key="p_how")
    if how == "Describe symptoms":
        sx = symptom_form("p", location_input, districts)
        if sx:
            st.divider()
            show_result(run_symptom_search(claims, sx), sx, results_map, "p")
        return
    search, labels = search_form("p", claims, districts)
    if not search:
        return
    ranked, unplaced = run_search(claims, search, profile)
    st.divider()
    results_header(search, labels, ranked, unplaced)
    if ranked.empty:
        st.info("No facility in this dataset says it offers this within that distance. "
                "That does not mean there is none. Try a wider distance, or call 108.")
    else:
        left, right = st.columns([3, 2])
        with right:
            results_map(ranked, search)
        with left:
            n = st.session_state.get("p_show", 5)
            for _, r in ranked.head(n).iterrows():
                patient_card(r, profile, f"p_{r['facility_id']}")
            if len(ranked) > n and st.button(f"Show more ({len(ranked) - n} left)", key="p_more"):
                st.session_state["p_show"] = n + 5
                st.rerun()
    unplaced_block(unplaced)


def unplaced_block(unplaced):
    if unplaced is None or unplaced.empty:
        return
    with st.expander(f"{len(unplaced)} more in this district with an unreliable map location"):
        st.caption("Their address says this district, but their map pin looks wrong, so no distance is shown.")
        st.dataframe(unplaced[["name", "district", "care_level", "claim_strength", "contact_phone",
                               "location_quality_flag"]].rename(columns={
                                   "name": "Facility", "district": "District", "care_level": "Care level",
                                   "claim_strength": "Claim strength", "contact_phone": "Phone",
                                   "location_quality_flag": "Location"}),
                     hide_index=True, use_container_width=True)


# --------------------------------------------------------------------------
# EMR view
# --------------------------------------------------------------------------
def tab_emrs(claims, districts):
    st.markdown("Compact list for emergency medical responders and dispatchers. "
                "Destination decisions stay with your protocol and medical direction.")
    with st.expander("Crew profile (optional, kept only for this session)"):
        c = st.columns(3)
        profile = {
            "role": c[0].selectbox("Role", ["Ambulance crew", "Dispatcher", "Hospital staff", "Other"], key="e_role"),
            "vehicle": c[1].selectbox("Vehicle", ["Not applicable", "Basic life support", "Advanced life support",
                                                  "Patient transport"], key="e_veh"),
            "min_level_only": c[2].checkbox("Hide facilities below the usual care level", key="e_min"),
        }
    how = st.radio("How do you want to search?", ["Describe symptoms", "I know the service I need"],
                   horizontal=True, key="e_how")
    if how == "Describe symptoms":
        sx = symptom_form("e", location_input, districts)
        if sx:
            st.divider()
            show_result(run_symptom_search(claims, sx), sx, results_map, "e")
        return
    search, labels = search_form("e", claims, districts, default_radius=80)
    if not search:
        return
    ranked, unplaced = run_search(claims, search, profile)
    if profile["min_level_only"]:
        ranked = ranked[ranked["level_meets"]]
    st.divider()
    results_header(search, labels, ranked, unplaced)
    if ranked.empty:
        st.info("No facility in this dataset claims this service within that distance.")
        unplaced_block(unplaced)
        return
    table = pd.DataFrame({
        "#": ranked["rank"],
        "Facility": ranked["name"],
        "km (straight)": ranked["km"].round(0),
        "Care level": ranked["care_level"],
        f"Meets level {MIN_LEVEL.get(search['service'], 3)}+": ranked["level_meets"].map({True: "yes", False: "no"}),
        "Claim": ranked["claim_strength"],
        "Checked": ranked["verification_status"].map({"inferred": "no (self-reported)",
                                                      "externally_supported": "outside support",
                                                      "verified": "verified"}).fillna("no"),
        "Open now": "unknown",
        "24x7 claim": ranked["hours_claim"].map({"24x7": "claims 24x7"}).fillna("unknown"),
        "Phone (unchecked)": ranked["contact_phone"],
        "District": ranked["district"],
        "Evidence": ranked["supporting_quote"],
    })
    left, right = st.columns([3, 2])
    with left:
        st.dataframe(table.head(50), hide_index=True, use_container_width=True, height=380)
    with right:
        results_map(ranked.head(50), search)
    st.markdown("**Details and report**")
    top = ranked.head(50)
    pick = st.selectbox("Facility", top["facility_id"].tolist(), key="e_pick",
                        format_func=lambda i: "{rank}. {name}".format(**top[top["facility_id"] == i].iloc[0]))
    r = top[top["facility_id"] == pick].iloc[0]
    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        c1.markdown((f"**No claim by the facility.** Blood bank {DIRECTORY_LABEL} (eRaktKosh): directory evidence only, "
                     "not verified, no information on current stock.  \n" if bool(r.get("directory_only")) else "") +
                    f"**Claim:** {r['service_label']} ({r['claim_strength']}, {r['claim_basis']})  \n"
                    f"**Quote:** “{clean(r['supporting_quote']) or 'none'}” ({r['source_field']})  \n"
                    f"**Source:** {source_link(r)}")
        c2.markdown(f"**Verification:** {r['verification_status']}"
                    f"{' via ' + r['verification_source'] if clean(r['verification_source']) else ''}  \n"
                    f"**Facility exists:** {r['facility_existence_status']}  \n"
                    f"**Care level:** {clean(r['care_level']) or 'unknown'} ({r['level_confidence']} confidence)  \n"
                    f"**Flag:** {clean(r['plausibility_flag']) or 'none'}")
        c3.markdown(f"**Phone:** {clean(r['contact_phone']) or 'none'} (unchecked)  \n"
                    f"**Availability now:** unknown  \n**Hours:** {r['hours_claim']}  \n"
                    f"**Position:** {r['latitude']:.5f}, {r['longitude']:.5f}")
        for note in resolved_notes(r):
            st.markdown(note)
        feedback_buttons(r, "emrs", f"e_{r['facility_id']}")
    unplaced_block(unplaced)


# --------------------------------------------------------------------------
# Policy view
# --------------------------------------------------------------------------
# One teal ramp for the whole tab, ordered dark -> pale. Dark always means "close to a
# listed facility / well served", pale means "far". (Name kept from the first version.)
PALETTES = {      # dark -> pale; the pale end is still clearly tinted, never white
    "Indigo": [(38, 34, 98), (58, 56, 138), (84, 86, 168), (112, 118, 192),
               (142, 150, 210), (170, 178, 224), (194, 200, 235)],
    "Plum": [(74, 20, 70), (110, 36, 94), (146, 60, 114), (178, 92, 134),
             (204, 128, 152), (224, 162, 168), (238, 192, 184)],
    "Ocean": [(8, 48, 107), (8, 76, 148), (28, 104, 174), (54, 132, 192),
              (88, 158, 208), (126, 182, 220), (162, 204, 230)],
    "Teal": [(11, 79, 74), (18, 108, 100), (34, 140, 128), (66, 166, 153),
             (104, 188, 176), (142, 206, 196), (176, 222, 213)],
}
BLUE_RAMP = PALETTES["Indigo"]          # the active ramp; tab_policy swaps it when the user picks another
# Stripe colour per palette: a hue the ramp does not contain, so stripes never read as shading.
ACCENTS = {"Indigo": (245, 166, 35), "Ocean": (240, 110, 80), "Teal": (236, 112, 84), "Plum": (24, 170, 170)}
ACCENT = ACCENTS["Indigo"]
# Facility dots are dark with a thin pale ring. (White dots merged into white patches where facilities cluster.)
FAC_FILL, FAC_LINE = [28, 28, 38, 235], [*BLUE_RAMP[-1], 255]
GRADIENT_MAX_KM = 200.0
GRADIENT_POWER = 0.7     # 1 = even fade; lower = faster fade near the facility


def _merc(lat):
    return np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))


def _unit(lat, lon):
    la, lo = np.radians(lat), np.radians(lon)
    return np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])


INDIA_BOUNDS = (67.9, 6.4, 97.6, 37.3)       # west, south, east, north


@st.cache_data(ttl=3600, show_spinner="Drawing distance shading...", max_entries=40)
def distance_image(service: str, tier: str, palette: str = "Indigo", flat: bool = False,
                   bounds: tuple = INDIA_BOUNDS, W: int = 1000):
    """PNG of straight-line distance to the nearest catchment facility, dark near -> pale far.

    Drawn on a Web-Mercator grid (about 3 km per pixel) so it lines up with the map, and
    masked to the catchment polygons, which are already clipped to India.
    Returns (data_url, [west, south, east, north]).
    """
    from PIL import Image, ImageDraw
    from scipy.spatial import cKDTree

    cat = load_catchments(service, tier)
    west, south, east, north = bounds
    m_s, m_n = _merc(south), _merc(north)
    H = int(round(W * (m_n - m_s) / np.radians(east - west)))
    lons = west + (np.arange(W) + 0.5) / W * (east - west)
    merc = m_n - (np.arange(H) + 0.5) / H * (m_n - m_s)
    lats = np.degrees(2 * np.arctan(np.exp(merc)) - np.pi / 2)
    glon, glat = np.meshgrid(lons, lats)

    pts = cat.drop_duplicates("facility_id")
    tree = cKDTree(_unit(pts["facility_latitude"].to_numpy(float), pts["facility_longitude"].to_numpy(float)))
    chord, _ = tree.query(_unit(glat.ravel(), glon.ravel()))
    km = (2 * 6371.0088 * np.arcsin(np.clip(chord / 2, 0, 1))).reshape(H, W)

    t = np.clip(km / GRADIENT_MAX_KM, 0, 1) ** GRADIENT_POWER
    stops = np.array(PALETTES[palette], dtype=float)
    pos = t * (len(stops) - 1)
    lo = np.floor(pos).astype(int).clip(0, len(stops) - 2)
    frac = (pos - lo)[..., None]
    rgb = stops[lo] * (1 - frac) + stops[lo + 1] * frac
    if flat:                                            # one even tint, used under the district circles
        rgb = np.broadcast_to(stops[-1], rgb.shape)

    mask = Image.new("L", (W, H), 0)
    draw = ImageDraw.Draw(mask)
    for ring in cat["polygon"]:
        a = np.asarray(ring, dtype=float)
        x = (a[:, 0] - west) / (east - west) * W
        y = (m_n - _merc(a[:, 1])) / (m_n - m_s) * H
        draw.polygon(list(zip(x.tolist(), y.tolist())), fill=255, outline=255)
    rgba = np.dstack([rgb, np.asarray(mask, dtype=float)]).round().astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(), [west, south, east, north]


@st.cache_data(ttl=3600)
def load_critical_blocks(service: str) -> pd.DataFrame:
    """Critical 5 km blocks: the service's own critical zones OR the 150 km life-threatening rule."""
    parts = []
    for q, p in ((f"SELECT latitude, longitude FROM {GOLD}.critical_zones WHERE service = :s AND zone_type = 'critical'",
                  {"s": service}),
                 (f"SELECT latitude, longitude FROM {GOLD}.critical_zones_emergency_150km", {})):
        try:
            parts.append(query(q, p))
        except Exception:      # table not built yet
            pass
    if not parts:
        return pd.DataFrame(columns=["latitude", "longitude"])
    return pd.concat(parts, ignore_index=True).apply(pd.to_numeric, errors="coerce").dropna()


@st.cache_data(ttl=3600, show_spinner="Drawing critical-area stripes...", max_entries=40)
def stripes_image(service: str, palette: str = "Indigo", bounds: tuple = INDIA_BOUNDS, W: int = 1600):
    """Transparent PNG with diagonal stripes over critical 5 km blocks. Returns (data_url, bounds) or None."""
    from PIL import Image, ImageDraw, ImageFilter

    blocks = load_critical_blocks(service)
    if blocks.empty:
        return None
    west, south, east, north = bounds
    pad = 0.1                                           # keep blocks just outside the frame so edges fill
    blocks = blocks[blocks["longitude"].between(west - pad, east + pad) & blocks["latitude"].between(south - pad, north + pad)]
    if blocks.empty:
        return None
    m_s, m_n = _merc(south), _merc(north)
    H = int(round(W * (m_n - m_s) / np.radians(east - west)))
    x = ((blocks["longitude"].to_numpy(float) - west) / (east - west) * W).round().astype(int)
    y = ((m_n - _merc(blocks["latitude"].to_numpy(float))) / (m_n - m_s) * H).round().astype(int)
    ok = (x >= 0) & (x < W) & (y >= 0) & (y < H)
    m = np.zeros((H, W), dtype="uint8")
    m[y[ok], x[ok]] = 255
    # each block is 5 km: grow each point to a bit more than block size so neighbours join up
    km_per_px = (east - west) * 111.32 * np.cos(np.radians((south + north) / 2)) / W
    k = int(np.ceil(7.0 / km_per_px)) | 1               # odd filter size
    zone = Image.fromarray(m, "L").filter(ImageFilter.MaxFilter(max(k, 3)))
    if k <= 5:
        zone = zone.filter(ImageFilter.MinFilter(3))
    zone = np.asarray(zone) > 0
    # never stripe outside India: clip to the catchment polygons, which are already clipped to the outline
    try:
        inside = Image.new("L", (W, H), 0)
        draw = ImageDraw.Draw(inside)
        for ring in load_catchments(service, "claimed")["polygon"]:
            a = np.asarray(ring, dtype=float)
            if a[:, 0].max() < west or a[:, 0].min() > east or a[:, 1].max() < south or a[:, 1].min() > north:
                continue
            px = (a[:, 0] - west) / (east - west) * W
            py = (m_n - _merc(a[:, 1])) / (m_n - m_s) * H
            draw.polygon(list(zip(px.tolist(), py.tolist())), fill=255, outline=255)
        zone &= np.asarray(inside) > 0
    except Exception:
        pass
    yy, xx = np.mgrid[0:H, 0:W]
    stripe = ((xx + yy) % 20) < 7                       # diagonal, about 40 km apart
    rgba = np.zeros((H, W, 4), dtype="uint8")
    rgba[..., :3] = ACCENTS[palette]
    rgba[..., 3] = np.where(zone & stripe, 240, 0)
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(), [west, south, east, north]


def stripe_chip():
    dark = "rgb%s" % (ACCENT,)
    pale = "rgb%s" % (BLUE_RAMP[3],)
    return ("<span style='display:inline-flex;align-items:center'><span style='width:22px;height:12px;border-radius:2px;"
            f"background:repeating-linear-gradient(135deg, {dark} 0 3px, {pale} 3px 7px);display:inline-block;"
            "margin-right:4px'></span>striped = critical area</span>")


def gradient_legend(extra=""):
    css = ", ".join(f"rgb{c}" for c in BLUE_RAMP)
    st.markdown(
        "<div style='font-size:0.8rem;display:flex;align-items:center;flex-wrap:wrap;gap:10px'>"
        "<span>Straight-line distance from the catchment's facility:</span>"
        "<span>at the facility</span>"
        f"<span style='display:inline-block;width:220px;height:12px;border-radius:3px;background:linear-gradient(90deg, {css})'></span>"
        f"<span>{GRADIENT_MAX_KM:.0f} km or more</span>"
        f"<span style='color:#666'>(the middle of the bar is about {GRADIENT_MAX_KM * 0.5 ** (1 / GRADIENT_POWER):.0f} km)</span>"
        f"{extra}</div>", unsafe_allow_html=True)
KM_BREAKS = [10, 20, 30, 45, 60, 100]
KM_LABELS = ["under 10", "10-20", "20-30", "30-45", "45-60", "60-100", "over 100"]


def km_colour(km):
    if km is None or np.isnan(km):
        return [200, 200, 200, 160]
    return [*BLUE_RAMP[int(np.searchsorted(KM_BREAKS, km, side="right"))], 225]


AREA_BREAKS = [25, 150, 750, 3000, 10000, 30000]
AREA_LABELS = ["under 25", "25-150", "150-750", "750-3,000", "3,000-10,000", "10,000-30,000", "over 30,000"]
PEOPLE_BREAKS = [1e5, 2.5e5, 5e5, 1e6, 2.5e6, 5e6]
PEOPLE_LABELS = ["under 0.1M", "0.1-0.25M", "0.25-0.5M", "0.5-1M", "1-2.5M", "2.5-5M", "over 5M"]


def ramp_colour(v, breaks, alpha=225, reverse=False):
    if v is None or np.isnan(v):
        return [215, 215, 215, 140]
    ramp = BLUE_RAMP[::-1] if reverse else BLUE_RAMP
    return [*ramp[int(np.searchsorted(breaks, v, side="right"))], alpha]


def ramp_legend(title, labels_, extra="", reverse=False):
    chips = "".join(
        f"<span style='display:inline-flex;align-items:center;margin-right:10px'>"
        f"<span style='width:12px;height:12px;border-radius:2px;background:rgb{c};display:inline-block;margin-right:4px'></span>{lab}</span>"
        for c, lab in zip(BLUE_RAMP[::-1] if reverse else BLUE_RAMP, labels_))
    st.markdown(f"<div style='font-size:0.8rem'>{title}: {chips}{extra}</div>", unsafe_allow_html=True)


def legend(extra=""):
    chips = "".join(
        f"<span style='display:inline-flex;align-items:center;margin-right:10px'>"
        f"<span style='width:12px;height:12px;border-radius:2px;background:rgb{c};display:inline-block;margin-right:4px'></span>{lab}</span>"
        for c, lab in zip(BLUE_RAMP, KM_LABELS))
    st.markdown(f"<div style='font-size:0.8rem'>Straight-line km to the nearest facility claiming the service: {chips}{extra}</div>",
                unsafe_allow_html=True)


def tab_policy(claims, facilities, districts, access):
    global BLUE_RAMP, FAC_LINE, ACCENT
    st.markdown("Where are people far from a facility that claims a service, and how much of that picture rests on "
                "weak evidence? Distances are straight-line and only to the roughly 10,000 facilities in this dataset.")
    with st.expander("Your role (optional, kept only for this session)"):
        c = st.columns(2)
        c[0].selectbox("Role", ["State health department", "District administration", "NGO / funder",
                                "Researcher", "Other"], key="g_role")
        c[1].text_input("Organisation (optional)", key="g_org")
    services = claims[["service", "service_label"]].drop_duplicates().sort_values("service_label")
    labels = dict(zip(services["service"], services["service_label"]))
    c1, c2, c3 = st.columns([3, 3, 1])
    service = c1.selectbox("Service", list(labels), format_func=labels.get, key="g_service")
    tier = c2.radio("Evidence tier", ["Any claim", "Better-evidenced claims only"], horizontal=True, key="g_tier",
                    help="Better-evidenced = direct wording, no plausibility flag, a usable location, and the facility is "
                         "matched in an outside source. Still self-reported, not verified.")
    palette = c3.selectbox("Map colours", list(PALETTES), key="g_palette")
    BLUE_RAMP = PALETTES[palette]
    FAC_LINE = [*BLUE_RAMP[-1], 255]
    ACCENT = ACCENTS[palette]
    km_col = "pop_weighted_km_claimed" if tier == "Any claim" else "pop_weighted_km_better"
    pct_col = "pct_pop_within_30km_claimed" if tier == "Any claim" else "pct_pop_within_30km_better"
    svc_fac = claims[(claims["service"] == service) & (claims["location_quality_flag"] == "good")]
    if tier != "Any claim":
        svc_fac = svc_fac[svc_fac["claim_strength"].isin(["strong", "moderate"]) & svc_fac["plausibility_flag"].isna()
                          & svc_fac["negating_quote"].isna()
                          & ((svc_fac["facility_existence_status"] == "externally_supported")
                             | svc_fac["verification_status"].isin(["externally_supported", "verified"]))]

    st.subheader("National overview")
    if districts is None or access is None:
        st.info("District access tables are not available, so the map shows facility points only.")
        pts = svc_fac[["name", "latitude", "longitude"]].copy()
        pts["tip"] = pts["name"]
        st.pydeck_chart(pdk.Deck(
            layers=[pdk.Layer("ScatterplotLayer", pts, get_position="[longitude, latitude]",
                              get_fill_color=[20, 116, 107, 200], radius_min_pixels=2, get_radius=1500, pickable=True)],
            tooltip={"text": "{tip}"},
            initial_view_state=pdk.ViewState(latitude=22.5, longitude=80, zoom=3.8)), height=560)
        return

    d = districts.merge(access[access["service"] == service], on="district_id", how="left")
    d["km"] = d[km_col]
    d["colour"] = d["km"].apply(km_colour)
    d["radius"] = (np.sqrt(d["population_2020"].fillna(0).clip(lower=0)) * 14).clip(lower=6000)
    d["tip"] = d.apply(lambda r: (f"{r['district_name']}, {r['state_name']}\n"
                                  f"Average person is {r['km']:.0f} km from the nearest (straight line)\n"
                                  f"{r[pct_col]:.0f}% of people within 30 km\n"
                                  f"Population {r['population_2020'] / 1e6:.2f} million") if pd.notna(r["km"])
                       else f"{r['district_name']}, {r['state_name']}\nno figure", axis=1)
    fpts = svc_fac[["name", "latitude", "longitude"]].copy()
    fpts["tip"] = fpts["name"] + "\n(claims the service)"
    tier_key = "claimed" if tier == "Any claim" else "better_evidenced"
    cat = optional(lambda: load_catchments(service, tier_key))
    FAC_CHIP = ("<span style='display:inline-flex;align-items:center'><span style='width:8px;height:8px;border-radius:50%;"
                "background:rgb(28,28,38);border:1px solid #bbb;display:inline-block;margin-right:4px'></span>facility claiming the service</span>")
    v1, v2, v3 = st.columns([2, 3, 2])
    views = (["Catchments (Voronoi)"] if cat is not None else []) + ["District circles"]
    view = v1.radio("Map view", views, horizontal=True, key="g_view")
    shade = v2.radio("Shade catchments by", ["Distance from its facility", "Area", "People inside"], horizontal=True, key="g_shade",
                     disabled=view != "Catchments (Voronoi)")
    show_fac = v3.checkbox("Show facilities that claim the service", value=True, key="g_fac")
    show_crit = v3.checkbox("Stripe critical areas", value=True, key="g_crit")
    stripes = optional_value(lambda: stripes_image(service, palette)) if show_crit else None
    fac_layer = pdk.Layer("ScatterplotLayer", fpts, get_position="[longitude, latitude]",
                          get_fill_color=FAC_FILL, get_line_color=FAC_LINE, stroked=True, line_width_min_pixels=1,
                          radius_min_pixels=2, radius_max_pixels=6, get_radius=900, pickable=True)
    CRIT_CHIP = ("<span style='margin-left:10px'></span>" + stripe_chip()) if stripes is not None else ""
    if view == "Catchments (Voronoi)":
        c = cat.copy()
        c["tip"] = c.apply(lambda r: (f"Nearest listed facility: {r['facility_name']}\n"
                                      f"Catchment area {r['area_km2']:,.0f} km²\n"
                                      + (f"About {r['population'] / 1e6:.2f} million people inside"
                                         if pd.notna(r["population"]) else "People inside: unknown")), axis=1)
        if shade == "Distance from its facility":
            img, bounds = distance_image(service, tier_key, palette)
            c["colour"] = [[0, 0, 0, 0]] * len(c)
            layers = [pdk.Layer("BitmapLayer", image=pdk.types.String(img), bounds=bounds, opacity=1.0),
                      pdk.Layer("PolygonLayer", c[["polygon", "colour", "tip"]], get_polygon="polygon",
                                get_fill_color="colour", get_line_color=[*BLUE_RAMP[0], 110],
                                line_width_min_pixels=0.8, stroked=True, filled=True, pickable=True)]
        else:
            if shade == "Area":
                c["colour"] = c["area_km2"].apply(lambda v: ramp_colour(v, AREA_BREAKS))
            else:
                c["colour"] = c["population"].apply(lambda v: ramp_colour(v, PEOPLE_BREAKS, reverse=True))
            layers = [pdk.Layer("PolygonLayer", c[["polygon", "colour", "tip"]], get_polygon="polygon",
                                get_fill_color="colour", get_line_color=[255, 255, 255, 200], line_width_min_pixels=0.6,
                                stroked=True, filled=True, pickable=True)]
        if stripes is not None:
            layers.append(pdk.Layer("BitmapLayer", image=pdk.types.String(stripes[0]), bounds=stripes[1], opacity=1.0))
        if show_fac:
            layers.append(fac_layer)
        st.pydeck_chart(pdk.Deck(layers=layers, tooltip={"text": "{tip}"},
                                 initial_view_state=pdk.ViewState(latitude=22.5, longitude=80.5, zoom=3.9)), height=620)
        if shade == "Distance from its facility":
            gradient_legend(FAC_CHIP + CRIT_CHIP)
        elif shade == "Area":
            ramp_legend("Catchment area in km²", AREA_LABELS, FAC_CHIP + CRIT_CHIP)
        else:
            ramp_legend("People living inside the catchment", PEOPLE_LABELS, FAC_CHIP + CRIT_CHIP, reverse=True)
        st.caption(f"{len(c['facility_id'].unique()):,} catchments. Each shape is the area closer, in a straight line, to one "
                   "listed facility than to any other that claims this service. Colour is darkest at the facility and "
                   "fades as you move away from it, so pale areas are far from any listed facility. Big shapes mean one "
                   "facility is the nearest option for a wide area. Facilities with a suspect location are left out. This is not travel "
                   "time or a referral area, and it only knows the facilities in this dataset. Switch the evidence tier "
                   "above to see how the picture changes when weak claims are removed."
                   + (" Striped areas are critical for either of two reasons. (1) For this service: a 5 km block with at "
                      "least 5,000 people, more than 30 km from the nearest facility claiming it, in a district in the most "
                      "deprived third on NFHS-5 (defined for 8 of the 14 services). (2) For any service: more than 150 km "
                      "from the nearest hospital that claims emergency or trauma care plus an ICU, whatever the population."
                      if stripes is not None else ""))
    else:
        layers = []
        if cat is not None:
            base = optional_value(lambda: distance_image(service, tier_key, palette, True))
            if base is not None:
                layers.append(pdk.Layer("BitmapLayer", image=pdk.types.String(base[0]), bounds=base[1], opacity=1.0))
        layers.append(pdk.Layer("ScatterplotLayer", d[["centre_lat", "centre_lon", "colour", "radius", "tip"]],
                                get_position="[centre_lon, centre_lat]", get_fill_color="colour", get_radius="radius",
                                get_line_color=[*BLUE_RAMP[0], 160], stroked=True, line_width_min_pixels=1, pickable=True))
        if stripes is not None:
            layers.append(pdk.Layer("BitmapLayer", image=pdk.types.String(stripes[0]), bounds=stripes[1], opacity=1.0))
        if show_fac:
            layers.append(fac_layer)
        st.pydeck_chart(pdk.Deck(layers=layers, tooltip={"text": "{tip}"},
                                 initial_view_state=pdk.ViewState(latitude=22.5, longitude=80.5, zoom=3.9)), height=620)
        legend(FAC_CHIP + CRIT_CHIP)
        st.caption("One circle per district, placed at its centre and sized by population. Darker means the average "
                   "resident is closer to the nearest facility claiming the service; pale means far. Hover for figures.")

    m = st.columns(4)
    far = d[d["km"] > 30]
    m[0].metric("Facilities claiming it (usable location)", f"{len(svc_fac):,}")
    m[1].metric("Districts where the average person is over 30 km away", f"{len(far)} of {len(d)}")
    m[2].metric("People living in those districts", f"{far['population_2020'].sum() / 1e6:,.0f} million")
    m[3].metric("Verified by an official source", "0")
    with st.expander("Districts furthest from this service (table view)"):
        t = d.sort_values("km", ascending=False)[["district_name", "state_name", "population_2020", "km", pct_col,
                                                  "facilities_located", "deprivation_tercile"]].head(100)
        t.columns = ["District", "State", "Population (2020)", "Avg straight-line km", "% people within 30 km",
                     "Facilities in dataset", "Deprivation (NFHS)"]
        st.dataframe(t.round(1), hide_index=True, use_container_width=True)

    st.subheader("District zoom")
    c1, c2 = st.columns(2)
    states = sorted(d["state_name"].dropna().unique())
    state = c1.selectbox("State", states, key="g_state", index=states.index("Bihār") if "Bihār" in states else 0)
    sub = d[d["state_name"] == state].sort_values("district_name")
    name = c2.selectbox("District", sub["district_name"].tolist(), key="g_district")
    row = sub[sub["district_name"] == name].iloc[0]
    k = st.columns(5)
    k[0].metric("Population (2020)", f"{row['population_2020'] / 1e6:.2f} M" if pd.notna(row["population_2020"]) else "unknown")
    k[1].metric("Facilities in dataset", f"{int(row['facilities_located'])}" if pd.notna(row["facilities_located"]) else "0")
    k[2].metric("Avg straight-line km", f"{row['km']:.0f}" if pd.notna(row["km"]) else "unknown")
    k[3].metric("People within 30 km", f"{row[pct_col]:.0f}%" if pd.notna(row[pct_col]) else "unknown")
    k[4].metric("Deprivation (NFHS)", clean(row["deprivation_tercile"]) or "unknown")

    blocks = optional(lambda: load_blocks(row["district_id"], service))
    zones = optional(lambda: load_zones(row["district_id"], service))
    near = facilities.copy()
    near["km0"] = haversine_km(row["centre_lat"], row["centre_lon"], near["latitude"].to_numpy(float),
                               near["longitude"].to_numpy(float))
    span = max(60.0, float(np.sqrt(row["area_km2"])) * 1.2) if pd.notna(row["area_km2"]) else 80.0
    claim_ids = set(svc_fac["facility_id"])
    near = near[near["km0"] <= span].copy()
    near["claims"] = near["facility_id"].isin(claim_ids)
    near["colour"] = near["claims"].map({True: [28, 28, 38, 240], False: [150, 150, 150, 220]})
    near["tip"] = near.apply(lambda r: f"{r['name']}\n{'claims ' + labels[service] if r['claims'] else 'does not claim it'}"
                                       f"\ncare level {clean(r['care_level']) or 'unknown'}", axis=1)
    layers = []
    # the district map gets its own sharper images, drawn just for the area in view
    dlat = span * 2.2 / 111.0
    dlon = dlat / np.cos(np.radians(float(row["centre_lat"])))
    zb = (round(float(row["centre_lon"]) - dlon, 3), round(float(row["centre_lat"]) - dlat, 3),
          round(float(row["centre_lon"]) + dlon, 3), round(float(row["centre_lat"]) + dlat, 3))
    shaded = optional_value(lambda: distance_image(service, tier_key, palette, False, zb, 900)) if cat is not None else None
    zstripes = optional_value(lambda: stripes_image(service, palette, zb, 900)) if show_crit else None
    if shaded is not None:
        layers.append(pdk.Layer("BitmapLayer", image=pdk.types.String(shaded[0]), bounds=shaded[1], opacity=1.0))
    if zstripes is not None:
        layers.append(pdk.Layer("BitmapLayer", image=pdk.types.String(zstripes[0]), bounds=zstripes[1], opacity=1.0))
    if blocks is not None:
        bcol = "km_claimed" if tier == "Any claim" else "km_better_evidenced"
        blocks = blocks.copy()
        # with the shading underneath, blocks are invisible hover targets; without it they carry the colour
        blocks["colour"] = [[0, 0, 0, 0]] * len(blocks) if shaded is not None else blocks[bcol].apply(km_colour)
        blocks["tip"] = blocks.apply(lambda r: f"{r[bcol]:.0f} km to the nearest (straight line)\n"
                                               f"about {r['population']:,.0f} people in this 5 km block", axis=1)
        layers.append(pdk.Layer("ScatterplotLayer", blocks[["latitude", "longitude", "colour", "tip"]],
                                get_position="[longitude, latitude]", get_fill_color="colour", get_radius=2600,
                                pickable=True))
    if cat is not None:
        ck = haversine_km(row["centre_lat"], row["centre_lon"], cat["facility_latitude"].to_numpy(float),
                          cat["facility_longitude"].to_numpy(float))
        layers.append(pdk.Layer("PolygonLayer", cat[ck <= span * 2.5][["polygon"]], get_polygon="polygon",
                                filled=False, stroked=True, get_line_color=[*BLUE_RAMP[0], 200],
                                line_width_min_pixels=1.2, pickable=False))
    layers.append(pdk.Layer("ScatterplotLayer", near[["latitude", "longitude", "colour", "tip"]],
                            get_position="[longitude, latitude]", get_fill_color="colour", get_radius=700,
                            get_line_color=FAC_LINE, stroked=True, line_width_min_pixels=1.5,
                            radius_min_pixels=4, radius_max_pixels=8, pickable=True))
    zoom = 9.2 if span <= 60 else 8.4 if span <= 110 else 7.4
    st.pydeck_chart(pdk.Deck(layers=layers, tooltip={"text": "{tip}"},
                             initial_view_state=pdk.ViewState(latitude=float(row["centre_lat"]),
                                                              longitude=float(row["centre_lon"]), zoom=zoom)), height=560)
    if blocks is None:
        st.caption("No distance blocks for this district and service, so the map shows facility points only.")
    zoom_extra = ("<span style='margin-left:10px'>dark dot = claims the service</span><span style='margin-left:10px'>grey dot = other facility</span>"
                  "<span style='margin-left:10px'>lines = catchment edges</span>" + CRIT_CHIP)
    if shaded is not None:
        gradient_legend(zoom_extra)
    else:
        legend(zoom_extra)
    st.caption("Same shading as the national map: darkest at a facility, fading with straight-line distance. Hover for the "
               "distance and population of each 5 km block. Stripes mark critical areas, as defined under the national map. "
               "A district with no facility in this dataset may still have facilities in reality.")


# --------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------
def main():
    st.title("Facility Finder (India)")
    st.caption("Datathon prototype. Every claim below is what a facility says about itself unless marked otherwise.")
    safety_block()
    try:
        claims = load_claims()
    except Exception as e:
        st.error(f"Could not read the service table ({type(e).__name__}: {e}).")
        st.stop()
    facilities = optional(load_facilities)
    if facilities is None:
        facilities = claims.drop_duplicates("facility_id")[["facility_id", "name", "facility_type", "district", "state",
                                                            "latitude", "longitude", "care_level", "level_kind"]].dropna(
            subset=["latitude", "longitude"])
    districts = optional(load_districts)
    access = optional(load_district_access)
    if not ensure_feedback_table():
        st.caption("Feedback table is not writable right now, so reports may not save.")

    t_policy, t_emr, t_patient = st.tabs(["Policy makers", "EMRs (emergency responders)", "Patients"])
    with t_policy:
        tab_policy(claims, facilities, districts, access)
    with t_emr:
        tab_emrs(claims, districts)
    with t_patient:
        tab_patients(claims, districts)


main()
