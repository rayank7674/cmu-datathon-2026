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
- Not built yet: the service search table (`facility_service`, with ranking by service, location and confidence) and `district_context` (NFHS need next to facility supply).

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

## Sources

- Datathon challenge brief (shared by the team).
- *India Facility Care Levels for Routing* (project doc): IPHS 2022, FBNC Operational Guidelines 2025, PMNDP.
- [HOT OpenStreetMap export, Health Facilities of India (HDX)](https://data.humdata.org/dataset/hotosm_ind_health_facilities).
- [AIKosh: National Hospital Directory with Geo Code](https://aikosh.indiaai.gov.in/home/datasets/details/national_hospital_directory_with_geo_code_and_additional_parameters_updated_till_last_month.html), [All India Health Centres Directory](https://aikosh.indiaai.gov.in/home/datasets/details/all_india_health_centres_directory_till_october_2016.html), [CGHS empanelled hospitals](https://aikosh.indiaai.gov.in/home/datasets/details/list_of_hospitals_empaneled_under_cghs_all_over_india.html).
- [OpenStreetMap wiki: India Health Facilities Import](https://wiki.openstreetmap.org/wiki/India_Health_Facilities_Import).
