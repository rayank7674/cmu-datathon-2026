"""EMR view: dense destination support for crews and dispatchers.

Shows what the patient view leaves out: the screen score, the model's working list of
possibilities, the evidence tier of every candidate, how strong each statement is, how often
statements of that strength matched the source text in our hand check (with its sample size),
location and care-level confidence flags, and how stable the ranking is.
Every indicator is labelled for what it measures. None of them is a probability that a
service exists or is available, and nothing is independently verified.
"""
from __future__ import annotations

import os

import altair as alt
import pandas as pd
import streamlit as st

import ui_theme as ui
from symptom_ui import hospital_card, run_symptom_search, show_alerts, symptom_form

_REL_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recommender", "knowledge", "claim_reliability.csv")
ORDER = {"strong": 0, "moderate": 1, "weak": 2}
STRENGTH_TXT = {"strong": "2+ places", "moderate": "once", "weak": "label only"}
# from validation/RESULTS.md (300 patients x 60 redraws of the travel-time assumptions)
STABILITY = {"first": "92%", "top3": "99%", "n": "300 patients, 60 redraws each"}


@st.cache_data
def reliability() -> dict:
    try:
        d = pd.read_csv(_REL_CSV)
        return {r.claim_strength: (int(r.supported), int(r.checked)) for r in d.itertuples()}
    except Exception:
        return {"strong": (20, 20), "moderate": (16, 20), "weak": (7, 20)}


def _rel_txt(strength) -> str:
    r = reliability().get(strength)
    return f"{r[0]}/{r[1]}" if r else "n/a"


def _facility_fields(claims, ids) -> pd.DataFrame:
    c = claims[claims["facility_id"].isin(ids)]
    return c.groupby("facility_id").agg(
        says_24x7=("hours_claim", lambda x: "claims 24x7" if (x == "24x7").any() else "unknown"),
        beds=("capacity_beds", "first"), phone=("contact_phone", "first"), district=("district", "first"),
        location=("location_quality_flag", "first"), level_conf=("level_confidence", "first"),
        exists=("facility_existence_status", "first"))


def _kpi_strip(res):
    rel = reliability()
    tc = res.get("tier_counts") or {}
    n = len(res["ranked"]) if res["ranked"] is not None else 0
    screen = "RED" if res["is_red"] else ("See a doctor today" if res["disposition"] == "hospital" else "Not red")
    ui.kpis([
        (screen, f"Symptom screen, score {res['p_lt']:.0%} (red line 20%)",
         "Rule-based warning-sign score. Uncalibrated, not a diagnosis probability, not clinician-reviewed.", res["is_red"]),
        (f"{n}", f"Candidates ranked within {res['radius']:.0f} km",
         ("By evidence tier: " + ", ".join(f"T{k} {tc[k]}" for k in sorted(tc))) if tc else "Ordered nearest-suitable (non-emergency)."),
        (STABILITY["first"], "Ranking stability: first choice stays first",
         f"{STABILITY['n']} of travel-time assumptions; top three {STABILITY['top3']}. Consistency, not correctness."),
        (", ".join(f"{rel[k][0]}/{rel[k][1]}" for k in ("strong", "moderate", "weak") if k in rel),
         "Text-reading check by claim strength (2+ places, once, label only)",
         "Hand check, n = 60: how often our reading matched the source text. Not whether the service exists."),
    ])


def _differential(res):
    if res.get("unclear") or res["conditions"] is None:
        st.caption("Symptoms were not recognised: handled as an emergency of an unknown kind (emergency + ICU statements).")
        return
    c = res["conditions"].head(8)
    df = pd.DataFrame({"Possibility planned for": c["label"], "Model weight": c["p"].astype(float),
                       "Can be life-threatening": c["life_threatening"].map({True: "yes", False: "no"})})
    st.dataframe(df, hide_index=True, use_container_width=True, height=min(38 + 35 * len(df), 330),
                 column_config={"Model weight": st.column_config.ProgressColumn(
                     "Model weight", format="%.2f", min_value=0.0, max_value=1.0,
                     help="Naive Bayes over the listed symptoms with hand-set priors. Uncalibrated; a planning weight, not a diagnosis.")})


def _destinations(res, claims) -> pd.DataFrame:
    r = res["ranked"].head(40).copy()
    f = _facility_fields(claims, r["facility_id"])
    best = (claims[claims["facility_id"].isin(r["facility_id"])]
            .assign(o=lambda d: d["claim_strength"].map(ORDER))
            .sort_values("o").drop_duplicates(["facility_id", "service"]).set_index(["facility_id", "service"])["claim_strength"])
    rows = []
    for _, x in r.iterrows():
        fid = x["facility_id"]
        ff = f.loc[fid] if fid in f.index else None
        row = {"#": int(x["rank"]), "Facility": x["name"], "Min (est.)": round(float(x["minutes"])),
               "km (straight)": round(float(x["km"]))}
        if "tier" in r.columns and pd.notna(x.get("tier")):
            states, missing = list(x["states"]), list(x["missing"])
            weakest = max((best.get((fid, s)) for s in states if best.get((fid, s)) in ORDER), key=ORDER.get, default=None)
            row.update({"Evidence tier": f"T{int(x['tier'])} {ui.TIER_SHORT.get(int(x['tier']), '')}",
                        "Needed statements": f"{len(states)} of {len(states) + len(missing)}",
                        "Not stated": ", ".join(m.replace('_', ' ') for m in missing) or "none",
                        "Weakest statement": STRENGTH_TXT.get(weakest, "n/a"),
                        "Text check": _rel_txt(weakest)})
        elif "suitable" in r.columns:
            row["Fits likely cause"] = "yes" if bool(x["suitable"]) else f"no: {x.get('unsuitable_why')}"
        lvl = x.get("care_level")
        row.update({"Care level": None if pd.isna(lvl) else int(lvl),
                    "Level confidence": ff["level_conf"] if ff is not None else None,
                    "Location": ff["location"] if ff is not None else None,
                    "24x7": ff["says_24x7"] if ff is not None else "unknown",
                    "Beds (listed)": None if ff is None or pd.isna(ff["beds"]) else int(ff["beds"]),
                    "Outside source lists it": {"externally_supported": "yes"}.get(ff["exists"] if ff is not None else None, "no"),
                    "Phone (unchecked)": ff["phone"] if ff is not None else None,
                    "District": ff["district"] if ff is not None else None})
        rows.append(row)
    return pd.DataFrame(rows)


def _tier_chart(res):
    tc = res.get("tier_counts") or {}
    if not tc:
        return
    df = pd.DataFrame([{"Tier": f"T{k}", "Meaning": ui.TIER_SHORT[k], "Facilities": tc.get(k, 0)} for k in (1, 2, 3, 4, 5)])
    ch = alt.Chart(df).mark_bar().encode(
        y=alt.Y("Tier:N", title=None), x=alt.X("Facilities:Q", title="Facilities in range"),
        color=alt.Color("Tier:N", legend=None, scale=alt.Scale(domain=[f"T{k}" for k in (1, 2, 3, 4, 5)],
                                                             range=[ui.TIER_COLOUR[k] for k in (1, 2, 3, 4, 5)])),
        tooltip=["Tier", "Meaning", "Facilities"])
    tx = alt.Chart(df).mark_text(align="left", dx=3, fontSize=11, color=ui.ink()).encode(y="Tier:N", x="Facilities:Q", text="Facilities:Q")
    st.altair_chart(alt.layer(ch, tx).properties(height=alt.Step(22)), use_container_width=True)
    st.caption("T1 states everything needed, T2 an accepted alternative, T3 part, T4 label or hint only, T5 none.")


def _symptom_results(res, search, claims, h):
    show_alerts(res)
    _kpi_strip(res)
    if res["ranked"] is None or res["ranked"].empty:
        st.info("No facility with a usable map location in range. Call 108 / follow protocol.")
        return
    st.markdown("<div class='vf-panel-title'>Candidate destinations, in ranked order</div>", unsafe_allow_html=True)
    st.dataframe(_destinations(res, claims), hide_index=True, use_container_width=True, height=388,
                 column_config={
                     "Text check": st.column_config.TextColumn(
                         "Text check", help="Hand check (n = 20 per strength): how often statements of this strength "
                                            "matched the source text. Not a chance that the service exists."),
                     "Level confidence": st.column_config.TextColumn(
                         "Level confidence", help="Confidence in the 1 to 5 care-level assignment, from how much the listing says."),
                     "Location": st.column_config.TextColumn("Location", help="Map pin quality flag: good, suspect or invalid."),
                     "Min (est.)": st.column_config.NumberColumn("Min (est.)", help="Estimate from straight-line distance, not a road route.")})
    st.caption(f"From {search['label']}. Emergencies: evidence tier first, then nearest within a 5-minute band. Statements are "
               "the facility's own; open-now, staffing and bed status are unknown. Destination stays with protocol and medical direction.")
    c1, c2, c3 = st.columns([5, 3, 4])
    with c1:
        st.markdown("<div class='vf-panel-title'>Possibilities the model planned for</div>", unsafe_allow_html=True)
        _differential(res)
    with c2:
        st.markdown("<div class='vf-panel-title'>Evidence tiers in range</div>", unsafe_allow_html=True)
        if res.get("tier_counts"):
            _tier_chart(res)
        else:
            st.caption("Tiers apply to red cases only. This case is ordered by distance among places that fit.")
    with c3:
        st.markdown("<div class='vf-panel-title'>Map</div>", unsafe_allow_html=True)
        h.results_map(res["ranked"].head(40), {**search, "radius_km": min(res["radius"], 150)}, height=300)
    with st.expander("Quotes and per-possibility statements for the top five"):
        rows = res["ranked"].set_index("facility_id")
        for e in res["explanations"]:
            hospital_card(e, rows.loc[e["facility_id"]], f"emr_{e['facility_id']}")
    with st.expander("Model trace: each step, what it decided, time taken"):
        st.dataframe(pd.DataFrame([{"Step": t["step"], "Result": t["summary"], "ms": t.get("ms")} for t in res["trace"]]),
                     hide_index=True, use_container_width=True)
        st.caption("Decision support, not dispatch. Rule-based; no step predicts outcome or benefit.")
    if res["nearest_not_chosen"] or res.get("excluded"):
        with st.expander("Closer facilities ranked lower, and places excluded for an emergency"):
            for n in res["nearest_not_chosen"]:
                st.markdown(f"- {n['name']}: {n['minutes']:.0f} min, ranked #{n['rank']}. {n['why']}")
            for x in res.get("excluded") or []:
                st.markdown(f"- Excluded: {x['name']} ({x['km']:.0f} km), single-specialty or diagnostic-only.")


def _service_results(claims, districts, search, labels, profile, h):
    ranked, unplaced = h.run_search(claims, search, profile)
    if profile["min_level_only"]:
        ranked = ranked[ranked["level_meets"]]
    st.markdown(f"<div class='vf-panel-title'>{ui.esc(labels[search['service']])} near {ui.esc(search['label'])}</div>",
                unsafe_allow_html=True)
    if ranked.empty:
        st.info("No facility in this dataset claims this service within that distance.")
        h.unplaced_block(unplaced)
        return
    rel = reliability()
    strength = ranked["claim_strength"]
    ui.kpis([
        (f"{len(ranked)}", f"Facilities claiming it within {search['radius_km']} km", "About 10,000 facilities in the dataset; real ones may be missing."),
        (f"{int((strength == 'strong').sum())}, {int((strength == 'moderate').sum())}, {int((strength == 'weak').sum())}",
         "Claims by strength (2+ places, once, label only)", "Strength of wording in the facility's own text."),
        (", ".join(f"{rel[k][0]}/{rel[k][1]}" for k in ("strong", "moderate", "weak") if k in rel),
         "Text-reading check for those strengths", "Hand check, n = 60. Extraction accuracy, not service existence."),
        ("0", "Verified by an official source", "Open-now status is unknown for every facility.", True),
    ])
    st.caption(h.PLACEHOLDER_NOTE)
    table = pd.DataFrame({
        "#": ranked["rank"], "Facility": ranked["name"], "km (straight)": ranked["km"].round(0),
        "Care level": ranked["care_level"],
        f"Meets level {h.MIN_LEVEL.get(search['service'], 3)}+": ranked["level_meets"].map({True: "yes", False: "no"}),
        "Level confidence": ranked["level_confidence"],
        "Claim": strength.map(STRENGTH_TXT).fillna(strength),
        "Text check": strength.map(_rel_txt),
        "Checked": ranked["verification_status"].map({"inferred": "no (self-reported)", "externally_supported": "outside support",
                                                      "verified": "verified"}).fillna("no"),
        "Location": ranked["location_quality_flag"],
        "Flag": ranked["plausibility_flag"],
        "24x7": ranked["hours_claim"].map({"24x7": "claims 24x7"}).fillna("unknown"),
        "Phone (unchecked)": ranked["contact_phone"], "District": ranked["district"],
        "Evidence": ranked["supporting_quote"]})
    left, right = st.columns([3, 2])
    with left:
        st.dataframe(table.head(50), hide_index=True, use_container_width=True, height=380)
    with right:
        h.results_map(ranked.head(50), search)
    top = ranked.head(50)
    pick = st.selectbox("Details and report for", top["facility_id"].tolist(), key="e_pick",
                        format_func=lambda i: "{rank}. {name}".format(**top[top["facility_id"] == i].iloc[0]))
    r = top[top["facility_id"] == pick].iloc[0]
    clean = h.clean
    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        c1.markdown((f"**No claim by the facility.** Blood bank {h.DIRECTORY_LABEL} (eRaktKosh): directory evidence only, "
                     "not verified, no information on current stock.  \n" if bool(r.get("directory_only")) else "") +
                    f"**Claim:** {r['service_label']} ({r['claim_strength']}, {r['claim_basis']})  \n"
                    f"**Quote:** “{clean(r['supporting_quote']) or 'none'}” ({r['source_field']})  \n"
                    f"**Source:** {h.source_link(r)}")
        c2.markdown(f"**Verification:** {r['verification_status']}"
                    f"{' via ' + r['verification_source'] if clean(r['verification_source']) else ''}  \n"
                    f"**Facility exists:** {r['facility_existence_status']}  \n"
                    f"**Care level:** {clean(r['care_level']) or 'unknown'} ({r['level_confidence']} confidence)  \n"
                    f"**Flag:** {clean(r['plausibility_flag']) or 'none'}")
        c3.markdown(f"**Phone:** {clean(r['contact_phone']) or 'none'} (unchecked)  \n"
                    f"**Availability now:** unknown  \n**Hours:** {r['hours_claim']}  \n"
                    f"**Position:** {r['latitude']:.5f}, {r['longitude']:.5f} ({r['location_quality_flag']})")
        for note in h.resolved_notes(r):
            st.markdown(note)
        h.feedback_buttons(r, "emrs", f"e_{r['facility_id']}")
    h.unplaced_block(unplaced)


def view(claims, districts, h):
    """h is the app module (shared helpers: location_input, search_form, results_map, ...)."""
    st.markdown("<h1>Which hospital, on what evidence?</h1><p class='vf-lede'>For crews and dispatchers. The destination "
                "decision stays with your protocol and medical direction.</p>", unsafe_allow_html=True)
    form, out = st.columns([4, 9], gap="medium")
    with form:
        with st.container(border=True):
            how = st.radio("Search by", ["Symptoms", "Service needed"], horizontal=True, key="e_how")
            if how == "Symptoms":
                sx = symptom_form("e", h.location_input, districts, narrow=True)
                search = labels = None
            else:
                sx = None
                search, labels = h.search_form("e", claims, districts, default_radius=80)
            with st.expander("Crew profile (this session only)"):
                profile = {
                    "role": st.selectbox("Role", ["Ambulance crew", "Dispatcher", "Hospital staff", "Other"], key="e_role"),
                    "vehicle": st.selectbox("Vehicle", ["Not applicable", "Basic life support", "Advanced life support",
                                                        "Patient transport"], key="e_veh"),
                    "min_level_only": st.checkbox("Hide facilities below the usual care level", key="e_min")}
    with out:
        if how == "Symptoms" and sx:
            _symptom_results(run_symptom_search(claims, sx), sx, claims, h)
        elif how != "Symptoms" and search:
            _service_results(claims, districts, search, labels, profile, h)
        else:
            rel = reliability()
            ui.kpis([
                (STABILITY["first"], "Ranking stability: first choice stays first",
                 f"{STABILITY['n']} of travel-time assumptions. Consistency, not correctness."),
                (", ".join(f"{rel[k][0]}/{rel[k][1]}" for k in ("strong", "moderate", "weak") if k in rel),
                 "Text-reading check by claim strength (2+ places, once, label only)",
                 "Hand check, n = 60. Extraction accuracy, not service existence."),
                ("0", "Services verified by an official source", "Every statement is the facility's own.", True)])
            st.markdown("<div class='vf-empty'>Enter symptoms or a service and a starting point. Candidates appear here "
                        "in ranked order with their evidence group, statement strength, and location and care-level "
                        "confidence.</div>", unsafe_allow_html=True)
