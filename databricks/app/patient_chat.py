"""Patient view: one conversation, with the evidence for each answer shown beside it.

Who answers what:
- Symptoms -> the recommender (recommender/). It alone picks and orders hospitals, raises the
  108 / 112 emergency block and applies the evidence tiers.
- Follow-up questions about that result ("why this one?", "compare the top 3", "is there a
  closer one?") -> answered from the recommender's own output: scripted answers for the common
  questions, and, when a chat model is attached, a model that is only given that output.
- Questions about the data ("how many facilities in Gaya claim an ICU?") -> Genie, over the gold tables.
The right-hand panel only shows fields that exist in the data. No ratings, no costs.
"""
from __future__ import annotations

import re

import altair as alt
import pandas as pd
import streamlit as st

import genie_chat
import llm_explain
import ui_theme as ui
from recommender import Config
from ranking import haversine_km
from recommender.knowledge import load
from symptom_ui import ONSET_CHOICES, STRENGTH_SHORT, TRANSPORT_LABELS, hospital_card, run_symptom_search

QUICK = ["chest_pain", "breathing_difficulty", "fever", "headache", "abdominal_pain", "vomiting",
         "heavy_bleeding", "unconscious", "seizure", "pregnant"]
FOLLOW_UPS = ["Why this hospital?", "Compare the top 3", "Is there a closer one?", "Where can I get an MRI near me?"]
DATA_WORDS = re.compile(r"\b(how many|which (district|state)s?|count|number of|list|per 100k|across (states|districts)"
                        r"|districts?|states?|shortfall|population|dataset|data)\b", re.I)
STRENGTH_DOT = {"strong": ("#2E8B62", "2+ places"), "moderate": ("#7DB89A", "once"),
                "weak": ("#C9941F", "label only"), None: ("#8B8D93", "not stated")}
SVC_LABEL: dict = {}


def _sl(code) -> str:
    return SVC_LABEL.get(code) or str(code).replace("_", " ")


def _from(label: str) -> str:
    return label if label.startswith(("PIN", "your")) or label[:1].isdigit() else f"the {label}"


GREETING = ("Hello. Tell me **what is wrong** in your own words, or tap the symptoms below, and I will look for "
            "hospitals near you that say they can help. You can then ask me why I picked one, compare the "
            "options, or ask about facilities in your area.\n\nI can't diagnose or give medical advice. "
            "**If it feels like an emergency, call 108 now.**")


# ----------------------------------------------------------------------------
# reading the message
# ----------------------------------------------------------------------------
@st.cache_resource
def _kb():
    return load(Config().knowledge_dir)


def _symptoms_in(text: str) -> list:
    """Symptom codes the recommender itself would read from this text."""
    from recommender.steps import State, normalise_symptoms
    s = State(raw_symptoms=[t.strip() for t in re.split(r"[;,.]| and ", text) if t.strip()], lat=0.0, lon=0.0,
              claims=pd.DataFrame(), kb=_kb())
    try:
        return list(normalise_symptoms(s, Config()).symptoms)
    except Exception:
        return []


def _district_in(text: str, districts):
    """'I am in Gaya' -> that district's start point. Only after in / near / at / from."""
    if districts is None:
        return None
    m = re.findall(r"\b(?:in|near|at|from)\s+([A-Za-z][A-Za-z ]{2,30})", text)
    if not m:
        return None
    names = districts["district_name"].dropna().astype(str)
    low = names.str.lower()
    for cand in m:
        words = cand.lower().split()
        for n in (3, 2, 1):
            key = " ".join(words[:n])
            if len(key) < 4:
                continue
            hit = districts[low == key]
            if len(hit) == 1:
                r = hit.iloc[0]
                has_town = pd.notna(r.get("town_lat")) and pd.notna(r.get("town_lon"))
                return {"lat": float(r["town_lat"] if has_town else r["centre_lat"]),
                        "lon": float(r["town_lon"] if has_town else r["centre_lon"]),
                        "district": r["district_name"],
                        "label": f"{'main town' if has_town else 'centre'} of {r['district_name']} district, {r['state_name']}",
                        "approx": "Distances are measured from the main town of the district, so they are rough."}
    return None


# ----------------------------------------------------------------------------
# facts about a result (the only thing any answer may use)
# ----------------------------------------------------------------------------
def _details(claims, ids) -> pd.DataFrame:
    c = claims[claims["facility_id"].isin(ids)]
    g = c.groupby("facility_id")
    out = g.agg(says_24x7=("hours_claim", lambda x: bool((x == "24x7").any())),
                beds=("capacity_beds", "first"), phone=("contact_phone", "first"),
                district=("district", "first"), state=("state", "first"),
                location=("location_quality_flag", "first"), level_conf=("level_confidence", "first"),
                exists=("facility_existence_status", "first"), kind=("level_kind", "first"))
    return out


def _main_part(e):
    return next((p for p in e["by_condition"] if p["condition"] == e.get("main_condition")), e["by_condition"][0])


def _facts(res, search, det) -> dict:
    opts = []
    for e in res["explanations"]:
        d = det.loc[e["facility_id"]] if e["facility_id"] in det.index else {}
        beds = d.get("beds") if len(d) else None
        opts.append({
            "rank": e["rank"], "name": e["name"], "minutes_estimate": round(e["minutes"]), "km_straight_line": round(e["km"]),
            "evidence_group": ui.TIER_SHORT.get(e.get("tier")) if e.get("tier") else None,
            "its_own_text_states": [{"service": _sl(s["service"]), "how": STRENGTH_SHORT.get(s["claim_strength"]),
                                     "quote": s["quote"]} for s in _main_part(e)["services"]],
            "not_stated": e.get("missing") or [], "care_level_1_to_5": e["care_level"],
            "says_open_24x7": bool(d.get("says_24x7")) if len(d) else None,
            "beds": int(beds) if beds is not None and pd.notna(beds) else None,
            "phone_unchecked": d.get("phone") if len(d) else None,
            "why_ranked_lower_than_first": e.get("why_lower"), "summary": e["summary"]})
    return {"emergency": bool(res["is_red"]),
            "start_point": "the address the person typed" if str(search["label"]).startswith("your address") else search["label"],
            "possibilities_planned_for_not_a_diagnosis": res["conditions"]["label"].head(5).tolist()
            if res["conditions"] is not None and not res.get("unclear") else [],
            "options": opts, "closer_facilities_ranked_lower": res["nearest_not_chosen"],
            "facilities_ranked": int(len(res["ranked"])) if res["ranked"] is not None else 0,
            "how_the_order_works": ("In an emergency: facilities whose own text states everything needed come first, then the "
                                    "nearest among those. So option 1 is the nearest of those that state everything needed, "
                                    "not the nearest facility overall; closer_facilities_ranked_lower lists the nearer ones."
                                    if res["is_red"] else "The nearest facility that fits what was described comes first."),
            "search_radius_km": res["radius"],
            "unknown": ["whether any service is available or working today", "opening hours unless the facility says 24x7",
                        "cost", "free beds", "waiting time", "whether the phone number works",
                        "nothing is independently verified"]}


def _no_conditions(facts: dict, res) -> dict:
    """Facts for the chat model with every condition name removed, so it cannot name an illness."""
    import json
    f = {k: v for k, v in facts.items() if k != "possibilities_planned_for_not_a_diagnosis"}
    txt = json.dumps(f, default=str, ensure_ascii=False)
    labels = []
    if res.get("conditions") is not None and len(res["conditions"]):
        labels = sorted({str(x) for x in res["conditions"]["label"].dropna()}, key=len, reverse=True)
    for lab in labels:
        core = re.escape(lab.split(" (")[0].strip())
        if len(core) >= 4:
            txt = re.sub(r"(?:an? )?(?:possible |suspected |likely )?" + core + r"(?: \([^)]*\))?", "what you described", txt, flags=re.I)
    try:
        return json.loads(txt)
    except Exception:
        return f


RED_LINE = "This could be an emergency, so **call 108 first**. The crew decides where to go."


def _with_108(answer: str, red: bool) -> str:
    return answer if not red or "108" in answer else answer.rstrip() + "\n\n" + RED_LINE


# ----------------------------------------------------------------------------
# "I need an MRI": a named service, searched from the same starting point
# ----------------------------------------------------------------------------
SERVICE_WORDS = [
    ("mri", r"\bm\.?r\.?i\b|magnetic resonance"),
    ("ct", r"\bct\b|\bcat scan"),
    ("c_section", r"\bc[- ]?sections?\b|c(a)?esar(e|i)an|\blscs\b"),
    ("dialysis", r"dialys"),
    ("blood_bank", r"blood bank|transfusion|need blood|units? of blood"),
    ("nicu", r"\bnicu\b|newborn (icu|intensive)|neonatal"),
    ("icu", r"\bicu\b|intensive care|ventilator"),
    ("cardiac_cath", r"cath lab|angioplast|angiogra|\bstent"),
    ("burns", r"burns? (unit|care|ward|centre|center|hospital)"),
    ("stroke", r"stroke (unit|care|centre|center|hospital)|thromboly"),
    ("trauma", r"trauma (unit|care|centre|center|hospital)"),
    ("obstetric_delivery", r"maternity|give birth|childbirth|normal delivery|deliver(y|ing)? (of )?(a |my |the )?bab"),
    ("paediatrics", r"p(a)?ediatric|children'?s (doctor|hospital|specialist)|child specialist"),
    ("emergency", r"emergency (room|department|ward|care)|casualty"),
]
SERVICE_WORDS = [(code, re.compile(rx, re.I)) for code, rx in SERVICE_WORDS]
COUNTING = re.compile(r"how many|\bcount\b|number of|per 100k|across (states|districts)|which (district|state)s?", re.I)
URGENT_NOTE = {
    "c_section": "If you are in labour, bleeding, or your waters have broken, call **108** or **102** now instead of travelling on your own.",
    "obstetric_delivery": "If you are in labour, bleeding, or your waters have broken, call **108** or **102** now instead of travelling on your own.",
    **{k: "If someone needs this right now, call **108**. The crew decides where to go."
       for k in ("emergency", "trauma", "stroke", "cardiac_cath", "burns", "icu", "nicu")},
}
SVC_RADII = (30, 60, 120, 250, 500, 1500)
HOW = {"strong": "states it in 2+ places", "moderate": "states it once", "weak": "label or indirect wording only"}


def _services_in(text: str) -> list:
    found = [code for code, rx in SERVICE_WORDS if rx.search(text)]
    if "nicu" in found and "icu" in found and not re.search(r"\bicu\b", text, re.I):
        found.remove("icu")
    return found


def _service_search(claims, service, place) -> dict:
    """Facilities whose own text states one service, from one starting point.

    Order: facilities that state it directly (in 2+ places, or once) nearest first, then those
    with only a label or indirect wording, nearest first. The radius widens until three direct
    statements are found, so the answer can say how far the nearest one really is.
    """
    c = claims[(claims["service"] == service) & claims["location_quality_flag"].eq("good")
               & claims["latitude"].notna() & claims["longitude"].notna()].copy()
    c["direct"] = c["claim_strength"].isin(["strong", "moderate"])
    c["s_ord"] = c["claim_strength"].map({"strong": 0, "moderate": 1, "weak": 2}).fillna(3)
    c = c.sort_values("s_ord").drop_duplicates("facility_id")
    if len(c):
        c["km"] = haversine_km(place["lat"], place["lon"], c["latitude"].to_numpy(float), c["longitude"].to_numpy(float))
    else:
        c["km"] = []
    radius, inside = SVC_RADII[-1], c
    for r in SVC_RADII:
        inside = c[c["km"] <= r]
        radius = r
        if int(inside["direct"].sum()) >= 3:
            break
    near30 = c[c["km"] <= SVC_RADII[0]]
    rows = inside.sort_values(["direct", "km"], ascending=[False, True]).head(5).reset_index(drop=True)
    rows["rank"] = rows.index + 1
    return {"service": service, "label": _sl(service), "rows": rows, "radius": radius,
            "n_direct": int(inside["direct"].sum()), "n_any": int(len(inside)),
            "n_direct_30": int(near30["direct"].sum()), "n_any_30": int(len(near30)), "place": place}


def _row_phone(r):
    p = r.get("contact_phone")
    return p.strip() if isinstance(p, str) and p.strip() else None


def _service_text(svc, cur, text, turn=0) -> str:
    rows, label, place = svc["rows"], svc["label"], svc["place"]
    low = label if label.isupper() or label[:2].isupper() else label[0].lower() + label[1:]
    out = []
    if re.search(r"\b(may|might|maybe|could|do i|should i)\b", text, re.I):
        out.append(f"Whether you need {('an ' if low[:1].lower() in 'aeiou' or low.startswith('MRI') else 'a ')}{low} is for a doctor to decide. "
                   "What I can do is show where the listings mention it.")
    if not len(rows):
        out.append(f"I found no facility in this data whose listing mentions {low} with a usable map location. "
                   "That is a gap in the data, not proof that none exists. A district hospital or 108 can point you to one.")
        return "\n\n".join(out)
    first = rows.iloc[0]
    if svc["n_direct_30"] == 0:
        lead = (f"No listing within {SVC_RADII[0]} km of {_from(place['label'])} states {low} directly. "
                f"The nearest that does is **{first['name']}**, about **{first['km']:.0f} km** away in a straight line."
                if bool(first["direct"]) else
                f"No listing near {_from(place['label'])} states {low} directly. The closest mentions are only labels or indirect wording.")
        if svc["n_any_30"]:
            lead += f" ({svc['n_any_30']} closer place{'s' if svc['n_any_30'] > 1 else ''} only mention{'s' if svc['n_any_30'] == 1 else ''} it as a label.)"
        out.append(lead)
        out.append(f"Widening the search to {svc['radius']} km, these come up:")
    else:
        n = svc["n_direct_30"]
        out.append((f"{n} place{'s' if n > 1 else ''} within {SVC_RADII[0]} km of {_from(place['label'])} "
                    f"state{'s' if n == 1 else ''} {low} in {'its' if n == 1 else 'their'} own listing. The nearest:")
                   if turn % 2 == 0 else
                   f"For {low}, measured from {_from(place['label'])}, the nearest listings that state it are:")
    earlier = {}
    if cur and cur["res"].get("ranked") is not None and len(cur["res"]["ranked"]):
        rk = cur["res"]["ranked"]
        earlier = dict(zip(rk["facility_id"], rk["rank"]))
    items = []
    for _, r in rows.iterrows():
        q = r.get("supporting_quote")
        quote = f" “{str(q)[:110]}”" if isinstance(q, str) and q.strip() else ""
        kind = f" ({r['level_kind']})" if isinstance(r.get("level_kind"), str) else ""
        ph = _row_phone(r)
        also = f" It was also option {int(earlier[r['facility_id']])} for your symptoms." if r["facility_id"] in earlier and int(earlier[r["facility_id"]]) <= 5 else ""
        items.append(f"{int(r['rank'])}. **{r['name']}**{kind}, {r['km']:.0f} km. {HOW.get(r['claim_strength'], 'mentions it').capitalize()}:{quote or ' no quote kept'}."
                     + (f" Phone {ph}." if ph else "") + also)
    out.append("\n".join(items))
    if cur and cur["res"]["explanations"]:
        top = cur["res"]["explanations"][:3]
        ids = set(svc.get("stated_ids", []))
        hits = [e["name"] for e in top if e["facility_id"] in ids]
        if hits:
            out.append(f"Of the hospitals I suggested for your symptoms, {', '.join('**' + h + '**' for h in hits)} "
                       f"also mention{'s' if len(hits) == 1 else ''} {low} in {'its' if len(hits) == 1 else 'their'} listing, so one place may cover both.")
        else:
            out.append(f"None of the top hospitals I suggested for your symptoms mention {low} in their listing, so this is a separate list.")
        if cur["res"]["is_red"]:
            out.append("For the emergency itself, **108 still comes first**.")
    if URGENT_NOTE.get(svc["service"]) and not (cur and cur["res"]["is_red"]):
        out.append(URGENT_NOTE[svc["service"]])
    out.append(("A listing is a claim, not a confirmation, so phone and ask before you travel.",
                "None of this is verified. Ring them first and ask if it is running today.",
                "These are the facilities' own words and nobody has checked them, so call ahead.")[turn % 3])
    return "\n\n".join(out)


def _service_again(svc) -> str:
    """The same service asked about again: answer the question, don't repeat the list."""
    rows, label = svc["rows"], svc["label"]
    n = rows.sort_values("km").iloc[0]
    clear = rows[rows["claim_strength"] == "strong"]["name"].tolist()
    out = [f"The nearest place whose listing states {label} is **{n['name']}**, about {n['km']:.0f} km from "
           f"{_from(svc['place']['label'])} ({HOW.get(n['claim_strength'])})."]
    if clear:
        out.append("The listing" + ("s" if len(clear) > 1 else "") + " that say" + ("" if len(clear) > 1 else "s")
                   + " it most clearly, in two or more places: " + ", ".join(f"**{c}**" for c in clear[:3]) + ".")
    out.append(f"All {len(rows)} are in the list above and on the map beside this chat. I'd ring the nearest one and ask "
               "whether it is running today and whether you need a doctor's referral.")
    return "\n\n".join(out)


def _svc_facts(svc) -> dict:
    return {"service_asked_for": svc["label"],
            "measured_from": "the address the person typed" if str(svc["place"]["label"]).startswith("your address") else svc["place"]["label"], "search_radius_km": svc["radius"],
            "listings_stating_it_directly_within_30km": svc["n_direct_30"],
            "order_rule": "direct statements nearest first, then label-only mentions",
            "options": [{"rank": int(r["rank"]), "name": r["name"], "kind": r.get("level_kind"), "km_straight_line": round(float(r["km"])),
                         "how_its_text_states_it": HOW.get(r["claim_strength"]), "quote": r.get("supporting_quote"),
                         "says_open_24x7": r.get("hours_claim") == "24x7", "phone_unchecked": _row_phone(r),
                         "district": r.get("district")} for _, r in svc["rows"].iterrows()],
            "unknown": ["whether the service is working today", "cost", "waiting time", "nothing is independently verified"]}


# ----------------------------------------------------------------------------
# scripted answers
# ----------------------------------------------------------------------------
def _says(e) -> str:
    sv = _main_part(e)["services"]
    if not sv:
        return "no special service is needed for the likely cause"
    return "; ".join(f"{_sl(s['service']).lower()} ({STRENGTH_SHORT.get(s['claim_strength'])})" for s in sv)


def _result_text(res, search, det) -> str:
    ex = res["explanations"]
    if not ex:
        return ("I could not find a facility with a usable map location near "
                f"{_from(search['label'])}. **Call 108.** You can also change your starting point above.")
    lines = []
    if res.get("unclear"):
        lines.append("I could not match your words to a symptom I know, so to be safe I treated this as a "
                     "**possible emergency of an unknown kind**. Tapping symptoms below gives a better match.")
    elif res["is_red"]:
        lines.append("Some of what you describe can be a warning sign, so I treated this as a **possible emergency**. "
                     "This is a cautious screen, not a diagnosis.")
    elif res["disposition"] == "telehealth_first":
        lines.append("This does not look like an emergency from what you told me. A free phone or video consultation "
                     "(eSanjeevani) is a good first step. If you are told to go in, these are the nearest places that fit.")
    else:
        lines.append("I did not treat this as an emergency, but it is worth **seeing a doctor today**. If it gets worse, call 108.")
    e = ex[0]
    d = det.loc[e["facility_id"]] if e["facility_id"] in det.index else None
    lines.append(f"**First choice: {e['name']}**, about **{e['minutes']:.0f} minutes** away ({e['km']:.0f} km in a straight line). "
                 f"Its own text says: {_says(e)}.")
    if d is not None and isinstance(d.get("phone"), str) and d["phone"].strip():
        lines.append(f"Nothing here is verified, so **call before you go**: {d['phone']} (number not checked).")
    else:
        lines.append("Nothing here is verified, and there is no phone number in the data, so check before you travel if you can.")
    if len(ex) > 1:
        lines.append("Ask me why, or to compare the options.")
    return "\n\n".join(lines)


def _why(res, det, idx=0) -> str:
    ex = res["explanations"]
    e = ex[min(idx, len(ex) - 1)]
    out = [f"**{e['name']}** is option {e['rank']}. {e['summary']}"]
    if e.get("why_lower"):
        out.append(e["why_lower"])
    elif res["is_red"] and e.get("tier"):
        out.append("How the order works in an emergency: places whose own text directly states everything needed come "
                   "first, then the nearest among those. No formula predicts how well you would be treated.")
    else:
        out.append("How the order works here: the nearest place that fits the most likely cause comes first.")
    return "\n\n".join(out)


def _compare(res, det) -> str:
    items = []
    for e in res["explanations"][:3]:
        d = det.loc[e["facility_id"]] if e["facility_id"] in det.index else None
        h = "says it is open 24x7" if d is not None and d["says_24x7"] else "opening hours unknown"
        items.append(f"{e['rank']}. **{e['name']}**: about {e['minutes']:.0f} min, {e['km']:.0f} km, {h}.  \n"
                     f"   {ui.TIER_SHORT.get(e.get('tier'), 'Fits the likely cause')}: {_says(e)}.")
    return ("The top three, nearest first within the same evidence group. Times are estimates from straight-line distance.\n\n"
            + "\n".join(items) + "\n\nNone of this is verified. There is no data on cost, quality or waiting time, "
            "so those can't be compared.")


def _closer(res, det, cant_travel=False) -> str:
    ranked, ex = res["ranked"], res["explanations"]
    first = ex[0]
    near = ranked.sort_values("minutes").head(3)
    out = []
    if cant_travel and res["is_red"]:
        out.append("In a possible emergency, please don't try to travel on your own. **Call 108**: the ambulance is free "
                   "and the crew decides where to take you.")
    elif cant_travel:
        out.append("If travelling is hard: the 108 ambulance is free, and a free eSanjeevani teleconsultation "
                   "(esanjeevani.mohfw.gov.in) needs no travel at all.")
    if near.iloc[0]["facility_id"] == first["facility_id"]:
        out.append(f"**{first['name']}** is already the closest of the {len(ranked)} facilities I ranked, "
                   f"at about {first['minutes']:.0f} minutes.")
        near = near.iloc[1:]
        lead = "The next closest are:"
    else:
        lead = "Closer places exist, but they ranked lower because of what their own text does or does not state:"
    items = []
    for _, r in near.iterrows():
        why = ""
        if "tier" in r and pd.notna(r.get("tier")):
            miss = list(r["missing"]) if "missing" in r and r["missing"] is not None else []
            why = f" {ui.TIER_SHORT.get(int(r['tier']))}" + (f"; not stated: {', '.join(_sl(m).lower() for m in miss)}" if len(miss) else "") + "."
        elif "suitable" in r and not bool(r["suitable"]):
            why = f" Not a good fit: {r.get('unsuitable_why')}."
        items.append(f"- **{r['name']}**: about {r['minutes']:.0f} min ({r['km']:.0f} km), ranked #{int(r['rank'])}.{why}")
    if items:
        out.append(lead + "\n" + "\n".join(items))
    if res["is_red"]:
        out.append("A closer place can still be right for first aid on the way. Tell the 108 crew about it and let them decide.")
    return "\n\n".join(out)


def _unknown(res, det) -> str:
    return ("Here is what I **don't** know, for every facility listed:\n"
            "- whether the service is open or working today\n- opening hours, unless the facility itself says 24x7\n"
            "- cost, free beds and waiting time\n- whether the phone number still works\n\n"
            "What I do know is only what each facility **says about itself** in its listing. We hand-checked 60 such "
            "statements against the source text: 20 of 20 that were stated in two or more places matched, 16 of 20 stated "
            "once, and 7 of 20 that were only a label. That measures how well we read the text, not whether the service "
            "exists. So please phone before you travel.")


def _phone(res, det) -> str:
    items = []
    for e in res["explanations"][:3]:
        d = det.loc[e["facility_id"]] if e["facility_id"] in det.index else None
        p = d["phone"] if d is not None and isinstance(d["phone"], str) and d["phone"].strip() else None
        items.append(f"- **{e['name']}**: {p + ' (number not checked)' if p else 'no phone number in the data'}")
    return "Phone numbers from the listings:\n" + "\n".join(items) + "\n\nIn an emergency call **108** first."


def _hours(res, det) -> str:
    items = []
    for e in res["explanations"][:3]:
        d = det.loc[e["facility_id"]] if e["facility_id"] in det.index else None
        items.append(f"- **{e['name']}**: " + ("says it is open 24x7 (its own claim)" if d is not None and d["says_24x7"]
                                               else "opening hours unknown"))
    return ("I don't know what is open right now. This is what the listings say:\n" + "\n".join(items)
            + "\n\nPlease phone first.")


def _no_diagnosis(res) -> str:
    how = ("I treated it as a possible emergency, to be safe. **Call 108.**" if res["is_red"] else
           "I did not treat it as an emergency, but if it gets worse or you are worried, call 108 or see a doctor today.")
    return "I can't tell you what it is or how serious it is. Only a doctor who examines you can. " + how


INTENTS = [
    (re.compile(r"serious|diagnos|what (do|did) i have|what is (it|wrong)|am i (ok|okay|dying)|should i worry", re.I), "nodx"),
    (re.compile(r"compare|top (3|three)|side by side|difference|versus|\bvs\b|which is better", re.I), "compare"),
    (re.compile(r"can'?t travel|cannot travel|too far|no (car|vehicle|transport|money)|can'?t (go|reach)|walk", re.I), "cant"),
    (re.compile(r"clos(er|est)|near(er|est)|shorter", re.I), "closer"),
    (re.compile(r"phone|number|contact|\bcall\b", re.I), "phone"),
    (re.compile(r"\bopen\b|24|hours|night|sunday", re.I), "hours"),
    (re.compile(r"don'?t (we|you) know|not know|unknown|verif|trust|sure|certain|reliab|accurate|true\b|confiden", re.I), "unknown"),
    (re.compile(r"second|2nd|#2|option 2|number 2", re.I), "why2"),
    (re.compile(r"third|3rd|#3|option 3|number 3", re.I), "why3"),
    (re.compile(r"\bwhy\b|how did you|reason|explain|pick|chose|choose", re.I), "why"),
]


def _scripted(intent, res, det) -> str:
    return {"nodx": lambda: _no_diagnosis(res), "compare": lambda: _compare(res, det),
            "cant": lambda: _closer(res, det, True), "closer": lambda: _closer(res, det),
            "phone": lambda: _phone(res, det), "hours": lambda: _hours(res, det),
            "unknown": lambda: _unknown(res, det), "why2": lambda: _why(res, det, 1),
            "why3": lambda: _why(res, det, 2), "why": lambda: _why(res, det)}[intent]()


# ----------------------------------------------------------------------------
# one turn
# ----------------------------------------------------------------------------
def _handle(text, claims, districts, origin, opts, forced_symptoms=None):
    """Append the user message and the reply to the conversation."""
    ss = st.session_state
    msgs = ss["pc_msgs"]
    msgs.append({"role": "user", "text": text})
    cur = ss.get("pc_result")
    intent = next((name for rx, name in INTENTS if rx.search(text)), None)
    symptoms = forced_symptoms if forced_symptoms is not None else _symptoms_in(text)
    question_like = text.strip().endswith("?") or bool(re.match(r"\s*(why|what|which|how|is there|compare|can you)\b", text, re.I))

    medical = bool(genie_chat._MEDICAL.search(text))
    named = _district_in(text, districts)
    if named:
        ss["pc_place_text"] = named
    here = named or (None if ss.get("pc_geo") else ss.get("pc_place_text")) or origin

    # 0. a named service ("I also need a C-section", "where can I get an MRI") -> search for it from the same place
    services = _services_in(text)
    if services and not forced_symptoms and not set(symptoms) - {"pregnant"} and not COUNTING.search(text):
        if here is None:
            msgs.append({"role": "assistant", "text": "Tell me where you are first: pick a district or PIN code under "
                         "**Change location**, then ask again. **If this is an emergency, call 108 now.**"})
            return
        found, parts = [], []
        for code in services[:2]:
            svc = _service_search(claims, code, here)
            if cur and cur["res"]["explanations"]:
                ids = [e["facility_id"] for e in cur["res"]["explanations"][:3]]
                svc["stated_ids"] = claims.loc[(claims["service"] == code) & claims["facility_id"].isin(ids), "facility_id"].unique().tolist()
            found.append(svc)
            prev = next((v for v in (ss.get("pc_svc") or []) if v["service"] == code
                         and abs(v["place"]["lat"] - here["lat"]) < 1e-6 and abs(v["place"]["lon"] - here["lon"]) < 1e-6), None)
            same_place = bool(cur) and abs(cur["search"]["lat"] - here["lat"]) < 1e-6 and abs(cur["search"]["lon"] - here["lon"]) < 1e-6
            parts.append(_service_again(svc) if prev is not None and len(svc["rows"]) else
                         _service_text(svc, cur if same_place else None, text, turn=len(msgs)))
        ss["pc_svc"] = found
        ss["pc_focus"] = "svc"
        ss["pc_panel"] = {"kind": "svc"}
        ss["pc_qa"] = []
        msgs.append({"role": "assistant", "text": "\n\n".join(parts), "svc": True})
        return

    # 1. symptoms -> recommender (always wins, unless it is clearly a question about the current result)
    if forced_symptoms or (symptoms and not (cur and intent and question_like)) or \
            (medical and not symptoms and not (cur and intent)):
        place = here
        if place is None:
            msgs.append({"role": "assistant", "text": "I need to know where you are first. Pick your district or PIN "
                         "code under **Change location**, then tell me again. **If this is an emergency, call 108 now.**"})
            return
        raw = list(symptoms) if symptoms else [text]
        if cur and re.search(r"\balso\b|as well|and now", text, re.I):
            raw = list(dict.fromkeys(cur["search"]["symptoms"] + raw))
        search = {"symptoms": raw, "transport": opts["transport"],
                  "pregnant": opts["pregnant"] or bool(re.search(r"pregnan", text, re.I)) or "pregnant" in raw,
                  "tele": False, "onset_min": opts["onset_min"], **place}
        try:
            res = run_symptom_search(claims, search)
        except Exception as e:
            msgs.append({"role": "assistant", "text": f"Something went wrong while ranking hospitals ({type(e).__name__}). "
                         "**If this is an emergency, call 108 now.** Otherwise please try again."})
            return
        ids = [e["facility_id"] for e in res["explanations"]]
        if res["ranked"] is not None and len(res["ranked"]):
            ids = list(dict.fromkeys(ids + res["ranked"].sort_values("minutes").head(3)["facility_id"].tolist()))
        det = _details(claims, ids)
        ss["pc_result"] = {"res": res, "search": search, "det": det}
        ss["pc_panel"] = {"kind": "rec"}
        ss["pc_focus"] = "rec"
        ss["pc_qa"] = []
        msgs.append({"role": "assistant", "text": _result_text(res, search, det), "red": bool(res["is_red"]),
                     "alerts": [] if res["is_red"] else [a["text"] for a in res["alerts"] if a["level"] == "warning"][:1]})
        return

    # 2a. a follow-up while a service list is on screen
    data_q = bool(DATA_WORDS.search(text))
    svcs = ss.get("pc_svc")
    if svcs and ss.get("pc_focus") == "svc" and (intent or not data_q) and intent != "nodx":
        answer, source = None, "scripted"
        if llm_explain.enabled() and intent not in ("phone", "hours"):
            # only the list on screen: "those" and "they" mean these facilities, not the earlier symptom result
            facts = {"the_list_on_screen": [_svc_facts(v) for v in svcs],
                     "emergency": bool(cur and cur["res"]["is_red"]),
                     "note": "'those', 'they' and 'these' in the question mean the options in the_list_on_screen."}
            with st.spinner("Reading the results..."):
                answer = llm_explain.explain(text, facts, ss.get("pc_qa"))
            source = "model" if answer else source
        if not answer:
            rows = svcs[0]["rows"]
            if intent == "phone" and len(rows):
                answer = "Phone numbers from the listings:\n" + "\n".join(
                    f"- **{r['name']}**: {(_row_phone(r) + ' (number not checked)') if _row_phone(r) else 'no phone number in the data'}"
                    for _, r in rows.iterrows())
            elif intent == "hours" and len(rows) and not (rows["hours_claim"] == "24x7").any():
                answer = (f"I can't tell you that. None of these {len(rows)} listings says anything about its opening hours, "
                          "so I don't know which is open at night. The quickest way to find out is to ring the nearest one, **"
                          + str(rows.sort_values("km").iloc[0]["name"]) + "**"
                          + (f", on {_row_phone(rows.sort_values('km').iloc[0])}" if _row_phone(rows.sort_values("km").iloc[0]) else "") + ".")
            elif intent == "hours" and len(rows):
                answer = "I don't know what is open right now. This is what the listings say:\n" + "\n".join(
                    f"- **{r['name']}**: {'says it is open 24x7 (its own claim)' if r.get('hours_claim') == '24x7' else 'opening hours unknown'}"
                    for _, r in rows.iterrows()) + (
                        "\n\nNone of these listings says anything about its hours, so I can't tell you which is open at night. "
                        "Please phone first." if not (rows["hours_claim"] == "24x7").any() else
                        "\n\nThat is each facility's own claim about the whole place, not about " + svcs[0]["label"]
                        + " specifically, so phone and ask.")
            elif intent in ("closer", "cant") and len(rows):
                n = rows.sort_values("km").iloc[0]
                answer = (f"The closest listing that mentions {svcs[0]['label']} is **{n['name']}**, about {n['km']:.0f} km away in a "
                          f"straight line ({HOW.get(n['claim_strength'])}). The list is ordered with direct statements first, then by distance.")
            elif intent in ("why", "why2", "why3", "compare", "unknown") and len(rows):
                answer = (f"The {svcs[0]['label']} list is ordered simply: facilities whose own text states it directly come first, "
                          "nearest first, then those with only a label. It says nothing about quality, cost or whether the "
                          "service is running today, because the data has none of that. Please phone before you travel.")
        if answer:
            answer = _with_108(answer, bool(cur and cur["res"]["is_red"]))
            ss.setdefault("pc_qa", []).append((text, answer))
            msgs.append({"role": "assistant", "text": answer, "ai": source == "model"})
            return

    # 2. a follow-up about the current result
    if cur and (intent or not data_q):
        res, det = cur["res"], cur["det"]
        if res["explanations"]:
            answer, source = None, "scripted"
            if intent:
                answer = _scripted(intent, res, det)
            if llm_explain.enabled() and intent not in ("nodx",):
                with st.spinner("Reading the results..."):
                    said = llm_explain.explain(text, _no_conditions(_facts(res, cur["search"], det), res), ss.get("pc_qa"))
                if said:
                    answer, source = (said if not intent or intent in ("why", "why2", "why3", "closer", "cant") else answer), "model"
                    if answer is not said:
                        source = "scripted"
            if answer:
                answer = _with_108(answer, bool(res["is_red"]))
                ss.setdefault("pc_qa", []).append((text, answer))
                msgs.append({"role": "assistant", "text": answer, "red": bool(res["is_red"]) and intent in ("cant", "nodx"),
                             "ai": source == "model"})
                ss["pc_panel"], ss["pc_focus"] = {"kind": "rec"}, "rec"
                return

    # 3. a question about the data -> Genie
    if genie_chat.enabled() and not medical:
        with st.spinner("Looking that up in the facility data..."):
            try:
                turn = genie_chat._ask(text)
            except Exception as e:
                turn = {"error": f"The data assistant did not answer ({type(e).__name__})."}
        body = "\n\n".join(turn.get("text") or []) or turn.get("description") or \
            ("Here is what the data shows. The table is beside this chat." if turn.get("table") is not None else "")
        if turn.get("error") and not body:
            body = "I could not get an answer from the data just now. Please try rephrasing."
        msgs.append({"role": "assistant", "text": body or "I found nothing for that. Try asking in another way.",
                     "genie": True})
        if turn.get("table") is not None or turn.get("sql"):
            ss["pc_panel"] = {"kind": "genie", "turn": turn, "q": text}
        return

    msgs.append({"role": "assistant", "text": "I can help in two ways. Tell me **what is wrong** (for example "
                 "“chest pain and sweating”) and I will find hospitals near you. Or, after I have suggested hospitals, ask "
                 "me why, or to compare them. **If it feels like an emergency, call 108 now.**"})


# ----------------------------------------------------------------------------
# the evidence panel
# ----------------------------------------------------------------------------
def _first_card(e, d):
    facts = [(f"{e['minutes']:.0f} min", "estimated travel time", False), (f"{e['km']:.0f} km", "straight line", False),
             (f"{e['care_level']}", "care level, 1 to 5", False)]
    if d is not None:
        if d["says_24x7"]:
            facts.append(("24x7", "open, by its own claim", False))
        if pd.notna(d["beds"]):
            facts.append((f"{int(d['beds'])}", "beds, as listed", False))
    fh = "".join(f"<div class='vf-fact'><div class='v{' small' if s else ''}'>{ui.esc(v)}</div><div class='l'>{ui.esc(l)}</div></div>"
                 for v, l, s in facts)
    quotes = "".join(f"<blockquote>“{ui.esc(s['quote'])}”<br><span class='vf-note'>{ui.esc(_sl(s['service']))}, "
                     f"{ui.esc(STRENGTH_SHORT.get(s['claim_strength']))}</span></blockquote>"
                     for s in _main_part(e)["services"][:2] if s.get("quote"))
    where = f"{ui.esc(d['district'] or '')}, {ui.esc(str(d['state'] or '').title())}" if d is not None else ""
    phone = ""
    if d is not None and isinstance(d["phone"], str) and d["phone"].strip():
        tel = "".join(ch for ch in d["phone"] if ch.isdigit() or ch == "+")
        phone = (f"<div><a href='tel:{tel}' class='call'>"
                 f"Call before you go: {ui.esc(d['phone'])}</a> <span class='vf-note'>(number not checked)</span></div>")
    st.markdown(
        f"<div class='vf-card'><div class='vf-note'>First choice</div><div class='name'>{ui.esc(e['name'])}</div>"
        f"<div class='where'>{where}</div><div style='margin-top:.45rem'>{ui.tier_chip(e.get('tier'))}</div>"
        f"<div class='vf-facts'>{fh}</div>{quotes}{phone}</div>", unsafe_allow_html=True)


def _time_chart(ex):
    df = pd.DataFrame([{"Facility": f"{e['rank']}. {e['name'][:34]}", "Minutes": round(e["minutes"]),
                        "What its text states": ui.TIER_SHORT.get(e.get("tier"), ui.TIER_SHORT[None]),
                        "km": round(e["km"])} for e in ex])
    keys = [k for k in (1, 2, 3, 4, 5, None) if ui.TIER_SHORT[k] in set(df["What its text states"])]
    base = alt.Chart(df).encode(y=alt.Y("Facility:N", sort=None, title=None, axis=alt.Axis(labelLimit=250, labelFontSize=12)),
                                x=alt.X("Minutes:Q", title="Estimated minutes to reach (shorter is sooner)"))
    bars = base.mark_bar().encode(
        color=alt.Color("What its text states:N",
                        scale=alt.Scale(domain=[ui.TIER_SHORT[k] for k in keys], range=[ui.TIER_COLOUR[k] for k in keys]),
                        legend=(alt.Legend(orient="bottom", title=None, columns=1, labelLimit=300) if len(keys) > 1 else None)),
        tooltip=["Facility", "Minutes", "km", "What its text states"])
    text = base.mark_text(align="left", dx=4, fontSize=12, color=ui.ink()).encode(text=alt.Text("Minutes:Q", format=".0f"))
    st.altair_chart(alt.layer(bars, text).properties(height=alt.Step(30)), use_container_width=True)


def _evidence_table(ex):
    services = list(dict.fromkeys(s["service"] for e in ex for s in _main_part(e)["services"]))
    if not services:
        return
    st.markdown("<div class='vf-panel-title'>What each one says it offers</div>", unsafe_allow_html=True)
    head = "".join(f"<th>{ui.esc(_sl(s))}</th>" for s in services)
    body = ""
    for e in ex:
        have = {s["service"]: s["claim_strength"] for s in _main_part(e)["services"]}
        cells = ""
        for s in services:
            col, lab = STRENGTH_DOT.get(have.get(s), STRENGTH_DOT[None])
            cells += f"<td><span class='vf-tag'><span class='vf-sw' style='background:{col}'></span>{lab}</span></td>"
        body += f"<tr><td><b>{e['rank']}. {ui.esc(e['name'][:40])}</b></td>{cells}</tr>"
    st.markdown(f"<table class='vf-ev'><tr><th>Facility</th>{head}</tr>{body}</table>", unsafe_allow_html=True)
    st.caption("How often each facility's own text states the service. A statement is a claim, not a confirmation "
               "that the service exists or is open.")


def _panel_rec(cur, results_map):
    res, search, det = cur["res"], cur["search"], cur["det"]
    ex = res["explanations"]
    if not ex:
        st.info("No facility with a usable map location was found nearby. Call 108.")
        return
    e = ex[0]
    _first_card(e, det.loc[e["facility_id"]] if e["facility_id"] in det.index else None)
    if len(ex) > 1:
        st.markdown("<div class='vf-panel-title'>How long to reach each option</div>", unsafe_allow_html=True)
        _time_chart(ex)
        _evidence_table(ex)
    st.markdown("<div class='vf-panel-title'>Where they are</div>", unsafe_allow_html=True)
    results_map(res["ranked"].head(len(ex)), {**search, "radius_km": min(res["radius"], 150)}, height=300)
    with st.expander("Every option in full, with quotes"):
        rows = res["ranked"].set_index("facility_id")
        for x in ex:
            hospital_card(x, rows.loc[x["facility_id"]], f"pc_{x['facility_id']}")
    st.caption(f"Ranked {len(res['ranked'])} facilities within {res['radius']:.0f} km of {search['label']}. Travel times "
               "are estimates from straight-line distance, not road routes. No quality rating or cost is shown because "
               "the data has none. " + (search.get("approx") or ""))


def _panel_svc(svcs, results_map):
    for i, svc in enumerate(svcs):
        rows, place = svc["rows"], svc["place"]
        if not len(rows):
            st.markdown(f"<div class='vf-empty'>No listing in this data mentions {ui.esc(svc['label'])} with a usable map location.</div>",
                        unsafe_allow_html=True)
            continue
        r = rows.iloc[0]
        facts = [(f"{r['km']:.0f} km", "straight line"), (f"{int(r['care_level'])}" if pd.notna(r.get("care_level")) else "?", "care level, 1 to 5")]
        if r.get("hours_claim") == "24x7":
            facts.append(("24x7", "open, by its own claim"))
        if pd.notna(r.get("capacity_beds")):
            facts.append((f"{int(r['capacity_beds'])}", "beds, as listed"))
        fh = "".join(f"<div class='vf-fact'><div class='v'>{ui.esc(v)}</div><div class='l'>{ui.esc(l)}</div></div>" for v, l in facts)
        q = r.get("supporting_quote")
        quote = (f"<blockquote>“{ui.esc(str(q)[:220])}”<br><span class='vf-note'>{ui.esc(svc['label'])}, "
                 f"{ui.esc(HOW.get(r['claim_strength'], ''))}</span></blockquote>") if isinstance(q, str) and q.strip() else ""
        ph = _row_phone(r)
        phone = ""
        if ph:
            tel = "".join(ch for ch in ph if ch.isdigit() or ch == "+")
            phone = (f"<div><a href='tel:{tel}' class='call'>Call before you go: {ui.esc(ph)}</a> "
                     "<span class='vf-note'>(number not checked)</span></div>")
        st.markdown(
            f"<div class='vf-card'><div class='vf-note'>Nearest listing that states {ui.esc(svc['label'])}</div>"
            f"<div class='name'>{ui.esc(r['name'])}</div>"
            f"<div class='where'>{ui.esc(str(r.get('district') or ''))}, {ui.esc(str(r.get('state') or '').title())}</div>"
            f"<div class='vf-facts'>{fh}</div>{quote}{phone}</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='vf-panel-title'>Who states {ui.esc(svc['label'])}, nearest first</div>", unsafe_allow_html=True)
        body = ""
        for _, x in rows.iterrows():
            col, lab = STRENGTH_DOT.get(x["claim_strength"], STRENGTH_DOT[None])
            body += (f"<tr><td><b>{int(x['rank'])}. {ui.esc(str(x['name'])[:40])}</b></td>"
                     f"<td class='num'>{x['km']:.0f} km</td>"
                     f"<td><span class='vf-tag'><span class='vf-sw' style='background:{col}'></span>{lab}</span></td></tr>")
        st.markdown(f"<table class='vf-ev'><tr><th>Facility</th><th>Distance</th><th>How its text states it</th></tr>{body}</table>",
                    unsafe_allow_html=True)
        st.markdown("<div class='vf-panel-title'>Where they are</div>", unsafe_allow_html=True)
        far = float(rows["km"].max())
        results_map(rows, {"lat": place["lat"], "lon": place["lon"], "radius_km": max(20.0, far * 1.2)}, height=280)
        st.caption(f"{svc['n_direct']} listings state {svc['label']} directly within {svc['radius']} km of {place['label']} "
                   f"({svc['n_any']} counting labels). Direct statements come first, then distance. A statement is a claim, "
                   "not a confirmation that the service exists or is open. " + (place.get("approx") or ""))


def _panel_genie(p):
    turn = p["turn"]
    st.markdown(f"<div class='vf-card'><div class='vf-note'>From the facility data</div>"
                f"<div class='name' style='font-size:1.02rem'>{ui.esc(p['q'])}</div></div>", unsafe_allow_html=True)
    df = turn.get("table")
    if df is not None and len(df):
        if len(df) > 1 and df.shape[1] >= 2 and pd.api.types.is_numeric_dtype(df.iloc[:, -1]) \
                and not pd.api.types.is_numeric_dtype(df.iloc[:, 0]):
            d2 = df.iloc[:, [0, -1]].head(15)
            d2.columns = ["label", "value"]
            st.altair_chart(alt.Chart(d2).mark_bar(color=ui.SERIES).encode(
                y=alt.Y("label:N", sort="-x", title=None, axis=alt.Axis(labelLimit=220)),
                x=alt.X("value:Q", title=str(df.columns[-1])), tooltip=["label", "value"]).properties(
                    height=40 + 24 * len(d2)), use_container_width=True)
        st.dataframe(df, hide_index=True, use_container_width=True)
    elif df is not None:
        st.info("The query ran but returned no rows.")
    if turn.get("sql"):
        with st.expander("How this was computed (the SQL that ran)"):
            st.code(turn["sql"], language="sql")
    if turn.get("error"):
        st.warning(turn["error"])
    who = ui.signed_in_user()
    st.caption("Written by an AI data assistant (Databricks Genie): check the SQL before relying on it. "
               + (f"You are signed in as {who}. " if who else "")
               + "Queries run with the app's own service account, not your personal permissions. " + genie_chat.CAVEAT)


# ----------------------------------------------------------------------------
# the view
# ----------------------------------------------------------------------------
_GEO_JS = """
export default function(component) {
  const { setStateValue } = component;
  if (!navigator.geolocation) { setStateValue('pos', {error: 'unsupported'}); return; }
  navigator.geolocation.getCurrentPosition(
    (p) => setStateValue('pos', {lat: p.coords.latitude, lon: p.coords.longitude, acc: p.coords.accuracy}),
    (e) => setStateValue('pos', {error: e.code === 1 ? 'denied' : 'unavailable'}),
    {enableHighAccuracy: false, timeout: 12000, maximumAge: 300000});
}
"""
try:
    _geolocate = st.components.v2.component("vf_geolocate", js=_GEO_JS)
except Exception:      # older Streamlit: the button is simply not offered
    _geolocate = None
_ADDR_JS = """
export default function(component) {
  const { setStateValue, data } = component;
  const q = data && data.q;
  if (!q) return;
  fetch('https://nominatim.openstreetmap.org/search?format=jsonv2&countrycodes=in&limit=1&q=' + encodeURIComponent(q))
    .then((r) => r.json())
    .then((a) => setStateValue('hit', a.length ? {lat: +a[0].lat, lon: +a[0].lon, name: a[0].display_name, q: q}
                                              : {error: 'notfound', q: q}))
    .catch(() => setStateValue('hit', {error: 'failed', q: q}));
}
"""
try:
    _geocode = st.components.v2.component("vf_geocode", js=_ADDR_JS)
except Exception:
    _geocode = None


def _geocode_server(q: str):
    """Fallback when the browser could not reach the geocoder: ask it from the app instead."""
    import json
    import urllib.parse
    import urllib.request
    url = ("https://nominatim.openstreetmap.org/search?format=jsonv2&countrycodes=in&limit=1&q="
           + urllib.parse.quote(q))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "facility-finder-prototype/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            a = json.loads(r.read().decode())
        return {"lat": float(a[0]["lat"]), "lon": float(a[0]["lon"]), "name": a[0].get("display_name", q), "q": q} if a \
            else {"error": "notfound", "q": q}
    except Exception:
        return {"error": "failed", "q": q}


def _use_my_address(districts):
    """Turn a typed address into a point (OpenStreetMap). The address is not stored."""
    ss = st.session_state
    q = ss.get("pc_addr_ask")
    if not q:
        return
    hit = None
    if _geocode is not None:
        res = _geocode(key=f"pc_addr_c{ss.get('pc_addr_n', 0)}", data={"q": q}, on_hit_change=lambda: None)
        hit = getattr(res, "hit", None)
        if not hit:
            st.caption("Looking up that address...")
            return
    if hit is None or hit.get("error") == "failed":
        hit = _geocode_server(q)
    ss["pc_addr_ask"] = None
    err = hit.get("error")
    if not err and not (6.0 <= float(hit["lat"]) <= 37.5 and 68.0 <= float(hit["lon"]) <= 97.5):
        err = "notfound"
    if err:
        ss["pc_geo_msg"] = ("I could not find that address in India. Try adding the area, city and state, or use a PIN code."
                            if err == "notfound" else
                            "The address lookup could not be reached just now, so the district below is used. A PIN code works without it.")
    else:
        lat, lon = float(hit["lat"]), float(hit["lon"])
        near = _nearest_district(lat, lon, districts)
        short = ", ".join(str(hit.get("name") or q).split(", ")[:3])
        ss["pc_geo"] = {"lat": lat, "lon": lon, "district": None if near is None else near["district_name"],
                        "label": f"your address ({short})",
                        "approx": "Distances are measured from the point OpenStreetMap found for the address you typed."}
        ss["pc_geo_msg"] = None
    st.rerun()


GEO_FAIL = {"denied": "Location permission was not given, so the district below is used. You can allow location for this "
                      "site in your browser and try again.",
            "unavailable": "Your device could not work out its location, so the district below is used.",
            "unsupported": "This browser cannot share a location, so the district below is used.",
            "outside": "Your location is outside India, which is all this data covers, so the district below is used."}


def _nearest_district(lat, lon, districts):
    if districts is None or not len(districts):
        return None
    d2 = (districts["centre_lat"] - lat) ** 2 + ((districts["centre_lon"] - lon) * 0.9) ** 2
    return districts.loc[d2.idxmin()]


def _use_my_location(districts):
    """Ask the browser for the device location. The coordinates stay in this session and are never saved."""
    ss = st.session_state
    if not ss.get("pc_geo_ask"):
        return
    res = _geolocate(key=f"pc_geo_c{ss.get('pc_geo_n', 0)}", on_pos_change=lambda: None)
    pos = getattr(res, "pos", None)
    if not pos:
        st.caption("Waiting for your browser to share your location. Choose Allow if it asks.")
        return
    ss["pc_geo_ask"] = False
    err = pos.get("error")
    if not err and not (6.0 <= float(pos["lat"]) <= 37.5 and 68.0 <= float(pos["lon"]) <= 97.5):
        err = "outside"
    if err:
        ss["pc_geo"], ss["pc_geo_msg"] = None, GEO_FAIL.get(err, GEO_FAIL["unavailable"])
    else:
        lat, lon = float(pos["lat"]), float(pos["lon"])
        near = _nearest_district(lat, lon, districts)
        acc = float(pos.get("acc") or 0)
        ss["pc_geo"] = {"lat": lat, "lon": lon, "district": None if near is None else near["district_name"],
                        "label": "your current location" + (f" (near {near['district_name']}, {near['state_name']})" if near is not None else ""),
                        "approx": f"Your device reports this position to within about {acc / 1000:.1f} km." if acc else None}
        ss["pc_geo_msg"] = None
    st.rerun()


def _controls(districts, location_input, started):
    """Location and optional details. Same widgets on the start screen and beside the chat."""
    ss = st.session_state
    c0, c1, c2 = st.columns([1, 1, 1]) if _geolocate else (None, *st.columns([1, 1]))
    if c0 is not None and c0.button("Use my location", icon=":material/my_location:", key="pc_geo_btn", use_container_width=True):
        ss["pc_geo_ask"], ss["pc_geo_n"], ss["pc_geo_msg"] = True, ss.get("pc_geo_n", 0) + 1, None
    with c1.popover("Change location", use_container_width=True):
        if ss.get("pc_geo") and st.button("Stop using this point", key="pc_geo_off"):
            ss["pc_geo"] = None
            st.rerun()
        addr = st.text_input("Your address", key="pc_addr", placeholder="House or street, area, city")
        st.caption("The address is sent to OpenStreetMap to find its coordinates. It is not saved.")
        if st.button("Use this address", key="pc_addr_btn", disabled=not (addr or "").strip()):
            ss["pc_addr_ask"], ss["pc_addr_n"], ss["pc_geo_msg"] = addr.strip()[:200], ss.get("pc_addr_n", 0) + 1, None
        st.caption("Or pick a district, PIN code or coordinates.")
        origin = location_input("pc", districts)
    with c2.popover("Travel and timing", use_container_width=True):
        transport = st.multiselect("How can you travel?", list(TRANSPORT_LABELS), format_func=TRANSPORT_LABELS.get,
                                   default=["ambulance_108", "auto_taxi"], key="pc_tr")
        onset = st.selectbox("When did it start?", list(ONSET_CHOICES), key="pc_onset")
        pregnant = st.checkbox("Pregnant", key="pc_preg")
    if _geolocate:
        _use_my_location(districts)
    _use_my_address(districts)
    if ss.get("pc_geo_msg"):
        st.caption(ss["pc_geo_msg"])
    if ss.get("pc_geo"):
        origin = ss["pc_geo"]
    return origin, {"transport": transport, "onset_min": ONSET_CHOICES[onset], "pregnant": pregnant}


def _messages():
    for m in st.session_state["pc_msgs"]:
        with st.chat_message(m["role"]):
            if m["role"] == "user":
                st.markdown(f"<span class='vf-me'></span>{ui.esc(m['text'])}", unsafe_allow_html=True)
                continue
            if m.get("red"):
                st.markdown("<div class='vf-redcard'><div class='t'>Call 108 now</div>This could be an emergency. "
                            "The ambulance is free. Call <a href='tel:108'>108</a> or <a href='tel:112'>112</a> "
                            "before anything else. The crew decides where to go.</div>", unsafe_allow_html=True)
            st.markdown(m["text"])
            for a in m.get("alerts", []):
                st.caption(a)
            if m.get("ai"):
                st.caption("Worded by an AI model from the results shown here. Not medical advice.")
            if m.get("genie"):
                st.caption("From the data assistant (Genie). Data exploration, not medical advice.")


def view(claims, districts, location_input, results_map):
    ss = st.session_state
    ss.setdefault("pc_msgs", [])
    if not SVC_LABEL:
        SVC_LABEL.update(dict(zip(claims["service"], claims["service_label"])))
    kb = _kb()
    labels = dict(zip(kb.symptoms["symptom"], kb.symptoms["label"]))
    quick = [s for s in QUICK if s in labels] or list(labels)[:10]
    started = bool(ss["pc_msgs"])
    go = picked = None

    if not started:
        # start screen: the question, the input, nothing in the way
        _, mid, _ = st.columns([1, 4, 1])
        with mid:
            st.markdown("<div class='vf-hero'><h1>What's wrong?</h1><p>Type it or tap below. You get the nearest hospitals "
                        "that say they treat it.</p></div>",
                        unsafe_allow_html=True)
            typed = st.chat_input("For example: chest pain and sweating", key="pc_input")
            picked = st.pills("Or tap what applies", quick, selection_mode="multi", format_func=labels.get,
                              key="pc_pills", label_visibility="collapsed")
            if picked:
                go = st.button(f"Find hospitals for {len(picked)} symptom{'s' if len(picked) > 1 else ''}", type="primary", key="pc_go")
            st.write("")
            origin, opts = _controls(districts, location_input, started)
            where = f"Searching near {_from(origin['label'])}." if origin else "Set your location to search."
            st.markdown(f"<p class='vf-fine'>{ui.esc(where)} Not medical advice. Listings are what hospitals say "
                        "about themselves and are not verified, so phone before you travel.</p>", unsafe_allow_html=True)
    else:
        left, right = st.columns([11, 10], gap="large")
        with left:
            _messages()
            typed = st.chat_input("Ask a question, or describe something else", key="pc_input")
            if ss.get("pc_result") and ss["pc_result"]["res"]["explanations"]:
                sugg = list(FOLLOW_UPS)
                dname = ss["pc_result"]["search"].get("district")
                if genie_chat.enabled() and dname:
                    sugg.append(f"How many facilities in {dname} district claim an ICU?")
                pick = st.pills("Ask about these results", sugg, key=f"pc_sugg_{len(ss['pc_msgs'])}",
                                label_visibility="collapsed")
                if pick:
                    ss["pc_pending"] = pick   # handled at the end of this run; the key changes once the answer is added
            st.write("")
            origin, opts = _controls(districts, location_input, started)
            if st.button("Start again", key="pc_reset"):
                for k in ("pc_msgs", "pc_result", "pc_panel", "pc_qa", "genie_conversation_id", "pc_pills", "pc_svc", "pc_focus", "pc_place_text"):
                    ss.pop(k, None)
                st.rerun()
        with right:
            p = ss.get("pc_panel")
            if p and p["kind"] == "genie":
                _panel_genie(p)
                if ss.get("pc_result") and st.button("Back to the hospital results", key="pc_back"):
                    ss["pc_panel"] = {"kind": "rec"}
                    st.rerun()
            elif p and p["kind"] == "svc" and ss.get("pc_svc"):
                _panel_svc(ss["pc_svc"], results_map)
                if ss.get("pc_result") and st.button("Back to the hospitals for my symptoms", key="pc_back2"):
                    ss["pc_panel"], ss["pc_focus"] = {"kind": "rec"}, "rec"
                    st.rerun()
            elif ss.get("pc_result"):
                _panel_rec(ss["pc_result"], results_map)

    pending = ss.pop("pc_pending", None)
    if go and picked:
        with st.spinner("Finding hospitals near you..."):
            _handle(", ".join(labels[s] for s in picked), claims, districts, origin, opts, forced_symptoms=list(picked))
        st.rerun()
    elif typed or pending:
        with st.spinner("Working on it..."):
            _handle((typed or pending).strip(), claims, districts, origin, opts)
        st.rerun()
