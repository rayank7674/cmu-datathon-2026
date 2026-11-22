# Hospital recommender: symptoms + location → best hospital

This is decision support for finding a facility. It does not diagnose. The conditions it reasons about are the "possibilities we planned for", and Call 108 / 102 always comes first.

```python
from recommender import recommend, Config
r = recommend(["road accident", "heavy bleeding"], lat=18.52, lon=73.86, claims=claims_df,
              transport=["ambulance_108", "own_car"], onset_min=30)   # started 30 minutes ago
r.disposition          # "hospital" or "telehealth_first"
r.p_life_threatening   # e.g. 0.97
r.conditions           # condition, p, severity, weight, ...
r.ranked               # facilities, best first: score, minutes, mode, km, rank
r.alerts               # 108/102 first, then telehealth, stabilise-and-transfer, close call...
r.explanations         # top 5: summary sentence, why ranked lower, per-condition breakdown with quotes
r.trace                # one line per step, in order: what it did and how long it took
```

`claims_df` is `vf_gold.facility_service_canonical WHERE claim_detected`, which is exactly what the app's `load_claims()` returns.

## How an emergency is ranked (changed after the critical review, 2026-11-22)

**What counts as an emergency (changed after the test report, 2026-11-22).** A case is an emergency if it has a red-flag symptom, or the chance of a life-threatening cause is 20% or more (`red_case_threshold`; it was 5%, which made a sore throat an emergency: 14 of 15 non-emergency test cases got the red screen, now 3 of 15, at the cost of 1 of 39 test emergencies becoming "see a doctor today"). From 5% to 20% the result is "see a doctor today" with the ordinary ranking. Under 5%, and only if every phrase was understood, it is telehealth first. If nothing typed was understood, the case is an **emergency of an unknown kind**: hospitals are judged only on emergency care and ICU (`unclear_needs`) and the screen asks the person to use the symptom list. Test scripts and results: `validation/rigor/`.

**Non-emergencies (changed 2026-11-22 after a sore throat was sent 50 minutes away).** The weighted score favoured big hospitals for any complaint, and the trip was timed with the ambulance wait. A non-emergency is now ordered by step `nearest_suitable`: places that fit the most likely condition first (care level at or above what it needs, its text mentions any needed service, not single-specialty, name not pointing at another specialty), then nearest first; the score only breaks ties. The 108 ambulance is not used to time the trip, and walking is considered when it is faster (`nonred_*` in config.py).

No formula decides an emergency (a "red case"). The order is:

1. **Call 108 / 102 or 112 first.** The crew decides where to go; the list is for them and the caller.
2. **Who is ranked at all.** Facilities with a usable location, minus single-specialty places (eye, dental, IVF, skin, diagnostic-only; `single_specialty_mask`). In validation these were first choice for 2.4% of emergencies before the rule. Claims carrying a plausibility flag are ignored.
3. **Evidence tiers** for the main emergency (the most likely life-threatening possibility), step `evidence_tiers`:
   * tier 1: the facility's own text directly states every needed service (a strong or moderate claim), at the needed care level;
   * tier 2: for every need it directly states the service or a listed alternative (`conditions.csv` needs, e.g. CT instead of a stroke unit);
   * tier 3: it directly states part; tier 4: only a label or hint, or below the care level; tier 5: nothing.
   * **Name check.** A hospital whose name points at another specialty (a heart institute for a pregnancy emergency, a children's hospital for a stroke, a cancer centre) is ranked one tier lower and its card says so, unless the name also says general, multispeciality or medical college (`focus_mismatch`, `SPECIALTY_FOCUS` in steps.py). A hand audit of validation first choices found 3.2% of them were such hospitals. A name is weak evidence, so this demotes and does not exclude.
4. **Nearest first inside a tier.** Times within 5 minutes count as equal, and only then does the old score break the tie.
5. **Cross-tier time cap.** If the first tier-1 facility is more than 60 minutes further than a lower-tier hospital that directly states emergency care (care level 3+), that nearer hospital is shown second and the alert says "Two options for the 108 crew to choose between". The 60 minutes is our judgement, not a published threshold (`tier_time_cap_min`).

"Directly states" means the facility says so in its own text. Nothing is independently verified, and the cards say that. Non-urgent cases still use the score below.

What the numbers in this package do and do not mean:

* The hand-check counts (strong 20/20, moderate 16/20, weak 7/20; `claim_reliability.csv`) measure **how often our extraction matched the source text**. They are not the chance that a service exists or is open. They are used only as evidence weights in the tie-break score and are never shown as a probability of treatment.
* The published delay curves (`time_curves.csv`) come from studies of different treatments and endpoints. They are shown on the card as a cited reason why time matters, and feed the tie-break score and the offline sensitivity run. They do not decide an emergency ranking, and no "benefit left" percentage is shown.
* `trip_risk_curve` is off. It was tried, improved the look of the results, and was dropped because it was chosen after seeing them.

## Control flow

| # | Step | Reads | Writes | Parameters (config.py unless stated) |
|---|---|---|---|---|
| 1 | `normalise_symptoms` | raw text, patient | symptom codes, red flags, unrecognised | `symptoms.csv` (synonyms, red_flag) |
| 2 | `infer_conditions` | symptoms | P(condition) | `conditions.csv` prior; `likelihood.csv` P(symptom \| condition); leak per symptom in `symptoms.csv`, otherwise `likelihood_leak`; `posterior_temperature`; `use_absent_symptoms` |
| 3 | `assess_severity` | P(condition) | weight, P(life-threatening), is_red | `conditions.csv` severity and life_threatening; `severity_exponent`; `red_case_threshold` |
| 4 | `triage_gate` | P(life-threatening), red flags | disposition | `telehealth_threshold` (5%), `red_flag_forces_hospital`, `unrecognised_means_hospital`, input `telehealth_says_go` |
| 5 | `find_candidates` | facilities, location | candidates with straight-line km | `search_radius_km`, `expand_radius_km`, `usable_location_flags`, `min_candidates` |
| 5b | `emergency_eligibility` | candidates, is_red | red case: single-specialty facilities removed; `excluded` | `red_exclude_single_specialty`, `single_specialty_*` |
| 6 | `travel_time` | candidates, transport | minutes and mode per facility | `transport.csv` (wait, speed, road/straight ratio, max km, allowed in red cases); `transport_overrides`; input `time_provider` (OSRM or Google) |
| 7 | `care_probability` | candidates, conditions, claims, onset_min | P(care \| h, c) and its factors | `claim_reliability.csv` (hand-check counts), `cautious_for_red`, `cautious_quantile`, `p_no_claim`; `time_curves.csv` (published delay curves) and `conditions.csv` time_curve; verification adjustments, `quality_weight`, `rung_shortfall_factor`, `unknown_rung_factor`, `unknown_hours_factor`; fallback `timeliness_curve`, `late_care_benefit`, tau_min |
| 8 | `score` | P(care), P(condition), weight | ranked (need_met) | (none) |
| 8b | `travel_burden` | ranked, P(life-threatening) | score = need_met − burden | `travel_burden_weight`, `travel_burden_cap_min` |
| 8c | `nearer_on_ties` | ranked, is_red | red case: near-tied scores reordered soonest first | `nearer_on_ties_margin` |
| 8d | `evidence_tiers` | ranked, care, is_red | red case: tier, states, missing, nearer_option; re-ranked | `rank_by_tier_for_red`, `tier_direct_strengths`, `tier_time_band_min`, `tier_time_cap_min`, `red_ignore_flagged_claims` |
| 9 | `safety_rules` | everything | alerts | `SAFETY_RULES` list, `stabilise_threshold_min`, `stabilise_min_rung`, `stabilise_claim_strengths`, `stabilise_exclude_*`, `single_specialty_*`, `close_call_margin` |
| 10 | `explain` | ranked, factors | explanations, nearest_not_chosen | `top_n` |

### The maths

* **Step 2.** P(c | S) ∝ prior(c) × Π over the reported symptoms of P(s | c). The result is then tempered (raised to the power 1/T) and normalised to sum to 1.
* **Step 6.** minutes = wait + km × circuity / speed × 60, using the fastest mode the person has that is allowed for this case. In a red case, walking, a two-wheeler and the bus are excluded. If no mode is left, the 108 ambulance is used.
* **Step 7.** P(care | h, c) = capability × rung × hours × timeliness:
  * capability = Π over the condition's requirements of the best alternative's P(service exists) × credit. `needs` is written as `cardiac_cath|icu:0.6;emergency`: `;` means all of these are required, `|` means any one of these is enough, and `:0.6` gives an alternative partial credit.
  * Evidence weight per claim (called `p_exists` in the code for historical reasons; it is NOT the chance the service exists). `claim_reliability.csv` holds our hand check of 60 claims (medallion README section 24): the facility's text states the service outright for 20 of 20 strong claims, 16 of 20 moderate and 7 of 20 weak. With a uniform prior that is a Beta(supported + 1, missed + 1) belief about text support, whose mean is 0.95 / 0.77 / 0.36. In a red case the model plans on the cautious side and uses the lower quartile of that belief, 0.94 / 0.72 / 0.29 (`cautious_quantile`). No claim at all is 0.03 (assumed). Then: verified raises it to at least 0.97, externally supported adds 0.05, contradicted halves it, a plausibility flag multiplies it by 0.8, and it is blended with the record's quality score. The ordering agrees with one outside list: of blood-bank claims, 29% of strong, 19% of moderate and 2% of weak ones match a blood bank in the government eRaktKosh list. Matching is incomplete and can be wrong, so this is corroboration of the ordering only.
  * rung = 0.5 for each rung below the condition's minimum.
  * hours = 0.9 for a life-threatening condition when the facility doesn't say it runs the service 24x7.
  * timeliness = the share of the benefit of care still left after the total delay t = time since onset + wait + trip. For six conditions it is a **published curve** (`time_curves.csv`, with the source shown on each result card):

    | Conditions | Curve | Source |
    | --- | --- | --- |
    | Heart attack | 1.075^(−t / 30 min) | De Luca 2004, Circulation: each 30 min of delay raises 1-year mortality 7.5% |
    | Stroke | 100% to 90 min, 53% at 225 min, 20% from 360 min, straight lines between | Emberson 2014, Lancet: good-outcome gain 9.8, 5.2, 2.0 points for under 3 h, 3 to 4.5 h, later |
    | Major injury with bleeding, pregnancy emergency | 75% falls in a straight line to zero at 180 min; the other 25% follows the injury curve | Gayet-Ageron 2018, Lancet: tranexamic acid benefit falls 10% per 15 min, none after 3 h. The 25% split is assumed. |
    | Serious head injury | 1.04^(−t / 10 min) | Gauss 2019, JAMA Surgery: odds of death up 4% per 10 min before hospital |
    | Sepsis | 1.04^(−t / 60 min) | Seymour 2017, NEJM: odds of death up 4% per hour of delay |

    A risk that multiplies by r every m minutes is turned into "benefit left" as r^(−t/m). Conditions with no published curve yet (newborn emergency, breathing failure, seizure, burns, poisoning, kidney failure, and the non-urgent ones) keep the assumed form late + (1 − late) × exp(−t / tau) and are labelled "assumed". `use_published_time_curves=False` turns the published curves off.
* **Step 8.** score(h) = Σ_c P(c) · severity(c)^γ · P(care | h, c) / Σ_c P(c) · severity(c)^γ. This is the expected share of the severity-weighted need that the hospital meets, from 0 to 1. The explanation's per-condition contributions add up to this `need_met`.
  * trip risk: the curves above come from patients who reached a hospital, so for every life-threatening condition the trip itself (wait + travel) is also charged 1.04^(−trip / 10 min) (Gauss 2019). That figure was measured for major injury; applying it to other emergencies is our assumption, shown on the card. `trip_risk_curve=None` turns it off.
* **Step 8c.** In a red case, facilities whose score is within 0.03 of the best are ordered by time, soonest first. The published curves come from patients who reached care, so they understate the risk of the trip itself; when the evidence can't separate two hospitals, the nearer one goes first.
* **Step 8b.** score = need_met − 0.15 × (1 − P(life-threatening)) × min(minutes, 240) / 240. For a non-urgent case, a 4-hour trip is a cost in its own right. In a red case the penalty is close to zero. Remove the step to rank on need_met alone.

## How to change it

* **Numbers.** Use `Config(telehealth_threshold=0.1)`, `cfg.replace(...)`, or `Config.from_json(path)`.
* **Medical knowledge.** Edit the CSVs in `knowledge/`, or point `knowledge_dir` at your own copies.
* **Steps.** `PIPELINE` is a plain list. `without(PIPELINE, "triage_gate")`, `replace_step(PIPELINE, "infer_conditions", my_fn)` and `insert_after(PIPELINE, "travel_time", my_fn)` each return a new list, which you pass as `recommend(..., pipeline=...)`. A step is any `fn(state, cfg) -> state`, and each step's docstring lists the fields it reads and writes. Setting `state.stop = True` ends the run early.
* **Safety rules.** Rules are listed in `steps.SAFETY_RULES`. Each is `rule(state, cfg)` and calls `state.alert(rule, level, text)`.
* **Road times.** Pass `time_provider=fn(lat, lon, candidates_df, mode_row) -> minutes array` to replace the straight-line driving estimate.

## Known limits

* The symptom-to-condition likelihoods, the severity weights and the condition priors are still illustrative judgement, not clinical values. They need a clinician's review. (Claim reliability and six time curves are now measured or published; see step 7.)
* The published delay curves come from other countries and from patients who reached a hospital. They are used for the shape of the trade-off, not as a prediction of lives saved. Because they are flatter than the old hand-set curves, the model is more willing to travel for better-evidenced care; `validation/RESULTS.md` reports by how much.
* The hand check measures "the facility's text states the service", not "the service works today", and has only 20 claims per strength, hence the error bars.
* Travel time is a straight-line estimate until a road-time provider is plugged in. The ambulance wait (25 min) comes from Maharashtra 108 data (Jana et al. 2023).
* Service presence comes from the facility's own text: 99.94% of claims are self-reported. Whether a service is available right now is always unknown.
* Facilities with a suspect or invalid location are never ranked by travel time.
* A facility is named as "stabilise here first" only if it meets all of these: it states emergency care directly (a strong or moderate claim, not a specialty tag), it is care level 3 or higher, none of its claims has a plausibility flag, and it is not single-specialty (judged by level_kind or by name, using the same pattern as notebook 04). This rule was added after a live test named an eye hospital.

Tests: `python -m pytest -q tests` from the `app/` folder (79 tests, 40 of them must-never-happen cases in `tests/test_negative_cases.py`, including the known wrong-claim cases: dental delivery unit, dental trauma, DVT thrombolysis, eye-hospital emergency).

## Validation without outcome labels

`python validation/validate.py` (from `app/`, about 15 minutes) places 3,000 synthetic emergency patients in proportion to population, compares the pick with the nearest facility and with the nearest facility that claims every needed service, and redraws every uncertain number from its error bar to see how often the first choice changes. This is model behaviour and stability, not clinical validation. Results: `validation/RESULTS.md`, `results.json`, `stability.png`. Inputs are in `validation_data/` (an export of the app's claims query and a population-weighted sample of the access grid).

### Everyday symptoms (calibration note)

Fourteen everyday symptoms (cough, sore throat or cold, mild fever, headache, dizziness, vomiting, sweating, wheeze and so on), alone or in any pair, are not emergencies. Three settings make that hold: `likelihood_leak_minor` (0.10) is the chance that an everyday symptom shows up in a non-life-threatening condition that does not list it; a few rows in `knowledge/likelihood.csv` were raised for minor illness; and `red_threshold_when_unread` (0.10) keeps a more cautious line when part of the typed text could not be read. Tests loop over every everyday symptom, every pair and typed versions, and check for a nearby general first pick with no ambulance wait. These numbers are hand-set judgement, not fitted to patient outcomes.
