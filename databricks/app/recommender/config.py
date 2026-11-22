"""Every tunable number in the recommender lives here.

Change a default here, or override any field at call time:

    cfg = Config(telehealth_threshold=0.10, severity_exponent=2.0)
    cfg = Config.from_json("my_settings.json")      # same field names
    cfg = cfg.replace(search_radius_km=100)

The knowledge tables (symptoms, conditions, symptom likelihoods, transport
modes) are CSV files in knowledge/. Point `knowledge_dir` somewhere else to
use your own copies. These tables are illustrative judgement, not clinical
values; a clinician should review them before real use.
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"


@dataclass(frozen=True)
class Config:
    # ---- knowledge tables -------------------------------------------------
    knowledge_dir: str = str(KNOWLEDGE_DIR)

    # ---- step 2: infer_conditions -----------------------------------------
    # Conditions are treated as mutually exclusive (naive Bayes), so the
    # probabilities sum to 1. Swap the step itself to use another method.
    # P(symptom | condition) for a pair not listed in likelihood.csv. Keeps one
    # unexpected symptom from ruling a condition out entirely.
    likelihood_leak: float = 0.02
    # Use symptoms the person did NOT report as evidence against conditions.
    # Off by default: people list what they notice, not everything they lack.
    use_absent_symptoms: bool = False
    # >1 flattens the posterior (less overconfident), <1 sharpens it.
    posterior_temperature: float = 1.5
    # Conditions below this probability are dropped from scoring (speed + clarity).
    min_condition_probability: float = 0.005

    # ---- step 3/4: severity and triage gate -------------------------------
    telehealth_threshold: float = 0.05     # P(life-threatening) below this -> telehealth first
    red_flag_forces_hospital: bool = True  # any red-flag symptom skips telehealth
    # No symptom recognised: "if unsure, treat it as an emergency" (plan section 3).
    unrecognised_means_hospital: bool = True
    # Ambulance-first rule: P(life-threatening) at or above this counts as a red case
    # (excludes two-wheeler, bus and walking; adds the stabilise rule).
    red_case_threshold: float = 0.05

    # ---- step 5: find_candidates ------------------------------------------
    search_radius_km: float = 150.0
    expand_radius_km: tuple = (300.0, 600.0)   # tried in order if nothing is found
    usable_location_flags: tuple = ("good",)
    min_candidates: int = 3

    # ---- step 6: travel_time ----------------------------------------------
    # Modes the person has if they don't say. 108 is free in most states.
    default_transport: tuple = ("ambulance_108", "auto_taxi")
    # Per-mode overrides, e.g. {"ambulance_108": {"wait_min": 15}}.
    transport_overrides: dict = field(default_factory=dict)

    # ---- step 7: care_probability -----------------------------------------
    # P(the facility really offers the service | how it claims it).
    p_service_by_strength: dict = field(default_factory=lambda: {
        "strong": 0.85, "moderate": 0.70, "weak": 0.40, None: 0.03})
    # Verification status adjusts that: verified sets a floor, contradicted halves.
    verified_floor: float = 0.97
    externally_supported_bonus: float = 0.05
    contradicted_factor: float = 0.5
    plausibility_flag_factor: float = 0.8
    # Blend in the record's data-quality score: p *= (1 - w) + w * quality_score.
    quality_weight: float = 0.3
    # Multiplied once per rung the facility is below the condition's minimum.
    rung_shortfall_factor: float = 0.5
    unknown_rung_factor: float = 0.6
    # For life-threatening conditions, a facility that doesn't say it runs the
    # needed service 24x7 gets this factor (we don't know it's open now).
    unknown_hours_factor: float = 0.9
    # Timeliness: how much of the benefit of care survives the trip.
    #   timeliness = late + (1 - late) * decay(minutes / tau)
    # decay is "exp": exp(-x) or "linear": max(0, 1 - x / 2). `late` is the
    # share of benefit that late care still gives (conditions.csv column
    # late_benefit overrides it per condition).
    timeliness_curve: str = "exp"
    late_care_benefit: float = 0.25

    # ---- step 8: score ----------------------------------------------------
    severity_exponent: float = 1.0   # weight = severity ** exponent

    # ---- step 8b: travel_burden -------------------------------------------
    # For non-urgent cases a long trip is a cost in itself (time, money, risk),
    # even when it barely changes the medical benefit. Subtracted from the score:
    #   burden = weight × (1 - P(life-threatening)) × min(minutes, cap) / cap
    travel_burden_weight: float = 0.15
    travel_burden_cap_min: float = 240.0
    top_n: int = 5

    # ---- step 9: safety rules ---------------------------------------------
    stabilise_threshold_min: float = 60.0  # golden hour
    stabilise_min_rung: int = 3
    close_call_margin: float = 0.03

    # ---- helpers ----------------------------------------------------------
    def replace(self, **kw) -> "Config":
        return dataclasses.replace(self, **kw)

    @classmethod
    def from_json(cls, path: str | Path) -> "Config":
        data = json.loads(Path(path).read_text())
        for k in ("expand_radius_km", "usable_location_flags", "default_transport"):
            if k in data:
                data[k] = tuple(data[k])
        if "p_service_by_strength" in data:
            data["p_service_by_strength"] = {
                (None if k in ("null", "none", "None") else k): v
                for k, v in data["p_service_by_strength"].items()}
        return cls(**data)

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["p_service_by_strength"] = {("none" if k is None else k): v
                                      for k, v in self.p_service_by_strength.items()}
        return d
