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
ONSET_CHOICES = {"Just now (under 15 min)": 0, "About 30 min ago": 30, "About 1 hour ago": 60,
                 "About 2 hours ago": 120, "About 3 hours ago": 180, "About 4 hours ago": 240,
                 "About 6 hours ago": 360, "About 12 hours ago": 720, "A day ago or more": 1440}
STRENGTH_SHORT = {"strong": "states it in 2+ places", "moderate": "states it once",
                  "weak": "only a label or hint", None: "not stated"}
TIER_BADGE = {1: "🟢 States everything needed", 2: "🟢 States an accepted alternative for everything",
              3: "🟡 States part of what is needed", 4: "🟠 Only a label or hint", 5: "⚪ States none of it"}


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
    onset_label = st.selectbox("When did it start?", list(ONSET_CHOICES), key=f"{key}_onset",
                               help="Some treatments only work in the first hours, so this changes which hospital is best.")
    origin = location_input(f"{key}_sx", districts)
    raw = list(chosen) + [t.strip() for t in extra.replace(";", ",").split(",") if t.strip()]
    if st.button("Find the best hospital", type="primary", key=f"{key}_sxgo",
                 disabled=origin is None or not raw):
        st.session_state[f"{key}_sxsearch"] = {"symptoms": raw, "transport": transport, "pregnant": pregnant,
                                               "tele": went_tele, "onset_min": ONSET_CHOICES[onset_label], **origin}
    return st.session_state.get(f"{key}_sxsearch")


@st.cache_data(ttl=600, show_spinner="Working out the best options...")
def _run(_claims, n_claims, symptoms, lat, lon, transport, pregnant, tele, onset_min=0):
    r = recommend(list(symptoms), lat, lon, _claims, transport=list(transport) or None,
                  patient={"pregnant": pregnant}, telehealth_says_go=tele, onset_min=onset_min)
    return {"disposition": r.disposition, "p_lt": r.p_life_threatening, "is_red": r.is_red,
            "conditions": r.conditions, "ranked": r.ranked, "alerts": r.alerts,
            "explanations": r.explanations, "nearest_not_chosen": r.nearest_not_chosen,
            "trace": r.trace, "symptoms": r.symptoms, "radius": r.radius_used_km,
            "excluded": r.excluded, "unclear": getattr(r, "unclear", False), "tier_counts": (r.care or {}).get("tier_counts")}


def run_symptom_search(claims, search):
    return _run(claims, len(claims), tuple(search["symptoms"]), search["lat"], search["lon"],
                tuple(search["transport"]), search["pregnant"], search["tele"], search.get("onset_min", 0))


def show_alerts(res):
    for a in res["alerts"]:
        {"critical": st.error, "warning": st.warning, "advice": st.info}.get(a["level"], st.caption)(
            a["text"], **({"icon": "🚨"} if a["level"] == "critical" else {}))


def show_conditions(res):
    c = res["conditions"].head(6)
    if res.get("unclear"):
        st.markdown("**Treated as a possible emergency of an unknown kind.** We could not read the symptoms, so no "
                    "specific condition was planned for. Pick symptoms from the list for a better match.")
        return
    if res["is_red"]:
        st.markdown("**Treated as a possible emergency.** This is a cautious warning-sign screen, not a diagnosis. "
                    "Possibilities we planned for:")
    elif res["disposition"] == "hospital":
        st.markdown("**Not treated as an emergency, but see a doctor today.** If it gets worse, call 108. "
                    "Possibilities we planned for (not a diagnosis):")
    else:
        st.markdown("**Possibilities we planned for** (not a diagnosis):")
    st.markdown("  \n".join(f"- {lab}" + (" (can be life-threatening)" if lt else "")
                             for lab, lt in zip(c["label"], c["life_threatening"])))


def hospital_card(e, row, key, compact=False):
    with st.container(border=True):
        tag = "  \n📍 **Closer option to raise with the 108 crew**" if e["stabilise"] else ""
        badge = f" · {TIER_BADGE.get(e.get('tier'))}" if e.get("tier") else ""
        st.markdown(f"**{e['rank']}. {e['name']}** · ⏱ **about {e['minutes']:.0f} min**{badge}  \n"
                    f"{row.get('district') or ''}, {str(row.get('state') or '').title()} · "
                    f"care level {e['care_level']}{tag}")
        st.markdown(e["summary"])
        if e["why_lower"]:
            st.caption(e["why_lower"])
        main = next((p for p in e["by_condition"] if p["condition"] == e.get("main_condition")),
                    e["by_condition"][0])
        for s in main["services"]:
            if s["quote"]:
                st.markdown(f"> “{s['quote']}” · *{s['service']}*, {STRENGTH_SHORT.get(s['claim_strength'])}")
        tb = main.get("time_basis")
        if tb and tb.get("status") == "published":
            src = f"[{tb['source']}]({tb['url']})" if tb.get("url") else tb.get("source")
            st.caption(f"⏳ Why time matters here: {tb['label'].lower()}. {src}. "
                       "A finding about one treatment in another setting, shown for urgency, not as a prediction.")
        phone = row.get("contact_phone")
        if isinstance(phone, str) and phone.strip():
            tel = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
            st.markdown(f"📞 **Call before you go:** [{phone}](tel:{tel}) (number not checked)")
        with st.expander("What its own text says, for each possibility"):
            st.dataframe(pd.DataFrame([{
                "Possibility": p["label"],
                "Says it offers (from its own text)": ", ".join(
                    f"{s['service']} ({STRENGTH_SHORT.get(s['claim_strength'])})" for s in p["services"]) or "nothing special needed",
            } for p in e["by_condition"]]), hide_index=True, width="stretch")
            st.caption("This is what the facility says about itself. We hand-checked 60 such statements against the "
                       "source text (20 of 20 stated in 2+ places, 16 of 20 stated once and 7 of 20 label-only were "
                       "stated outright). That measures how well we read the text, not whether the service exists "
                       "or is open now. Nothing is independently verified.")


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
    if res["is_red"] and res.get("tier_counts"):
        tc = res["tier_counts"]
        st.caption(f"Order: facilities whose own text directly states everything needed come first "
                   f"({tc.get(1, 0)} in range), then those stating an accepted alternative for everything ({tc.get(2, 0)}), "
                   f"then those stating part ({tc.get(3, 0)}); nearest first within each group. "
                   "No facility's services are independently verified.")
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
    if res.get("excluded"):
        with st.expander(f"Not listed for an emergency: {len(res['excluded'])} nearby single-specialty places"):
            for x in res["excluded"]:
                st.markdown(f"- {x['name']} ({x['km']:.0f} km): eye, dental, fertility, skin or diagnostic-only "
                            "facilities are not ranked for an emergency, whatever their text says.")
    with st.expander("How this was worked out (each step of the model)"):
        for i, t in enumerate(res["trace"], 1):
            st.markdown(f"{i}. **{t['step']}**: {t['summary']}")
        st.caption("Decision support, not medical advice or dispatch. Emergencies are ordered by what each "
                   "facility's own text states and then by estimated time; no formula predicts benefit. "
                   "The symptom screen is cautious by design and has not been reviewed by a clinician.")
