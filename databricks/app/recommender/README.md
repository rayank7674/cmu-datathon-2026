# Hospital recommender: symptoms + location → best hospital

This is decision support for finding a facility. It does not diagnose. The conditions it reasons about are the "possibilities we planned for", and Call 108 / 102 always comes first.

```python
from recommender import recommend, Config
r = recommend(["road accident", "heavy bleeding"], lat=18.52, lon=73.86, claims=claims_df,
              transport=["ambulance_108", "own_car"])
r.disposition          # "hospital" or "telehealth_first"
r.p_life_threatening   # e.g. 0.97
r.conditions           # condition, p, severity, weight, ...
r.ranked               # facilities, best first: score, minutes, mode, km, rank
r.alerts               # 108/102 first, then telehealth, stabilise-and-transfer, close call...
r.explanations         # top 5: summary sentence, why ranked lower, per-condition breakdown with quotes
r.trace                # one line per step, in order: what it did and how long it took
```

`claims_df` is `vf_gold.facility_service_canonical WHERE claim_detected`, which is exactly what the app's `load_claims()` returns.

## Control flow

| # | Step | Reads | Writes | Parameters (config.py unless stated) |
|---|---|---|---|---|
| 1 | `normalise_symptoms` | raw text, patient | symptom codes, red flags, unrecognised | `symptoms.csv` (synonyms, red_flag) |
| 2 | `infer_conditions` | symptoms | P(condition) | `conditions.csv` prior; `likelihood.csv` P(symptom \| condition); leak per symptom in `symptoms.csv`, otherwise `likelihood_leak`; `posterior_temperature`; `use_absent_symptoms` |
| 3 | `assess_severity` | P(condition) | weight, P(life-threatening), is_red | `conditions.csv` severity and life_threatening; `severity_exponent`; `red_case_threshold` |
| 4 | `triage_gate` | P(life-threatening), red flags | disposition | `telehealth_threshold` (5%), `red_flag_forces_hospital`, `unrecognised_means_hospital`, input `telehealth_says_go` |
| 5 | `find_candidates` | facilities, location | candidates with straight-line km | `search_radius_km`, `expand_radius_km`, `usable_location_flags`, `min_candidates` |
| 6 | `travel_time` | candidates, transport | minutes and mode per facility | `transport.csv` (wait, speed, road/straight ratio, max km, allowed in red cases); `transport_overrides`; input `time_provider` (OSRM or Google) |
| 7 | `care_probability` | candidates, conditions, claims | P(care \| h, c) and its factors | `p_service_by_strength`, verification adjustments, `quality_weight`, `rung_shortfall_factor`, `unknown_rung_factor`, `unknown_hours_factor`, `timeliness_curve`, `late_care_benefit`; `conditions.csv` needs, min_rung, tau_min |
| 8 | `score` | P(care), P(condition), weight | ranked (need_met) | (none) |
| 8b | `travel_burden` | ranked, P(life-threatening) | score = need_met − burden | `travel_burden_weight`, `travel_burden_cap_min` |
| 9 | `safety_rules` | everything | alerts | `SAFETY_RULES` list, `stabilise_threshold_min`, `stabilise_min_rung`, `close_call_margin` |
| 10 | `explain` | ranked, factors | explanations, nearest_not_chosen | `top_n` |

### The maths

* **Step 2.** P(c | S) ∝ prior(c) × Π over the reported symptoms of P(s | c). The result is then tempered (raised to the power 1/T) and normalised to sum to 1.
* **Step 6.** minutes = wait + km × circuity / speed × 60, using the fastest mode the person has that is allowed for this case. In a red case, walking, a two-wheeler and the bus are excluded. If no mode is left, the 108 ambulance is used.
* **Step 7.** P(care | h, c) = capability × rung × hours × timeliness:
  * capability = Π over the condition's requirements of the best alternative's P(service exists) × credit. `needs` is written as `cardiac_cath|icu:0.6;emergency`: `;` means all of these are required, `|` means any one of these is enough, and `:0.6` gives an alternative partial credit.
  * P(service exists) depends on claim strength (strong 0.85, moderate 0.70, weak 0.40, no claim 0.03). Verified raises it to at least 0.97, externally supported adds 0.05, contradicted halves it, a plausibility flag multiplies it by 0.8, and it is blended with the record's quality score.
  * rung = 0.5 for each rung below the condition's minimum.
  * hours = 0.9 for a life-threatening condition when the facility doesn't say it runs the service 24x7.
  * timeliness = late + (1 − late) × exp(−minutes / tau), where tau is set per condition (30 minutes for a newborn emergency, 48 hours for a minor illness).
* **Step 8.** score(h) = Σ_c P(c) · severity(c)^γ · P(care | h, c) / Σ_c P(c) · severity(c)^γ. This is the expected share of the severity-weighted need that the hospital meets, from 0 to 1. The explanation's per-condition contributions add up to this `need_met`.
* **Step 8b.** score = need_met − 0.15 × (1 − P(life-threatening)) × min(minutes, 240) / 240. For a non-urgent case, a 4-hour trip is a cost in its own right. In a red case the penalty is close to zero. Remove the step to rank on need_met alone.

## How to change it

* **Numbers.** Use `Config(telehealth_threshold=0.1)`, `cfg.replace(...)`, or `Config.from_json(path)`.
* **Medical knowledge.** Edit the CSVs in `knowledge/`, or point `knowledge_dir` at your own copies.
* **Steps.** `PIPELINE` is a plain list. `without(PIPELINE, "triage_gate")`, `replace_step(PIPELINE, "infer_conditions", my_fn)` and `insert_after(PIPELINE, "travel_time", my_fn)` each return a new list, which you pass as `recommend(..., pipeline=...)`. A step is any `fn(state, cfg) -> state`, and each step's docstring lists the fields it reads and writes. Setting `state.stop = True` ends the run early.
* **Safety rules.** Rules are listed in `steps.SAFETY_RULES`. Each is `rule(state, cfg)` and calls `state.alert(rule, level, text)`.
* **Road times.** Pass `time_provider=fn(lat, lon, candidates_df, mode_row) -> minutes array` to replace the straight-line driving estimate.

## Known limits

* The symptom, condition and likelihood numbers are illustrative judgement, not clinical values. They need a clinician's review.
* Travel time is a straight-line estimate until a road-time provider is plugged in. The ambulance wait (25 min) comes from Maharashtra 108 data (Jana et al. 2023).
* Service presence comes from the facility's own text: 99.94% of claims are self-reported. Whether a service is available right now is always unknown.
* Facilities with a suspect or invalid location are never ranked by travel time.

Tests: `python -m pytest -q tests` from the `app/` folder.
