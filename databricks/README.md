# Virtue Foundation facility data: medallion pipeline

*Running document. Last updated 2026-11-21. Add a line to the change log at the bottom whenever something in the pipeline changes.*

This README explains what the pipeline does, the order to run it in, and **why** each decision was made: the evidence behind it, the trade-offs, and what it buys us. Read it first if you are new to the project or about to change a notebook.

---

## 1. What we are building and why it shapes everything

The CMU Datathon 2026 challenge asks for a tool that takes a **service** (dialysis, C-section, NICU, MRI, trauma care and so on) and a **location**, and returns the best facilities, each with:

- an **explanation that quotes the facility's own text** as evidence, and
- an **honest statement of uncertainty**.

It must run on **Databricks Free Edition**.

Every cleaning decision below follows from those three requirements:

| Requirement | What it forces in the pipeline |
| --- | --- |
| Quote evidence | Never throw away the original wording. Every derived claim keeps a pointer to the raw text it came from (`raw_text`, `quote`, `evidence`). |
| Honest uncertainty | Don't delete suspicious rows. **Flag** them, score them (`quality_score`), and explain the flag in plain words (`quality_notes`, `level_reason`), so the tool can say *why* it is unsure. |
| Free Edition | Use only SQL and small notebooks on the serverless starter warehouse. No paid features (no DLT, no model serving). Every table rebuilds in seconds to a minute. |

## 2. Where everything lives

**Databricks folder:** `/Workspace/Users/rayankha08@gmail.com/CMU Datathon 2026` (renamed from "Virtue Foundation Analysis" on 2026-11-21)

**Catalog:** `workspace`. There are three schemas, one per layer: `vf_bronze`, `vf_silver` and `vf_gold`. The source data lives in the read-only Marketplace catalog `databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset`.

**Copies of every notebook:** the project files folder `medallion/`.

### Run order

| # | Notebook | Language | Reads | Writes | Runtime |
| --- | --- | --- | --- | --- | --- |
| 01 | Data Exploration & Overview | Python | source | nothing | (team's own exploration) |
| 02 | Bronze Ingest | SQL | Marketplace source | `vf_bronze.*_raw` | ~15 s |
| 03 | Silver Clean | SQL | `vf_bronze` | `vf_silver.*` | ~1 min |
| 04 | Gold Care Levels | SQL | `vf_silver` | `vf_gold.facility_level*` | ~40 s |
| 05 | External Gov Upload | Python | files in the `vf_bronze.external` volume | `vf_bronze.ext_*_raw`, `vf_silver.external_facilities_gov` | ~1 min, only when gov files exist |
| 06 | External Checks | SQL | OSM file + `vf_silver` + `vf_gold.facility_level` | `vf_silver.external_facilities`, `vf_gold.facility_external_*` | ~1 min |
| 07 | Gold Fixes and Tests | Python (SQL inside) | `vf_silver.facilities`, `vf_gold.facility_level`, `facility_level_evidence`, `facility_external_check`, `facility_service` if present | `vf_gold.facility_level_v2`, `vf_gold.quality_tests`, `vf_silver.facility_geo_quality`, `vf_silver.facility_duplicate_groups`, `vf_silver.facility_duplicate_pairs` | ~2 min |
| 09 | Gold Facility Service | Python (SQL inside) | `vf_silver.facilities`, `facility_claims`, `vf_gold.facility_level`, `facility_external_*`, and notebook 07 / 10 tables if they exist (run 07 first) | `vf_gold.facility_service`, `facility_verification`, `facility_service_rules`, `facility_service_checks` | ~2.5 min |
| 11 | Geography | Python | geoBoundaries + WorldPop downloads, `vf_silver.pincode`, `nfhs_district`, `vf_gold.facility_service_canonical`, `facility_level_canonical`, `vf_silver.facility_geo_quality` | `vf_bronze.geo_*`, `vf_silver.district_polygons`, `vf_gold.district_crosswalk(_issues)`, `access_grid`, `access_evidence_gap`, `district_service_access`, `district_context`, `critical_zones`, `geography_checks` | ~3 min |

Every notebook is **safe to rerun** (`CREATE OR REPLACE`). After changing an earlier layer, rerun everything after it in order.

**Dashboard:** *Facility Field Explorer* (same folder). The **Care levels** page reads `vf_gold.facility_level` and `vf_gold.facility_external_check`.

---

## 3. How I decided what to focus on

The raw table has 10,088 rows and 51 fields, and not all problems matter equally. I ranked them by asking: *does this problem change which facility the tool recommends, or how confident it should be?*

1. **Highest priority: problems that change the answer.**
   - The services are scattered across five text fields. For example, CT appears in `equipment` for 1,377 rows but in `specialties` for none. A tool that searched one field would miss most facilities.
   - Location: the tool needs a reliable district and state for every facility.
2. **Next: problems that change confidence.**
   - Merged records whose claims grow with the number of sources: 2,783 facilities come from 50 or more web sources.
   - Contradictions, such as a postcode in another state or a map pin in another state.
   - Implausible numbers, such as 200,000 beds or the year 0.
3. **Then: problems that break joins or charts.**
   - The text "null" written in place of real nulls.
   - 254 spellings of about 36 states.
   - NFHS percentages stored as text like "(64.2)" and "*".
4. **Ignored on purpose (for now):**
   - Social-media metrics. They are kept and typed, but nothing ranks on them, because follower counts measure marketing more than care.
   - `area`. It is 1% filled.

Each fix was checked against counts from profiling, the earlier Field Explorer work, before it was written. After building, each layer was checked again with automated checks (section 8).

---

## 4. Bronze: a frozen raw copy

**What:** the three source tables copied unchanged into `vf_bronze`, plus three columns: `_ingested_at`, `_source_table` and `_row_num`.

**Why:**
- The Marketplace catalog is read-only and could change underneath us. A snapshot makes every later result reproducible.
- `_row_num` exists because `unique_id` is **not** unique: 11 ids appear twice. We need a stable key for every raw row.
- Nothing is cleaned here, so we can always compare a cleaned value with exactly what arrived.

**Check:** the row counts match the source (10,088 facilities, 165,627 post offices, 706 NFHS districts).

---

## 5. Silver: clean, typed, flagged (no rows silently dropped)

### 5.1 Helper functions (saved in `vf_silver` so anyone can reuse them)

| Function | What it does | Why |
| --- | --- | --- |
| `clean_str(s)` | Trims, collapses whitespace, and turns `"null"`, `""`, `"none"`, `"n/a"` and `[]` into real NULL | The raw table writes NULL as text in every category column (for example, 688 `operatorTypeId`s are the text "null") |
| `parse_list(s)` | Parses a JSON-array string, cleans each item and drops blanks | Service fields are JSON arrays stored as strings, with about 2,300 rows holding blank items |
| `state_key(s)` / `canon_state(s)` | Maps any state spelling to the official India Post name, or to NULL | 254 distinct values, such as "Tamilnadu", "U.p.", "Ts", "Ghaziabad, Uttar Pradesh" and "Maharastra" |
| `norm_claim(s)` | Splits camelCase, lowercases, strips punctuation and filler verbs ("provides", "offers") | Makes "gynecologyAndObstetrics" and "Provides 24/7 emergency care." matchable by keyword |

### 5.2 Broken import rows: investigated, not guessed

The profile said "~118 broken rows". Looking at them showed two different things:

- **88 fragment rows.** They have no valid `unique_id`: their id column holds text such as `*  __Clinical Pathology`. They are the leftover pieces of a description that contained line breaks and was split across rows by the CSV import. **These are not facilities**, so they go to `vf_silver.facilities_rejected` with a reason, rather than being deleted.
- **30 truncated records.** These are real facilities (valid id, name, city and zip) whose row was cut off at that line break, so they have no coordinates or source. They are **kept** and flagged with `is_truncated_record`, and their score drops by 0.40.

*Why not stitch them back together?* The source order was lost, and nothing links a fragment to its parent. Guessing would invent data, which would break the "quote real evidence" rule.

### 5.3 `facilities` (9,989 rows)

| Step | Decision | Evidence | Benefit |
| --- | --- | --- | --- |
| Duplicate ids | Keep the most complete row and list the others in `merged_row_nums` | 11 ids, each appearing twice, with the same names | One row per facility without losing which rows were merged |
| Types | Cast to INT, DOUBLE, BOOLEAN or DATE with `try_cast` | Every raw column is STRING | A bad value becomes NULL instead of failing the whole build |
| Implausible numbers | Null them, keep the original in `*_raw` and flag `implausible_numbers` | Beds up to 200,000, doctors up to 15,000, year 0, coordinates at latitude -81 | Charts and ranking aren't distorted, and the original is still there to inspect |
| Thresholds | Year 1800 to 2026; doctors 1 to 5,000; beds 1 to 10,000; coordinates inside India's box (lat 6 to 37.5, lon 68 to 97.5) | India's largest hospitals have about 3,000 to 5,000 beds | Generous enough not to clip real giants |
| Names | `facility_type`: "farmacy" becomes "pharmacy". `operator_type`: "government" becomes "public" | Value counts | Consistent groups |
| Contacts | Emails checked against a pattern; websites reduced to the bare domain | — | Usable for de-duplication later |
| Provenance | `source_count` (number of source records merged) and `distinct_source_ids` | 2,783 rows sit at the 50-source cap | A signal that claims may be inflated by merging |

### 5.4 `pincode` (19,586 rows) and `facility_geo`

- The India Post directory lists every post office, so one pincode appears many times, sometimes with several districts. We take the **most common district and state** and average the valid coordinates into a centroid.
- Facility location takes the **first method that works**:
  1. the cleaned 6-digit zip (non-digits stripped) joined to `pincode`: **9,703** facilities;
  2. otherwise, the nearest pincode centroid within 50 km of the facility's coordinates: **285**;
  3. otherwise, the stated state only: **1**.

  `geo_method` records which one was used.
- **Cross-checks become flags, not fixes:**
  - `state_mismatch` (61): the pincode's state differs from the stated state.
  - `coords_state_mismatch` (220): the map pin is nearest to a pincode in another state.
  - `coords_far_from_any_pincode` (2).

  *Why flag instead of correcting?* We don't know which of the two conflicting fields is wrong. The tool can say "location uncertain".

### 5.5 `facility_claims`: the evidence trail (about 537,000 rows)

There is one row per facility, field and normalised claim. It keeps `raw_text` (the exact wording to quote), `norm_text` (for matching), `first_position`, and `mentions`, the number of times the claim repeated before de-duplication. Merged records repeat claims, so `mentions` shows that inflation without letting it double-count.

| Field | Claims | Facilities |
| --- | --- | --- |
| capability | 220,295 | 9,918 |
| procedure | 147,335 | 8,840 |
| specialty | 117,989 | 9,959 |
| equipment | 51,939 | 7,107 |

### 5.6 `nfhs_district` (706 districts)

NFHS prints a value in parentheses when it rests on 25 to 49 cases, and `*` when there are fewer than 25. So for each of the 49 text columns:
- the parentheses are stripped and `<col>__low_sample = true` is set;
- `*` becomes NULL;
- the value is cast to DOUBLE.

We kept the low-sample flag instead of dropping those values, because low-sample districts are often exactly the under-served ones.

### 5.7 Quality flags and `quality_score`

The score starts at 1.0 and loses a fixed penalty for each flag (it never goes below 0). `quality_notes` spells out each flag in words.

| Flag | Penalty | Count | Why this weight |
| --- | --- | --- | --- |
| is_truncated_record | 0.40 | 30 | Most of the record is missing |
| specialty_doctor_mismatch (1 doctor, 30+ distinct specialties) | 0.20 | 22 | A clear sign of scraped or merged inflation |
| state_mismatch | 0.15 | 61 | Location conflict |
| coords_state_mismatch | 0.15 | 220 | Location conflict |
| high_source_merge (20+) / at_source_cap (50+) | 0.10 + 0.10 | 5,595 / 2,783 | Claims are inflated by the number of sources |
| coords_invalid, coords_far_from_any_pincode, implausible_numbers, no district | 0.10 each | 6 / 2 / 10 / 1 | Smaller data errors |
| is_duplicate_id | 0.05 | 11 | Merged, but otherwise fine |

The result: 7,101 facilities score above 0.8, and 64 score 0.6 or below.

*A correction worth knowing:* the early profile said 529 facilities had "1 doctor and 30+ specialties". That counted repeated specialty entries. With distinct specialties the real number is **22**.

---

## 6. Gold: care levels

### 6.1 The framework

The project document *India Facility Care Levels for Routing* places each facility on India's five-rung referral ladder:

| Rung | Public facility | Private equivalent | Can reliably do |
| --- | --- | --- | --- |
| 1 | Sub-centre / Ayushman Arogya Mandir | (none) | Vaccines, antenatal checks, no doctor |
| 2 | Primary Health Centre | Clinic / polyclinic | MBBS doctor, normal deliveries at 24x7 PHCs |
| 3 | Community Health Centre / FRU | Nursing home / small hospital | 30 beds, specialists, C-section at FRUs |
| 4 | Sub-district / district hospital | Multispecialty hospital | ICU, SNCU, dialysis, CT, blood bank |
| 5 | Medical college / AIIMS | Super-specialty hospital | Neurosurgery, cath lab, Level III NICU, MRI, transplant |

### 6.2 How a level is assigned (`vf_gold.facility_level`, notebook 04)

1. **Signals.** The editable table `level_signal_rules` holds 29 keyword rules in groups: tertiary, district, block, service, accreditation and scheme. They run against every claim and the description. The best quote for each facility and signal goes to `facility_level_evidence`, preferring equipment, then procedure, capability, specialty and finally description, because equipment is the most concrete proof.
2. **The name stating a public tier wins**, for example "District Hospital Pulwama", "Primary Health Centre Marauna" or "Government Medical College". This applies only when the operator is public or unknown. Names that are really labs or pharmacies are excluded (one lab's address said "GMCH Main Gate").
3. **Academic affiliation** puts a hospital at rung 5, except AYUSH colleges (ayurvedic, homoeopathic, unani, siddha), which aren't allopathic tertiary care.
4. **Otherwise the level is built up from evidence:**
   - Start at rung 2 for clinics and rung 3 for hospitals.
   - Raise to **rung 4** with 100+ beds, or 3 or more district-level services (ICU, CT, dialysis, blood bank, 24x7 emergency, SNCU, ventilator).
   - Raise to **rung 5** with 300+ beds plus a tertiary service, or 3 tertiary plus 3 district services (neurosurgery, cardiac surgery, cath lab, organ transplant, MRI, NICU, radiotherapy).
5. **Guards that came from looking at real results:**
   - *Single-specialty hospitals* (eye, dental, IVF, skin, ENT, psychiatry, rehab) are capped at rung 3 unless they have 100+ beds. Without this cap, "ASG Eye Hospital" reached rung 4 because it listed a corneal "transplant" and CT.
   - *Transplant* now means organ or marrow transplant only, so corneal and hair transplants don't count.
   - *Diagnostic centres and pharmacies* get no rung, only a kind, which matches the framework's "standalone" category.
   - *Delivery* signals need a real delivery phrase ("labour room", "normal delivery", "maternity ward"). The specialty name "gynecologyAndObstetrics" alone doesn't count; it had tagged 4,280 facilities as delivery points.
6. **Confidence**
   - **High** when the name, a teaching affiliation, or beds plus services back the level.
   - **Medium** when beds alone back it, or two or more kinds of service signal.
   - **Low** when it rests on the facility type alone.
   - It drops one step when `quality_score` is under 0.7.
7. **Reason.** `level_reason` explains the level in words, for example "650 beds; tertiary services: cardiac_surgery; district-level services: emergency_24x7".

### 6.3 Result

| Rung | Facilities | Main kinds |
| --- | --- | --- |
| No rung | 519 | Diagnostic centres, pharmacies |
| 1 | 2 | Sub-centres |
| 2 | 3,640 | Clinics, dental clinics, PHCs |
| 3 | 3,124 | Nursing homes, CHCs, single-specialty hospitals |
| 4 | 1,686 | Multispecialty and district hospitals |
| 5 | 1,018 | Super-specialty hospitals, 154 medical colleges |

### 6.4 Service-specific levels

These are in the same table and follow the framework's service schemes:

- **Newborn:**
  - NICU (Level III): 725
  - SNCU (Level II): 12
  - Stabilisation unit: 1
  - Newborn care corner at a delivery point
- **Obstetric:**
  - CEmONC (C-section plus blood bank): 61
  - C-section without a listed blood bank: 382
  - BEmONC (deliveries only)
- **Trauma:**
  - Like Levels I and II (trauma plus neurosurgery, CT and ICU): 420
  - Like Level III (stabilise, with CT or ICU)
  - Stabilise and transfer
- **Dialysis:**
  - Emergency plus maintenance (dialysis plus ICU plus 24x7 emergency): 741
  - Maintenance only: 308
- **Self-claims, not yet verified:** NABH, JCI, PM-JAY, CGHS.

**Caveat:** these are inferences from scraped website claims, not official designations. A listed service may be out of date or aspirational, and 75% of rows have no bed count.

---

## 7. External checks: does an outside source agree?

### 7.1 Sources

| Source | Status | What it adds |
| --- | --- | --- |
| **OpenStreetMap**, Humanitarian OpenStreetMap Team export of India health facilities, October 2026, from [HDX](https://data.humdata.org/dataset/hotosm_ind_health_facilities) | Loaded: 139,664 records, `vf_bronze.osm_health_facilities_raw` | Independent type (hospital, clinic, doctor), sometimes operator, and names that state the public tier. It includes the 2019 community import of the government hospital directory. |
| National Hospital Directory with Geo Code (MoHFW/NIHFW, via AIKosh or data.gov.in) | Waiting: AIKosh needs a sign-in | Hospital category, ownership and specialties for government and private hospitals |
| All India Health Centres Directory (MoHFW, Oct 2016) | Waiting | The official SC / PHC / CHC / DH label: the strongest check on public rungs |
| Hospitals empanelled under CGHS | Waiting | A government-vetted list of capable private hospitals |

*Why OpenStreetMap first:* the government files have no direct download link, and the OpenStreetMap download server dropped our connections. HOT publishes the same OpenStreetMap data as a ready-made GeoParquet file, which we pulled directly.

### 7.2 Matching method (notebook 06)

1. **Common schema.** Every source is reshaped into `vf_silver.external_facilities` with the same columns: name, kind, tier read from the name, operator, beds, coordinates. Adding a source therefore never changes the matching code.
2. **Candidates** are outside records within 2 km. A ±0.02° box is used first, which is fast, and then the true distance in km.
3. **Name similarity** is word-set (Jaccard) overlap after removing:
   - generic words (hospital, clinic, centre, medical, dental, eye, and others);
   - **the facility's own city, district and state words**. Without this, "Dental Care Center Bhubaneswar" matched "AIIMS, Bhubaneswar".
4. **Thresholds** get stricter with distance: similarity ≥ 0.34 within 300 m, ≥ 0.5 within 1 km, and ≥ 0.67 within 2 km. The best match per facility and source is kept.
5. **Verdict** in `vf_gold.facility_external_check.level_check`:
   - *confirms*: the same public tier in the name, or the outside type fits our rung (a hospital for rungs 3 to 5, a clinic or doctor for rungs 1 to 2);
   - *close (1 rung apart)*;
   - *conflicts*: the tier is 2 or more rungs away;
   - *type differs*: a hospital that we put at rung 1 to 2, or a doctor or dentist that we put at rung 4 to 5. OpenStreetMap often tags small clinics as hospitals, so this is a prompt to look, not proof of an error;
   - *matched, no tier info*;
   - *no outside record*.

   `external_evidence` gives the outside name, type and distance in metres.

### 7.3 Result (OpenStreetMap only)

| Verdict | Facilities |
| --- | --- |
| confirms | 2,411 |
| matched, no tier info | 198 |
| type differs | 132 |
| close (1 rung apart) | 7 |
| conflicts | 2 |
| matched, no rung to check | 23 |
| no outside record | 7,216 |

### 7.4 Adding the government files (or any other list)

1. In Catalog, go to `workspace` > `vf_bronze` > Volumes > `external`. Upload each CSV or Excel file into its folder: `gov_hospital_directory/`, `gov_health_centres/` or `cghs/`. A new folder name also works and becomes the source name.
2. Run **05 - External Gov Upload**.
   - It lands each folder as `vf_bronze.ext_<folder>_raw`.
   - It **finds the columns by name patterns** (name, type or category, ownership, beds, pincode, state, district, latitude and longitude, or a combined "coordinates" column) and prints which column it used.
   - If a guess is wrong, set it in the `OVERRIDES` dictionary.
   - It reads the tier from the name and category (for example "PHC" becomes rung 2 and "District Hospital" becomes rung 4), and the operator from the ownership or category text.
3. Run **06 - External Checks**. The new rows are matched alongside OpenStreetMap automatically.

This was tested on 2026-11-21 with a two-row sample, which produced the correct tier and operator. The sample was deleted afterwards.

---

## 8. Checks that run every time

- **Bronze:** row counts equal the source.
- **Silver (`vf_silver.silver_checks`, 10 of 10 pass):**
  - no facility lost (facilities + rejected + merged = 10,088);
  - ids are unique;
  - no "null" text is left;
  - all coordinates are inside India;
  - 36 or fewer states;
  - district coverage is at least 95%;
  - pincodes are unique;
  - every claim has evidence text;
  - NFHS percentages are between 0 and 100;
  - NFHS states are recognised.
- **Gold:** a summary of levels by confidence at the end of notebook 04, and of verdicts at the end of notebook 06.

---

## 9. How to change things

| You want to… | Do this |
| --- | --- |
| Add or tune a care-level keyword | Edit `vf_gold.level_signal_rules` (or the `RULES` list in the notebook 04 source), then rerun 04 and 06 |
| Change a level threshold (beds, signal counts) | Edit the `scored` step in notebook 04 |
| Fix a state spelling that maps to NULL | Add it to the alias `CASE` in `canon_state` (notebook 03), then rerun 03 onward |
| Add an outside data source | Drop the file in a new volume folder, then run 05 and 06 |
| Fix a wrong column guess in 05 | Add `{"<folder>": {"name": "<column>"}}` to `OVERRIDES` |

---

## 10. Known limitations and open questions

- Levels are inferred from self-reported, scraped claims. Treat rung 5 as "lists tertiary services", not "certified tertiary".
- 75% of facilities have no bed count, and 64% have no doctor count. Most clinics therefore have low confidence.
- OpenStreetMap confirms only about a quarter of facilities. Government lists should raise this, especially for public rungs.
- NABH and PM-JAY are self-claims until checked against official lists.
- Not built yet: ranking on top of `facility_service`. `district_context` now exists (section 15).

---

## 11. Change log

| Date | Change |
| --- | --- |
| 2026-11-21 | Plan written; schema names approved |
| 2026-11-21 | Bronze built (02); silver built (03), 10 of 10 checks passing |
| 2026-11-21 | Gold care levels built (04); dashboard "Care levels" page added |
| 2026-11-21 | Guards added after reviewing results: single-specialty cap, organ-only transplant, stricter delivery signal, lab names excluded from name tiers |
| 2026-11-21 | OpenStreetMap (HOT, Oct 2026) loaded and matched (06); location words removed from name matching after false matches |
| 2026-11-21 | Government upload path built and tested (05); volume `vf_bronze.external` created |
| 2026-11-21 | Databricks folder renamed to "CMU Datathon 2026" by the team |
| 2026-11-21 | Chunk C: `facility_service` and `facility_verification` built and tested (09), 14 of 14 checks passing. See section 12 |
| 2026-11-21 | Chunk A: `facility_level_v2`, `quality_tests`, `facility_geo_quality`, `facility_duplicate_groups` built (07). Notebook 09 rerun to read them. See section 13 |
| 2026-11-21 | Bug fix in notebook 03: the camelCase split used `'$1 $2'`, which a notebook run treats as parameters and blanks out. A rerun from the notebook screen garbled specialty text ("emergencyMedicine" -> "emergenc edicine") and dropped about 3,700 weak claims. Replaced with a lookaround pattern that has no `$`; 03, 04, 06, 07, 09 rebuilt in order as notebook jobs. `facility_claims` is again identical to the original build |
| 2026-11-21 | Team decision: duplicates above 80% confidence are one centre with the higher rung. `vf_silver.facility_canonical` and the per-centre views `facility_level_canonical`, `facility_service_canonical` added (07, 09). See section 13.7 |
| 2026-11-21 | Notebook 09 now reads notebook 10's `ext_facility_matches`, accepted matches only: 133 Wikidata matches support that a facility exists (never a service) |
| 2026-11-21 | Chunk E: district polygons, 1 km population grid, crosswalk, `district_context`, straight-line access grid, evidence gap, critical zones, routing function (11). See section 15 |
| 2026-11-22 | Chunk F: app shell deployed as Databricks App `vf-facility-finder` (folder `app/`); `vf_gold.feedback_reports` created. Ranking is a labelled placeholder. See section 16 |
| 2026-11-22 | Voronoi catchments per service and evidence tier built (12), 5 of 5 checks passing, and added to the app's Policy tab as the default national map. See section 17 |
| 2026-11-22 | App map restyle: catchments are shaded by straight-line distance from their facility (dark at the facility, fading outward), one teal ramp across the Policy tab with dark = close everywhere. See section 17 |
| 2026-11-22 | Team rule: any area over 150 km (straight line) from a facility that could handle a life-threatening emergency is a critical area. `vf_gold.critical_zones_emergency_150km` built in notebook 12 and striped on every service map in the app. See section 17.1 |
| 2026-11-22 | Two raw government files saved: NMC MBBS colleges (839 rows) and eRaktKosh blood centres (6,524 rows), as `vf_bronze.ext_nmc_colleges_raw` and `vf_bronze.ext_eraktkosh_blood_centres_raw`, with files in the `vf_bronze.external` volume and `external_sources/`. Not matched yet. `External Sources.md` added. See section 14.1 |
| 2026-11-22 | Task D evidence integrated into chunk C: notebook 09 reads NABH and eRaktKosh matches (at most `externally_supported`, 0 verified), new notebook 13 builds `facility_external_summary`, `facility_external_evidence`, `facility_external_conflicts`, `facility_external_review` and 15 checks (all pass). See section 18 |
| 2026-11-22 | App: blood bank search now also shows the 29 facilities that eRaktKosh lists as a Blood Bank without a claim of their own, labelled as a directory listing only (`app.py`, `ranking.py`). See section 18.6 |
| 2026-11-22 | Rural Hospital Network: 42 self-reported profiles loaded (`vf_bronze.ext_rhn_profiles_raw`) and matched in notebook 14 by name, state and place: 5 accepted (medium), 3 ambiguous, 3 bed differences flagged. See section 19 |
| 2026-11-22 | Team decision: trust the external source on value conflicts. `vf_gold.facility_resolved_values` built in notebook 15 (7 beds, 17 founding years, 2 NABH withdrawn; originals kept); app cards read it. See section 20 |
| 2026-11-22 | Team decision: also fill missing beds, founding year and doctors from accepted outside matches (`external_gap_fill` in `facility_resolved_values`): 4 beds, 10 founding years, 1 doctor count. See section 20 |
| 2026-11-22 | Task B text extraction audited (notebook 16): raw error about 36% beds, 27% founding year, 5% doctors; only plain statements kept and used as `text_gap_fill` (54 beds, 73 years, 103 doctor counts). See section 21 |
| 2026-11-22 | Matching second pass (notebook 17): 136 eRaktKosh and 27 Wikidata matches added as medium, 94 ambiguous; view `ext_facility_matches_all` read by notebooks 09 and 13; facilities with outside evidence 1,144 to 1,253. See section 22 |
| 2026-11-22 | Rural Health Statistics 2021-22 loaded (notebook 18): government facility counts for 737 districts (706 linked to map districts, 31 left unlinked) and state beds and staff; `district_public_facilities`, `state_public_health_capacity`; shown in the app's Policy tab. See section 23 |
| 2026-11-22 | 28 of the 31 unlinked RHS districts confirmed by hand (734 of 737 linked). Critical blocks in districts with government health centres or hospitals but no facility in our data are flagged (`critical_zones_govt_caveat`) and ringed on the Policy map. See sections 23 and 23.1 |
| 2026-11-22 | Final hand check of 60 claims (20 per strength): strong 20 of 20 supported, moderate 16 of 20 (2 wrong), weak 7 of 20 (1 wrong, 8 label only); about 6% wrong when weighted. `facility_service_handcheck_final`. See section 24 |
| 2026-11-22 | Hand review of the 289 held matches (notebook 19): 245 accepted as medium, 16 rejected, 28 left for Ishaan; decisions in `ext_match_review_decisions`, Task D tables untouched; facilities with outside evidence 1,253 to 1,455. See section 25 |
| 2026-11-22 | App redeployed with the recommender update (`app/recommender/`, `symptom_ui.py`): claim reliability is now a Beta-Binomial estimate from the section 24 hand check (0.95 strong, 0.77 moderate, 0.36 weak; lower quartile used in red cases), published time-benefit curves for six conditions with a "When did it start?" input, a trip-risk factor (Gauss 2019), and a nearer-on-ties step. Details in `app/recommender/README.md`; validation results in `recommender_validation/` |

## 12. Implemented: service search tables (chunk C, notebook 09)

Built and run in Databricks on 2026-11-21 (job run succeeded, 14 of 14 checks pass). Only what is listed here exists.

### 12.1 What was built

| Table | Rows | What it is |
| --- | --- | --- |
| `vf_gold.facility_service_rules` | 24 | Editable keyword rules for 14 services, split into `direct` wording (states the service) and `indirect` wording (only hints at it) |
| `vf_gold.facility_service` | 139,846 (9,989 facilities x 14 services) | One row per facility and service. The table the ranking, map and explanation cards should read |
| `vf_gold.facility_verification` | 147,320 | One row per facility, service and outside source. `is_primary` marks the one row per facility and service that `facility_service` shows |
| `vf_gold.facility_service_checks` | 14 | Pass/fail tests |

Services: emergency, trauma, obstetric_delivery, c_section, nicu, dialysis, icu, ct, mri, cardiac_cath, stroke, blood_bank, burns, paediatrics.

Nothing from notebooks 01 to 06 was changed. `facility_service_rules` and `facility_service_checks` are two small helper tables that were not in the brief; they sit in the same namespace so they cannot collide with other chunks.

### 12.2 How a claim is detected, and why

Text read: the four list fields in `facility_claims`, every sentence of the description, and the facility name.

| Decision | Reason |
| --- | --- |
| Separate `direct` and `indirect` wording | "Dialysis unit" states a service. The specialty tag "pediatrics" or the word "trauma" in "orthopaedics and trauma" only hints at one. Indirect hits are kept but always marked weak |
| Negating text is never a claim | The data contains lines like "No MRI facility on site", "CT scan services outsourced", "Does not provide emergency services". Plain keyword matching would count these as claims. They are kept in `negating_quote` and lower the strength when a record says both |
| `claim_strength` instead of one yes/no | strong = direct wording in 2+ different fields; moderate = direct wording in one field; weak = specialty tag or indirect wording only. Drops one step when the record's quality score is under 0.7 or it also has negating text |
| `supporting_quote`, `supporting_signal`, `source_field`, `all_quotes` on every claim | Every claim can be shown with the exact words it rests on. `all_quotes` keeps up to five |
| `hours_claim` only from explicit text | `24x7` only when a sentence that directly states the service also says 24x7, 24 hours or round the clock. That sentence is kept in `hours_quote` |
| `availability_status` is always `unknown` | No source tells us whether a bed, machine or doctor is available now |
| `plausibility_flag` | Marks claims that are unusual for the facility type (for example a small clinic listing an ICU) and records with 30+ specialties for one doctor. Flag only; the row is kept |
| `location_quality_flag` | Comes from `vf_silver.facility_geo_quality` (notebook 07) since the rerun on 2026-11-21: 9,624 good, 329 suspect, 36 invalid. `duplicate_group_id` and `duplicate_confidence` are filled for the 121 records notebook 07 flags. If the notebook 07 tables are missing, notebook 09 falls back to the silver flags and leaves the duplicate columns empty |
| `contact_verification_status` | `listed_unverified` or `missing`. No phone number has been checked against anything |

Rules tightened after reading samples: neonatal and paediatric ICU no longer count as adult ICU; dental cone-beam CT, PET-CT, CT simulators and "CT University" no longer count as CT; "Straumann" no longer matches trauma; "instrument delivery system" no longer matches childbirth; thrombectomy counts as stroke care only when stroke is named.

### 12.3 Verification layer

Ladder: claimed in the facility's own text -> `inferred` -> `externally_supported` -> `verified` -> currently available (always unknown).

| Status | Meaning | What can grant it today |
| --- | --- | --- |
| `unknown` | No claim and no outside source | default |
| `inferred` | Claimed in the facility's own text only | notebook 09 step 1 |
| `externally_supported` | An outside record independently lists that speciality | OpenStreetMap speciality text |
| `verified` | A source that establishes that one specific thing | nothing loaded yet |
| `contradicted` | An outside source disagrees | type or tier mismatch from notebook 06 |

- An OpenStreetMap or directory match supports that the facility **exists** (`_facility_existence`) and its **type** (`_facility_type`). It never raises the status of a service. A test enforces this.
- `verified` is reserved for four cases, each only for the thing named: eRaktKosh -> `blood_bank`; official CGHS list -> `_cghs_empanelment`; official PM-JAY list -> `_pmjay_empanelment`; NABH directory -> `_nabh_accreditation`. None of those sources is loaded yet, so there are 0 verified rows.
- `matched_at` is the time the row was computed. `source_date` and `verification_date` stay null unless the source itself gives a date. Both are null everywhere today.
- Self-claimed badges are recorded as `inferred`: NABH 1,301, PM-JAY 646, CGHS 181.

**Notebook 10 output is now read (rerun 2026-11-21).** Notebook 09 reads `vf_silver.ext_facility_matches` and uses only rows whose `match_status` starts with `accepted` (133 Wikidata matches; the 3,803 rejected and 3 ambiguous rows are ignored). The source name decides what a match can establish: Wikidata supports that the facility exists, nothing more. Result: `_facility_existence` now has 2,773 OpenStreetMap rows and 133 Wikidata rows; no service status changed. The eRaktKosh / CGHS / PM-JAY / NABH paths have still only been dry-run with made-up matches, because those sources are not loaded.

### 12.4 Measured results

26,405 claims across 7,747 facilities (2,242 facilities have no claim for any of the 14 services).

| Service | Claimed | Strong | Moderate | Weak | Only negating text | Says 24x7 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| paediatrics | 5,460 | 482 | 1,030 | 3,948 | 0 | 77 |
| obstetric_delivery | 4,732 | 514 | 803 | 3,415 | 0 | 41 |
| emergency | 3,910 | 694 | 2,215 | 1,001 | 3 | 2,269 |
| icu | 2,922 | 936 | 1,365 | 621 | 0 | 292 |
| trauma | 1,815 | 393 | 737 | 685 | 1 | 264 |
| nicu | 1,605 | 251 | 457 | 897 | 0 | 55 |
| ct | 1,441 | 809 | 611 | 21 | 4 | 70 |
| dialysis | 1,055 | 565 | 476 | 14 | 1 | 95 |
| mri | 946 | 525 | 404 | 17 | 4 | 25 |
| cardiac_cath | 822 | 382 | 325 | 115 | 0 | 30 |
| blood_bank | 642 | 209 | 352 | 81 | 4 | 71 |
| c_section | 442 | 93 | 340 | 9 | 1 | 4 |
| stroke | 330 | 32 | 90 | 208 | 1 | 5 |
| burns | 283 | 46 | 116 | 121 | 2 | 6 |

- **Claim strength:** 22.5% strong, 35.3% moderate, 42.2% weak. The weak share is highest for paediatrics, childbirth, stroke and NICU, where most hits are a specialty tag.
- **Verification status of the 26,405 claims:** 26,389 inferred (99.94%), 16 externally supported (0.06%), 0 verified. In other words almost every service claim rests only on the facility's own text.
- **Facility seen in an outside source:** 10,654 of the claims (40.3%) belong to a facility matched in OpenStreetMap or Wikidata. That supports the facility, not the service.
- **Type contradictions:** 134 facilities have an outside type that conflicts with ours.
- 32 claims also have negating text on the same record; 21 facility-service pairs have only negating text and are not counted as claims.

### 12.5 Tests (`vf_gold.facility_service_checks`, 16 of 16 pass; two were added for the per-centre view)

One row per facility x service; every silver facility present; no claim without a quote and matched rule; no quote without a claim; negated-only text is never a claim; no `verified` from a map or generic directory; `verified` only for the one thing a source establishes; every outside status has a source and record id; no import time used as a verification date; exactly one primary verification row per pair; `facility_service` status equals the primary row; availability always unknown; `24x7` only with a claim; inferred means a claim and unknown means none.

### 12.6 Hand check and known limits

- I read two random samples of strong and moderate claims, 3 per service. First sample: 38 of 42 quotes really stated the service. The 4 misses became the rule fixes above. Fresh sample after the fixes: 40 of 42 (misses: "paediatric critical care" counted as adult ICU, and eye trauma counted as trauma care). This is one reader and 84 rows, so treat it as a rough indication, not an accuracy figure.
- Weak claims were not hand-checked and include loose hits such as "reachable in times of emergency".
- Keyword rules cannot tell scope. "Orthopaedic emergency services 24/7" counts as an emergency claim.
- `service_specific_level` is copied from `facility_level` (newborn, obstetric, trauma, dialysis). Those levels in notebook 04 do not yet ignore negating text.
- `care_level` comes from `vf_gold.facility_level_v2` (notebook 07) since the rerun; the column `care_level_source` says which table was used.

---

## 13. Implemented: fixes and quality flags (chunk A, notebook 07)

Built and run in Databricks on 2026-11-21 (job run succeeded). Notebook 09 was rerun afterwards and still passes 14 of 14. Nothing from notebooks 01 to 06 was edited, and no row was merged or deleted.

### 13.1 Why a diagnostic centre had care level 5

*Anderson Diagnostics & Labs* has an academic affiliation. In notebook 04 the rule "academic affiliation -> rung 5" is tested before the rule "pharmacy or standalone diagnostic centre -> no rung", so it took rung 5 and kept the label "Diagnostic centre". It was the only such record.

The same branch has two related gaps in the single-specialty guard from section 6 ("capped at rung 3 unless 100+ beds"):
- the academic rule also runs before that guard, so dental colleges and eye institutes with a teaching link took rung 5;
- the guard's pattern in notebook 04 has unescaped word boundaries, so the words eye, skin, IVF, ENT and hair never matched.

### 13.2 `vf_gold.facility_level_v2`

Same columns as `facility_level`, plus `care_level_v1`, `level_kind_v1`, `v2_change` (`rung` or `kind only`) and `v2_change_reason`. The original table is unchanged, so the dashboard still shows v1.

| Change | Facilities |
| --- | ---: |
| Rung 5 -> 3: single-specialty teaching hospital, under 100 beds or beds unknown | 39 |
| Rung 4 -> 3: single-specialty hospital under 100 beds that the broken pattern missed | 19 |
| Rung 5 -> 4: single-specialty teaching hospital with 100+ beds, now placed by beds and services | 7 |
| Rung 5 -> none: the diagnostic centre | 1 |
| **Rung changed** | **66** |
| Label only (now "Single-specialty hospital", rung unchanged) | 388 |

Rung counts, v1 -> v2: rung 5 1,018 -> 971; rung 4 1,686 -> 1,674; rung 3 3,124 -> 3,182; no rung 519 -> 520; rungs 1 and 2 unchanged. The full list is the second output of notebook 07, or `SELECT * FROM vf_gold.facility_level_v2 WHERE v2_change = 'rung'`.

These are rule corrections, not checked facts. A dental college with many chairs or a large eye institute may deserve a higher rung for its own specialty; for general and emergency routing the lower rung is the safer reading.

### 13.3 `vf_silver.facility_geo_quality` (one row per facility)

| Flag | Facilities | When |
| --- | ---: | --- |
| good | 9,624 | no known problem (not the same as checked on a map) |
| suspect | 329 | coordinates in a different state than the address (220), stated state disagrees with the PIN code state (61), far from any known PIN code (2), or the exact same point as an unrelated record (69). A record can have more than one |
| invalid | 36 | no usable coordinates |

Every non-good row has a plain-language `reason`.

### 13.4 `vf_silver.facility_duplicate_groups` (flag only)

Two records are linked when their names overlap and something else agrees. Phones and website domains shared by more than 5 records (call centres, chains, listing sites) are ignored.

| Confidence | Link | Facilities | Groups |
| --- | --- | ---: | ---: |
| high | same phone or same website, names alike, within 1 km | 22 | 11 |
| medium | within 100 m, names alike | 8 | 4 |
| low | same phone or website, names alike, more than 1 km apart (usually branches of one group) | 91 | 41 |

121 records in 56 groups, from 73 links kept in `vf_silver.facility_duplicate_pairs`. The low number is expected: silver already merged exact duplicate ids. Only the 15 high and medium groups are likely the same place; treat low as "same organisation".

### 13.5 `vf_gold.quality_tests` (22 tests, 21 pass; three were added for the canonical map)

The one failing test is deliberate: "no standalone diagnostic centre or pharmacy with a rung (original table)" fails with 1 row on `facility_level` and passes on `facility_level_v2`. It stays in the table as the record of the known issue.

The rest cover: one row per facility in both level tables; the single-specialty guard; every v2 change has a reason; rung is 1 to 5 or empty; no blank evidence quote; one evidence row per facility and signal; the outside check did not duplicate facilities; geo and duplicate tables are well formed; flagging removed no facility; `facility_service` has one row per facility and service and always carries a location flag; no verification date without a dated source; no stray verification-date column; no ingest timestamp exposed in gold.

### 13.7 One centre for near-certain duplicates (`facility_canonical`)

Team decision on 2026-11-21: when the chance that two records are the same place is above 80%, treat them as one centre whose rung is the higher of the two.

- The duplicate confidence is a label, not a measured probability. **`high` is treated as above 80%.** A high link needs the same phone or website, overlapping names and positions within 1 km, all at once. `medium` and `low` links are not merged.
- **22 records become 11 centres.** Three centres get a higher rung than one of their records had: IRIS Hospital (4 -> 5), Manas Hospital (2 -> 3), Nerlikar Hospital (3 -> 5). 9,989 records -> 9,978 centres.
- `vf_silver.facility_canonical` maps every record to its centre (`canonical_facility_id`). The centre's record is the one with the higher rung, then the higher quality score. No row is deleted anywhere.
- `vf_gold.facility_level_canonical` (view): one row per centre, with `member_facility_ids`, `member_names`, `member_care_levels` and a `merge_note`.
- `vf_gold.facility_service_canonical` (view, built in notebook 09): one row per centre and service. It shows the strongest claim among the records, `evidence_facility_id` says which record the quote came from, and `claims_by_record` keeps every record's claim. The 22 merged records carry 115 claims, which become 75 per-centre claims.
- Least certain merge: Chettinad Hospital and Research Institute with Chettinad Superspeciality Hospital (same phone, 600 m apart; possibly two hospitals on one campus).
- To undo or change the rule, edit the `confidence = 'high'` filter in notebook 07 section 3b and rerun 07, 09 and 11.

### 13.6 Limits

- Consumers have to choose v2. The dashboard, `facility_external_check` (notebook 06) and the service-specific levels still come from v1.
- Name overlap uses whole words, so spelling variants ("Sri" / "Shri") can hide a duplicate.
- I read about 24 duplicate links by eye. The high ones looked right; no accuracy figure is claimed.

---

## 14. Implemented: external data ingestion and matching (Task D)

> **Update 2026-11-22:** Task D has since loaded NABH (21,582 records) and eRaktKosh (6,524), and the match tables now hold 20,122 rows with 1,186 accepted matches on 1,144 facilities. The counts in 14.1 to 14.5 below describe the earlier Wikidata-only run. Current counts and how chunk C uses them are in section 18.

**Implemented and validated on 2026-11-21.** Task D runs independently of notebooks 01–06 and writes only to `workspace.vf_bronze.ext_*` and `workspace.vf_silver.ext_*`. The original Virtue Foundation facilities remain the primary source of truth.

### 14.1 External data loaded

| Table | Rows |
| --- | ---: |
| `vf_bronze.ext_wikidata_hospitals` | 2,772 |
| `vf_bronze.ext_cost_reference` | 8 |
| `vf_silver.ext_facilities_clean` | 2,772 |
| `vf_silver.ext_facility_match_candidates` | 8,935 |
| `vf_silver.ext_facility_matches` | 3,939 |
| `vf_silver.ext_facility_match_summary` | 133 |
| `vf_silver.ext_source_status` | 8 |
| `vf_bronze.ext_nmc_colleges_raw` | 839 |
| `vf_bronze.ext_eraktkosh_blood_centres_raw` | 6,524 |

The Wikidata extraction includes hospital identity information and available attributes. Missing attributes remain unknown rather than being estimated from averages.

The cost-reference table contains published aggregate statistics, not facility-specific prices.

**Added 2026-11-22: two raw government files, loaded as downloaded and not yet matched to any facility.** The files are in the volume `vf_bronze.external` (`nmc_colleges/`, `eraktkosh/`) and in the project folder under `external_sources/`. Where each came from and how it was collected is written up in `External Sources.md` in the project folder. Every column is kept as text, plus a `source_file` column.

| Table | What it is | Caveat |
| --- | --- | --- |
| `vf_bronze.ext_nmc_colleges_raw` | National Medical Commission list of 839 colleges teaching MBBS: name, state, university, management type, inception year, approved seats | `inception_year` is the college's founding year, not the attached hospital's. Do not use it as a hospital's year of establishment |
| `vf_bronze.ext_eraktkosh_blood_centres_raw` | eRaktKosh blood centre directory, 6,524 centres with address and coordinates: 4,754 blood banks and 1,770 blood storage units (BSU) | Only `centre_type = 'Blood Bank'` can verify a `blood_bank` claim. A storage unit cannot |

No existing table was changed. `facility_service` still has 0 verified rows until these are matched.

### 14.2 Facility matching results

| Match status | Count |
| --- | ---: |
| Accepted — high confidence | 114 |
| Accepted — medium confidence | 19 |
| Ambiguous | 3 |
| Rejected | 3,803 |
| **Total** | **3,939** |

The matcher uses normalized name similarity, geographic distance and pincode agreement where available. Conflicting or insufficient evidence leads to rejected or ambiguous outcomes.

Validation confirmed:

- 133 accepted Wikidata matches.
- Zero duplicate accepted `(facility_id, source)` combinations.
- 3,939 distinct facility IDs represented in the match table.
- The original silver facilities table remains accessible with 9,989 rows.

**Important:** A Wikidata or directory match supports facility identity, existence or descriptive attributes. It does not verify that a hospital currently provides dialysis, C-sections, NICU, trauma care or another specific clinical service.

Only accepted-high and accepted-medium matches should be considered for downstream identity enrichment. Ambiguous and rejected records must not establish facility identity or clinical capability.

### 14.3 External sources not yet downloaded

Five sources are still not downloaded (NABH was loaded on 2026-11-22, see section 18):

| Source | Status |
| --- | --- |
| PM-JAY | Not downloaded |
| CGHS | Not downloaded |
| HMIS | Not downloaded |
| Government Health Centres Directory | Not downloaded |
| National Hospital Directory | Not downloaded |

All five have zero loaded records, because no eligible downloadable file has been provided. eRaktKosh was downloaded on 2026-11-22 (section 14.1). The `ext_source_status` table was written before that and still lists eRaktKosh as not downloaded; it will be corrected when the matching notebook is next run.

These sources must not be presented as having been successfully integrated. Their absence does not imply that a facility lacks accreditation, empanelment, a blood bank or a particular clinical capability.

### 14.4 Provenance and uncertainty

The pipeline preserves source identifiers, source references, matching methods, confidence classifications and match decisions.

Source publication dates and actual verification dates must remain distinct from ingestion timestamps. An ingestion timestamp is not evidence that a source or clinical capability was verified on that date.

No facility's capacity, staffing, clinical capabilities or prices are imputed from population or national averages.

### 14.5 Remaining work

1. Obtain authoritative PM-JAY and NABH lists, including publication or validity information.
2. Upload eligible source files and their metadata to the external volume.
3. Rerun Task D ingestion and matching.
4. Repeat duplicate, provenance, confidence and ambiguity validations.
5. Allow downstream Chunk C consumers to use only accepted external identity matches, retaining original Virtue Foundation evidence for service claims.

**Limitations:** The current accepted matches are exclusively from Wikidata. They provide external identity enrichment, not independent clinical service verification. The remaining seven official source lists have not been ingested.

## 15. Implemented: geography and district context (chunk E, notebook 11)

Built and run in Databricks on 2026-11-21 (job run succeeded in about 3 minutes, 12 of 12 checks pass).

**Read this first.** Every distance here is **straight-line**, not travel time. And every distance is to the nearest facility **in this dataset**, which holds about 10,000 facilities, a fraction of India's real hospitals and clinics. 199 of 735 districts (128 million people) have no located facility in the dataset at all. So a long distance means "nothing in this dataset nearby", which is partly a real gap and partly missing data. Use the numbers to compare places and evidence tiers, not as the true distance to care.

### 15.1 Sources

| Source | Vintage | What it gives | Licence |
| --- | --- | --- | --- |
| geoBoundaries gbOpen India ADM2 (districts) | 2021 | 735 district polygons, simplified | Open Data Commons Open Database License 1.0 |
| geoBoundaries gbOpen India ADM1 (states) | 2011 | 36 state polygons (older: no Telangana or Ladakh) | Creative Commons Attribution 2.5 India (CC BY 2.5 IN) |
| WorldPop India, UN-adjusted counts | 2020 | population per ~1 km cell (30 arc-seconds). A modelled estimate, not a census | CC BY 4.0 |

Files are kept in the volume `vf_bronze.external` under `geo/` and reused on rerun. `vf_bronze.geo_sources` records URL, vintage, licence and download time (a download time, not a verification date).

### 15.2 Tables

| Table | Rows | What it is |
| --- | ---: | --- |
| `vf_bronze.geo_district_polygons_raw` | 735 | polygons as downloaded (GeoJSON text) |
| `vf_silver.district_polygons` | 735 | district, current state (`state`), the file's own state (`state_name`), area, population, centre, WKT geometry |
| `vf_gold.district_crosswalk` | 1,451 | every PIN-code district and NFHS district with the polygon it links to, the method and the name similarity |
| `vf_gold.district_crosswalk_issues` | 93 | everything that did not link cleanly |
| `vf_gold.access_grid` | 4,105,644 | one row per 1 km land cell: population, district, straight-line km per service (`km_*` claimed, `kmb_*` better evidenced) and per hospital class |
| `vf_gold.access_evidence_gap` | 2,331,462 | 166,533 blocks of 5 km x 14 services: nearest claimed vs nearest better-evidenced facility |
| `vf_gold.district_service_access` | 12,495 | district x service (and hospital class): population- vs area-weighted km and coverage |
| `vf_gold.district_context` | 735 | one row per district (below) |
| `vf_gold.critical_zones` | 199,362 | flagged 5 km blocks per service, with criteria and a priority rank |
| `vf_gold.geography_checks` | 12 | tests |

Grid population adds up to 1,378 million, which matches India's 2020 UN total. Two helper tables (`district_crosswalk`, `district_service_access`) were not in the brief.

### 15.3 How places are put in districts

Each 1 km cell, facility and PIN-code centre gets the district whose polygon covers it. That is a lookup by position, so it does not depend on spelling. Precision is one cell (about 1 km) at borders.

### 15.4 District crosswalk: no joins on name alone

| Step | Rule |
| --- | --- |
| States | Linked by position: the polygon state that holds most of a state's PIN-code centres. No state names are compared |
| District, first choice | A polygon **in the same state** with the same name (similarity 0.9+, no close runner-up) |
| PIN-code district, fallback | 60%+ of its PIN-code centres (at least 5) fall in one polygon. PIN-code centres turned out to be rough, so this is the fallback |
| NFHS district, fallback | Follows the PIN-code district of the same state and name, or a clear close spelling (0.85+) in the same state |
| Otherwise | Left unlinked and written to `district_crosswalk_issues` |

Result: 729 of 745 PIN-code districts linked (645 by state + name, 84 by position), 3 linked with low evidence, 13 unlinked. 701 of 706 NFHS districts linked. 698 of 735 polygons have NFHS indicators; 37 do not (districts created after NFHS-5, such as new Andhra Pradesh and Telangana districts). Position links catch renames that names miss, for example Bengaluru Urban -> Bangalore, Y.S.R. -> Kadapa(YSR), Konaseema -> East Godavari (its parent district in this boundary file).

### 15.5 `district_context` (one row per polygon)

- **Identity:** district, state, area, WorldPop 2020 population, density.
- **NFHS-5:** 12 cleaned indicators (institutional births, C-section births, 4+ antenatal visits, skilled birth attendance, full vaccination, stunting, women's literacy, health insurance, sanitation, electricity, clean fuel, anaemia), `nfhs_districts_linked` and `nfhs_status`. Where several NFHS districts map to one polygon the plain average is used.
- **Deprivation:** `deprivation_score` is the average national percentile rank over six of those indicators (higher = more deprived), with terciles. A simple index for ranking, not an official one.
- **Supply, by position, good locations only, one count per centre:** facilities by rung, claims per service (`claimed_*`) and better-evidenced claims (`better_evidenced_*`), per 100,000 people.
- **Missing-data rates:** share of the district's facilities with no bed count, no doctor count, and a low-confidence level. No bed or doctor number is filled from averages.
- **Location quality:** `facilities_suspect_or_no_location` counts facilities whose address is in the district but whose position is suspect or missing. They are counted here and never placed on the map.
- **Coverage:** for emergency, C-section, ICU and rung 3+ hospitals: population-weighted km, area-weighted km, share of people within 30 km and share of land within 30 km. The two weightings differ a lot in districts where people cluster in one town.

What the context shows: the most deprived third of districts (520 million people) has 0.21 dataset facilities per 100,000 people, against 1.28 in the least deprived third, and a population-weighted distance to a claimed emergency facility of 29.7 km against 17.6 km. Part of that is real and part is that the dataset covers poorer districts less.

### 15.6 Access grid and the evidence gap

Two tiers per service:
- **claimed:** any facility whose own text claims the service.
- **better evidenced:** direct wording (strong or moderate), no negating text, no plausibility flag, **and** the facility is matched in OpenStreetMap or Wikidata. This is not verification of the service. It removes the weakest claims and unconfirmed facilities.

The brief asked for "externally supported or verified" as the second tier. Only 16 service claims have that status, so that tier would be empty for most services; it is kept as `km_service_supported` in the gap table and the better-evidenced tier is used for the comparison.

Facilities with a suspect or invalid location are left out of all distances (944 of 26,361 per-centre claims). Near-certain duplicates count once.

| Service | Facilities claiming | Better evidenced | Pop-weighted km, claimed | Pop-weighted km, better | People over 30 km, claimed | People over 30 km, better | People where better is 30+ km further |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| emergency | 3,759 | 1,210 | 24.8 | 38.3 | 32.4% | 53.3% | 17.1% |
| trauma | 1,749 | 483 | 34.6 | 56.9 | 47.4% | 66.6% | 27.3% |
| obstetric_delivery | 4,543 | 509 | 23.2 | 55.4 | 29.7% | 66.3% | 40.7% |
| c_section | 424 | 175 | 55.1 | 79.4 | 66.3% | 76.3% | 26.8% |
| nicu | 1,541 | 305 | 34.0 | 63.0 | 47.2% | 70.1% | 34.2% |
| dialysis | 1,016 | 471 | 40.2 | 57.5 | 52.6% | 65.7% | 20.4% |
| icu | 2,814 | 984 | 28.5 | 44.3 | 37.7% | 58.3% | 19.9% |
| ct | 1,393 | 551 | 35.1 | 51.8 | 49.4% | 63.7% | 20.0% |
| mri | 917 | 371 | 43.8 | 59.9 | 58.3% | 68.4% | 18.4% |
| cardiac_cath | 794 | 328 | 45.7 | 61.4 | 58.9% | 71.3% | 20.3% |
| stroke | 320 | 58 | 67.2 | 128.8 | 71.3% | 85.8% | 47.7% |
| blood_bank | 619 | 269 | 42.1 | 62.0 | 56.6% | 70.2% | 24.1% |
| burns | 270 | 72 | 64.2 | 109.9 | 70.7% | 83.0% | 41.7% |
| paediatrics | 5,258 | 610 | 23.8 | 54.9 | 31.2% | 65.2% | 37.7% |

In `access_evidence_gap`, `differs` is true when the nearest claimed and nearest better-evidenced facility are different, and `gap_flag` when the better-evidenced one is more than 30 km further. For emergency care the two differ in 69% of blocks.

### 15.7 Critical zones

A 5 km block is flagged for a service when it has 5,000+ people, the population-weighted straight-line distance to the nearest claimed facility is over 30 km, and its district is in the most deprived third. Two more types are kept apart: far and populated with no NFHS link, and far only on better evidence (within 30 km of a claimed facility, over 30 km from a better-evidenced one). `priority_rank` orders zones by people x distance so a map can take the top N. For emergency care: 12,901 critical blocks with 164 million people. The thresholds are planning choices written in the `criteria` column; 30 km is not a clinical rule. Because of the dataset coverage caveat above, treat zones as "where to look first", not as proven gaps.

### 15.8 Road time

`route_minutes(pairs, provider="osrm" | "google")` in notebook 11 returns driving minutes and km for a short list of trips. OSRM (public demo server, OpenStreetMap roads, no traffic) needs no key and was tested live from Databricks. The Google option takes a key and its results must not be stored in tables. Test on six critical-zone blocks, shown and not stored:

| District | Straight km | Road km (OSRM) | Minutes (OSRM, no traffic) |
| --- | ---: | ---: | ---: |
| Budaun | 40.5 | 43.4 | 32.7 |
| Panch Mahals | 30.6 | 34.5 | 32.8 |
| Bulandshahr | 33.4 | 51.4 | 44.1 |
| Kheri | 85.7 | 114.1 | 84.6 |
| Murshidabad | 34.1 | 38.0 | 30.5 |
| Bulandshahr | 30.3 | 49.3 | 41.9 |

Road distance was 7% to 63% longer than straight-line in this small test. The national grid stays straight-line.

### 15.9 Tests (`vf_gold.geography_checks`, 12 of 12 pass)

Population total plausible (1.30 to 1.45 billion); every cell has a district and is unique; district populations add up to the grid; distances present and not negative; better-evidenced distance never shorter than claimed; one context row per polygon; facilities counted by position never exceed good-location facilities; no suspect or invalid location used as a nearest facility; gap table one row per block and service; every crosswalk link has a method and a state; critical zones meet their own criteria; NFHS percentages within 0 to 100.

### 15.10 Limits

- Straight-line and dataset-only distances, as above.
- District boundaries are 2021; NFHS-5 is 2019-21; PIN-code districts are current. 93 names are in the issues table rather than guessed.
- `state_name` in the polygon tables is the boundary file's spelling (with diacritics, and Telangana under Andhra Pradesh in the state file). Use `state` for joins and display.
- The Google routing path is written but was not called (no key). The dashboard has no geography page yet.


---

## 16. Implemented: app shell (chunk F, folder `app/`)

A Databricks App (Streamlit) named `vf-facility-finder`. It is **facility discovery, not medical triage**, and says so on every screen.

### 16.1 Files

| File | What it holds |
| --- | --- |
| `app/app.py` | The three tabs, search, cards, maps, feedback writes |
| `app/ranking.py` | `rank(candidates, context)`, the only place results are ordered, plus the per-service minimum care level |
| `app/app.yaml` | Start command and the SQL warehouse resource (`sql-warehouse`, CAN_USE) |
| `app/requirements.txt` | Streamlit (pinned to the tested version), SQL connector, pydeck |

### 16.2 What each tab does

- **Policy makers.** National map: one circle per district at its centre, sized by population, shaded by population-weighted straight-line km to the nearest facility claiming the chosen service (`district_service_access`), with a switch between "any claim" and "better-evidenced claims only". District zoom: 5 km blocks from `access_evidence_gap`, critical zones from `critical_zones` (8 of 14 services), and facility points. If the geography tables are missing, both maps fall back to facility points only.
- **EMRs.** Same search, shown as a compact table (distance, care level, claim strength, checked or not, "open now: unknown", phone, quote), with a details panel.
- **Patients.** Plain-language cards: what the facility says, the quote and which field it came from, a link to the page the record was collected from, how sure we are, whether anyone checked, phone, and a "Why this hospital?" section listing what is unknown.

Each tab has a small optional profile form. Answers live in the browser session only and are never written to a table. The placeholder ranking ignores them; the patient's insurance answer only adds a note ("says it accepts PM-JAY, its own claim").

### 16.3 Choices and why

- **One ranking function, labelled PLACEHOLDER.** Order: care level meets the usual minimum for the service, then direct claims (strong or moderate) before label-only ones (weak), then straight-line distance. The middle step was added after an eye hospital with only an "emergency eye care" tag came first for emergency care. The minimum levels in `ranking.MIN_LEVEL` are assumptions, not rules. Replacing the body of `rank()` changes nothing else in the app.
- **Suspect or invalid locations are never ranked by distance.** They are listed separately, only when their address is in the searched district, with a warning.
- **Availability is always "unknown".** No source tells us whether a service is working today, so the app never implies it.
- **The source link is the record's first source page**, not proof of the specific quote. The card words it as "page this record was collected from".
- **The safety block ("Call 108, 102 for pregnancy or newborn") is fixed** at the top of the page and repeated at the top of every result. It cannot be collapsed.
- **Feedback is recorded, not used.** The four buttons append to `vf_gold.feedback_reports` (report_id, created_at, facility_id, facility_name, service, report_type, label = 'unverified report', source_tab). Nothing reads it for ranking. No profile data is stored with a report.
- **Search uses the per-centre view** `facility_service_canonical`, so likely duplicates appear once.
- **District and PIN searches measure from the centre of the area**, and the app says the distances are rough.

### 16.4 Access

The app runs as its own service principal, which was granted USE CATALOG on `workspace`, USE SCHEMA and SELECT on `vf_gold` and `vf_silver`, and MODIFY on `vf_gold.feedback_reports`. Only the creator can open the app until others are added under the app's Permissions.

### 16.5 Tested

Run locally against the real tables with a headless browser: all three tabs render with no exceptions, a patient search for emergency care near Patna returns 69 facilities within 50 km, and a feedback click wrote one row (then removed). The deployed copy starts successfully; it has not been opened by a signed-in user from this side.

### 16.6 Limits

- Straight-line distance only, and only to the roughly 10,000 facilities in this dataset. Real facilities nearby may be missing.
- No travel time, cost, bed availability or quality measure yet.
- The district map uses centre points, not boundaries.
- To redeploy after an edit: upload the files to `app/` and deploy the app again from its page under Compute, Apps.

---

## 17. Implemented: Voronoi catchments (notebook 12)

**Why it was added late.** The first app build (section 16) drew district circles instead, because chunk F's brief only asked for facility points plus the district tables. The plan's headline visual was the Voronoi map, so it was added straight after.

**What it is.** For each service, India is divided into catchments: the area closer, in a straight line, to one facility claiming the service than to any other. `vf_gold.service_catchments` holds one row per polygon piece, for two tiers (`claimed` and `better_evidenced`, the same definitions as section 15.6).

| Choice | Reason |
| --- | --- |
| Good-location facilities only | A suspect pin would draw a catchment in the wrong place. 944 of 26,361 claims are left out for this reason |
| Projected to India NSF LCC (EPSG:7755) before computing | Voronoi on raw latitude and longitude distorts distances; this projection is made for all-India maps |
| Clipped to the union of the district polygons | Keeps catchments inside India |
| Population from `access_evidence_gap` | Each 5 km block already records its nearest facility, so people per catchment is a sum, and it agrees with the access tables |
| Polygon stored as a JSON ring of [lon, lat] | The app can draw it with no geometry library |

**Measured.** 31,813 catchments across 14 services and two tiers (34,965 polygon pieces, since coasts and islands split some). For every service and tier the areas sum to 100.00% of the outline and the people inside sum to 1.378 billion. Median catchment for emergency care is 16 km² on any claim and 305 km² on better-evidenced claims; for stroke care it is 3,211 km² and 33,666 km².

**In the app.** The Policy tab's national map defaults to "Catchments (Voronoi)" with the evidence-tier switch redrawing it. By default each catchment is shaded by straight-line distance from its facility: darkest at the facility, fading to pale at 200 km or more (a gentle curve, power 0.7, so about 74 km is the middle of the ramp; both numbers are constants at the top of the Policy section in `app.py`). The shading is an image the app computes at about 3 km per pixel from the catchment facilities, drawn on a Web-Mercator grid so it lines up with the map, with catchment edges as thin lines on top. Shading by area or by people inside is still available. The whole tab uses one single-colour ramp where dark always means close to a listed facility and pale means far. A "Map colours" picker offers Indigo (default), Plum, Ocean and Teal; the choice changes colours only, never the data. Every cell is fully shaded and the pale end of each ramp is still clearly tinted, never white. Critical areas (5 km blocks from `vf_gold.critical_zones` with `zone_type = 'critical'`: at least 5,000 people, more than 30 km from the nearest facility claiming the service, district in the most deprived NFHS third) are drawn as diagonal stripes on top of the shading, in an accent colour the ramp does not contain (amber on Indigo, coral on Ocean and Teal, cyan on Plum) and clipped to India. Facility markers are dark dots with a pale ring; the earlier white dots merged into white patches where facilities cluster. The district-circle view and the district zoom sit on the same shading, so nothing inside India is left unshaded; the district zoom draws its own sharper shading and stripes for the area in view. They exist for 8 of the 14 services and are defined on the any-claim distance, so they do not change with the evidence-tier switch. The district zoom shows catchment edges as dark lines. District circles remain as a second view.

**Tests** (`vf_gold.catchment_checks`, 5 of 5 pass): full coverage of India, one catchment per facility point, no suspect locations, valid polygons, better-evidenced never exceeds claimed.

**Limits.** Straight-line, so not travel time and not a referral area. Only the roughly 10,000 facilities in this dataset, so a huge catchment may mean missing data, not missing care. Edges across borders, rivers and hills are not adjusted.

### 17.1 Critical areas for life-threatening emergencies (150 km rule)

Team rule (Ishaan, 2026-11-22): any area more than 150 km in a straight line from the nearest facility that could handle a life-threatening emergency is a critical area, with no population or deprivation condition. It is an extra way to qualify, alongside `vf_gold.critical_zones` (section 15.7), which is unchanged.

**"Could handle a life-threatening emergency" is defined as** a facility with a `good` location that states emergency care or trauma care in direct wording (strong or moderate claim, no negating text), also states an ICU in direct wording, and has care level 3 or higher. 1,636 facilities meet this. It is built from the facilities' own claims: none is verified and availability today is unknown.

**Tables.** `vf_gold.critical_zones_emergency_150km` (one row per 5 km block over 150 km, with the distance and the nearest capable facility) and `vf_gold.life_threatening_capable_facilities`.

**Measured.** 9,753 of 166,533 blocks (about 244,000 km², 53 districts) holding about 5.87 million of 1.378 billion people. By people: Odisha 2.34 million, Chhattisgarh 2.25 million, Mizoram 0.39 million, Andaman and Nicobar 0.37 million, Ladakh 0.16 million, Arunachal Pradesh 0.11 million. By area it is mostly Ladakh and Arunachal Pradesh.

**In the app.** Stripes on every service's map now mean "critical for this service (section 15.7 rule, 8 services) or over 150 km from life-threatening-capable care (all services)".

**Limits.** Straight-line distance understates real travel in mountains and islands, so the true picture is worse there. The dataset holds only about 10,000 facilities, so some flagged areas may have a capable hospital that is simply not listed; this is most likely for the Odisha and Chhattisgarh blocks.
---

## 18. Implemented: Task D evidence in chunk C (notebooks 09 and 13)

> The counts in this section are from the first run. After the second matching pass they changed; see section 22 for the current figures.

**Implemented and tested on 2026-11-22.** The teammate's Task D tables (NABH 21,582 records, eRaktKosh 6,524, Wikidata 2,772; 1,186 accepted matches on 1,144 facilities) are now used by chunk C. Everything is additive: notebooks 01 to 06 and every `ext_` table are untouched, no Virtue Foundation claim or quote was changed, and no clinical service was upgraded to verified.

### 18.1 Rules applied

- Entry point is `vf_silver.ext_facility_match_summary` (one row per facility). Match rows are never joined straight onto the facility table; the per-facility table is built by aggregating first.
- Only `accepted_high` and `accepted_medium` matches count as identity evidence. The 192 `ambiguous` matches go to a review table and are used nowhere else. Rejected rows are ignored.
- Each evidence row keeps the source, source record id, match status, match method, match score, matched fields, source reference and source URL.
- The time a file was downloaded is stored as `source_downloaded_at` and is never shown as a verification date. No table has a verification date for these sources, because none of them publishes one.
- An outside match grants at most `externally_supported`. Only an official list that names the specific capability could grant `verified`, and none is loaded.

### 18.2 What each source is allowed to establish

| Source | Establishes | Does not establish |
| --- | --- | --- |
| NABH, status accredited or renewed | That a directory record with this name and pincode holds an NABH accreditation in the stated programme (hospital, small healthcare organisation, eye care, AYUSH, dental and others). Recorded as `_nabh_accreditation`, `externally_supported`, validity unknown | That the accreditation is current (the directory gives no validity dates), or any clinical service |
| NABH, entry-level certification or empanelment | Only that the facility exists in the directory (`_nabh_listing`) | Accreditation. Certification and empanelment are different programmes |
| NABH, withdrawn or suspended | That the record is withdrawn or suspended. If the facility's own text claims accreditation, the row is `contradicted` | |
| eRaktKosh, centre type Blood Bank | That the national directory lists a licensed blood bank at this facility. `blood_bank` gets `externally_supported` with the note "directory listing only" | Stock today, that the bank is working today, or that transfusions are performed |
| Wikidata | That the facility exists; beds, founding year and coordinates are shown beside the Virtue Foundation values | Any service. Wikidata values never replace Virtue Foundation values |

A medium-confidence NABH identity match alone does not establish current accreditation. All 986 accepted NABH matches are medium confidence (exact normalised name plus pincode), which is why none is above `externally_supported`.

The teammate's `ext_eraktkosh_raw` was ingested from the same file as `ext_eraktkosh_blood_centres_raw` (all 6,524 codes join), so there is one source and no double counting. Evidence flows only through `ext_facility_matches`.

### 18.3 New and changed tables

| Table | Grain | Use |
| --- | --- | --- |
| `vf_gold.facility_external_summary` | one row per facility (9,989) | **The table consumers should read.** `nabh_status`, `eraktkosh_blood_bank_listed`, Wikidata beds and founding year beside the Virtue Foundation values, contradiction count and types, sources matched, `ambiguous_sources_for_review`, `evidence_refs` |
| `vf_gold.facility_external_evidence` | one row per facility and accepted outside record (1,186) | Detail and source links, with plain-language `establishes` and `does_not_establish` columns |
| `vf_gold.facility_external_conflicts` | one row per difference (398) | `is_contradiction = true` only where both sources cannot be right. Both values are kept |
| `vf_gold.facility_external_review` | one row per ambiguous match (192) | Hand review only. Never evidence |
| `vf_gold.external_integration_checks` | 15 checks | Measured validation, below |
| `vf_gold.facility_verification` (changed, notebook 09) | adds NABH and eRaktKosh rows | Feeds `verification_status` in `facility_service` |

`nabh_status` values: `no_accepted_match` 9,003, `listed_accredited_validity_unknown` 390, `listed_non_accreditation_programme` 589, `listed_withdrawn` 6, `listed_suspended` 1. `no_accepted_match` means nothing was matched; it does not mean the facility lacks accreditation.

### 18.4 Measured validation (`vf_gold.external_integration_checks`, 15 of 15 pass)

| # | Check | Measured |
| --- | --- | --- |
| 1 | Facilities before and after | 9,989 and 9,989 |
| 2 | Distinct facility ids | 9,989 |
| 3 | Duplicate facility ids | 0 |
| 4 | Accepted matches by source | NABH 986, eRaktKosh 67, Wikidata 133 |
| 5 | Facilities with accepted evidence against the Task D summary | 1,144 and 1,144 |
| 6 | Evidence rows missing source reference, source type or confidence | 0 |
| 7 | NABH split | accredited, validity unknown 390 (287 hospital programmes, 103 other programmes); certification 515 and empanelment 74 (not accreditation); withdrawn 6; suspended 1; valid 0; expired 0 |
| 8 | NABH rows marked verified | 0 |
| 9 | eRaktKosh rows | 67 directory listings, 0 verified |
| 10 | Contradictions and other differences | 23 and 375 |
| 11 | Clinical services marked verified, any source | 0 |
| 12 | Claims and quotes untouched | 26,405 claims, same as rebuilt from the rules |
| 13 | Ambiguous matches kept for review only | 192 |
| 14 | Download time shown as a verification date | 0 |
| 15 | Existing chunk C tests | 16 of 16 |

Valid and expired are both 0 by construction: the NABH directory carries no validity dates.

Contradictions (23): founding year differs from Wikidata by more than 2 years (17), beds differ from Wikidata (4), and the facility claims NABH accreditation while its matched record is withdrawn (2: Tristar Hospital, Ayursundra Superspeciality Hospital).

Other differences (375, flagged, not contradictions): NABH lists a facility whose text never mentions NABH (202); the facility says "NABH accredited" but the matched record is a certification or empanelment (144, not called a contradiction because the match is by name and pincode and the facility may hold another record); eRaktKosh lists a blood bank the facility's text never mentions (29).

Effect on existing results: `facility_service` is still 139,846 rows. Verification statuses are now unknown 113,408, inferred 26,351, externally supported 87, verified 0. Facilities with outside support that they exist rose from 2,820 to 3,315. `quality_tests` is unchanged at 21 of 22. Notebooks 11 and 12 were rerun afterwards so the better-evidenced map tier uses the new matches: `geography_checks` 12 of 12 and `catchment_checks` 5 of 5 still pass, better-evidenced catchment pieces rose from 7,939 to 9,251 (2,256 facilities), the claimed tier is unchanged at 27,026, and the 150 km critical area is unchanged (9,753 blocks, 5.87 million people).

Examples. High: Regional Hospital, Bilaspur matched to an eRaktKosh Blood Bank by name and distance (score 0.9). Medium: Amdavad Eye Laser Hospitals matched to an NABH eye-care accreditation by name and pincode (score 0.8). Ambiguous: Guru Nanak Hospital & Research Centre, Mumbai, an NABH entry-level record with a competing candidate. Conflicting: Hamidia Hospital, 1,820 beds in the Virtue Foundation data and 800 in Wikidata; both are kept.

### 18.5 Which table each consumer should read

| Consumer | Services | Outside evidence |
| --- | --- | --- |
| Dashboard | `vf_gold.facility_service` (or `facility_service_canonical` per centre) | `vf_gold.facility_external_summary`, and `facility_external_conflicts` for a conflicts panel |
| Recommendation engine | `vf_gold.facility_service`, column `verification_status` | `facility_external_summary`. Treat `externally_supported` as a tie-breaker at most, never as proof a service works |
| Patient app | `vf_gold.facility_service` | `facility_external_summary` for badges; `facility_external_evidence` for the source link and the `does_not_establish` wording |

No consumer should read `ext_facility_matches` or `facility_external_review` directly.

### 18.6 Limits and source gaps

- No NABH validity dates, so "currently accredited" cannot be shown for any facility.
- NABH matching is name plus pincode only. A same-named facility in the same pincode would be a wrong match; this has not been hand checked.
- eRaktKosh is a directory, not live stock. The 29 facilities listed with a blood bank but no claim in their own text have no claim of their own, so `claim_detected` stays false for them. Since 2026-11-22 (Ishaan's decision) the app's blood bank search in the Patients and EMRs tabs adds them, ranked after facilities that state the service directly and labelled "listed in the national blood centre directory": directory evidence only, not verified, no information on current stock. The Policy maps and the symptom recommender still use the facilities' own claims only.
- Wikidata beds and founding years are community edited and undated.
- Still not loaded: PM-JAY, CGHS, HMIS, National Hospital Directory, Government Health Centres Directory. Their absence says nothing about a facility.
- `ext_source_status` belongs to Task D and was not edited here.
- The matcher is the teammate's notebook-10 logic as delivered. The old `F.when(condition, 'fpin')` matcher was not restored.

---

## 19. Implemented: Rural Hospital Network profiles (notebook 14)

**Implemented and tested on 2026-11-22.** 42 self-reported hospital profiles from ruralhospitalnetwork.org (32 with beds, 11 with doctor counts, 15 with a founding year, each with its quote and page URL) are loaded and matched. The file is in the volume `vf_bronze.external` under `rural_hospital_network/` and in `external_sources/`.

| Table | Grain | Rows |
| --- | --- | ---: |
| `vf_bronze.ext_rhn_profiles_raw` | one row per profile, all strings, plus provenance | 42 |
| `vf_silver.ext_rhn_facility_matches` | profile and candidate facility, same columns as Task D's `ext_facility_matches`, source `rural_hospital_network` | 8,110 |
| `vf_gold.facility_rhn_evidence` | one row per accepted match, profile values beside Virtue Foundation values with quotes and URL | 5 |
| `vf_gold.facility_rhn_conflicts` | one row per difference, both values kept | 3 |
| `vf_gold.rhn_checks` | measured checks | 8 of 8 pass |

The matches sit in their own table, not in Task D's `ext_facility_matches`, so the teammate's tables are never overwritten. Notebook 13's summary does not include this source yet.

**Matching rule.** The profiles have no coordinates, so the best status is `accepted_medium`, never high. Accepted needs all three: the same distinctive name words, the same state, and a place word from the profile found in the facility's district, city or address. A similar name (0.80 or more) in the same state and place with different distinctive words is `ambiguous`, for hand review only. Everything else is rejected.

**Results.** 5 accepted, 3 ambiguous, 34 profiles with no candidate worth reviewing. Most of these rural hospitals are simply not in the Virtue Foundation dataset; an unmatched profile says nothing about any facility.

| Profile | Matched facility | Beds (VF, profile) | Founded (VF, profile) |
| --- | --- | --- | --- |
| Akhand Jyoti Eye Hospital, Mastichak | Akhand Jyoti Eye Hospital, Bihar | 500, 250 | 2005, not stated |
| Basel Mission CSI Hospital | CSI Basel Mission Hospital, Gadag, Karnataka | 130, 150 | 1902, 1902 |
| Cachar Cancer Hospital and Research Centre | same name, Silchar, Assam | 141, not stated | 1996, 1996 |
| Padhar Hospital | Padhar Hospital, Betul, Madhya Pradesh | unknown, 200 | 1970, not stated |
| Shaheed Hospital | Shaheed Hospital, Dalli Rajhara, Chhattisgarh | 150, 157 | 1983, not stated |

Differences: one contradiction (Akhand Jyoti, 500 beds against 250; counted as a contradiction because it is above 25% and 20 beds) and two smaller bed differences flagged only (Basel Mission 130 against 150, Shaheed 150 against 157). No founding-year differences. Padhar's 200 beds is shown beside an unknown Virtue Foundation value and is not copied into it.

Ambiguous, for review: Vishnu Dental Care against Vishnu Dental College (Bhimavaram); JMJ Hospital Kadaba against JMJ Hospital (the dataset's record is in Bengaluru); Kasturba Hospital (SEWA Rural) against Kakaba Hospital (Gujarat, probably a different hospital).

**Limits.** Values are self-reported and undated; `downloaded_at` is when the page was read, not a verification date. A match supports that the facility exists and never a clinical service. TAPS Hospital is listed on the site under Jharkhand but its address is Boisar, Maharashtra; it matched nothing in either state. For Akhand Jyoti the Virtue Foundation district is Samastipur while the profile says Saran; the match rests on the name and the town Mastichak.

---

## 20. Implemented: resolved values, "trust the external source" (notebook 15)

> Counts in this section were updated after the second matching pass (section 22) and the text audit (section 21): beds from outside 8, founding years 19, NABH 2; outside gap fills 4 beds, 12 founding years, 1 doctor count; text gap fills 54 beds, 73 founding years, 103 doctor counts. Coverage of 9,989 facilities: beds 2,496 to 2,554, founding year 4,768 to 4,853, doctors 3,613 to 3,717. Checks 13 of 13.

**Team decision (Ishaan, 2026-11-22):** where an accepted outside record conflicts with the Virtue Foundation record, trust the outside source. Applied additively in `vf_gold.facility_resolved_values` (one row per facility, 9,989). Original values are kept in `vf_beds`, `vf_year_founded` and `vf_nabh_claimed`; silver and every other table are unchanged.

| Field | Facilities changed | From |
| --- | ---: | --- |
| `beds_resolved` | 7 | Wikidata 4 (Hamidia 1,820 to 800, Osmania General 2,000 to 1,168, Victoria 1,800 to 1,000, Voluntary Health Services 218 to 465); Rural Hospital Network 3 (Akhand Jyoti 500 to 250, CSI Basel Mission 130 to 150, Shaheed 150 to 157) |
| `year_founded_resolved` | 17 | Wikidata, the 17 founding-year contradictions from section 18.4 |
| `nabh_status_resolved = 'withdrawn'` | 2 | NABH directory: Tristar Hospital, Ayursundra Superspeciality Hospital |

Each changed row has `..._resolution_source = 'external'`, the outside source name and reference, and `resolution_by = 'Ishaan manual decision 2026-11-22: trust the external source'`. 25 facilities are affected in total (Osmania General has both a beds and a year change). For every other facility the resolved value equals the Virtue Foundation value.

`vf_gold.resolved_values_checks`: 7 of 7 pass (one row per facility, counts 7 / 17 / 2, no field with two competing outside values, originals equal silver, nothing changed without an outside value, every change has a source reference and the decision note, no missing value filled in).

**Gap fill (second decision, same day: "that is one of the main reasons for the external data").** Where the Virtue Foundation value is missing, it is filled from an accepted (high or medium) match and marked `external_gap_fill`, with the source, link, match status and the decision note. Gains: beds 4 (Wikidata 3, Rural Hospital Network 1: Padhar Hospital 200), founding year 10 (all Wikidata), doctors 1 (CSI Basel Mission, 6 full-time doctors from its profile). The gains are small because only 1,144 facilities have an accepted match and most of those already had the value. NMC college founding years are not used as a hospital's founding year. `vf_doctors` and `doctors_resolved` were added. Checks are now 10 of 10.

Not covered: the ambiguous matches stay held for review; no clinical service is affected. A resolved value is a team decision, not a verification, and the outside values are undated.

App: the Patients and EMRs cards read the resolved values. They show a warning for the 2 facilities whose NABH accreditation is withdrawn, and a beds line where the outside figure is used. The ranking and the symptom recommender do not use beds, founding year or NABH status, so their order is unchanged.

---

## 21. Implemented: text extraction (Task B) and its audit (notebooks `07_Facility_Attribute_Extraction` and 16)

**Task B (a teammate's notebook, `07_Facility_Attribute_Extraction`).** It reads each facility's own text (description, capabilities, equipment, procedures) with strict regular expressions and writes `vf_silver.facility_extracted_attributes`: 7,042 rows on 4,926 facilities, one row per facility and attribute (`beds`, `doctors`, `year_founded`, `icu_beds`), each with the matched quote, the source field, a confidence and a `review_status`.

| `review_status` | Meaning | Rows |
| --- | --- | ---: |
| `existing_agrees` | the text gives the same value the record already has | 4,483 |
| `candidate_fill` | the record has no value and the text gives one | 700 (beds 137, doctors 135, founding year 151, ICU beds 277) |
| `conflict` | the text gives a different value from the record | 638 |
| `ambiguous` | the text gives several values, or an approximate or qualified one | 1,221 |

Task B never wrote into the facility record. Nothing used its output until this audit.

**Audit (notebook 16, 2026-11-22).** 22 random `candidate_fill` rows per attribute were read by hand against the full source sentence.

| Attribute | Wrong in sample | What went wrong |
| --- | --- | --- |
| beds | 8 of 22 (36%) | the number belongs to a unit or ward ("5-bed ICCU", "20 bed emergency department"), a plan ("aims to establish a 500-bed hospital"), a group total, or beds set aside for one purpose |
| founding year | 6 of 22 (27%) | "since YEAR" about a doctor, a registration, an accreditation or one department |
| doctors | 1 of 22 (5%) | "12 doctors and staff" |
| ICU beds | 0 of 22 | none found |

The raw candidates are therefore not safe to use as they are. Notebook 16 keeps a value only when the whole clause that holds it is a plain statement about the facility ("Has 100 beds", "60-bed hospital", "Established in 1995", "Operating since 2017", "Has 11 doctors on staff"), the value is in range, and the facility has one such value. A second hand check of 40 passing rows per attribute found no wrong value. Result in `vf_silver.facility_extracted_attributes_audit` (700 rows with the clause, `audit_pass` and `audit_reason`): beds 54 of 137 pass, founding year 73 of 151, doctors 103 of 135. ICU beds are not used by any resolved field yet. The teammate's table is unchanged.

**Use.** Passing values fill gaps in `vf_gold.facility_resolved_values` as `text_gap_fill` (section 20), only where neither the record nor an outside source has a value: 54 beds, 73 founding years, 103 doctor counts, each with its quote.

**Limits.** These are the facility's own words, not outside evidence. "Has N doctors available" usually comes from a listing site and may count visiting doctors. The samples are small (22 and 40 per attribute), so the error rates are rough. `conflict` and `ambiguous` rows were not audited or used. The facilities' linked websites were not read.

---

## 22. Implemented: matching, second pass (notebook 17)

Task D's matcher compares whole name strings, so it rejected true matches whose outside name has extra words ("Jaslok Hospital And Research Centre, Blood Centre", 20 m from "Jaslok Hospital and Research Center"). Notebook 17 re-reads Task D's rejected Wikidata and eRaktKosh candidates within 1 km and compares the distinctive name words. Task D's tables are unchanged: decisions are in `vf_silver.ext_facility_matches_pass2`, and the view `vf_silver.ext_facility_matches_all` lays them over Task D's rows. Notebooks 09 and 13 now read the view.

**Rule.** Drop noise words (hospital, blood, centre, bank, research, ltd, speciality and similar) and the facility's own city, district and state. Accept as medium, never high, when the remaining words are the same (one or two letters of spelling difference allowed) and the points are within 0.5 km, or 0.3 km when one word is left. Similar but not the same within 1 km is ambiguous. Different place words on the two sides is ambiguous. One-to-one only.

| Source | New accepted (medium) | New ambiguous (review) |
| --- | ---: | ---: |
| eRaktKosh | 136 | 69 |
| Wikidata | 27 | 25 |

All 163 new accepted pairs were read by hand; two wrong ones in the first run (a Dausa hospital against a Baran record, and a Kota college against "New Hospital Medical College") led to the place-word rule and to keeping "new" as a name word, and both are now ambiguous.

**Effect (after rerunning 09, 13 and 15).** Facilities with accepted outside evidence 1,144 to 1,253. eRaktKosh accepted matches 67 to 203; facilities listed with a blood bank but no claim of their own 29 to 102 (all shown in the app's blood bank search as directory listings). Wikidata accepted matches 133 to 160. `externally_supported` service rows 87 to 222, verified still 0. Facilities with outside support that they exist 3,315 to 3,363. Contradictions 23 to 26 (one more beds, two more founding years), all resolved to the outside value under the team decision. Outside gap fills: founding year 10 to 12. Ambiguous matches held for review 192 to 286. `external_integration_checks` 15 of 15, `facility_service_checks` 16 of 16, `resolved_values_checks` 13 of 13.

**OpenStreetMap.** Not in this pass. It was already matched by distance and name in notebook 06, and its export has no bed counts (the capacity column is empty for all 139,664 rows), so it cannot fill beds.

**Limits.** The new matches gain little for beds, doctors and founding year because eRaktKosh carries none of them and few Wikidata hospitals have them. Candidates beyond 2 km were never generated by Task D, so a facility with a wrong map pin is still missed.

---

## 23. Implemented: Rural Health Statistics 2021-22, government facility counts and staff (notebook 18)

**Source.** Rural Health Statistics 2021-22, Ministry of Health and Family Welfare, as on 31 March 2022. Two extracted files, in the volume `vf_bronze.external` under `rhs/` and in `external_sources/`. All-India totals of both files match the published book.

| Table | Grain | Rows |
| --- | --- | ---: |
| `vf_bronze.ext_rhs_district_facilities_raw` | RHS district: functioning sub centres, PHCs, CHCs, sub-divisional hospitals, district hospitals (rural + urban) | 737 |
| `vf_bronze.ext_rhs_state_beds_staff_raw` | state, table, measure, column (long format; blanks kept with their note) | 1,692 |
| `vf_gold.district_rhs_crosswalk` | RHS district to map district, with the link method | 737 |
| `vf_gold.district_rhs_link_issues` | RHS districts left unlinked, with the reason | 3 |
| `vf_gold.district_public_facilities` | one row per map district: government counts beside the facilities in our data | 735 |
| `vf_gold.district_context_with_rhs` | view: `district_context` plus the government columns | 735 |
| `vf_gold.state_public_health_capacity` | one row per state: government beds by facility type, PHC doctors and CHC specialists (required, in position, shortfall) | 36 |
| `vf_gold.rhs_checks` | measured checks | 13 of 13 pass |

`district_context` itself is not altered (notebook 11 rebuilds it); read the view or `district_public_facilities`.

**Linking rule.** Same as section 15.4: never the name alone. An RHS district links only when the state is the same and the name is the same as the map district, or is a name the existing crosswalk already links in that state. No fuzzy matching. That automatic pass linked 706 of 737. The other 31 were then checked by hand (team decision, 2026-11-22): 28 are clear spelling variants or renames within the same state and are linked, each with its reason in `link_method` (for example Kanyakumari to Kanniyakumari, Kawardha to Kabeerdham, Hathras to Mahamaya Nagar, Purba Bardhhaman to the map's Barddhaman, and Yanam, a Puducherry enclave the map places in Andhra Pradesh by position). Three are left unlinked on purpose: Noklak (Nagaland) and Malerkotla (Punjab) were created in 2021 and the map has no separate district, and Hanumakonda (Telangana) changed its boundary when it was renamed. Final: 734 of 737 linked, covering 731 of 735 map districts; the other 4 map districts show no count (unknown, not zero).

**What it shows.** In the 731 linked districts the government counted 200,798 public facilities, 8,102 of them community health centres or hospitals. Our data has 9,599 facilities there, mostly private. In 195 districts our data has no facility at all, yet the government runs 1,084 health centres or hospitals in them. State level: of 21,920 specialists required at community health centres, 4,485 are in position (shortfall 17,435, about 80%).

**Reading it.** Government counts and our facilities are different sets, so one is not a share of the other. The comparison measures how much public care our data cannot see, which matters most for the critical-area maps: a district shown as far from care may have a government hospital that is not in the dataset. Counts are for March 2022 and district boundaries have changed since. A blank staff value means surplus or not available, never zero.

**App.** Policy tab, expander "Government facilities beside this dataset": the summary above, a district table, and the state specialist table.

### 23.1 Critical-area caveat: government care that our data cannot see

A critical block means "far from any facility in this dataset". Where the block's district has no facility in our data at all, but the government counts community health centres or hospitals there, the block is flagged `govt_care_exists_not_in_our_data` so the area is not presented as having no care. The flag does not remove the block and does not say the care is near: the report gives district counts, not locations, so no distance and no catchment is drawn for those facilities.

- `vf_gold.critical_zones_govt_caveat`: every block of `critical_zones` and `critical_zones_emergency_150km` (202,839 rows) with the government counts for its district, the flag and a plain-language `govt_caveat`. `govt_hospitals_in_district` is filled for all blocks, flagged or not, because government health centres exist in almost every district and most are missing from our data.
- `vf_gold.district_critical_govt_caveat`: critical blocks and people per district and service, with the flag, for the map.

| Rule | Critical blocks | People (million) | In flagged districts: blocks | People (million) | Districts |
| --- | ---: | ---: | ---: | ---: | ---: |
| 150 km life-threatening rule | 9,753 | 5.87 | 8,117 | 3.06 | 37 |
| Emergency | 12,901 | 163.96 | 3,259 | 35.99 | 68 |
| Obstetric delivery | 12,021 | 156.19 | 3,274 | 36.82 | 68 |
| ICU | 15,332 | 202.39 | 3,605 | 41.09 | 69 |
| NICU | 18,010 | 254.27 | 3,665 | 42.01 | 68 |
| Trauma | 18,676 | 265.99 | 3,740 | 43.03 | 70 |
| Dialysis | 20,142 | 296.38 | 3,732 | 43.28 | 69 |
| Blood bank | 20,775 | 304.63 | 3,767 | 43.19 | 69 |
| C-section | 23,361 | 356.50 | 3,932 | 46.67 | 70 |

So 83% of the blocks under the 150 km rule, and about half of the people in them, sit in districts where the government runs health centres or hospitals that this dataset does not list. Whether those can handle a life-threatening emergency is unknown.

App: on the Policy map, flagged districts are ringed in red with the label "government care exists, not in our data" and a hover note giving the counts; a warning under the map states the number of ringed districts and government facilities for the chosen service. `rhs_checks` is now 15 of 15.

---

## 24. Final hand check of service claims (2026-11-22)

**Method.** 60 claims from `vf_gold.facility_service` (after all external evidence was integrated): the first 20 per claim strength in a fixed pseudo-random order (`xxhash64(facility_id, service, 'final-handcheck-2026-11-22')`), so the sample can be redrawn. Each was read against its quotes, the facility type and the outside evidence, and given one verdict. The sample was stratified because a plain random 50 would hold too few strong claims to say anything about them. Rows and verdicts: `vf_gold.facility_service_handcheck_final` and `handcheck_final_2026-11-22.csv`. Checked by Claude, not by a clinician.

| Claim strength | Claims in data | Supported: text states the service | Narrower than the service | Label only | Wrong |
| --- | ---: | ---: | ---: | ---: | ---: |
| Strong (direct wording in 2+ fields) | 5,931 | 20 of 20 | 0 | 0 | 0 |
| Moderate (direct wording in one field) | 9,321 | 16 of 20 | 2 | 0 | 2 |
| Weak (label or indirect wording) | 11,153 | 7 of 20 | 4 | 8 | 1 |

- **Wrong (3 of 60).** "Dental chair with integrated delivery unit" read as childbirth; "trauma care" at a dental clinic, meaning dental trauma (the pipeline had already flagged it as unusual for the facility type); "DVT catheter-directed thrombolysis" read as stroke care.
- **Narrower (6).** The text is about one specialty: children's eye care or children's dentistry counted as paediatrics, emergency eye care counted as emergency care, and a diagnostic lab that says it "provides emergency services".
- **Label only (8).** A specialty tag such as obstetrics and gynaecology, with no statement that deliveries are done. This is what "weak" means by design.

**Numbers to quote.** Weighted by how many claims of each strength exist, about 6% of all claims are wrong (0% strong, 10% moderate, 5% weak) and about 65% are stated outright by the facility's own text (100% strong, 80% moderate, 35% weak). With 20 per group these are rough: 0 of 20 still allows a true error rate of up to about 14%, and 2 of 20 a range of about 1% to 32%.

**What it does not measure.** "Supported" means the facility's text says it, not that the service exists or works today. Only 1 of the 60 claims had outside support for the service itself (Silchar Medical College's blood bank, listed in eRaktKosh), and none is verified. The earlier hand check is in section 12.6.

**Not fixed.** The three wrong patterns are left in the data and documented here; the better-evidenced tier already excludes weak claims and plausibility-flagged ones, which removes two of the three.

---

## 25. Hand review of the held matches (notebook 19, 2026-11-22)

Task D, matching pass 2 and the Rural Hospital Network matcher held 289 matches as `ambiguous` (NABH 187, eRaktKosh 71, Wikidata 28, Rural Hospital Network 3). Ishaan asked for them to be reviewed. Each was read by hand against the names, pincodes, addresses and map distances. Claude did the review, not a second person, so treat an accepted match as medium confidence.

**Rule.** Accept only when the distinctive name words agree and the place agrees: the same pincode and town for NABH, map points within about 1 km for eRaktKosh and Wikidata. Reject a clear mismatch: a different town, a different name, or a different kind of institution (dental college against medical college). Leave anything else for Ishaan with a one-line reason. Accepted matches are stored as `accepted_medium`, never high.

| Source | Accepted | Rejected | Left for Ishaan |
| --- | ---: | ---: | ---: |
| NABH | 173 | 4 | 10 |
| eRaktKosh | 53 | 6 | 12 |
| Wikidata | 19 | 4 | 5 |
| Rural Hospital Network | 0 | 2 | 1 |
| **Total** | **245** | **16** | **28** |

**Why most NABH rows were held.** NABH lists one organisation several times, once per programme (hospital accreditation, nursing, emergency, digital health, empanelment). Task D saw equally good records with the same name and pincode and held them all. Where the records are the same organisation, one is kept per facility: the highest class present (hospital accreditation, then other accreditation, then certification, then withdrawn, then empanelment). For 66 facilities this is a different record from the one Task D listed. The reason on each row names the record kept and the other classes. Of the 173 accepted: 83 hospital accreditation, 9 other accreditation, 63 certification, 18 empanelment.

**Where it is stored (nothing overwritten).**

| Object | What it is |
| --- | --- |
| `vf_silver.ext_match_review_decisions` | One row per held match: decision, record kept, reason, who decided and when (289 rows) |
| `vf_silver.ext_facility_matches_reviewed` | The accepted and rejected rows in Task D's column layout, `match_method = 'manual_review'` (260 rows) |
| `vf_silver.ext_facility_matches_prereview` | View: Task D plus pass 2, as before the review |
| `vf_silver.ext_facility_matches_all` | View read by notebooks 09 and 13: the pre-review rows with the reviewed rows laid over them |
| `vf_silver.ext_match_review_checks` | 8 checks, all passing |
| `medallion/match_review_decisions_2026-11-22.csv` | The same decisions as a file |

Task D's `ext_facility_matches` and the pass 2 table still hold their 286 ambiguous rows. The Rural Hospital Network decisions are recorded in the decisions table only: none was accepted, so notebook 14's tables are unchanged. To change a decision, edit its line in notebook 19 and rerun 19, 09, 13, 15.

**Effect (after rerunning 09, 13, 14, 15, 11, 12, 18).**

| Measure | Before | After |
| --- | ---: | ---: |
| Facilities with accepted outside evidence | 1,253 | 1,455 |
| Facilities whose existence an outside source supports | 3,363 | 3,449 |
| NABH accepted matches | 986 | 1,159 |
| eRaktKosh accepted matches | 203 | 256 |
| Wikidata accepted matches | 160 | 179 |
| `externally_supported` service rows | 222 | 274 |
| Blood banks listed in the directory without a claim of their own (shown in the app) | 102 | 126 |
| Founding years taken from the outside source on a conflict | 19 | 23 |
| Founding years filled from an outside source | 12 | 13 |
| `verified` rows | 0 | 0 |

The four new founding-year conflicts, resolved to the outside value under the team rule: M.V. Hospital for Diabetes (1972 to 1954), Mohak Hitech Speciality Hospital (2010 to 1999), Kamla Nehru Memorial Hospital (1939 to 1931), Asian Institute of Medical Sciences Faridabad (1996 to 2010). No bed value changed. All checks pass: external evidence 15 of 15, resolved values 13 of 13, facility service 16 of 16, review 8 of 8; `quality_tests` is 21 of 22 with the same deliberate failure as before.

**Left for Ishaan (28).** These stay `ambiguous` and are used nowhere.

Twelve are a second row in our data for a hospital whose other row already holds the match (one outside record is kept per facility): Shushrusha Nursing Home (nabh); SAL Hospital (nabh); Nerlikar Hospital (nabh); Siddh Hospital (nabh); BRJ Ortho Center & MAK Hospital (nabh); Infinity Medisurge Centre-Multispeciality Hospital (nabh); IRIS MULTISPECIALITY HOSPITAL (nabh); Jaslok Hospital (eraktkosh); Charak Hospital (eraktkosh); Rajindra Hospital, Patiala (wikidata); Siddh Hospital (eraktkosh); Nemcare SuperSpeciality Hospital (eraktkosh). Accepting them would be safe only if the two rows are the same hospital, which is a duplicate question for silver, not a matching question.

The other sixteen:

| Source | Our row | Outside record | Why it is unclear |
| --- | --- | --- | --- |
| nabh | Fortis Hospital | Fortis Hospital | Name and pincode say Fortis Mohali, but our row's street address (Chandigarh Road, Duggri, Dhandra) is in Ludhiana |
| nabh | Life Care Diagnostic Centre | LIFECARE DIAGNOSTIC CENTRE | Same name and pincode in Supela, Bhilai, but the street addresses differ (G.E. Road, Akash Ganga against Shivnath Complex) |
| nabh | Vikram Hospital | Vikram Multispeciality Hospital | Vikram Hospital at Anna Nagar Main Road against the NABH record at the Sivagangai ring road site, which is matched to our 'Vikram Multispeciality Hospital' row; may be another branch |
| wikidata | St. Martha's Hospital | St. Martha's Heart Centre | The Wikidata item is the St. Martha's Heart Centre, a unit inside the hospital, not the hospital itself |
| wikidata | Fernandez Hospital, Hyderabad, India | Fernandez Hospital | Fernandez has several branches in Hyderabad and the points are 750 m apart |
| eraktkosh | Sion Hospital | Sion Blood Centre | 'Sion' is also the locality name; 'Sion Blood Centre' is 1 km away and may not be the Sion Hospital blood bank |
| wikidata | Maharaja Jitendra Narayan Medical College and Hospital | Maharaja Jitendra Narayan District Hospital | Medical college and hospital against the district hospital of the same name, 800 m apart; probably the same institution after an upgrade, not confirmed from the data |
| wikidata | Oyster & Pearl Prime Hospital | Oyster & Pearl Hospital | 'Prime' is in our name only and the points are 800 m apart; may be another Oyster & Pearl site |
| eraktkosh | Dispur Hospital | Dispur Blood Centre | 'Dispur' is also the locality name, and the blood centre 600 m away may be a separate organisation |
| eraktkosh | Government Medical College, Kota | New Hospital Medical College, Kota | The record is the New Medical College Hospital blood bank; the college has two attached hospitals and our row names only the college |
| eraktkosh | KMC Hospital, Mangalore | Kmc | The record is named only 'Kmc' and KMC has more than one hospital in Mangalore; 500 m apart |
| eraktkosh | Vardhman Institute of Medical Sciences, Pawapuri | Blood Centre, Bhagwan Mahavir Institute of Medical Sciences Hospital Pawapuri | Different names at the same place (Vardhman against Bhagwan Mahavir Institute, Pawapuri); probably a renaming, which I could not confirm from the data |
| eraktkosh | Rainbow Hospital | Rainbow Hospital Blood Centre | Rainbow has several branches in Hyderabad and the points are 900 m apart |
| eraktkosh | St Joseph Hospital Quarters | St Joseph Hospital | Our row is 'St Joseph Hospital Quarters', which may be the staff quarters and not the hospital |
| eraktkosh | MITTAL BHILAI HOSPITAL | Mittal Blood Centre | 'Mittal Blood Centre' is 900 m from Mittal Bhilai Hospital and may be a separate unit |
| rural_hospital_network | Vishnu Dental College | Vishnu Dental Care | Same town (Bhimavaram) and same Vishnu group, but 'Dental Care' against 'Dental College'; probably the college's clinic, not confirmed |

**Limits.** Same name and same pincode can still be two different hospitals in a large pincode; the NABH address was checked against our town for every accepted row and against the street where our row has one, but 9 accepted single-word names had no street words in common and rest on name, pincode and town alone. NABH validity dates are still unknown. Nothing here validates a service.

---

## Sources

- Datathon challenge brief (shared by the team).
- *India Facility Care Levels for Routing* (project doc): IPHS 2022, FBNC Operational Guidelines 2025, PMNDP.
- [HOT OpenStreetMap export, Health Facilities of India (HDX)](https://data.humdata.org/dataset/hotosm_ind_health_facilities).
- [AIKosh: National Hospital Directory with Geo Code](https://aikosh.indiaai.gov.in/home/datasets/details/national_hospital_directory_with_geo_code_and_additional_parameters_updated_till_last_month.html), [All India Health Centres Directory](https://aikosh.indiaai.gov.in/home/datasets/details/all_india_health_centres_directory_till_october_2016.html), [CGHS empanelled hospitals](https://aikosh.indiaai.gov.in/home/datasets/details/list_of_hospitals_empaneled_under_cghs_all_over_india.html).
- [OpenStreetMap wiki: India Health Facilities Import](https://wiki.openstreetmap.org/wiki/India_Health_Facilities_Import).
- [geoBoundaries](https://www.geoboundaries.org/) gbOpen India ADM1 and ADM2.
- [WorldPop](https://hub.worldpop.org/) India 2020 population counts, 1 km, UN-adjusted.
