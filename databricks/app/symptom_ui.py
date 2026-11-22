"""'Describe symptoms' mode for the Patients and EMR tabs.

All the logic is in recommender/; this file only renders its result.
"""
import pandas as pd
import streamlit as st

from recommender import Config, recommend
from recommender.knowledge import load

TRANSPORT_LABELS = {
    "ambulance_108": "108 / 102 ambulance (free)", "own_car": "Own car or family vehicle",
    "auto_taxi": "Auto-rickshaw or taxi", "two_wheeler": "Motorbike or scooter",
    "bus": "Public bus", "walk": "Walking"}
STRENGTH_SHORT = {"strong": "states it in 2+ places", "moderate": "states it once",
                  "weak": "only a label or hint", None: "no claim"}


def _pct(x):
    return f"{x:.0%}" if x >= 0.01 else "<1%"


def symptom_form(key, location_input, districts):
    kb = load(Config().knowledge_dir)
    sym = kb.symptoms.sort_values("label")
    labels = dict(zip(sym["symptom"], sym["label"]))
    chosen = st.multiselect("Symptoms (pick all that apply)", list(labels), format_func=labels.get,
                            key=f"{key}_sym")
    extra = st.text_input("Anything else, in your own words (optional)", key=f"{key}_symtext",
                          placeholder="e.g. fell off a bike, hit head, feeling dizzy")
    c1, c2, c3 = st.columns([3, 1, 1])
    transport = c1.multiselect("How can you travel?", list(TRANSPORT_LABELS), format_func=TRANSPORT_LABELS.get,
                               default=["ambulance_108", "auto_taxi"], key=f"{key}_tr")
    pregnant = c2.checkbox("Pregnant", key=f"{key}_preg")
    went_tele = c3.checkbox("A teleconsult said go in", key=f"{key}_tele")
    origin = location_input(f"{key}_sx", districts)
    raw = list(chosen) + [t.strip() for t in extra.replace(";", ",").split(",") if t.strip()]
    if st.button("Find the best hospital", type="primary", key=f"{key}_sxgo",
                 disabled=origin is None or not raw):
        st.session_state[f"{key}_sxsearch"] = {"symptoms": raw, "transport": transport, "pregnant": pregnant,
                                               "tele": went_tele, **origin}
    return st.session_state.get(f"{key}_sxsearch")


@st.cache_data(ttl=600, show_spinner="Working out the best options...")
def _run(_claims, n_claims, symptoms, lat, lon, transport, pregnant, tele):
    r = recommend(list(symptoms), lat, lon, _claims, transport=list(transport) or None,
                  patient={"pregnant": pregnant}, telehealth_says_go=tele)
    return {"disposition": r.disposition, "p_lt": r.p_life_threatening, "is_red": r.is_red,
            "conditions": r.conditions, "ranked": r.ranked, "alerts": r.alerts,
            "explanations": r.explanations, "nearest_not_chosen": r.nearest_not_chosen,
            "trace": r.trace, "symptoms": r.symptoms, "radius": r.radius_used_km}


def run_symptom_search(claims, search):
    return _run(claims, len(claims), tuple(search["symptoms"]), search["lat"], search["lon"],
                tuple(search["transport"]), search["pregnant"], search["tele"])


def show_alerts(res):
    for a in res["alerts"]:
        {"critical": st.error, "warning": st.warning, "advice": st.info}.get(a["level"], st.caption)(
            a["text"], **({"icon": "🚨"} if a["level"] == "critical" else {}))


def show_conditions(res):
    c = res["conditions"].head(6)
    st.markdown(f"**Possibilities we planned for** (not a diagnosis). "
                f"Chance one of them is life-threatening: **{_pct(res['p_lt'])}**.")
    st.dataframe(pd.DataFrame({
        "Possibility": c["label"], "Likelihood": c["p"].map(_pct),
        "Severity": c["severity"].map(lambda v: f"{v:.2f}"),
        "Life-threatening": c["life_threatening"].map({True: "yes", False: "no"}),
    }), hide_index=True, width="stretch")


def hospital_card(e, row, key, compact=False):
    with st.container(border=True):
        tag = "  \n🩹 **Stabilise here first, then transfer**" if e["stabilise"] else ""
        st.markdown(f"**{e['rank']}. {e['name']}** · ⏱ **{e['minutes']:.0f} min** · "
                    f"need met **{e['need_met']:.0%}**  \n{row.get('district') or ''}, "
                    f"{str(row.get('state') or '').title()} · care level {e['care_level']}{tag}")
        st.markdown(e["summary"])
        if e["why_lower"]:
            st.caption(e["why_lower"])
        main = max(e["by_condition"], key=lambda p: p["p_condition"] * p["weight"])
        for s in main["services"]:
            if s["quote"]:
                st.markdown(f"> “{s['quote']}” · *{s['service']}*, {STRENGTH_SHORT.get(s['claim_strength'])}")
        phone = row.get("contact_phone")
        if isinstance(phone, str) and phone.strip():
            tel = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
            st.markdown(f"📞 **Call before you go:** [{phone}](tel:{tel}) (number not checked)")
        with st.expander("Why this rank: full breakdown"):
            st.dataframe(pd.DataFrame([{
                "Possibility": p["label"], "Likelihood": _pct(p["p_condition"]), "Weight": p["weight"],
                "Offers what's needed": p["capability"], "Care level factor": p["rung_factor"],
                "24x7 factor": p["hours_factor"], "Still in time": p["timeliness"],
                "Chance of good care": p["p_care"], "Adds to score": p["contribution"],
                "Services used": ", ".join(f"{s['service']} ({STRENGTH_SHORT.get(s['claim_strength'])})"
                                           for s in p["services"]) or "none needed",
            } for p in e["by_condition"]]), hide_index=True, width="stretch")
            st.caption("Chance of good care = offers what's needed × care level factor × 24x7 factor × still in time. "
                       "Score = sum over possibilities of likelihood × weight × chance of good care, "
                       "divided by the sum of likelihood × weight.")


def show_result(res, search, results_map, key):
    show_alerts(res)
    show_conditions(res)
    if res["disposition"] == "telehealth_first":
        st.link_button("Start a free eSanjeevani teleconsultation", "https://esanjeevani.mohfw.gov.in/")
    ranked = res["ranked"]
    if ranked is None or ranked.empty:
        st.info("No facility with a usable map location was found nearby. Call 108.")
        return
    st.caption(f"Ranked {len(ranked)} facilities within {res['radius']:.0f} km (straight line) of "
               f"{search['label']}. Travel times are estimates from straight-line distance, not road routes. "
               + (search.get("approx") or ""))
    rows = ranked.set_index("facility_id")
    body = lambda: [hospital_card(e, rows.loc[e["facility_id"]], f"{key}_{e['facility_id']}")
                    for e in res["explanations"]]
    left, right = st.columns([3, 2])
    with right:
        results_map(ranked.assign(km=ranked["km"]), {**search, "radius_km": min(res["radius"], 150)})
    with left:
        if res["disposition"] == "telehealth_first":
            with st.expander("If the doctor says to go in: best options"):
                body()
        else:
            body()
    if res["nearest_not_chosen"]:
        st.markdown("**Closer facilities that ranked lower**")
        for n in res["nearest_not_chosen"]:
            st.markdown(f"- {n['name']}: {n['minutes']:.0f} min, ranked #{n['rank']}. {n['why']}")
    with st.expander("How this was worked out (each step of the model)"):
        for i, t in enumerate(res["trace"], 1):
            st.markdown(f"{i}. **{t['step']}**: {t['summary']}")
        st.caption("Decision support, not medical advice. The symptom and condition tables are our "
                   "illustrative judgement and have not been reviewed by a clinician.")
