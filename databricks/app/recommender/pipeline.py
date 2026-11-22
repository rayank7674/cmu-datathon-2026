"""The control flow: an ordered list of steps, and the function that runs it.

    PIPELINE = [normalise_symptoms, infer_conditions, assess_severity, triage_gate,
                find_candidates, emergency_eligibility, travel_time, care_probability, score,
                travel_burden, nearer_on_ties, evidence_tiers, safety_rules, explain]

Every step is `step(state, cfg) -> state`. To change the model:
    without(PIPELINE, "triage_gate")                    # remove a step
    replace_step(PIPELINE, "infer_conditions", my_fn)   # swap one
    insert_after(PIPELINE, "travel_time", my_fn)        # add one
and pass the new list as recommend(..., pipeline=new_list).
"""
from __future__ import annotations

import time

import pandas as pd

from .config import Config
from .steps import (State, assess_severity, care_probability, emergency_eligibility, evidence_tiers, explain,
                    find_candidates,
                    infer_conditions, nearer_on_ties, normalise_symptoms, safety_rules, score, travel_burden, travel_time,
                    triage_gate)

PIPELINE = [
    normalise_symptoms,   # 1  free text -> symptom codes, red flags
    infer_conditions,     # 2  P(condition | symptoms)
    assess_severity,      # 3  severity weights, P(life-threatening)
    triage_gate,          # 4  telehealth first, or hospital
    find_candidates,      # 5  facilities in range with a usable location
    emergency_eligibility,  # 5b red case: single-specialty places (eye, dental, IVF...) are not ranked
    travel_time,          # 6  minutes by the fastest allowed transport
    care_probability,     # 7  P(adequate care | hospital, condition)
    score,                # 8  Σ P(c) × severity(c) × P(care | h, c)
    travel_burden,        # 8b minus a travel penalty for non-urgent cases
    nearer_on_ties,       # 8c (only if tiers are off) near-tied scores -> the nearer hospital first
    evidence_tiers,       # 8d red case: evidence tier first, then nearest; the score only breaks ties
    safety_rules,         # 9  108/102 first, stabilise and transfer, ...
    explain,              # 10 per-condition breakdown, quotes, why others lower
]


def _name(fn) -> str:
    return getattr(fn, "__name__", str(fn))


def without(pipeline, name):
    return [f for f in pipeline if _name(f) != name]


def replace_step(pipeline, name, fn):
    if name not in [_name(f) for f in pipeline]:
        raise KeyError(name)
    return [fn if _name(f) == name else f for f in pipeline]


def insert_after(pipeline, name, fn):
    out = []
    for f in pipeline:
        out.append(f)
        if _name(f) == name:
            out.append(fn)
    if fn not in out:
        raise KeyError(name)
    return out


def describe(pipeline=PIPELINE) -> str:
    """The steps in order with the first line of each docstring."""
    return "\n".join(f"{i}. {_name(f)}: {(f.__doc__ or '').strip().splitlines()[0]}"
                     for i, f in enumerate(pipeline, 1))


def recommend(symptoms, lat: float, lon: float, claims: pd.DataFrame, *,
              facilities: pd.DataFrame | None = None, transport: list | None = None,
              patient: dict | None = None, telehealth_says_go: bool = False,
              cfg: Config | None = None, pipeline=None, time_provider=None,
              onset_min: float = 0.0, kb=None) -> State:
    """Run the pipeline and return the final State.

    symptoms   list of symptom codes or free-text phrases
    lat, lon   where the person is
    claims     one row per facility x claimed service (vf_gold.facility_service_canonical
               WHERE claim_detected, as the app loads it); facility columns come with it
    facilities optional full facility list (to include facilities with no claims)
    transport  modes the person has, e.g. ["ambulance_108", "own_car"]; see knowledge/transport.csv
    patient    e.g. {"pregnant": True}
    telehealth_says_go  True once a teleconsult has said to go in (skips the gate)
    onset_min  minutes since the problem started; added to the trip when reading the time curves
    kb         a Knowledge object to use instead of loading cfg.knowledge_dir (used by the validation runs)

    Useful fields on the result: disposition, p_life_threatening, conditions,
    ranked, alerts, explanations, nearest_not_chosen, trace.
    """
    cfg = cfg or Config()
    s = State(raw_symptoms=list(symptoms or []), lat=float(lat), lon=float(lon), claims=claims,
              facilities=facilities, transport=transport, patient=patient or {},
              telehealth_says_go=telehealth_says_go, time_provider=time_provider,
              onset_min=onset_min, kb=kb)
    for step in (pipeline or PIPELINE):
        t0 = time.perf_counter()
        s = step(s, cfg)
        if s.trace:
            s.trace[-1].setdefault("ms", round((time.perf_counter() - t0) * 1000, 1))
        if s.stop:
            break
    return s
