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
    # Raised from 0.05 after the 2026-11-22 test: at 0.05 a sore throat alone (7.6%) was an emergency.
    # Red-flag symptoms are emergencies whatever this number is. Between telehealth_threshold and this
    # line the result is "see a doctor today" with the ordinary ranking, not the emergency screen.
    red_case_threshold: float = 0.20
    # Nothing recognised in what was typed: treated as an emergency of an unknown kind, ranked on these needs only.
    unclear_needs: str = "emergency;icu"
    unclear_min_rung: int = 3

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
    # Measured, not guessed: knowledge/claim_reliability.csv holds the hand-check
    # counts (strong 20/20, moderate 16/20, weak 7/20) and the estimate is the
    # Beta-Binomial mean with a uniform prior: 0.95 / 0.77 / 0.36.
    # Set this dict (e.g. {"strong": 0.85, "moderate": 0.7, "weak": 0.4}) to override the table.
    p_service_by_strength: dict | None = None
    # A facility that makes no claim for the service (assumed, not measured).
    p_no_claim: float = 0.03
    # In a red case, plan on the cautious side of the error bar: this quantile of
    # the Beta posterior instead of the mean. The lower quartile (0.25) gives
    # 0.94 / 0.72 / 0.29. A harsher 0.05 gives 0.87 / 0.61 / 0.21, which with only
    # 20 checked claims per group mostly punishes our small sample and sends
    # people on much longer trips.
    cautious_for_red: bool = True
    cautious_quantile: float = 0.25
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
    # Timeliness: how much of the benefit of care survives the delay
    # (time since onset + wait + trip). A condition with a time_curve in
    # conditions.csv uses that published curve (knowledge/time_curves.csv).
    # The settings below are the fallback for conditions with no published
    # curve yet, and are assumed:
    #   timeliness = late + (1 - late) * decay(minutes / tau)
    # decay is "exp": exp(-x) or "linear": max(0, 1 - x / 2). `late` is the
    # share of benefit that late care still gives.
    use_published_time_curves: bool = True
    # Trip risk. The condition curves above come from patients who reached a
    # hospital, so they say nothing about getting worse on the way. For every
    # life-threatening condition the trip itself (wait + travel, not time since
    # onset) is also charged with this curve from time_curves.csv: "trauma" is
    # Gauss 2019, odds of death up 4% per 10 minutes before hospital. Measured
    # for major injury; using it for other emergencies is our assumption.
    # None switches it off. Not applied to a condition that already uses this curve.
    trip_risk_curve: str | None = None
    # NOTE (critical review, 2026-11-22): the curves and this trip-risk factor are
    # model assumptions built from studies with different endpoints. In a red case
    # they no longer decide the ranking (see rank_by_tier_for_red); they only break
    # ties and feed the offline sensitivity analysis. "trauma" was tried and dropped:
    # it was added after seeing results, and generalises an injury study to all emergencies.
    timeliness_curve: str = "exp"
    late_care_benefit: float = 0.25

    # ---- red cases: who may be ranked, and in what order ----------------------
    # Single-specialty places (eye, dental, IVF, diagnostic-only...) are not ranked
    # for a red case at all: in validation they were first choice for 2.4% of
    # emergencies because their text mentions "emergency" or "trauma".
    red_exclude_single_specialty: bool = True
    # A claim with a plausibility flag is ignored in a red case.
    red_ignore_flagged_claims: bool = True
    # Red cases are ranked in evidence tiers, nearest first inside a tier:
    #   tier 1  the facility's own text DIRECTLY states every service the main
    #           emergency needs (claim strength in tier_direct_strengths), and its
    #           care level is at least the condition's minimum
    #   tier 2  for everything needed it directly states the service or a listed alternative
    #           (conditions.csv needs, e.g. CT instead of a stroke unit), at that care level
    #   tier 3  it directly states part of what is needed, at that care level
    #   tier 4  only a label or hint, or below the care level needed
    #   tier 5  it states none of it
    # Times within tier_time_band_min of each other count as equal (the estimates
    # are not that precise); the old score then breaks the tie.
    rank_by_tier_for_red: bool = True
    tier_direct_strengths: tuple = ("strong", "moderate")
    tier_time_band_min: float = 5.0
    # Cross-tier time cap. If the first tier-1 facility is more than this many minutes
    # further than a lower-tier facility that directly states emergency care (care level 3+,
    # unflagged, not single-specialty), that nearer one is shown second, side by
    # side, as "two options for the 108 crew to choose between". Tiers alone would send
    # people very far for better paperwork; this keeps the nearer, partly documented option visible.
    tier_time_cap_min: float = 60.0
    # A hospital whose NAME shows a focus on something else (a heart institute for a pregnancy
    # emergency, a children's hospital for a stroke) is ranked one tier lower. See steps.SPECIALTY_FOCUS.
    tier_demote_focus_mismatch: bool = True
    focus_name_exempt: str = r"general|medical college|multi|super ?special"

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
    # A stabilising facility must state emergency care directly (not just a
    # specialty tag like "eye trauma and emergency eye care").
    stabilise_claim_strengths: tuple = ("strong", "moderate")
    stabilise_exclude_plausibility_flag: bool = True   # any flagged claim on the facility
    stabilise_exclude_single_specialty: bool = True
    single_specialty_kinds: tuple = ("Single-specialty hospital", "Diagnostic centre", "Pharmacy", "Dental clinic")
    single_specialty_facility_types: tuple = ("dentist",)
    # Same name pattern notebook 04 uses for is_single_specialty (catches records whose kind missed it).
    single_specialty_name_pattern: str = (
        r"\beye\b|netra|nethra|dental|dentist|\bskin\b|derma|\bivf\b|fertility|\bent\b|physiother"
        r"|\bhair\b|cosmetic|psychiatr|de-?addiction|rehab|ortho(?:paedic|pedic)? cent")
    # A name that also says general / medical college is kept (e.g. "City General Hospital & Dental Centre").
    # "multi" keeps it too, except for dental names: "Multispeciality Dental Clinic" is still a dental clinic.
    single_specialty_name_exempt: str = r"general|medical college"
    single_specialty_multi_exempt: str = r"multi"
    single_specialty_multi_not_for: str = r"dental|dentist|orthodont|implant"
    close_call_margin: float = 0.03
    # Red case: among facilities whose score is within this margin of the best,
    # the one reached soonest is ranked first. The published delay curves come
    # from patients who reached care, so they understate the risk of a long
    # trip; when the evidence can't separate two hospitals, go to the nearer.
    nearer_on_ties_margin: float = 0.03

    # ---- helpers ----------------------------------------------------------
    def replace(self, **kw) -> "Config":
        return dataclasses.replace(self, **kw)

    @classmethod
    def from_json(cls, path: str | Path) -> "Config":
        data = json.loads(Path(path).read_text())
        for k in ("expand_radius_km", "usable_location_flags", "default_transport",
                  "stabilise_claim_strengths", "single_specialty_kinds", "tier_direct_strengths",
                  "single_specialty_facility_types"):
            if k in data:
                data[k] = tuple(data[k])
        if data.get("p_service_by_strength") is not None:
            data["p_service_by_strength"] = {
                (None if k in ("null", "none", "None") else k): v
                for k, v in data["p_service_by_strength"].items()}
        return cls(**data)

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        if self.p_service_by_strength is not None:
            d["p_service_by_strength"] = {("none" if k is None else k): v
                                          for k, v in self.p_service_by_strength.items()}
        return d
