# External sources for missing facility data (Chunk D)

*Checked live on 2026-11-22 (about 11 PM ET, 21 Nov) from Ishaan's computer. NABH is excluded because a teammate is scraping it. Nothing has been loaded into Databricks.*

**Rules for any load:** the Virtue Foundation dataset stays primary. An outside value is only used where it is explicit for that facility, is stored with source, URL, date and match confidence, and is never averaged, imputed or derived from another number.

**Gaps we are trying to fill:** beds (about 25% filled), doctors (about 36%), year founded (about 48%), and the 72% of facilities with no outside match.

## Summary

| # | Source | Fills | Reachable now? | Login? | How to get it | Value |
|---|---|---|---|---|---|---|
| 1 | National Hospital Directory with Geo Code (NIHFW / National Health Portal) | Year founded, probably beds and doctors, coordinates, category, specialties | Yes, on AIKosh. data.gov.in copy returned 503 tonight | **Yes, AIKosh account (free)** | One CSV, 9.86 MB | **Highest** |
| 2 | NMC list of medical colleges teaching MBBS | Teaching-hospital status, govt/private, MBBS seats, date of permission | Yes | No | Scrape public HTML table (839 rows) | High for top-tier facilities |
| 3 | CGHS empanelled hospitals | Verifies CGHS empanelment | Yes, on AIKosh. cghs.mohfw.gov.in refused | **Yes, AIKosh** | One file (half-yearly, updated 2 Jul 2026) | Medium |
| 4 | eRaktKosh blood centre directory | Verifies blood bank | Yes | No | Public directory by state and district (JSON behind the page) | Medium |
| 5 | OpenStreetMap `beds` tag (already loaded) | Beds | Already in `vf_bronze.osm_health_facilities_raw` | No | Query only | Small but free |
| 6 | Wikidata (already loaded) | Beds, year founded | Yes | No | Already loaded | Small: of 2,772 Indian hospitals, 105 have beds and 473 have a founding date |
| 7 | City open data (OpenCity: Pune, Delhi, Bengaluru, Mumbai) | Beds for those cities | Site is up, but a bot check stopped my automated look | No, but a person must open it in a browser | CSV per dataset | Medium for those cities only |
| 8 | NITI Aayog district hospital report (2021) | Beds for district hospitals, possibly | Yes, 48.6 MB PDF | No | PDF table extraction | Low to medium, unconfirmed |
| 9 | PM-JAY hospital search | Verifies PM-JAY empanelment, specialties | **No.** pmjay.gov.in, hospitals.pmjay.gov.in and nha.gov.in all refused or returned 403 from a US connection | No login, but needs an India connection | Search by state and district | Medium, if someone in India can pull it |
| 10 | ABDM Health Facility Registry | Beds, ownership, registry ID | **No.** facility.abdm.gov.in returned 403 | Integrator credentials | API | Not feasible by noon |
| 11 | HMIS facility-wise service data | Deliveries, C-sections | **No.** hmis.mohfw.gov.in did not load | Yes, for facility-level reports | n/a | Stop: only state and district reports are public |
| 12 | Clinical establishment registries | Beds, registration | **No.** clinicalestablishments.gov.in refused; the MoHFW site only has standards | Search needs an admin login | n/a | Stop |

## Details

### 1. National Hospital Directory with Geo Code and additional parameters
- **Where:** [AIKosh dataset page](https://aikosh.indiaai.gov.in/home/datasets/details/national_hospital_directory_with_geo_code_and_additional_parameters_updated_till_last_month.html). Same data on [data.gov.in](https://data.gov.in/resource/nin-health-faclities-geo-code-and-additional-parameters-updated-till-last-month), which returned HTTP 503 tonight.
- **Format and licence:** structured file, 9.86 MB, GODL (Government Open Data Licence, attribution). Publisher NIHFW. The page says updated 11 Aug 2026, but the file's own timestamp is 2017, so treat values as historical.
- **Access:** the page shows Login and Register, so the download needs a free AIKosh account. Ishaan signs in once in the built-in browser and downloads it, or I drive the download after he signs in.
- **Fields:** confirmed from the [OSM import notes](https://wiki.openstreetmap.org/wiki/India_Health_Facilities_Import), which used this file: name, address, pincode, state, district, category, specialties, facilities, emergency services, registration number and **established year**. The OSM import dropped established year, so our OSM copy does not have it. Bed and doctor columns are in the published schema as I remember it, but I have not seen the file, so that needs confirming on download. Expect many of those cells to be blank.
- **Matching:** coordinates plus normalised name plus pincode, using the existing notebook 05/06 path (`vf_bronze.external/gov_hospital_directory/`). This is the biggest lever on the 72% unmatched, because it is a national hospital list with coordinates.
- **Use:** beds, doctors and year only where explicit, stored as `ext_` values with source and match confidence. Never overwrite a VF value; where both exist, record agree or disagree.

### 2. NMC list of medical colleges teaching MBBS
- **Where:** [NMC information desk](https://www.nmc.org.in/page/information-desk-for-students-to-study-in-india-list-of-college-teaching-mbbs). The table shows 839 colleges and 140,664 seats, up to 500 rows per page.
- **Fields:** state, college name and address, university, management (Govt or Pvt), annual MBBS intake, recognition status, date of letter of permission.
- **Access:** public, no login. Scrape the HTML table. Licence: public government page, no open licence stated, so cite it.
- **Matching:** name plus state plus city, against facilities with teaching words in the name. Supports rung 5 (teaching hospital).
- **Caution:** do not turn seat counts into bed counts. NMC rules tie seats to minimum beds, but that is a regulatory minimum, not the hospital's real count. If used at all, it goes in a separate `regulatory_minimum_beds` column, never in `beds`.

### 3. CGHS empanelled hospitals
- **Where:** [AIKosh dataset](https://aikosh.indiaai.gov.in/home/datasets/details/list_of_hospitals_empaneled_under_cghs_all_over_india.html), updated 2 Jul 2026, half-yearly, GODL. Fields: city, hospital name, address (includes diagnostic and eye centres).
- **Access:** AIKosh login, same session as source 1. cghs.mohfw.gov.in refused from here. Agencies re-post the consolidated list as PDFs (for example a BIS circular dated 5 Aug 2026), but the PDF did not open in the browser, and PDFs would need table extraction.
- **Matching:** name plus city. It is the official list, so a match can set `_cghs_empanelment` to `verified` (the notebook 09 rule). No match does not mean "not empanelled".

### 4. eRaktKosh blood centre directory
- **Where:** [eRaktKosh directory](https://eraktkosh.mohfw.gov.in/eraktkoshPortal/#/publicPages/bloodBankDirectory). It loaded and lists by state and district. The page calls a JSON endpoint (`/eraktkoshPortal/eraktkosh/master/all` for the state and district lists).
- **Fields:** name, address, phone, email, category (government, private, charitable), distance on the nearby page.
- **Access:** public, no login. Loop over state and district, gently. An AIKosh copy also exists ("Blood Bank Directory, updated till last month") behind the same login.
- **Matching:** many entries are named after their hospital ("X Hospital Blood Centre"). Name plus district plus address. A match sets `blood_bank` to `verified` (notebook 09 rule).

### 5. OpenStreetMap beds tag
Already loaded (139,664 records). Count how many of the 2,411 confirmed matches carry a `beds` tag, and take explicit values only. This is a query, not a download.

### 6. Wikidata
Already loaded by Task D (133 accepted matches). I checked live: of 2,772 Indian hospitals on Wikidata, only 105 have a bed count (P6801) and 473 have a founding date (P571). The gain is small: re-pull those two properties for the 133 matches.

### 7. City open data (OpenCity)
- [Pune ward-wise hospitals with capacity](https://data.opencity.in/dataset/pune-healthcare-hospitals-and-clinics), [Delhi hospital capacity 2023](https://data.opencity.in/dataset/delhi-health-centres/resource/delhi-hospital-capacity-2023), [Bengaluru hospitals](https://data.opencity.in/dataset/bengaluru-hospitals), [Mumbai hospitals list](https://data.opencity.in/dataset/mumbai-city-public-health-centres-maps).
- CSV downloads, no account. A bot check blocks automated access, so someone opens each page in a browser and downloads the CSV. Licence is per dataset; check each.
- Explicit bed counts, but only for those cities.

### 8. NITI Aayog "Best practices in the performance of district hospitals" (2021)
[PDF, 48.6 MB](https://niti.gov.in/sites/default/files/2023-03/District_Hospital_Report_for_digital_publication.pdf), reachable. It assessed about 700 district hospitals and reports beds per lakh population. I have not confirmed it lists a bed count per hospital. Only worth it if it does.

### 9. PM-JAY hospital search (blocked from here)
pmjay.gov.in, hospitals.pmjay.gov.in and nha.gov.in refused or returned 403 from Ishaan's connection, which looks like an India-only block. Options: a teammate in India pulls the state and district lists, or use district PDFs that collectors post (for example [Agra](https://agra.nic.in/document/list-of-empanelled-gov-pvt-hospitals-under-ayushman-bharat-pmjay-district-agra/)), which only cover a few districts. A match would set `_pmjay_empanelment` to `verified`.

### 10 to 12. Not feasible tonight
- **ABDM Health Facility Registry:** facility.abdm.gov.in returned 403. Bulk access needs ABDM integrator credentials.
- **HMIS:** the site did not load. Facility-level reports need a login; only state and district item-wise reports are public (on AIKosh). Per the brief: documented, stop.
- **Clinical establishment registries:** the national portal refused; the MoHFW site only has the standards; searching registrations needs an admin login. State portals differ and are not bulk-downloadable.
- **Not recommended:** Practo, JustDial and Google Places (terms forbid scraping or storing). No Kaggle mirror of the hospital directory turned up.
- **NSS 80th round costs:** already in `vf_bronze.ext_cost_reference`.

## Suggested order before the noon deadline
1. Ishaan signs in to AIKosh in the built-in browser. I download the hospital directory, CGHS list, blood bank directory and all-India health centres directory, then upload them to `vf_bronze.external`.
2. I scrape NMC colleges and eRaktKosh (no login needed).
3. I count OSM `beds` and re-pull Wikidata beds and year for the accepted matches.
4. City open data, if there is time.

Each loader writes only `ext_` tables with `source_name`, `source_url`, `publication_date`, `downloaded_at`, and the matcher keeps its ambiguous bucket. Nothing changes a VF value.

## Downloaded so far (files only, not loaded into Databricks)

Downloaded on 2026-11-22. These are in `/mnt/project-files/external-sources/raw/`. Each row carries `source_name`, `source_url` and `downloaded_at`.

| File | Rows | Notes |
|---|---:|---|
| `nmc_mbbs_colleges.csv` | 839 | From NMC's own JSON (`/college-courses/list`). 835 have `inception_year` (college founding year), plus management (Govt/Private), university, MBBS seats and permission date. The founding year is the **college's**, not necessarily the attached hospital's. Use it only as a separate, labelled value. |
| `eraktkosh_blood_centres.csv` | 6,524 | 4,754 blood banks + 1,770 blood storage units (BSU). Category: Govt 2,982 / Private 1,804 / Charitable 1,628 / Red Cross 110. 5,267 have coordinates. Only `centre_type = Blood Bank` should verify `blood_bank`. A BSU stores blood but does not collect or test it. Phone numbers and emails were left out. |

The AIKosh download is blocked because sign-up needs an Indian phone number. data.gov.in was still returning 503 at 11:10 PM ET.
