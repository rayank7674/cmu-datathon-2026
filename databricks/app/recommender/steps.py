"""The pipeline steps. Each one is `step(state, cfg) -> state`.

A step reads what earlier steps put on `state`, adds its own fields, and
records a one-line summary with `state.log(...)`. To change the model, edit
a step, write a replacement with the same signature, or change the list in
pipeline.py. The fields each step reads and writes are listed in its
docstring so a replacement knows its contract.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from .config import Config
from .knowledge import Knowledge, load, reliability_estimate


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
    onset_min: float = 0.0          # minutes since the problem started
    time_provider: Callable | None = None
    kb: Knowledge | None = None

    # filled in by the steps
    symptoms: list = field(default_factory=list)
    unmatched: list = field(default_factory=list)
    unclear: bool = False           # nothing in the description was recognised
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
    excluded: list = field(default_factory=list)   # facilities not ranked in a red case, with the reason
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
# Free text is keyword matching, made a little safer: a phrase that is negated ("no chest pain")
# or clearly history ("a seizure last year") is not counted. Anything ambiguous IS counted (cautious).
_NEGATION = re.compile(r"(?<!\w)(no|not|never|without|denies|denied|don't|dont|doesn't|doesnt|didn't|didnt|"
                       r"isn't|isnt|haven't|havent|hasn't|hasnt|nil|free of)(?!\w)")
_HISTORY = re.compile(r"(?<!\w)((years?|months?|weeks?) (ago|back)|last (year|month|week)|history of|in the past|"
                      r"previously|used to|as a child|long ago)(?!\w)")
_CLAUSE = re.compile(r"[,;.\n]|(?<!\w)(?:and|but|however|though)(?!\w)")


def _read_free_text(text: str, sym: pd.DataFrame) -> tuple[list, list]:
    """Symptoms found in one free-text entry, and the phrases skipped as negated or history.
    Whole words only, and a longer phrase wins over a shorter one inside it ('cold sweat' is not a cold)."""
    hits, skipped = [], []
    for clause in _CLAUSE.split(text):
        clause = clause.strip()
        if not clause:
            continue
        spans = []
        for r in sym.itertuples():
            for syn in r.synonyms:
                if not syn:
                    continue
                for m in re.finditer(r"(?<!\w)" + re.escape(syn) + r"(?:s|es|ing|ed)?(?!\w)", clause):
                    spans.append((m.start(), m.end(), r.symptom))
        spans = [a for a in spans if not any(b[2] != a[2] and b[0] <= a[0] and a[1] <= b[1] and (b[1] - b[0]) > (a[1] - a[0])
                                             for b in spans)]
        history = bool(_HISTORY.search(clause))
        for start, _end, code in spans:
            if history or _NEGATION.search(clause[:start]):
                skipped.append(f"{clause} ({'history' if history else 'negated'})")
            elif code not in hits:
                hits.append(code)
    return hits, list(dict.fromkeys(skipped))


def normalise_symptoms(s: State, cfg: Config) -> State:
    """reads raw_symptoms, patient; writes symptoms, unmatched, red_flags.

    Accepts symptom codes ('chest_pain') or free text ('my chest hurts');
    free text is matched against the synonyms column of symptoms.csv (whole words;
    negated or clearly historical phrases are skipped, see _read_free_text).
    patient={'pregnant': True} adds the 'pregnant' symptom.
    """
    s.kb = s.kb or load(cfg.knowledge_dir)
    sym = s.kb.symptoms
    codes = set(sym["symptom"])
    found, unmatched, ignored = [], [], []
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
        hits, skipped = _read_free_text(t, sym)
        hits += [r.symptom for r in sym.itertuples() if r.label.lower() == t and r.symptom not in hits]
        ignored.extend(skipped)
        if hits:
            found.extend(hits)
        elif not skipped:
            unmatched.append(item)
    s.symptoms = list(dict.fromkeys(found))
    s.unmatched = unmatched
    s.red_flags = sym.loc[sym["symptom"].isin(s.symptoms) & sym["red_flag"], "symptom"].tolist()
    s.log("normalise_symptoms", f"{len(s.symptoms)} recognised, {len(unmatched)} not recognised",
          symptoms=s.symptoms, unmatched=unmatched, red_flags=s.red_flags, ignored=ignored)
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
    L = kb.likelihood.copy()
    if cfg.likelihood_leak_minor is not None:
        # everyday symptoms (no leak of their own in symptoms.csv) turn up in minor conditions more often
        # than in a specific emergency they are not listed for; without this, two unrelated mild
        # complaints could only be "explained" by an emergency.
        common = [x for x in L.columns if pd.isna(kb.symptoms.set_index("symptom")["leak"].get(x))]
        minor = L.index.isin(cond.loc[~cond["life_threatening"], "condition"])
        L.loc[minor, common] = L.loc[minor, common].fillna(cfg.likelihood_leak_minor)
    L = L.fillna(leak).clip(1e-6, 1 - 1e-6)
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
    clear_red = s.p_life_threatening >= cfg.red_case_threshold or bool(s.red_flags)
    part_unread = bool(s.unmatched) and s.p_life_threatening >= cfg.red_threshold_when_unread and not clear_red
    s.unclear = bool(cfg.unrecognised_means_hospital and (not s.symptoms or part_unread))
    s.is_red = clear_red or s.unclear
    s.log("assess_severity",
          f"P(life-threatening) = {s.p_life_threatening:.1%}; "
          + ("red case" if s.is_red else "not a red case")
          + (" (nothing recognised: emergency of an unknown kind)" if s.unclear else "")
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
    elif cfg.unrecognised_means_hospital and s.unmatched:
        why.append("part of the description was not understood, so not sent to telehealth")
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


def single_specialty_mask(c: pd.DataFrame, cfg: Config) -> pd.Series:
    """True for facilities that are single-specialty: by level_kind, by facility_type
    (dentist), or by name (eye, dental, IVF, skin...), using the notebook-04 name pattern.
    A name that also says general / medical college is kept; so is "multi...", unless it is dental."""
    kind = c["level_kind"].fillna("") if "level_kind" in c else pd.Series("", index=c.index)
    ftype = c["facility_type"].fillna("") if "facility_type" in c else pd.Series("", index=c.index)
    name = c["name"].fillna("").str.lower() if "name" in c else pd.Series("", index=c.index)
    exempt = name.str.contains(cfg.single_specialty_name_exempt, regex=True) | (
        name.str.contains(cfg.single_specialty_multi_exempt, regex=True)
        & ~name.str.contains(cfg.single_specialty_multi_not_for, regex=True))
    by_name = name.str.contains(cfg.single_specialty_name_pattern, regex=True) & ~exempt
    by_kind = (kind.isin(cfg.single_specialty_kinds) | ftype.isin(cfg.single_specialty_facility_types)) \
        & ~name.str.contains(cfg.single_specialty_name_exempt, regex=True)
    return by_kind | by_name


def emergency_eligibility(s: State, cfg: Config) -> State:
    """reads candidates, is_red; writes candidates (filtered), excluded.

    In a red case, single-specialty facilities (eye, dental, IVF, diagnostic-only)
    are not ranked, whatever their text claims. They are listed in state.excluded.
    If that would leave nothing, the list is kept as it was.
    """
    c = s.candidates
    if not (s.is_red and cfg.red_exclude_single_specialty) or c is None or c.empty:
        return s
    bad = single_specialty_mask(c, cfg)
    if bad.all():
        s.log("emergency_eligibility", "every facility in range is single-specialty; none removed")
        return s
    s.excluded = [{"facility_id": r["facility_id"], "name": r["name"], "km": round(float(r["km"]), 1),
                   "why": "single-specialty facility, not ranked for an emergency"}
                  for _, r in c[bad].sort_values("km").head(10).iterrows()]
    s.candidates = c[~bad].reset_index(drop=True)
    s.log("emergency_eligibility", f"{int(bad.sum())} single-specialty facilities (eye, dental, IVF, diagnostic...) "
          f"not ranked for an emergency; {len(s.candidates)} left")
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
    if not s.is_red and cfg.nonred_no_ambulance and "ambulance_108" in modes:
        dropped.append("ambulance_108 (not an emergency)")
        modes = [m for m in modes if m != "ambulance_108"] or [cfg.nonred_fallback_mode]
    if not s.is_red and cfg.nonred_add_walk and "walk" in tr.index and "walk" not in modes:
        modes = modes + ["walk"]
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
          + (f"; not used: {', '.join(dropped)}" if dropped else "")
          + ("; source: " + ("custom provider" if s.time_provider else "straight-line estimate")))
    return s


# ---------------------------------------------------------------------------
# 7. care_probability
# ---------------------------------------------------------------------------
def strength_table(cfg: Config, kb: Knowledge | None, cautious: bool = False) -> dict:
    """claim_strength -> P(service exists). From the hand-check counts in
    claim_reliability.csv (Beta-Binomial, uniform prior): the mean, or in
    cautious mode the cfg.cautious_quantile lower bound. cfg.p_service_by_strength
    overrides the table."""
    if cfg.p_service_by_strength is not None:
        t = dict(cfg.p_service_by_strength)
        t.setdefault(None, cfg.p_no_claim)
        return t
    rel = (kb.reliability if kb is not None else None) or {}
    q = cfg.cautious_quantile if cautious else None
    t = {k: reliability_estimate(r["supported"], r["checked"], q) for k, r in rel.items()}
    for k, v in {"strong": 0.85, "moderate": 0.70, "weak": 0.40}.items():   # only if the table is missing
        t.setdefault(k, v)
    t[None] = cfg.p_no_claim
    return t


def p_service_exists(claims: pd.DataFrame, cfg: Config, kb: Knowledge | None = None,
                     cautious: bool = False) -> pd.Series:
    """P(the facility really offers the service) for each claim row."""
    table = strength_table(cfg, kb, cautious)
    p = claims["claim_strength"].map(lambda v: table.get(v, table[None]))
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
    """Fallback (assumed) curve for conditions with no published one."""
    x = np.asarray(minutes, float) / tau
    decay = np.maximum(0.0, 1 - x / 2) if cfg.timeliness_curve == "linear" else np.exp(-x)
    late = cfg.late_care_benefit if late is None or pd.isna(late) else float(late)
    t = late + (1 - late) * np.nan_to_num(decay, nan=0.0)
    return np.where(np.isinf(minutes), 0.0, t)  # unreachable by any allowed mode


def curve_value(curve: dict, minutes: np.ndarray, curves: dict | None = None) -> np.ndarray:
    """Share of the benefit of care left after `minutes` of total delay, from a
    published curve (a row of time_curves.csv):
      ratio   risk multiplies by `ratio` every `per_min` minutes -> benefit = ratio ** (-t / per_min)
      linear  straight line from 1 at t = 0 to 0 at `zero_at` minutes
      points  straight lines between (minutes, share) points, flat outside them
    then  late_benefit × L + (1 - late_benefit) × that, where L = 1, or the curve named in
    late_curve (so the part of the benefit outside the drug window still falls with delay)."""
    t = np.asarray(minutes, float)
    prm, kind = curve["params"], curve["kind"]
    with np.errstate(over="ignore", invalid="ignore"):
        if kind == "ratio":
            g = prm["ratio"] ** (-t / prm["per_min"])
        elif kind == "linear":
            g = np.clip(1 - t / prm["zero_at"], 0, 1)
        elif kind == "points":
            xs, ys = zip(*sorted(prm["points"]))
            g = np.interp(np.where(np.isinf(t), xs[-1], t), xs, ys)
        else:
            raise ValueError(f"unknown curve kind {kind!r}")
    late = curve.get("late_benefit")
    late = 0.0 if late is None or pd.isna(late) else float(late)
    lc = curve.get("late_curve")
    tail = curve_value(curves[lc], t, None) if (curves and isinstance(lc, str) and lc in curves) else 1.0
    return np.where(np.isinf(t), 0.0, late * tail + (1 - late) * np.nan_to_num(g, nan=0.0))


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
      timeliness  = share of benefit left after (onset_min + minutes): the published
                    curve named in conditions.csv time_curve (time_curves.csv), else the
                    assumed fallback  late + (1 - late) × exp(-t / tau)
                    For a life-threatening condition this is multiplied by the trip-risk
                    curve over the trip minutes alone (cfg.trip_risk_curve).
    In a red case P(service exists) uses the cautious lower bound (cfg.cautious_for_red).
    """
    c = s.candidates
    cond = s.conditions[s.conditions["p"] >= cfg.min_condition_probability].reset_index(drop=True)
    s.conditions["scored"] = s.conditions["condition"].isin(cond["condition"])
    ids = c["facility_id"]
    cl = s.claims[s.claims["facility_id"].isin(ids)].copy()
    if s.is_red and cfg.red_ignore_flagged_claims and "plausibility_flag" in cl and len(cl):
        flagged = cl["plausibility_flag"].fillna("").astype(str).str.strip().replace("None", "").str.len().gt(0)
        cl = cl[~flagged].copy()
    cautious = bool(cfg.cautious_for_red and s.is_red)
    cl["p_exists"] = p_service_exists(cl, cfg, s.kb, cautious) if len(cl) else pd.Series(dtype=float)
    services = sorted(set(s.kb.services) | set(cl["service"].unique()))
    p_none = strength_table(cfg, s.kb, cautious)[None]
    P = (cl.pivot_table(index="facility_id", columns="service", values="p_exists", aggfunc="max")
           .reindex(index=ids, columns=services).fillna(p_none))
    has24 = (cl[cl.get("hours_claim", pd.Series("", index=cl.index)).eq("24x7")]
             .groupby("facility_id")["service"].apply(set)) if "hours_claim" in cl else pd.Series(dtype=object)
    quotes = cl.set_index(["facility_id", "service"])["supporting_quote"].to_dict() if "supporting_quote" in cl else {}
    strengths = cl.set_index(["facility_id", "service"])["claim_strength"].to_dict()

    level = pd.to_numeric(c["care_level"], errors="coerce").to_numpy(float)
    minutes = c["minutes"].to_numpy(float)
    delay = minutes + max(float(s.onset_min or 0), 0.0)
    cap, rung, hours, timely, best, basis = {}, {}, {}, {}, {}, {}
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
        curve = s.kb.curves.get(getattr(r, "time_curve", None)) if cfg.use_published_time_curves else None
        if curve:
            timely[r.condition] = curve_value(curve, delay, s.kb.curves)
            basis[r.condition] = {"status": "published", "label": curve["label"], "source": curve["source"],
                                  "url": curve.get("url")}
        else:
            timely[r.condition] = timeliness(delay, float(r.tau_min), cfg, getattr(r, "late_benefit", None))
            basis[r.condition] = {"status": "assumed", "source": None, "url": None,
                                  "label": f"Assumed: benefit fades over about {float(r.tau_min):.0f} min"}
        trip = s.kb.curves.get(cfg.trip_risk_curve) if cfg.trip_risk_curve else None
        if trip and r.life_threatening and getattr(r, "time_curve", None) != cfg.trip_risk_curve:
            timely[r.condition] = timely[r.condition] * curve_value(trip, minutes, s.kb.curves)
            basis[r.condition]["trip_risk"] = ("Plus the risk of the trip itself: " + trip["label"].lower()
                                               + " (measured for major injury, assumed for other emergencies)")
        best[r.condition] = picks
    mk = lambda d: pd.DataFrame(d, index=c.index)
    q = mk(cap) * mk(rung) * mk(hours) * mk(timely)
    s.care = {"q": q, "capability": mk(cap), "rung": mk(rung), "hours": mk(hours),
              "timely": mk(timely), "best_service": best, "p_service": P.reset_index(drop=True),
              "conditions": cond, "time_basis": basis, "cautious": cautious, "claims_used": cl}
    tbl = strength_table(cfg, s.kb, cautious)
    s.log("care_probability", f"scored {len(c)} facilities against {len(cond)} conditions; "
          f"evidence weight by how the text states a service (how often our extraction matched the source "
          f"text in the hand check, NOT the chance the service exists): strong {tbl['strong']:.2f}, "
          f"moderate {tbl['moderate']:.2f}, weak {tbl['weak']:.2f}"
          + (" (cautious side, red case)" if cautious else ""))
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
# 8c. nearer_on_ties
# ---------------------------------------------------------------------------
def nearer_on_ties(s: State, cfg: Config) -> State:
    """reads ranked, is_red; re-ranks: in a red case, facilities scoring within
    nearer_on_ties_margin of the best are ordered by minutes (soonest first).

    The published delay curves describe patients who reached care, so they
    understate the risk of the trip itself. When the scores can't separate
    two hospitals, the nearer one goes first. Remove this step to rank on score alone.
    """
    c = s.ranked
    if c is None or len(c) < 2 or not s.is_red or cfg.nearer_on_ties_margin <= 0 or cfg.rank_by_tier_for_red:
        return s
    best = c["score"].max()
    tied = c["score"] >= best - cfg.nearer_on_ties_margin
    c = pd.concat([c[tied].sort_values(["minutes", "score"], ascending=[True, False]), c[~tied]])
    moved = c.index[0] != s.ranked.index[0]
    c["rank"] = np.arange(1, len(c) + 1)
    s.ranked = c
    s.log("nearer_on_ties", f"{int(tied.sum())} facilities within {cfg.nearer_on_ties_margin} of the best score; "
          + (f"nearest of them, {c.iloc[0]['name']}, moved to first" if moved else "order unchanged"))
    return s


# ---------------------------------------------------------------------------
# 8d. evidence_tiers
# ---------------------------------------------------------------------------
TIER_LABEL = {1: "Its own text directly states everything this emergency needs",
              2: "Its text directly states an accepted alternative for everything needed",
              3: "Its text directly states part of what is needed",
              4: "Only a label or hint of the needed services, or below the care level needed",
              5: "Its text states none of the services this emergency needs"}


def main_emergency(s: State, cfg: Config | None = None):
    """The condition the tiers are built for: the most likely life-threatening one
    (or, if none was scored, the one with the largest probability × weight)."""
    if s.unclear:
        needs = [[(g, 1.0) for g in grp.split("|")] for grp in cfg.unclear_needs.split(";") if grp]
        return pd.Series({"condition": "unclear", "label": "Emergency of an unknown kind",
                          "needs": needs, "min_rung": cfg.unclear_min_rung})
    cond = s.care["conditions"]
    lt = cond[cond["life_threatening"]]
    if len(lt):
        return lt.sort_values("p", ascending=False).iloc[0]
    return cond.assign(k=cond["p"] * cond["weight"]).sort_values("k", ascending=False).iloc[0]


# Specialist focus read from the facility NAME: (label, name pattern, emergencies it fits).
SPECIALTY_FOCUS = (
    ("children's", r"child|paediat|pediat|\bkids?\b|neonat|\bbaby\b", ("newborn_emergency", "child_high_fever")),
    ("maternity or women's", r"matern|women|mother|gyna?ec|obstet", ("obstetric_emergency", "newborn_emergency", "labour_normal")),
    ("heart", r"heart|cardi", ("heart_attack", "chest_pain_minor")),
    ("cancer", r"cancer|onco", ()),
    ("brain and spine", r"neuro|spine|brain", ("stroke", "head_injury_serious", "seizure_status", "major_trauma")),
    ("bone and joint", r"ortho|\bbone\b|\bjoint", ("major_trauma", "fracture", "minor_injury")),
    ("kidney or urology", r"kidney|nephro|\buro|dialysis", ("kidney_failure",)),
    ("digestive", r"gastro|\bliver\b", ()),
)


def focus_mismatch(name: str, condition: str, cfg: Config) -> str:
    """'' if the name fits the emergency or shows no special focus; else the focus label.
    A name with several focuses (mother and child) is a mismatch only if none fits."""
    n = str(name or "").lower()
    if re.search(cfg.focus_name_exempt, n):
        return ""
    hits = [(lab, conds) for lab, pat, conds in SPECIALTY_FOCUS if re.search(pat, n)]
    if not hits or any(condition in conds for _, conds in hits):
        return ""
    return hits[0][0]


def evidence_tiers(s: State, cfg: Config) -> State:
    """reads ranked, care, is_red; writes ranked.tier/.states/.missing and re-ranks a red case.

    No formula decides a red case. Facilities are put in evidence tiers for the
    main emergency (see config rank_by_tier_for_red) and ordered:
        tier, then time (in tier_time_band_min bands), then the old score as a tie-break.
    'Directly states' means the facility's own text says it (strong or moderate
    claim). It does not mean the service is verified or available now.
    """
    c = s.ranked
    if c is None or c.empty or not (s.is_red and cfg.rank_by_tier_for_red):
        return s
    m = main_emergency(s, cfg)
    cl = s.care["claims_used"]
    direct = cl[cl["claim_strength"].isin(cfg.tier_direct_strengths)].groupby("facility_id")["service"].apply(set).to_dict()
    anyc = cl.groupby("facility_id")["service"].apply(set).to_dict()
    level = pd.to_numeric(c["care_level"], errors="coerce")
    tiers, states, missing, focus = [], [], [], []
    names = c["name"] if "name" in c else pd.Series("", index=c.index)
    for fid, lvl, nm in zip(c["facility_id"], level, names):
        d, a = direct.get(fid, set()), anyc.get(fid, set())
        met, unmet, alt_met, partial = [], [], 0, False
        for grp in m["needs"]:
            full = [g for g, cr in grp if cr >= 1.0 and g in d]
            if full:
                met.append(full[0])
            else:
                unmet.append(grp[0][0])
                alt_met += any(g in d for g, _ in grp)          # a listed alternative, e.g. CT for a stroke unit
                partial = partial or any(g in a for g, _ in grp)
        level_ok = (not pd.isna(lvl)) and lvl >= m["min_rung"]
        if not unmet and level_ok:
            t = 1
        elif alt_met == len(unmet) and level_ok:
            t = 2
        elif (met or alt_met) and level_ok:
            t = 3
        elif met or alt_met or partial:
            t = 4
        else:
            t = 5
        fm = focus_mismatch(nm, m["condition"], cfg) if cfg.tier_demote_focus_mismatch else ""
        if fm and t < 5:
            t += 1                                              # one tier lower: its name points at another specialty
        tiers.append(t); states.append(met); missing.append(unmet); focus.append(fm)
    c = c.copy()
    c["tier"], c["states"], c["missing"], c["focus_note"] = tiers, states, missing, focus
    c["_band"] = np.floor(c["minutes"].to_numpy(float) / max(cfg.tier_time_band_min, 1e-9))
    c = c.sort_values(["tier", "_band", "need_met", "minutes"], ascending=[True, True, False, True]).drop(columns="_band")
    c["nearer_option"] = False
    if len(c) > 1 and c.iloc[0]["tier"] == 1 and cfg.tier_time_cap_min is not None:
        first = c.iloc[0]
        # the nearer option must at least state emergency care directly, be care level 3+, unflagged, general
        can_receive, _ = stabilise_eligible(s, cfg, c)
        near2 = c[(c["tier"] > 1) & can_receive & (c["minutes"] < first["minutes"] - cfg.tier_time_cap_min)]
        # of those, the best-documented tier, then the nearest
        near2 = near2.sort_values(["tier", "minutes"])
        if len(near2):                      # show it second, side by side with the first
            j = near2.index[0]
            order = [c.index[0], j] + [i for i in c.index[1:] if i != j]
            c = c.loc[order]
            c.loc[j, "nearer_option"] = True
    c["rank"] = np.arange(1, len(c) + 1)
    s.ranked = c
    s.care["main_emergency"] = m["condition"]
    n1, n2 = int((c["tier"] == 1).sum()), int((c["tier"] == 2).sum())
    s.care["tier_counts"] = {int(k): int(v) for k, v in c["tier"].value_counts().items()}
    t = c.iloc[0]
    s.log("evidence_tiers", f"for {m['label'].lower()}: {n1} facilities directly state everything needed, "
          f"{n2} directly state an accepted alternative for all of it; nearest first within a tier; first: {t['name']} (tier {int(t['tier'])}, "
          f"{t['minutes']:.0f} min)")
    return s


# ---------------------------------------------------------------------------
# 8e. nearest_suitable  (non-emergencies)
# ---------------------------------------------------------------------------
def nearest_suitable(s: State, cfg: Config) -> State:
    """reads ranked, conditions, is_red; writes ranked.suitable and re-ranks a NON-emergency.

    A non-emergency does not need the best-documented hospital, it needs the nearest place that can
    deal with the most likely condition. Suitable = care level at or above that condition's level,
    its text states (any strength) each service the condition needs, not a single-specialty place,
    and its name does not point at another specialty. Order: suitable first, then nearest
    (tier_time_band_min bands), then the weighted score as a tie-break.
    """
    c = s.ranked
    if c is None or c.empty or s.is_red or not cfg.nonred_nearest_suitable:
        return s
    cond = s.care["conditions"]
    m = cond.sort_values("p", ascending=False).iloc[0]
    cl = s.care["claims_used"]
    have = cl.groupby("facility_id")["service"].apply(set).to_dict()
    level = pd.to_numeric(c["care_level"], errors="coerce")
    single = single_specialty_mask(c, cfg) if cfg.nonred_exclude_single_specialty else pd.Series(False, index=c.index)
    names = c["name"] if "name" in c else pd.Series("", index=c.index)
    ok, why = [], []
    for fid, lvl, sg, nm in zip(c["facility_id"], level, single, names):
        h = have.get(fid, set())
        missing = [grp[0][0] for grp in m["needs"] if not any(g in h for g, _ in grp)]
        fm = focus_mismatch(nm, m["condition"], cfg) if cfg.tier_demote_focus_mismatch else ""
        reason = ("it is a single-specialty facility" if sg else
                  f"its name suggests a {fm} hospital" if fm else
                  "its care level is unknown or below what this needs" if pd.isna(lvl) or lvl < m["min_rung"] else
                  f"its text does not mention {', '.join(missing)}" if missing else "")
        ok.append(not reason); why.append(reason)
    c = c.copy()
    c["suitable"], c["unsuitable_why"] = ok, why
    c["_band"] = np.floor(c["minutes"].to_numpy(float) / max(cfg.tier_time_band_min, 1e-9))
    c = c.sort_values(["suitable", "_band", "score", "minutes"], ascending=[False, True, False, True]).drop(columns="_band")
    c["rank"] = np.arange(1, len(c) + 1)
    s.ranked = c
    s.care["main_nonurgent"] = m["condition"]
    t = c.iloc[0]
    s.log("nearest_suitable", f"not an emergency: most likely {m['label'].lower()}; {int(sum(ok))} suitable facilities, "
          f"nearest first; first: {t['name']} ({t['minutes']:.0f} min)")
    return s


# ---------------------------------------------------------------------------
# 9. safety_rules  (each rule is its own function; see SAFETY_RULES)
# ---------------------------------------------------------------------------
def rule_call_ambulance(s: State, cfg: Config) -> None:
    lines = set(s.conditions.loc[s.conditions["p"] >= 0.05, "ambulance_line"].astype(str))
    num = "102" if lines == {"102"} else ("108 (or 102 for pregnancy and newborns)" if "102" in lines else "108")
    level = "critical" if s.is_red else "info"
    text = (f"Call {num} now, or 112 (the national emergency number). The ambulance is free in most states. "
            "Tell the crew which hospital you are considering; they decide where to go." if s.is_red
            else f"If things get worse, call {num} (free). Call 112 for any emergency.")
    s.alert("call_ambulance", level, text)


def rule_telehealth_first(s: State, cfg: Config) -> None:
    if s.disposition == "telehealth_first":
        s.alert("telehealth_first", "advice",
                "This sounds unlikely to be an emergency. Try a free eSanjeevani teleconsultation first "
                "(esanjeevani.mohfw.gov.in). If the doctor says to go in, these are the best places.")


def stabilise_eligible(s: State, cfg: Config, c: pd.DataFrame) -> tuple[pd.Series, dict]:
    """Which ranked facilities may be named as 'stabilise here first'.

    Needs: care level >= stabilise_min_rung; a DIRECT emergency claim (strong or
    moderate); no plausibility flag on any of its claims; not single-specialty
    (by level_kind or by name). Returns the mask and how many each check removed.
    """
    cl = s.claims[s.claims["facility_id"].isin(c["facility_id"])]
    em = cl[(cl["service"] == "emergency") & cl["claim_strength"].isin(cfg.stabilise_claim_strengths)]
    ok_claim = c["facility_id"].isin(em["facility_id"])
    ok_rung = pd.to_numeric(c["care_level"], errors="coerce") >= cfg.stabilise_min_rung
    ok_flag = pd.Series(True, index=c.index)
    if cfg.stabilise_exclude_plausibility_flag and "plausibility_flag" in cl:
        flagged = cl.loc[cl["plausibility_flag"].fillna("").astype(str).str.strip().ne("")
                         & cl["plausibility_flag"].astype(str).ne("None"), "facility_id"]
        ok_flag = ~c["facility_id"].isin(flagged)
    ok_single = ~single_specialty_mask(c, cfg) if cfg.stabilise_exclude_single_specialty \
        else pd.Series(True, index=c.index)
    removed = {"no direct emergency claim": int((~ok_claim).sum()), "care level too low": int((~ok_rung).sum()),
               "plausibility flag": int((~ok_flag).sum()), "single-specialty": int((~ok_single).sum())}
    return ok_claim & ok_rung & ok_flag & ok_single, removed


def rule_stabilise_and_transfer(s: State, cfg: Config) -> None:
    if not s.is_red or s.ranked is None or s.ranked.empty:
        return
    top = s.ranked.iloc[0]
    if "nearer_option" in s.ranked:          # tier ranking: the cross-tier time cap decides what to show
        opt = s.ranked[s.ranked["nearer_option"]]
        if len(opt):
            n = opt.iloc[0]
            s.ranked.loc[n.name, "stabilise"] = True
            miss = ", ".join(n["missing"]) or "the needed care level"
            s.alert("stabilise_and_transfer", "warning",
                    f"Two options for the 108 crew to choose between. {top['name']}: about {top['minutes']:.0f} min, "
                    f"its text states everything this emergency needs. {n['name']}: about {n['minutes']:.0f} min, "
                    f"{top['minutes'] - n['minutes']:.0f} min closer, but its text does not directly state {miss}. "
                    "We have not verified what either place can do today.")
        return
    if top["minutes"] <= cfg.stabilise_threshold_min:
        return
    c = s.ranked
    ok, removed = stabilise_eligible(s, cfg, c)
    near = c[ok & (c["minutes"] < top["minutes"])].sort_values("minutes")
    s.log("stabilise_check", f"{int(ok.sum())} facilities could stabilise; excluded: "
          + ", ".join(f"{k} {v}" for k, v in removed.items()))
    if len(near):
        n = near.iloc[0]
        s.ranked.loc[n.name, "stabilise"] = True
        s.alert("stabilise_and_transfer", "warning",
                f"The first facility listed is about {top['minutes']:.0f} min away. {n['name']} is closer, about "
                f"{n['minutes']:.0f} min, and its own text says it has emergency care. Whether to stop there "
                "first is for the 108 crew to decide; we have not verified what either place can do today.")


def rule_close_call(s: State, cfg: Config) -> None:
    if s.ranked is not None and len(s.ranked) >= 2:
        a, b = s.ranked.iloc[0], s.ranked.iloc[1]
        if "tier" in s.ranked:
            if a["tier"] == b["tier"] and abs(a["minutes"] - b["minutes"]) < cfg.tier_time_band_min:
                s.alert("close_call", "info",
                        f"{a['name']} and {b['name']} are about equally far and equally documented; "
                        "call ahead to check which can see you sooner.")
            return
        if abs(a["score"] - b["score"]) < cfg.close_call_margin:
            s.alert("close_call", "info",
                    f"{a['name']} and {b['name']} are nearly tied; call ahead to check which can see you sooner.")


def rule_unrecognised(s: State, cfg: Config) -> None:
    if s.unclear:
        s.alert("unclear_symptoms", "warning",
                ("We could not read part of what you typed" if s.symptoms else "We could not read the symptoms")
                + (" (" + ", ".join(map(str, s.unmatched)) + ")" if s.unmatched else " because none were given")
                + ", so this is treated as a possible emergency of an unknown kind. Please also pick symptoms from the list. "
                "Hospitals below are ordered only on whether their text states emergency care and an ICU.")
    elif s.unmatched:
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
    if "tier" in s.ranked:
        a, b = s.ranked.loc[idx], s.ranked.loc[top_idx]
        if a.get("nearer_option") is True:
            return "being only partly documented; shown here because it is much closer"
        if a["tier"] > b["tier"]:
            miss = ", ".join(a["missing"]) or "the needed care level"
            return f"its text does not directly state everything needed ({miss})"
        return "being further away" if a["minutes"] > b["minutes"] else "weaker documentation at a similar distance"
    if "suitable" in s.ranked:
        a, b = s.ranked.loc[idx], s.ranked.loc[top_idx]
        if not a["suitable"]:
            return f"not being a good fit ({a['unsuitable_why']})"
        return "being further away" if a["minutes"] > b["minutes"] else "a similar distance with less on record"
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
                "time_basis": s.care.get("time_basis", {}).get(cr.condition),
                "contribution": round(float(s.care["contribution"].at[idx, cr.condition]), 4),
                "services": [{"service": a, "p_exists": round(b, 3), "credit": cr, "claim_strength": st,
                              "quote": qt} for a, b, cr, st, qt in picks],
            })
        main = max(parts, key=lambda p: p["p_condition"] * p["weight"])
        if s.care.get("main_emergency") == "unclear" and "tier" in s.ranked:
            cu = s.care["claims_used"]; cu = cu[cu["facility_id"] == r["facility_id"]]
            svc = []
            for name in list(r["states"]) + list(r["missing"]):
                hit = cu[cu["service"] == name].sort_values("claim_strength", key=lambda x: x.map({"strong": 0, "moderate": 1, "weak": 2}))
                svc.append({"service": name, "p_exists": 0.0, "credit": 1.0,
                            "claim_strength": hit.iloc[0]["claim_strength"] if len(hit) else None,
                            "quote": hit.iloc[0].get("supporting_quote") if len(hit) else None})
            main = {"condition": "unclear", "label": "Emergency of an unknown kind", "p_condition": 0.0, "weight": 0.0,
                    "services": svc, "time_basis": None}
            parts.insert(0, main)
        elif s.care.get("main_emergency") and "tier" in s.ranked:
            main = next(p for p in parts if p["condition"] == s.care["main_emergency"])
        svc_txt = "; ".join(f"{x['service']} ({x['claim_strength'] or 'no claim'})" for x in main["services"]) \
            or "no special service needed"
        lvl = "unknown" if pd.isna(r["care_level"]) else int(r["care_level"])
        head = f"About {r['minutes']:.0f} min by {r['mode_label'].lower()} ({r['km']:.0f} km straight line). "
        tier = int(r["tier"]) if "tier" in r and not pd.isna(r.get("tier")) else None
        if tier is not None:
            said = "; ".join(f"{x['service']} ({'stated in 2+ places' if x['claim_strength'] == 'strong' else 'stated once' if x['claim_strength'] == 'moderate' else 'only a label or hint' if x['claim_strength'] == 'weak' else 'not stated'})"
                             for x in main["services"]) or "no special service needed"
            sentence = (head + f"For a possible {main['label'].lower()}, its own text says: {said}. Care level {lvl}. "
                        + (f"Not stated directly: {', '.join(r['missing'])}. " if len(r["missing"]) else "")
                        + (f"Ranked one tier lower because its name suggests a {r['focus_note']} hospital: confirm it takes this emergency. " if r.get("focus_note") else "")
                        + "Nothing here is independently verified: call ahead, and let the 108 crew decide.")
        elif "suitable" in s.ranked:
            mm = next((p for p in parts if p["condition"] == s.care.get("main_nonurgent")), main)
            main = mm
            needs_txt = "; ".join(f"{x['service']} ({x['claim_strength'] or 'not mentioned'})" for x in mm["services"])
            sentence = (head + f"Not an emergency: for {mm['label'].lower()}, the most likely cause, "
                        + ("no special service is needed" if not mm["services"] else f"its text mentions {needs_txt}")
                        + f". Care level {lvl}. "
                        + ("Listed in order of distance among places that fit." if r["suitable"]
                           else f"Not a good fit: {r['unsuitable_why']}.")
                        + " Not independently verified: call ahead.")
        else:
            sentence = (head + f"For the most important possibility, {main['label'].lower()}, its text mentions "
                        f"{svc_txt}; care level {lvl}."
                        + (" A longer trip counted against it." if r["burden"] >= 0.005 else ""))
        why_not = None if idx == top_idx else \
            f"Ranked below #1 mainly because of {_why_lower(s, idx, top_idx)}."
        out.append({"rank": int(r["rank"]), "facility_id": r["facility_id"], "name": r["name"],
                    "score": round(float(r["score"]), 4), "need_met": round(float(r["need_met"]), 4),
                    "burden": round(float(r["burden"]), 4), "minutes": round(float(r["minutes"]), 1),
                    "mode": r["mode"], "km": round(float(r["km"]), 1), "care_level": lvl,
                    "stabilise": bool(r.get("stabilise", False) is True),
                    "tier": tier, "tier_label": TIER_LABEL.get(tier), "main_condition": main["condition"],
                    "missing": list(r["missing"]) if tier is not None else [],
                    "summary": sentence, "why_lower": why_not, "by_condition": parts})
    # nearest facilities that did NOT make the list, and why
    rest = s.ranked.iloc[cfg.top_n:].sort_values("minutes").head(3)
    s.nearest_not_chosen = [{"name": r["name"], "minutes": round(float(r["minutes"]), 1),
                             "rank": int(r["rank"]), "why": f"Not in the top {cfg.top_n} mainly because of "
                             f"{_why_lower(s, idx, top_idx)}."} for idx, r in rest.iterrows()]
    s.explanations = out
    s.log("explain", f"explained top {len(out)} and {len(s.nearest_not_chosen)} nearer facilities left out")
    return s
