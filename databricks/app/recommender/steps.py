"""The pipeline steps. Each one is `step(state, cfg) -> state`.

A step reads what earlier steps put on `state`, adds its own fields, and
records a one-line summary with `state.log(...)`. To change the model, edit
a step, write a replacement with the same signature, or change the list in
pipeline.py. The fields each step reads and writes are listed in its
docstring so a replacement knows its contract.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from .config import Config
from .knowledge import Knowledge, load


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
@dataclass
class State:
    # inputs
    raw_symptoms: list
    lat: float
    lon: float
    claims: pd.DataFrame
    facilities: pd.DataFrame | None = None
    transport: list | None = None
    patient: dict = field(default_factory=dict)
    telehealth_says_go: bool = False
    time_provider: Callable | None = None
    kb: Knowledge | None = None

    # filled in by the steps
    symptoms: list = field(default_factory=list)
    unmatched: list = field(default_factory=list)
    red_flags: list = field(default_factory=list)
    conditions: pd.DataFrame | None = None
    p_life_threatening: float = 0.0
    is_red: bool = False
    disposition: str = ""
    candidates: pd.DataFrame | None = None
    radius_used_km: float | None = None
    modes_used: list = field(default_factory=list)
    care: dict = field(default_factory=dict)
    ranked: pd.DataFrame | None = None
    alerts: list = field(default_factory=list)
    explanations: list = field(default_factory=list)
    nearest_not_chosen: list = field(default_factory=list)
    trace: list = field(default_factory=list)
    stop: bool = False

    def log(self, step: str, summary: str, **detail: Any) -> None:
        self.trace.append({"step": step, "summary": summary, **detail})

    def alert(self, rule: str, level: str, text: str) -> None:
        self.alerts.append({"rule": rule, "level": level, "text": text})


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0088
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = (np.sin((p2 - p1) / 2) ** 2
         + np.cos(p1) * np.cos(p2) * np.sin((np.radians(lon2) - np.radians(lon1)) / 2) ** 2)
    return 2 * r * np.arcsin(np.sqrt(a))


# ---------------------------------------------------------------------------
# 1. normalise_symptoms
# ---------------------------------------------------------------------------
def normalise_symptoms(s: State, cfg: Config) -> State:
    """reads raw_symptoms, patient; writes symptoms, unmatched, red_flags.

    Accepts symptom codes ('chest_pain') or free text ('my chest hurts');
    free text is matched against the synonyms column of symptoms.csv.
    patient={'pregnant': True} adds the 'pregnant' symptom.
    """
    s.kb = s.kb or load(cfg.knowledge_dir)
    sym = s.kb.symptoms
    codes = set(sym["symptom"])
    found, unmatched = [], []
    raw = list(s.raw_symptoms or [])
    if s.patient.get("pregnant"):
        raw.append("pregnant")
    for item in raw:
        t = str(item).strip().lower()
        if not t:
            continue
        if t.replace(" ", "_") in codes:
            found.append(t.replace(" ", "_"))
            continue
        hits = [r.symptom for r in sym.itertuples()
                if any(syn and (syn in t) for syn in r.synonyms) or r.label.lower() == t]
        if hits:
            found.extend(hits)
        else:
            unmatched.append(item)
    s.symptoms = list(dict.fromkeys(found))
    s.unmatched = unmatched
    s.red_flags = sym.loc[sym["symptom"].isin(s.symptoms) & sym["red_flag"], "symptom"].tolist()
    s.log("normalise_symptoms", f"{len(s.symptoms)} recognised, {len(unmatched)} not recognised",
          symptoms=s.symptoms, unmatched=unmatched, red_flags=s.red_flags)
    return s


# ---------------------------------------------------------------------------
# 2. infer_conditions
# ---------------------------------------------------------------------------
def infer_conditions(s: State, cfg: Config) -> State:
    """reads symptoms; writes conditions (condition, label, p, ...).

    Naive Bayes over the reported symptoms:
        P(c | S)  ∝  prior(c) × Π_{s in S} P(s | c)
    with P(s | c) = the symptom's leak (symptoms.csv, else likelihood_leak)
    for pairs not in likelihood.csv, then
    tempered by posterior_temperature so it isn't overconfident.
    """
    kb = s.kb
    cond = kb.conditions.copy()
    leak = kb.symptoms.set_index("symptom")["leak"].fillna(cfg.likelihood_leak)
    L = kb.likelihood.fillna(leak).clip(1e-6, 1 - 1e-6)
    logp = np.log(cond["prior"].to_numpy(float))
    present = [x for x in s.symptoms if x in L.columns]
    if present:
        logp = logp + np.log(L[present].to_numpy(float)).sum(axis=1)
    if cfg.use_absent_symptoms:
        absent = [x for x in L.columns if x not in present and x != "pregnant"]
        logp = logp + np.log(1 - L[absent].to_numpy(float)).sum(axis=1)
    logp = logp / max(cfg.posterior_temperature, 1e-6)
    p = np.exp(logp - logp.max())
    cond["p"] = p / p.sum()
    cond = cond.sort_values("p", ascending=False).reset_index(drop=True)
    s.conditions = cond
    top = ", ".join(f"{r.label} {r.p:.0%}" for r in cond.head(3).itertuples())
    s.log("infer_conditions", f"most likely: {top}")
    return s


# ---------------------------------------------------------------------------
# 3. assess_severity
# ---------------------------------------------------------------------------
def assess_severity(s: State, cfg: Config) -> State:
    """reads conditions, red_flags; writes conditions.weight, p_life_threatening, is_red."""
    c = s.conditions
    c["weight"] = c["severity"].astype(float) ** cfg.severity_exponent
    s.p_life_threatening = float(c.loc[c["life_threatening"], "p"].sum())
    s.is_red = s.p_life_threatening >= cfg.red_case_threshold or bool(s.red_flags)
    s.log("assess_severity",
          f"P(life-threatening) = {s.p_life_threatening:.1%}; "
          + ("red case" if s.is_red else "not a red case")
          + (f" (red flags: {', '.join(s.red_flags)})" if s.red_flags else ""))
    return s


# ---------------------------------------------------------------------------
# 4. triage_gate
# ---------------------------------------------------------------------------
def triage_gate(s: State, cfg: Config) -> State:
    """reads p_life_threatening, red_flags, telehealth_says_go; writes disposition.

    disposition is 'hospital' or 'telehealth_first'. The hospital list is
    still computed for telehealth_first, as the backup if the doctor says go.
    Set state.stop = True in a replacement step to end the pipeline here.
    """
    why = []
    if s.telehealth_says_go:
        why.append("telehealth doctor advised a hospital visit")
    if s.p_life_threatening >= cfg.telehealth_threshold:
        why.append(f"P(life-threatening) {s.p_life_threatening:.1%} ≥ {cfg.telehealth_threshold:.0%}")
    if cfg.red_flag_forces_hospital and s.red_flags:
        why.append("red-flag symptom")
    if cfg.unrecognised_means_hospital and not s.symptoms:
        why.append("no symptom recognised, so treated as possibly serious")
    s.disposition = "hospital" if why else "telehealth_first"
    s.log("triage_gate", s.disposition + (": " + "; ".join(why) if why else
          f": P(life-threatening) {s.p_life_threatening:.1%} < {cfg.telehealth_threshold:.0%}"))
    return s


# ---------------------------------------------------------------------------
# 5. find_candidates
# ---------------------------------------------------------------------------
FACILITY_COLS = ["facility_id", "name", "latitude", "longitude", "care_level", "location_quality_flag",
                 "district", "state", "facility_type", "level_kind", "quality_score", "contact_phone",
                 "level_confidence", "operator_type"]


def facilities_from_claims(claims: pd.DataFrame) -> pd.DataFrame:
    cols = [c for c in FACILITY_COLS if c in claims.columns]
    return claims[cols].drop_duplicates("facility_id").reset_index(drop=True)


def find_candidates(s: State, cfg: Config) -> State:
    """reads facilities (or claims), lat, lon; writes candidates (+ km), radius_used_km."""
    fac = s.facilities if s.facilities is not None else facilities_from_claims(s.claims)
    ok = fac["latitude"].notna() & fac["longitude"].notna()
    if "location_quality_flag" in fac:
        ok &= fac["location_quality_flag"].isin(cfg.usable_location_flags)
    fac = fac[ok].copy()
    fac["km"] = haversine_km(s.lat, s.lon, fac["latitude"].to_numpy(float), fac["longitude"].to_numpy(float))
    for radius in (cfg.search_radius_km, *cfg.expand_radius_km):
        cand = fac[fac["km"] <= radius]
        if len(cand) >= cfg.min_candidates:
            break
    s.candidates = cand.reset_index(drop=True)
    s.radius_used_km = radius
    s.log("find_candidates", f"{len(cand)} facilities with a usable location within {radius:.0f} km",
          excluded_bad_location=int((~ok).sum()))
    return s


# ---------------------------------------------------------------------------
# 6. travel_time
# ---------------------------------------------------------------------------
def travel_time(s: State, cfg: Config) -> State:
    """reads candidates, transport, is_red; writes candidates.minutes/.mode, modes_used.

    minutes = wait_min + km × circuity / speed_kmh × 60  (straight-line estimate)
    If state.time_provider is set, it replaces the driving part:
        time_provider(lat, lon, candidates_df, mode_row) -> array of minutes
    (e.g. OSRM or Google). The fastest allowed mode is used for each facility.
    """
    tr = s.kb.transport.set_index("mode").copy()
    for mode, over in cfg.transport_overrides.items():
        for k, v in over.items():
            tr.loc[mode, k] = v
    wanted = list(s.transport or cfg.default_transport)
    modes = [m for m in wanted if m in tr.index]
    dropped = []
    if s.is_red:
        dropped = [m for m in modes if not tr.loc[m, "allowed_when_life_threatening"]]
        modes = [m for m in modes if m not in dropped]
    if not modes:
        modes = ["ambulance_108"]
    s.modes_used = modes
    c = s.candidates
    times = {}
    for m in modes:
        row = tr.loc[m]
        if s.time_provider is not None:
            drive = np.asarray(s.time_provider(s.lat, s.lon, c, row), float)
        else:
            drive = c["km"].to_numpy(float) * float(row["circuity"]) / float(row["speed_kmh"]) * 60
        t = float(row["wait_min"]) + drive
        if not pd.isna(row["max_km"]):
            t = np.where(c["km"].to_numpy(float) <= float(row["max_km"]), t, np.inf)
        times[m] = t
    tm = pd.DataFrame(times, index=c.index)
    c["mode"] = tm.idxmin(axis=1) if len(c) else pd.Series(dtype=str)
    c["minutes"] = tm.min(axis=1)
    for m in modes:
        c[f"minutes_{m}"] = tm[m]
    c["mode_label"] = c["mode"].map(tr["label"])
    s.log("travel_time", f"modes considered: {', '.join(modes)}"
          + (f"; not used for a red case: {', '.join(dropped)}" if dropped else "")
          + ("; source: " + ("custom provider" if s.time_provider else "straight-line estimate")))
    return s


# ---------------------------------------------------------------------------
# 7. care_probability
# ---------------------------------------------------------------------------
def p_service_exists(claims: pd.DataFrame, cfg: Config) -> pd.Series:
    """P(the facility really offers the service) for each claim row."""
    p = claims["claim_strength"].map(lambda v: cfg.p_service_by_strength.get(v, cfg.p_service_by_strength[None]))
    p = p.astype(float)
    vs = claims.get("verification_status", pd.Series("", index=claims.index)).fillna("")
    p = np.where(vs.eq("verified"), np.maximum(p, cfg.verified_floor), p)
    p = np.where(vs.eq("externally_supported"), np.minimum(p + cfg.externally_supported_bonus, 0.99), p)
    p = np.where(vs.eq("contradicted"), p * cfg.contradicted_factor, p)
    if "plausibility_flag" in claims:
        flagged = claims["plausibility_flag"].fillna("").astype(str).str.len().gt(0)
        p = np.where(flagged, p * cfg.plausibility_flag_factor, p)
    if "quality_score" in claims and cfg.quality_weight:
        q = pd.to_numeric(claims["quality_score"], errors="coerce").fillna(1.0).clip(0, 1)
        p = p * ((1 - cfg.quality_weight) + cfg.quality_weight * q)
    return pd.Series(p, index=claims.index)


def timeliness(minutes: np.ndarray, tau: float, cfg: Config, late: float | None = None) -> np.ndarray:
    x = np.asarray(minutes, float) / tau
    decay = np.maximum(0.0, 1 - x / 2) if cfg.timeliness_curve == "linear" else np.exp(-x)
    late = cfg.late_care_benefit if late is None or pd.isna(late) else float(late)
    t = late + (1 - late) * np.nan_to_num(decay, nan=0.0)
    return np.where(np.isinf(minutes), 0.0, t)  # unreachable by any allowed mode


def care_probability(s: State, cfg: Config) -> State:
    """reads candidates, conditions, claims; writes care = {
        'q': facility x condition DataFrame of P(adequate care | h, c),
        'capability', 'rung', 'hours', 'timely': the factors, same shape,
        'best_service': facility x condition -> list of (service, p, quote) per requirement,
        'p_service': facility x service matrix}.

    P(care | h, c) = capability × rung factor × hours factor × timeliness
      capability  = Π over the condition's requirements of the best alternative's
                    P(service exists) × credit; 1 if the condition needs no special service
      rung factor = rung_shortfall_factor ** (rungs below min_rung)
      hours       = unknown_hours_factor if life-threatening and no 24x7 claim
      timeliness  = late + (1 - late) × exp(-minutes / tau)   (or linear)
    """
    c = s.candidates
    cond = s.conditions[s.conditions["p"] >= cfg.min_condition_probability].reset_index(drop=True)
    s.conditions["scored"] = s.conditions["condition"].isin(cond["condition"])
    ids = c["facility_id"]
    cl = s.claims[s.claims["facility_id"].isin(ids)].copy()
    cl["p_exists"] = p_service_exists(cl, cfg) if len(cl) else pd.Series(dtype=float)
    services = sorted(set(s.kb.services) | set(cl["service"].unique()))
    p_none = cfg.p_service_by_strength[None]
    P = (cl.pivot_table(index="facility_id", columns="service", values="p_exists", aggfunc="max")
           .reindex(index=ids, columns=services).fillna(p_none))
    has24 = (cl[cl.get("hours_claim", pd.Series("", index=cl.index)).eq("24x7")]
             .groupby("facility_id")["service"].apply(set)) if "hours_claim" in cl else pd.Series(dtype=object)
    quotes = cl.set_index(["facility_id", "service"])["supporting_quote"].to_dict() if "supporting_quote" in cl else {}
    strengths = cl.set_index(["facility_id", "service"])["claim_strength"].to_dict()

    level = pd.to_numeric(c["care_level"], errors="coerce").to_numpy(float)
    minutes = c["minutes"].to_numpy(float)
    cap, rung, hours, timely, best = {}, {}, {}, {}, {}
    for r in cond.itertuples():
        capv = np.ones(len(c))
        picks = [[] for _ in range(len(c))]
        for grp in r.needs:
            alts = [(g, cr) for g, cr in grp if g in P.columns] or [(grp[0][0], grp[0][1])]
            cols = [g for g, _ in alts]
            credit = np.array([cr for _, cr in alts], float)
            raw = np.column_stack([P[g].to_numpy(float) if g in P.columns else np.full(len(c), p_none)
                                   for g in cols])
            sub = raw * credit
            j = sub.argmax(axis=1)
            capv = capv * sub[np.arange(len(c)), j]
            for i, fid in enumerate(ids):
                svc = cols[j[i]]
                picks[i].append((svc, float(raw[i, j[i]]), float(credit[j[i]]),
                                 strengths.get((fid, svc)), quotes.get((fid, svc))))
        short = np.where(np.isnan(level), np.nan, np.maximum(r.min_rung - level, 0))
        rf = np.where(np.isnan(short), cfg.unknown_rung_factor, cfg.rung_shortfall_factor ** np.nan_to_num(short))
        if r.life_threatening and r.needs:
            first = {g for g, _ in r.needs[0]} | {"emergency"}
            ok24 = np.array([bool(has24.get(fid, set()) & first) for fid in ids])
            hf = np.where(ok24, 1.0, cfg.unknown_hours_factor)
        else:
            hf = np.ones(len(c))
        cap[r.condition], rung[r.condition], hours[r.condition] = capv, rf, hf
        timely[r.condition] = timeliness(minutes, float(r.tau_min), cfg, getattr(r, "late_benefit", None))
        best[r.condition] = picks
    mk = lambda d: pd.DataFrame(d, index=c.index)
    q = mk(cap) * mk(rung) * mk(hours) * mk(timely)
    s.care = {"q": q, "capability": mk(cap), "rung": mk(rung), "hours": mk(hours),
              "timely": mk(timely), "best_service": best, "p_service": P.reset_index(drop=True),
              "conditions": cond}
    s.log("care_probability", f"scored {len(c)} facilities against {len(cond)} conditions")
    return s


# ---------------------------------------------------------------------------
# 8. score
# ---------------------------------------------------------------------------
def score(s: State, cfg: Config) -> State:
    """reads care, conditions; writes ranked (candidates sorted by score, + rank).

    score(h) = Σ_c P(c) × weight(c) × P(care | h, c)  /  Σ_c P(c) × weight(c)
    i.e. the expected share of the severity-weighted need this hospital meets.
    """
    cond = s.care["conditions"]
    w = (cond["p"] * cond["weight"]).to_numpy(float)
    q = s.care["q"][cond["condition"]].to_numpy(float)
    c = s.candidates.copy()
    c["need_met"] = q @ w / w.sum() if w.sum() > 0 else 0.0
    c["burden"] = 0.0
    c["score"] = c["need_met"]
    contrib = pd.DataFrame(q * w / max(w.sum(), 1e-12), columns=cond["condition"], index=c.index)
    s.care["contribution"] = contrib
    c = c.sort_values(["score", "minutes"], ascending=[False, True])
    c["rank"] = np.arange(1, len(c) + 1)
    s.ranked = c
    if len(c):
        t = c.iloc[0]
        s.log("score", f"top: {t['name']} (score {t['score']:.3f}, {t['minutes']:.0f} min)")
    else:
        s.log("score", "no candidates to score")
    return s


# ---------------------------------------------------------------------------
# 8b. travel_burden
# ---------------------------------------------------------------------------
def travel_burden(s: State, cfg: Config) -> State:
    """reads ranked, p_life_threatening; writes ranked.burden, ranked.score, re-ranks.

    score = need_met - travel_burden_weight × (1 - P(life-threatening)) × min(minutes, cap) / cap
    Nearly zero in a red case, so it only reorders non-urgent options.
    Remove this step to rank on need_met alone.
    """
    c = s.ranked
    if c is None or c.empty:
        return s
    k = cfg.travel_burden_weight * (1 - s.p_life_threatening)
    c["burden"] = k * np.minimum(c["minutes"].to_numpy(float), cfg.travel_burden_cap_min) / cfg.travel_burden_cap_min
    c["score"] = c["need_met"] - c["burden"]
    c = c.sort_values(["score", "minutes"], ascending=[False, True])
    c["rank"] = np.arange(1, len(c) + 1)
    s.ranked = c
    t = c.iloc[0]
    s.log("travel_burden", f"travel penalty up to {k:.3f}; top now {t['name']} ({t['minutes']:.0f} min)")
    return s


# ---------------------------------------------------------------------------
# 9. safety_rules  (each rule is its own function; see SAFETY_RULES)
# ---------------------------------------------------------------------------
def rule_call_ambulance(s: State, cfg: Config) -> None:
    lines = set(s.conditions.loc[s.conditions["p"] >= 0.05, "ambulance_line"].astype(str))
    num = "102" if lines == {"102"} else ("108 (or 102 for pregnancy and newborns)" if "102" in lines else "108")
    level = "critical" if s.is_red else "info"
    text = (f"Call {num} now. The ambulance is free in most states." if s.is_red
            else f"If things get worse, call {num} (free). Call 112 for any emergency.")
    s.alert("call_ambulance", level, text)


def rule_telehealth_first(s: State, cfg: Config) -> None:
    if s.disposition == "telehealth_first":
        s.alert("telehealth_first", "advice",
                "This sounds unlikely to be an emergency. Try a free eSanjeevani teleconsultation first "
                "(esanjeevani.mohfw.gov.in). If the doctor says to go in, these are the best places.")


def rule_stabilise_and_transfer(s: State, cfg: Config) -> None:
    if not s.is_red or s.ranked is None or s.ranked.empty:
        return
    top = s.ranked.iloc[0]
    if top["minutes"] <= cfg.stabilise_threshold_min:
        return
    P = s.care["p_service"]
    c = s.ranked
    em = P.loc[c.index, "emergency"] if "emergency" in P else pd.Series(0, index=c.index)
    ok = (pd.to_numeric(c["care_level"], errors="coerce") >= cfg.stabilise_min_rung) & (em > cfg.p_service_by_strength[None])
    near = c[ok & (c["minutes"] < top["minutes"])].sort_values("minutes")
    if len(near):
        n = near.iloc[0]
        s.ranked.loc[n.name, "stabilise"] = True
        s.alert("stabilise_and_transfer", "warning",
                f"The best match is about {top['minutes']:.0f} min away. {n['name']} is about "
                f"{n['minutes']:.0f} min away and says it has emergency care: go there first to be "
                "stabilised, then transfer.")


def rule_close_call(s: State, cfg: Config) -> None:
    if s.ranked is not None and len(s.ranked) >= 2:
        a, b = s.ranked.iloc[0], s.ranked.iloc[1]
        if a["score"] - b["score"] < cfg.close_call_margin:
            s.alert("close_call", "info",
                    f"{a['name']} and {b['name']} are nearly tied; call ahead to check which can see you sooner.")


def rule_unrecognised(s: State, cfg: Config) -> None:
    if s.unmatched:
        s.alert("unrecognised_symptoms", "info",
                "Not recognised and not used: " + ", ".join(map(str, s.unmatched))
                + ". If unsure, treat it as an emergency.")


SAFETY_RULES = [rule_call_ambulance, rule_telehealth_first, rule_stabilise_and_transfer,
                rule_close_call, rule_unrecognised]


def safety_rules(s: State, cfg: Config, rules=None) -> State:
    """reads everything; writes alerts (ordered: 108/102 always first)."""
    for rule in (rules or SAFETY_RULES):
        rule(s, cfg)
    s.log("safety_rules", f"{len(s.alerts)} alerts: " + ", ".join(a["rule"] for a in s.alerts))
    return s


# ---------------------------------------------------------------------------
# 10. explain
# ---------------------------------------------------------------------------
FACTOR_PLAIN = {"capability": "whether it offers the services needed",
                "rung": "its care level", "hours": "no 24x7 claim", "timely": "travel time"}


def _why_lower(s: State, idx, top_idx) -> str:
    """Name the factor that costs this facility the most versus the top pick."""
    cond = s.care["conditions"]
    w = (cond["p"] * cond["weight"]).to_numpy(float)
    w = w / max(w.sum(), 1e-12)
    gaps = {}
    for f in ("capability", "rung", "hours", "timely"):
        M = s.care[f][cond["condition"]]
        gaps[f] = float(np.dot(np.log(M.loc[top_idx].to_numpy(float) + 1e-9)
                               - np.log(M.loc[idx].to_numpy(float) + 1e-9), w))
    f = max(gaps, key=gaps.get)
    if "burden" in s.ranked and gaps[f] < 0.05:
        if s.ranked.at[idx, "burden"] - s.ranked.at[top_idx, "burden"] > 0.01:
            return "a longer trip for a case that is probably not urgent"
    if gaps[f] <= 0:
        return "small differences across several factors"
    return FACTOR_PLAIN[f]


def explain(s: State, cfg: Config) -> State:
    """reads ranked, care, conditions; writes explanations (one dict per top_n facility)."""
    if s.ranked is None or s.ranked.empty:
        s.log("explain", "nothing to explain")
        return s
    cond = s.care["conditions"]
    top = s.ranked.head(cfg.top_n)
    top_idx = top.index[0]
    out = []
    for idx, r in top.iterrows():
        parts = []
        for cr in cond.itertuples():
            picks = s.care["best_service"][cr.condition][s.candidates.index.get_loc(idx)]
            parts.append({
                "condition": cr.condition, "label": cr.label, "p_condition": round(float(cr.p), 4),
                "weight": round(float(cr.weight), 3),
                "capability": round(float(s.care["capability"].at[idx, cr.condition]), 3),
                "rung_factor": round(float(s.care["rung"].at[idx, cr.condition]), 3),
                "hours_factor": round(float(s.care["hours"].at[idx, cr.condition]), 3),
                "timeliness": round(float(s.care["timely"].at[idx, cr.condition]), 3),
                "p_care": round(float(s.care["q"].at[idx, cr.condition]), 3),
                "contribution": round(float(s.care["contribution"].at[idx, cr.condition]), 4),
                "services": [{"service": a, "p_exists": round(b, 3), "credit": cr, "claim_strength": st,
                              "quote": qt} for a, b, cr, st, qt in picks],
            })
        main = max(parts, key=lambda p: p["p_condition"] * p["weight"])
        svc_txt = "; ".join(f"{x['service']} ({x['claim_strength'] or 'no claim'})" for x in main["services"]) \
            or "no special service needed"
        lvl = "unknown" if pd.isna(r["care_level"]) else int(r["care_level"])
        sentence = (f"About {r['minutes']:.0f} min by {r['mode_label'].lower()} ({r['km']:.0f} km straight line). "
                    f"For the most important possibility, {main['label'].lower()} "
                    f"({main['p_condition']:.0%} likely), it claims {svc_txt}; care level {lvl}. "
                    f"Overall it meets {r['need_met']:.0%} of the severity-weighted need"
                    + (f", less {r['burden']:.0%} for the length of the trip." if r["burden"] >= 0.005 else "."))
        why_not = None if idx == top_idx else \
            f"Ranked below #1 mainly because of {_why_lower(s, idx, top_idx)}."
        out.append({"rank": int(r["rank"]), "facility_id": r["facility_id"], "name": r["name"],
                    "score": round(float(r["score"]), 4), "need_met": round(float(r["need_met"]), 4),
                    "burden": round(float(r["burden"]), 4), "minutes": round(float(r["minutes"]), 1),
                    "mode": r["mode"], "km": round(float(r["km"]), 1), "care_level": lvl,
                    "stabilise": bool(r.get("stabilise", False) is True),
                    "summary": sentence, "why_lower": why_not, "by_condition": parts})
    # nearest facilities that did NOT make the list, and why
    rest = s.ranked.iloc[cfg.top_n:].sort_values("minutes").head(3)
    s.nearest_not_chosen = [{"name": r["name"], "minutes": round(float(r["minutes"]), 1),
                             "rank": int(r["rank"]), "why": f"Not in the top {cfg.top_n} mainly because of "
                             f"{_why_lower(s, idx, top_idx)}."} for idx, r in rest.iterrows()]
    s.explanations = out
    s.log("explain", f"explained top {len(out)} and {len(s.nearest_not_chosen)} nearer facilities left out")
    return s
