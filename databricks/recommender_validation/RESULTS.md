# Validation results (no outcome labels)

2,998 synthetic emergency patients placed in proportion to population (WorldPop 1 km cells), each given one of 8 emergencies with equal chance and an onset time of 0 to 120 minutes. Travel time is the straight-line estimate with the 108 ambulance or an auto/taxi. Produced by `validation/validate.py`; every number below is in `results.json`.

## Against going to the nearest facility

- The nearest facility makes **no claim for a service the emergency needs** for 69% of patients. Even the nearest facility that claims emergency care lacks one for 52%. Our pick lacks one for 7%.
- Our pick is a different facility from the nearest for 81% of patients (and from the nearest emergency-claiming facility for 76%).
- When it differs, the extra trip is 26 minutes at the median and 109 minutes for one patient in ten. Median total time to our pick is 91 minutes against 60 to the nearest.
- By the model's own score (share of the need met, which counts both capability and delay), our pick averages 0.26, the nearest facility 0.07 and the nearest emergency-claiming facility 0.10. This is the model marking its own homework: it shows the size of the trade-off, not a health outcome.
- **Against the nearest facility that claims every needed service** (a sensible simple rule): our pick is that same facility for 39% of patients and it is in our top three for 51%. When we pick something else, it is 11 minutes further at the median (72 for one in ten), chosen for stronger evidence, a higher care level or a 24x7 statement. Median time to that facility is 82 minutes; its mean score is 0.18. For 1% of patients no facility in range claims everything needed.
- 69% of picks are more than 60 minutes away and 33% more than 120.

| Emergency | Patients | Nearest lacks a needed claim | Pick differs from nearest | Median min to pick | Median min to nearest | Need met: pick | Need met: nearest |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| head injury serious | 401 | 84% | 95% | 118 | 62 | 0.33 | 0.05 |
| heart attack | 416 | 72% | 93% | 101 | 59 | 0.30 | 0.05 |
| major trauma | 322 | 60% | 65% | 77 | 61 | 0.19 | 0.08 |
| newborn emergency | 392 | 29% | 84% | 87 | 62 | 0.16 | 0.07 |
| obstetric emergency | 380 | 94% | 52% | 79 | 58 | 0.08 | 0.02 |
| poisoning envenoming | 356 | 70% | 83% | 84 | 59 | 0.28 | 0.10 |
| sepsis | 365 | 67% | 89% | 91 | 59 | 0.39 | 0.11 |
| stroke | 366 | 76% | 84% | 93 | 58 | 0.29 | 0.07 |

## Stability when every uncertain number is redrawn

300 patients, 60 redraws each. Claim reliability is drawn from the Beta posterior of the hand check, the delay curves from the published 95% intervals, the assumed curves by a factor of about 1.6 either way, and the ambulance wait (25 ± 8 min), speed (30 to 50 km/h) and road factor (1.25 to 1.6).

- **The first choice stays first in 78% of redraws.**
- When it changes, the new first choice was already in our top three in 89% of all redraws, and our first choice is still in the top three in 92%.
- For 21% of patients the first choice does not change in any of the redraws.

| Numbers redrawn (others held fixed) | First choice unchanged |
| --- | ---: |
| Claim reliability (hand-check error bars) | 86% |
| Time curves (published intervals and assumed curves) | 87% |
| Ambulance wait, speed and road factor | 93% |
| All three together | 78% |

| Emergency | First choice unchanged (all redrawn) |
| --- | ---: |
| head injury serious | 76% |
| heart attack | 76% |
| major trauma | 77% |
| newborn emergency | 68% |
| obstetric emergency | 84% |
| poisoning envenoming | 78% |
| sepsis | 76% |
| stroke | 89% |

## Checks

- The entered symptoms produced the intended emergency as the most likely possibility for 100% of patients, and 100% were treated as red cases.
- 25 automated tests (`python -m pytest -q tests`) cover the sanity properties: more delay never raises a score, every published curve starts at 1 and only falls, red-flag symptoms always go to hospital, a suspect location is never ranked.

## What this does not show

- No outcome data exists, so nothing here is a count of lives saved.
- A claim is the facility's own text; 'claims the service' is not 'has the service today'.
- Distances are straight-line to the roughly 10,000 facilities in this dataset; real road time and other facilities are not included.
- Scenarios are equally weighted, not weighted by how common each emergency is.
