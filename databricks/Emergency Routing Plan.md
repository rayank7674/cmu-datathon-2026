# Emergency routing map and assistant: plan

> Exported from the live plan doc (https://claude.ai/artifact/97prmoCcvpePu73Z7ySpkX) at 2026-11-21 21:57 UTC. Edit the doc, not this file; this copy is re-uploaded whenever the plan changes.

Nov 21, 2026 · Ishaan

## Summary

We can build all twelve asks, plus the ten core ideas in section 13, on Free Edition in two surfaces: the existing AI/BI dashboard for analysts and judges, and one Databricks App with three tabs (policy makers, EMRs, patients) that holds the emergency map, Google Maps routing and the assistant. The dashboard can already draw polygons (Voronoi cells, district zones) from geometry columns, so the map work is mostly data prep in notebooks, not new tooling.

The honest gaps are data, not software. Nobody publishes a price per hospital, so cost becomes a public vs private band with sources, never a quote. Empanelment can only be shown as "verified" once we have the official PM-JAY and CGHS lists; until then it stays "self-claimed". Distance is straight-line unless we load a travel-time raster.

| Ask | What we'd ship | Confidence it lands in datathon time |
| --- | --- | --- |
| India map, Voronoi, critical zones | Dashboard page + App map, catchments per emergency type, disadvantaged districts shaded | High |
| Cost per hospital, public vs private | Expected-cost band per facility from NSS + PM-JAY/CGHS rates | Medium |
| Rank by emergency level, implied priority | Triage tiers that change the ranking weights | High |
| Conversational assistant + telehealth | Chat in the App (or a Genie space as fallback), eSanjeevani routing for non-urgent cases | Medium |
| Empanelment labels | Verified / self-claimed / unknown badge for PM-JAY and CGHS | Medium (depends on lists) |
| Next hospital placement | Greedy maximal-coverage model over critical zones, top 10 sites | Medium |

Hospital contacts with a one-tap alert (section 7) sit on top of all of this: high confidence for click-to-call and a drafted message the person sends, low for fully automatic alerts sent by our server.

Added in this revision: three audience tabs with preloaded profiles (section 8), Google Maps routing with a district zoom that recomputes everything live (section 9), and a sourced lives-and-money-saved model (section 10). The money figure is firmly sourced. Lives saved is shown as a range whose low end is near zero, because studies disagree on whether faster arrival alone saves lives.

Decisions we need from you are at the end: how much time is left, whether to build the App or stay dashboard-only, and who can download the official lists.

## Re-scoped for the video due Sunday 12 PM ET

Claude does all the technical building, working through the night in two parallel build threads (data and tables in one, the App in the other). The team's time goes to approvals, keys, browser downloads, testing and recording. At that pace most of the plan fits by the deadline: about 22 hours of build work, done in about 11 to 12 hours of clock time. The video follows three people through the same emergency: a patient, an ambulance crew and a district health officer.

**What gets built, in priority order** (if something slips, we cut from the bottom)

| # | What | Thread | Claude hours | Needs from the team |
| --- | --- | --- | --- | --- |
| 1 | Ranking core, emergency and transit rules, contacts, cost bands, self-claimed badges | Data | 1.5 | Databricks token |
| 2 | Voronoi for all four services, critical zones, dashboard "Emergency map" page | Data | 3 | Boundary and population files uploaded |
| 3 | App with three tabs, national map, synthetic personas, call 108, click-to-call, pre-arrival message | App | 4.5 | Google Maps key |
| 4 | Google drive and transit routes with the transit safety gate | App | 1.5 | Same key |
| 5 | District zoom with live drive times, for any district | App | 2 |  |
| 6 | Impact model: money, measured outcomes, lives range, sensitivity chart | Data | 2 |  |
| 7 | Feedback loop: report buttons, belief scores, live re-ranking (section 12) | App | 2.5 |  |
| 8 | Assistant: Genie space, then chat in the App if a chat model is available | App | 2.5 | Check which models Serving lists |
| 9 | Verified PM-JAY / CGHS badges | Data | 1 | Lists uploaded |
| 10 | Placement of the next hospital, top 10 per service | Data | 1.5 |  |

Items 1 to 7 are the core of the video and are very likely to be done. Items 8 to 10 are likely but depend on the uploads and on Serving. The estimates include debugging through the API; the realistic slip is 20 to 30%, which is covered by the gap between finishing (about 7 AM) and recording (9 AM).

The ten core ideas from section 13 add about 10 hours, built by a third Claude thread in parallel, so the finish time stays around 7:30 AM. That's about 32 hours of build in roughly 40 thread-hours available, which leaves a 25% buffer; stretch ideas only start if the core is done by 3 AM. The video can't show everything in 3 to 5 minutes, so the script follows one story and the rest appears in a 20-second "also built" montage.

**Still cut until after the datathon:** the travel-time raster, PM-JAY package rates per procedure, and a Twilio alert demo.

**Schedule (ET)**

| When (ET) | Claude, data thread | Claude, App thread | Claude, extras thread (section 13) | Team |
| --- | --- | --- | --- | --- |
| Sat 5:00 to 5:45 PM | Waits for approval and token | Waits for the Google key | Waits | Approve; paste a Databricks token valid until Sunday noon; create the Google key; check Serving models |
| 5:45 to 7:30 PM | Item 1, agree table formats with the other threads | App skeleton and three tabs on sample data | Night availability, blood bank, women and children signals from claims | Upload boundaries, population, CGHS and PM-JAY lists (about 45 min) |
| 7:30 to 11:00 PM | Items 2 and 6 | Items 3 and 4 on live tables | Condition time windows, confidence-aware catchments, equity lens | Free |
| 11:00 to 11:30 PM | Checkpoint: a walkthrough link for you to try |  |  | Optional look, 15 min |
| 11:30 PM to 3:00 AM | Items 9 and 10 | Items 5 and 7 | Synthetic evaluation, reliability index, backup hospital, telehealth flow | Sleep |
| 3:00 to 7:00 AM | Fixes, README change log, buffer | Item 8, polish, test personas | Stretch ideas in order, only if the core is done | Sleep |
| Sun 7:00 to 8:30 AM | Fix what you report | Restart the App so its 24 h clock covers recording |  | Test the three personas, report anything broken |
| 8:30 to 9:00 AM |  |  |  | Final script (we draft it overnight) |
| 9:00 to 10:30 AM |  |  |  | Record |
| 10:30 to 11:30 AM |  |  |  | Edit, captions |
| 11:30 AM to 12:00 PM |  |  |  | Submit, with half an hour of buffer |

**Risks to the schedule.** Free Edition shuts compute for the day if the daily quota is exceeded; the datathon gives us unlimited top-ups, so the fix is a top-up, but someone must be reachable to request one overnight, and heavy steps still run once and are cached. The token must last until Sunday noon or the overnight work stops. Anything that needs a sign-in (AIKosh, Google Cloud, PM-JAY lists) can only be done by the team, so items 2, 3 and 8 wait on those.

## 1. Emergency map of India

The map answers one question with no typing: "I'm here, it's serious, where do I go?" A person picks an emergency type (injury, pregnancy, newborn, kidney/dialysis, general) and the map colours India into catchments, one cell per facility that can actually handle it.

**What it shows**

- **Voronoi catchments per emergency type.** A cell around every facility with the right rung and service (for example trauma needs rung 4+ with a trauma signal; obstetric needs CEmONC). Cells for "any hospital" would send a road accident to a PHC, so we never draw that one alone.
- **Facility points** sized by rung, coloured by confidence, with an empanelment badge (section 5).
- **Critical zones**: districts shaded where the catchment is large and the district is disadvantaged. Disadvantage comes from NFHS-5 district indicators we already hold (health insurance coverage, institutional births, women's schooling, child stunting), combined into one index with equal weights we can tune.
- **Click a cell or district** to see the facility, its quoted evidence from `facility_claims`, confidence, distance, and cost band.

**How it's built**

1. A Python notebook computes Voronoi cells with `scipy.spatial.Voronoi` from `vf_silver.facility_geo`, clips them to India's outline, and writes WKT polygons to `vf_gold.catchment_<service>`. Databricks SQL turns them into GEOMETRY with `ST_GeomFromWKT`.
2. Per cell: area, approximate population (from a population grid), farthest-point distance, and the NFHS index of the district it sits in.
3. `vf_gold.critical_zone` = district rows with index, catchment burden and a red / amber / none flag.
4. **Dashboard:** a new "Emergency map" page with a choropleth of catchments (polygons are supported from geometry columns) and a point map on top, plus a service filter. **App:** the same tables drawn with Leaflet or pydeck, with "use my location" and a large "nearest capable hospital" card.

**Data and gaps**

| Need | Source | Status |
| --- | --- | --- |
| Facility points, rungs, services | Our `vf_silver` / `vf_gold` tables | Have |
| India outline and district polygons | geoBoundaries (ADM0, ADM2) or Survey of India based files | Team uploads to the volume; Free Edition's outbound internet is limited |
| Population per area | Kontur population (H3, on HDX) or WorldPop | Team uploads |
| Travel time instead of straight line | Malaria Atlas Project travel-time-to-healthcare raster (from the EMS research doc) | Optional, later |

**Caveats.** Voronoi cells are straight-line catchments; across rivers and hills the nearest hospital by road can differ, so the map says "nearest as the crow flies" until the raster is in. India's official boundaries differ from some open datasets in disputed areas; we should use a boundary file that follows the Survey of India outline or note the source on the map. Some coordinates may be geocoded to a pincode or town centre rather than the building; we'll check for stacked duplicates before seeding cells and drop or flag them.

## 2. Cost per hospital, public vs private

No public dataset gives the price of care at a named Indian hospital, so we show an **expected out-of-pocket band** per facility and label how it was estimated. That is still useful: the national gap is about 7x.

| Source | What it gives | Use |
| --- | --- | --- |
| [NSS 75th round, Health, 2017-18 (PIB)](https://pib.gov.in/PressReleasePage.aspx?PRID=1593246) | Average spend per hospitalisation (excluding childbirth): public Rs 4,452, private Rs 31,845; rural Rs 16,676, urban Rs 26,475 | Baseline band for public vs private, rural vs urban |
| [CAMS, NSS 79th round, 2022-23 (ThePrint)](https://theprint.in/health/92-of-rural-households-hospitalisation-costs-are-out-of-pocket-77-for-urban-families-govt-data/2306247) | About 92% of rural and 77% of urban household hospitalisation cost is out of pocket | The "why this matters" line, and the share people actually pay |
| [PM-JAY Health Benefit Package 2.2 (NHA)](https://nha.gov.in/img/resources/HBP-2.2-manual.pdf) | Fixed package rates per procedure (e.g. a C-section, a trauma surgery) | What an empanelled hospital is paid, so what a covered patient avoids paying |
| CGHS rate list (cghs.gov.in) | City-tier rates, NABH vs non-NABH | A ceiling for private prices in a city, and a check on our band |

**How the band is built.** `vf_gold.facility_cost` holds, per facility: ownership (public / private / charitable from `operator_type`), urban or rural (from pincode), rung, and empanelment. Public facilities get the NSS public figure; private ones get the NSS private figure scaled by rung. If the facility is verified PM-JAY empanelled and the person is eligible, the expected cost for a covered package is shown as near zero, up to the Rs 5 lakh per family per year cover. Every band carries a "source" and "estimated" tag.

**Gaps and risks.** The NSS figures are 2017-18 national averages, so we inflate to 2026 with CPI (health) and say so. They aren't per-procedure: a dialysis session and a bypass get the same band unless we use PM-JAY package rates for the specific emergency, which is the better route for the four service levels we already have (NICU, CEmONC, trauma, dialysis). Showing a single rupee figure as a price would be misleading; the UI shows a range and "typical", never "cost".

## 3. Ranking by emergency level, and the implied priority

The more severe the emergency, the less personal preference and cost count: in a red case the only questions are "can they treat it?" and "how fast can I get there?". That rule is the implied priority, and it's written into the ranking weights rather than left to the user.

| Level | Examples | Hard filter | Ranking weights (capability / time / confidence / cost / preference) | What the UI says first |
| --- | --- | --- | --- | --- |
| Red: life-threatening | Major injury, heavy bleeding in pregnancy, newborn not breathing, chest pain, stroke signs | Must have the matching service level (trauma, CEmONC, NICU) and rung 4+ | 0 / 70 / 30 / 0 / 0 | "Call 108 or 112 now." Nearest capable facility, plus nearest stabilising facility if that is far |
| Orange: urgent, stable | Fracture, high fever in a child, labour without complications, missed dialysis | Matching service, rung 3+ | 0 / 45 / 25 / 20 / 10 | Top 3 options with travel and cost band |
| Green: can wait | Follow-up, chronic condition, mild symptoms | None | 0 / 20 / 20 / 30 / 30 | eSanjeevani teleconsult first, then nearby PHC or clinic |

Capability is a filter, not a weight, which is why it shows 0. Cost is shown at every level (the empanelment badge stays visible in red), but it doesn't reorder red results.

**How it's built.** `vf_gold.emergency_rule` (editable, like `level_signal_rules`) maps each emergency type to its filter and weights. A SQL function `rank_facilities(lat, lon, emergency, prefs)` returns the top N with distance, rung, confidence, quoted evidence and cost band. The map, the dashboard and the assistant all call this one function, so they never disagree.

**"Nearest stabilising" rule.** If the nearest capable facility is more than about 60 minutes away (the golden hour; straight-line distance converted at a conservative road speed until the raster is in), we also show the nearest rung 3 facility with a 24x7 emergency claim, labelled "stabilise and transfer".

**The choice model, step by step.** It's a transparent rule-based scoring model, not a trained one: there's no outcome data (who survived where) to train on, so every step is a stated rule a judge can read.

1. **Filter, never trade off.** Keep only facilities that have the service the condition needs (trauma, CEmONC, NICU, dialysis, or general) at the minimum rung for the triage level, with usable coordinates. A closer facility that can't treat the case is never ranked above one that can.
2. **Score each of the remaining facilities from 0 to 1 on four things:**
   - Time: 1 at 0 minutes, falling to 0 at 120 minutes (Google drive time, or straight-line distance x 1.4 at 30 km/h until routing is live).
   - Confidence: from our care-level confidence (high 1.0, medium 0.6, low 0.3), multiplied by the facility's data quality score.
   - Cost: 1 for public or verified-empanelled when the person is covered, 0.5 for self-claimed empanelment, and lower for uncovered private care as the expected cost band rises.
   - Preference: the share of the person's stated preferences it meets (language, public hospital, woman doctor, near family).
3. **Combine with the weights for the triage level** (the table above): total = sum of weight x score. Red ignores cost and preference entirely.
4. **Safety overrides after scoring:** 108 / 112 is always shown first; in red cases, if the top facility is more than 60 minutes away, the nearest rung 3 facility with a 24x7 emergency claim is added as "stabilise and transfer"; transit appears only under section 11's gates.
5. **Explain:** the top 3 are shown with each score's part, the quoted claims behind the capability, and the confidence.

**How we check it** without outcome data: hand-check the top pick for about 20 test cases against what a clinician would expect, see how often the top pick changes when each weight moves by 10 points, and confirm top picks against OpenStreetMap and government lists where we have them.

**Risks.** This is decision support, not medical triage. The level picker must use plain symptoms, carry a visible "if unsure, treat it as red" line, and never discourage calling an ambulance. Weights are our judgement; we'll show them in the README so judges can see and challenge them.

## 4. Conversational assistant and telehealth

The assistant asks a few questions, turns the answers into the same ranking call as the map, and explains its pick with quoted evidence and an honest confidence line. It doesn't invent facilities or facts: every recommendation comes from `rank_facilities`, and every claim it repeats is a `raw_text` quote from `facility_claims`.

**Priorities it captures** (each maps to a ranking input): location; emergency type and severity; budget or "I can't pay"; PM-JAY card, CGHS card or private insurance; maximum travel time; language; preference for a public hospital or a woman doctor; whether they need to stay near family. In red cases it skips the questions and gives the nearest capable facility plus "call 108".

**Telehealth routing.** For green cases, and as a second step for orange ones while travelling, it points to:

- **eSanjeevani**: the national free teleconsultation service (doctor-to-patient OPD and hub-and-spoke from Ayushman Arogya Mandirs).
- **Tele-MANAS 14416**: the national mental-health helpline, for crisis and distress.
- **108 / 112**: ambulance and emergency numbers, always shown.
- A "what to tell the doctor" summary the person can show at the hospital.

**Build options on Free Edition, best first**

| Option | How | Effort | Risk |
| --- | --- | --- | --- |
| A. Chat in the Databricks App | Streamlit or Gradio app; a Foundation Model API chat model with tool calls to `rank_facilities` and a claims lookup on the SQL warehouse | 6 to 8 h | Free Edition limits active serving endpoints and some models aren't available; we must confirm a pay-per-token chat model is listed in Serving before committing. Apps stop after 24 h and need a restart before judging |
| B. Genie space, embedded or linked | A Genie space over `facility_level`, `facility_claims`, `facility_cost`, `critical_zone` with instructions for priorities and refusals | 2 h | Genie writes SQL; it won't follow a conversation flow or triage rules as reliably, and it can't do telehealth routing beyond text |
| C. Both | Build B first as a safety net, then A | 8 to 10 h | Most time |

We recommend C if time allows, else B. Either way the assistant shows "not medical advice; in an emergency call 108" on every screen.

## 5. Empanelment labels

A badge on every private facility tells people whether a government scheme will pay, and how sure we are. The badge is only "verified" when the hospital appears on an official list; a hospital's own claim stays "self-claimed", because a wrong "covered" label is the one error that could cause the debt we're trying to prevent.

| Badge | Meaning | Source |
| --- | --- | --- |
| PM-JAY verified | On the National Health Authority's empanelled list | PM-JAY hospital finder (hospitals.pmjay.gov.in) or state health agency lists |
| CGHS verified | On the CGHS empanelled list for that city | CGHS list on AIKosh (already queued for upload) |
| Self-claimed | The facility's own text mentions PM-JAY, Ayushman or CGHS | Our `facility_claims` (already extracted) |
| Public, free or low cost | Government facility; PM-JAY also covers public hospitals | `operator_type` |
| Unknown | No signal | Shown plainly, with "ask about cashless schemes at the desk" |

**How it's built.** Upload the lists to `vf_bronze.external/pmjay/` and `cghs/`, then reuse notebook 06's matcher (name similarity + pincode + distance) to write `vf_gold.facility_empanelment` with the match score. Low-score matches stay "self-claimed". The dashboard and App read the badge; the assistant asks "Do you have an Ayushman card?" and, if yes, filters to verified PM-JAY first in orange and green cases.

**Gaps.** The PM-JAY finder is a search page, not a bulk download, and Free Edition can't reach it directly. Options: a team member exports state lists in a browser, we use state health agency PDFs for a few demo states, or we show verified badges only for the states we cover and say so. Empanelment is also per specialty: a hospital can be on PM-JAY for orthopaedics but not cardiology, so the badge should read "empanelled (check specialty)" unless the list gives specialties. State schemes (for example Maharashtra's MJPJAY, Telangana's Aarogyasri) are out of scope for the datathon unless a list is easy to get.

## 6. Where to place the next hospital

We'd frame it as a maximal coverage problem: choose K new sites (or K existing facilities to upgrade) that bring the most disadvantaged people within reach of a capable facility. Upgrading a rung 3 CHC to a rung 4 first referral unit is often more realistic than a new build, so we'd run both and show them side by side.

**Model**

1. **Demand:** population per H3 hexagon (resolution 6, about 36 sq km), weighted by the district's NFHS disadvantage index, so a person in a red critical zone counts more.
2. **Already covered:** hexagons within T of an existing capable facility for the chosen service (T = 60 minutes, or a distance proxy until the travel-time raster is in).
3. **Candidates:** (a) centroids of uncovered hexagons for new builds, (b) existing rung 2 to 3 facilities for upgrades.
4. **Solve:** greedy maximal coverage (pick the site that covers the most uncovered weighted population, repeat K times). It needs only numpy, runs in minutes on serverless, and has a known worst-case guarantee of about 63% of the optimum. If PuLP installs on serverless, we can check the greedy answer against an exact solve for one state.
5. **Output:** `vf_gold.placement_candidates` with rank, location, people newly covered, how many are in critical zones, and the nearest existing facility. A dashboard page shows the top 10 per service as stars on the map, with a "coverage gained" bar.

**Caveats.** Straight-line coverage overstates access in hilly and riverine districts. The model knows nothing about land, staff or budget; we present it as "where a new facility would help most", not as a siting decision. A p-median variant (minimise average travel time) is a stretch goal if the team wants a second lens.

## 7. Hospital contacts and notifying the hospital

Every recommendation shows the hospital's phone, and the assistant can alert the hospital in one tap, but the alert goes from the person's own phone. A silent server-side alert sounds faster, but in India it needs telecom registration we can't finish in datathon time, and most hospital numbers in our data are likely landlines that can't receive SMS or WhatsApp.

**Contact data we have.** In the raw facility data, 96.6% of rows have a phone number, 94.4% an official phone, 84.9% an email and 83.5% an official website (from the Field Explorer profile of 10,088 raw rows). None of it is verified, and records merged from up to 50 sources may carry an aggregator's or directory's number.

**Contact table.** `vf_gold.facility_contact` keeps per facility: official phone first, other numbers after, email, website, plus quality flags: a number shared by more than about 3 facilities (likely a directory or chain call centre), a number that isn't a valid Indian mobile or landline format, and whether OSM or a government list confirms it.

**How the alert works, in order of reliability**

1. **Call 108 or 112 first**, as a large button. There is no public API to dispatch a state 108 ambulance, so this stays a call.
2. **Click-to-call the hospital** (`tel:` link to the official number, with the next number as backup).
3. **One-tap pre-arrival message** from the person's phone: the assistant drafts an SMS or WhatsApp message (`sms:` or `wa.me` link) with the emergency type, patient age and sex, key symptoms, ETA and a location link, and the person presses Send. Email (`mailto:`) as a fallback when only an email exists. This needs no gateway, works on any phone, and the person's tap is their consent.
4. **Optional demo of a server-sent alert:** Twilio or the WhatsApp Business API sending to a team member's number playing the hospital. Real use would need TRAI DLT sender and template registration for SMS, Meta business verification for WhatsApp, and hospitals agreeing to receive alerts. The App's outbound access on Free Edition may also block these APIs; we'd test before promising it.

**Risks**

| Risk | Mitigation |
| --- | --- |
| Wrong or stale number wastes minutes | Show two numbers, flag shared and badly formatted ones, never make the hospital call the only path: 108 is always first |
| False or prank alerts annoy hospitals | Messages come from a real person's number, no automatic sending, and the draft states it's a pre-arrival note, not a booking |
| Health data shared without consent | Nothing leaves the phone until the person presses Send; the App stores no symptoms or numbers (India's Digital Personal Data Protection Act 2023 treats this as personal data) |
| A hospital reads the note as a guarantee of a bed | The draft and the UI say "please confirm by phone" |

**Effort:** contact table and click-to-call 2 h (fits phase 1); message drafting in the assistant 2 h (phase 4); Twilio demo 3 h, only if the team wants it.

## 8. Three tabs: policy makers, EMRs, patients

The same tables and ranking function sit behind three tabs, each with its own profile, vocabulary and layout. This needs the Databricks App: AI/BI dashboard pages can't hold a user profile or change their interface per user, so the dashboard stays as the analyst and judging view.

|  | Policy makers | EMRs (ambulance crews, dispatchers, ED staff) | Patients and families |
| --- | --- | --- | --- |
| Preloaded profile | State and districts they're responsible for; programme focus (trauma, maternal, newborn, dialysis); budget horizon | Base location or ambulance ID; vehicle type (basic or advanced life support); protocols they follow | Age, sex, location, pre-existing conditions, allergies, current medicines, insurance (PM-JAY, CGHS, private), income band, language, emergency contact |
| Main view | National map, then district zoom: critical zones, coverage gaps, where to place or upgrade the next facility, cost burden | Live: patient condition picker, top capable hospitals with route time, call and pre-alert buttons | One big button: "Emergency" (map + nearest capable hospital + call 108), then "Not urgent" (assistant + eSanjeevani) |
| Language and detail | Technical: rungs, confidence, NFHS indicators, model assumptions, sensitivity ranges, downloadable tables | Clinical shorthand: CEmONC, NICU level, trauma capability, 24x7 claims, quoted evidence | Plain words in the chosen language, no jargon, large type, cost in rupees with "covered by your card" in green |
| What the profile changes | Default region and metrics | Filters by what the crew can manage en route; route mode | Ranking weights (cost, insurance, public preference), warnings (for example a dialysis patient is steered to dialysis-capable units), the drafted pre-arrival message |

**Patient data and privacy.** A health profile is sensitive personal data under India's Digital Personal Data Protection Act 2023. The plan keeps it on the person's device: the profile lives in browser storage, is sent with a single ranking request, and isn't written to any Databricks table or log. There's a "delete my profile" button and a clear consent line before anything is saved. For the datathon we demo with synthetic personas (for example "62-year-old diabetic farmer in Barmer with a PM-JAY card"), never real patient data.

**Access caveat.** Databricks Apps are only reachable by signed-in workspace users, so real patients couldn't use this one as is. For the demo that's fine; we'd say a public version would be the same App code deployed on a public host.

## 9. Google Maps routing, the national map, and district zoom

The national map is the showpiece and is precomputed; the district view is where Google Maps routing runs live, because Google's terms don't let us store route results for all of India and the free tier is per request.

**National view (looks good, loads fast).** A dark Google basemap with a deck.gl overlay: glowing hexagons for critical zones, catchment edges as thin lines, facilities as points that brighten with rung, and a slow camera fly-in on load. It uses only our own precomputed tables (straight-line or the MAP travel-time raster), so it costs nothing per view. Google's Maps JavaScript API in India includes 70,000 free map loads a month.

**District zoom (computes on demand).** Clicking a district, or typing one, runs everything for that district only:

1. Pull facilities in the district plus a buffer around it (so border villages can choose a hospital next door), and population hexagons.
2. Recompute Voronoi cells in the browser (d3-delaunay), then call Google's Route Matrix for up to 25 hexagon centres x 25 nearest capable facilities = 625 elements, the maximum for one request.
3. Recompute, from real drive times: coverage within 30 and 60 minutes, the critical-zone flag, the cost burden, and the top 3 placement or upgrade sites (the greedy model is fast at district size).
4. Show it with a "computed just now, drive times from Google Maps" label; keep it for the session only.

**Patient and EMR routes.** For the chosen hospital, the Routes API returns the fastest route with live traffic (the Pro tier), drawn on the Google map with turn-by-turn directions and an ETA that goes into the pre-arrival message.

| Google Maps Platform, India pricing ([price list](https://developers.google.com/maps/billing-and-pricing/pricing-india)) | Free per month | After that, per 1,000 |
| --- | --- | --- |
| Route Matrix, Essentials | 70,000 elements | USD 1.50 |
| Routes / Route Matrix, Pro (live traffic) | 35,000 | USD 3.00 |
| Dynamic map loads (Maps JavaScript API) | 70,000 | USD 2.10 |

One district zoom is 625 elements, so the free tier covers about 110 district zooms a month, plus thousands of single-route lookups. That's enough for the datathon at no cost.

**Constraints.**

- **Needs a Google Cloud billing account and an API key** ([Routes billing](https://developers.google.com/maps/documentation/routes/usage-and-billing)). A teammate creates it; we restrict the key to the App's web address and cap daily usage so it can't run up a bill.
- **Calls go from the browser, not from Databricks.** Free Edition limits outbound internet from our compute, but the App's web page runs in the user's browser, which can reach Google directly.
- **Terms of use** ([Routes policies](https://developers.google.com/maps/documentation/routes/policies)): route results shown on a map must be on a Google map, and route and matrix results can't be stored or bulk-downloaded. That's why the national layer uses our own travel-time estimates and Google is used live, per district or per trip.
- **Fallback** if no billing account: the same district zoom with straight-line distance converted at a conservative road speed, clearly labelled.

## 10. Lives and money saved

We can back a money figure firmly, and we can measure how many people the tool would send to the right hospital first. A lives-saved figure can only be a range, and its honest low end is close to zero, because the research on faster arrival and survival is mixed. We'd present it that way rather than pick one big number a judge can knock down.

**What the evidence says**

| Fact | Value | Source |
| --- | --- | --- |
| Road accident deaths in India, 2023 | 172,890 deaths, 462,825 injured, 480,583 accidents | MoRTH, Road Accidents in India 2023 ([PTI report](https://www.theweek.in/wire-updates/business/2025/08/28/del82-biz-road-accidents-report.html)) |
| Maternal mortality ratio, 2019-21 | 93 per 100,000 live births (130 in 2014-16) | SRS Special Bulletin ([Tribune](https://www.tribuneindia.com/news/india/maternal-mortality-ratio-declines-by-37-points)) |
| Road accident patients reaching a hospital within the golden hour | 20.6% (65 of 315), one rural tertiary centre in Sangli, Maharashtra | [Gaikwad et al., Cureus 2025](https://assets.cureus.com/uploads/original_article/pdf/336199/20250209-885723-2s0rma.pdf) |
| Barriers to reaching care in that study | Vehicle 33%, money 30.5%, not knowing a nearby facility 14.6%, language 13.3% | Same study |
| Mortality by arrival time in that study | 46.2% within the hour vs 39.8% after (not significant, sicker patients arrive faster) | Same study |
| Prehospital time and trauma death, worldwide | Mixed: 96 studies show negative, neutral and positive associations; only 8 from low and middle-income countries | [Bedard et al., Int J Emerg Med 2020](https://pmc.ncbi.nlm.nih.gov/articles/PMC7724615/) |
| Cost per hospitalisation, public vs private | Rs 4,452 vs Rs 31,845 (2017-18), a gap of Rs 27,393 | [NSS 75th round](https://pib.gov.in/PressReleasePage.aspx?PRID=1593246) |

The two barriers our tool targets directly are "didn't know a nearby facility" and "money". That's the claim we can defend: we fix information and cost, not ambulances.

**The model**

```latex
\text{Lives saved} = \sum_{c} D_c \times a_c \times e_c \times u
```

```latex
\text{Money saved} = \sum_{c} H_c \times s_c \times (C_{\text{private}} - C_{\text{covered}}) \times u
```

| Symbol | Meaning | Where it comes from | Kind |
| --- | --- | --- | --- |
| D | Deaths per year for condition c (road injury, maternal, newborn, kidney failure) | MoRTH 2023; SRS MMR x births; SRS neonatal mortality | Sourced |
| a | Share of those whose nearest facility can't treat the condition, so they'd go somewhere wrong first | Our catchments: population in cells where the nearest facility lacks the service, vs the nearest capable one | Measured from our data |
| e | Share of those deaths avoided by going to a capable facility first | Literature; low 0, central and high from published transfer and time-to-care studies we cite one by one | Assumed range |
| u | Share of people who use the tool | 100% is the "everyone used it" ceiling you asked for; we also show 1% and 10% | Scenario |
| H | Emergency hospitalisations per year for c | NSS 75th round hospitalisation tables | Sourced (to pull) |
| s | Share who'd be steered from an uncovered private hospital to a public or empanelled one within the same travel time | Our catchments + empanelment badges | Measured |
| C | Cost private vs covered | NSS 75th round, inflated to 2026 with the health CPI; PM-JAY package rates for the specific condition | Sourced |

**How we keep it honest**

- Every number on screen links to its row in this table; the App shows the formula, not just the result.
- Lives appear as a range (low / central / high) with the low case at e = 0, plus a sensitivity chart showing which input moves the answer most.
- Measured inputs (a, s) come from our own tables and can be recomputed per district; the "everyone used it" figure is labelled a ceiling, not a forecast.
- We'd also report outcomes that don't need e at all: people who'd reach a capable facility first, minutes saved per case versus going to the nearest facility of any kind, and transfers avoided. These are the most defensible numbers and probably the headline.
- A teammate who didn't build the model checks every source link before the presentation.

**Effort:** 5 h for the model, parameter table and sensitivity chart; 2 h more to pull the NSS hospitalisation tables and neonatal figures.

## 11. Public transit for minor injuries

Public transit is offered only when the case is clearly minor, the person can travel safely, and a free ambulance isn't the better choice. Cost should rarely be the reason, because 108 is free in most states and 102 is free for pregnant women and sick infants under the national maternal and child programme. So the tool always tells people that first.

**The order on screen never changes:** 108 / 102 (free) first, then a family vehicle, auto or taxi, and public transit last, and only when every safety check below passes.

| Gate | Transit is offered only if | Why |
| --- | --- | --- |
| Severity | The case is green in section 3's triage, never red or orange | Anything time-critical goes by ambulance |
| Hard exclusions (any one blocks transit) | No head injury, chest pain, breathing difficulty, fainting or confusion, seizure, bleeding that hasn't stopped, suspected spine, hip or leg fracture, burns larger than the palm or on the face, hands or genitals, pregnancy problems, a child under 1, poisoning or a bite from a snake or animal | These can get worse suddenly, or the person can't move safely |
| Risk to others | No fever with cough or rash, no vomiting or diarrhoea, nothing that could spread to other passengers; a mask advised for any cough | Keeps other passengers safe |
| Can travel | Can walk unaided, is alert, and ideally has a companion | A person who can't board or change buses safely shouldn't try |
| The trip itself | Google route by transit of 60 minutes or less, at most one change, destination open now (24x7 emergency, or OPD hours for non-urgent care) | A long or complicated trip is itself a risk |
| Fallback | If no transit route is found, or any answer is "not sure", transit isn't shown | Unsure means ambulance |

**How it's built.** The rules live in `vf_gold.transit_rule` (editable, next to `emergency_rule`). The patient tab asks the gate questions as yes / no buttons. If all pass, the Routes API is called with transit as the travel mode, and the route, its fare if Google has it, and the time are shown under the ambulance option, with "if anything gets worse on the way, get off and call 108". The EMR tab never shows transit.

**Risks.** These exclusions are our reading of standard first-aid guidance, not a clinician's sign-off; we'll say that in the demo and in the README. Google's transit data covers India's large cities far better than small towns, so outside cities the option will often be absent, which is the safe failure. **Effort:** 3 h, built inside the patient tab.

## 12. Feedback loop: reports from the ground

Like Waze, people who just went to a hospital tell the next person what they found, and the ranking updates within minutes. Each report nudges our confidence in a hospital's capability up or down instead of flipping it, so one angry or mistaken report can't wipe a hospital off the map, but a pattern quickly can.

**What can be reported** (taps, not free text, so reports carry no health details)

| Report | Example | How long it counts |
| --- | --- | --- |
| Can't treat this at all | "No NICU here", "no trauma surgeon" | Permanent, until confirmed otherwise |
| Temporarily unavailable | "No ICU bed", "CT scanner down", "no doctor tonight" | Fades over hours: half-life of 6 hours for beds, 3 days for equipment |
| Confirmed it works | "Treated here, they had a NICU" | Permanent |
| Wrong phone or closed | Number dead, building shut | Until a newer confirmation |
| Scheme not honoured | "Refused my PM-JAY card" | Permanent; counts against the empanelment badge |

**Who reports, and how much each counts.** Ambulance crews and ED staff count most (weight 1.0) because they see capability directly; signed-in patients count 0.5; one report per person per hospital per day. Patients get a prompt 2 hours after they chose a hospital: "Were they able to treat you?"

**How it updates the ranking.** Each hospital-service pair holds a belief, starting from what we know today: high-confidence claims start strong, low-confidence ones start near 50/50. Each report adds its weight to the "works" or "doesn't work" side, and temporary reports fade over time. The belief replaces the confidence score in the choice model (section 3). If it falls below 30% the hospital drops out of red-case results and shows "reported unable to treat (4 reports, latest 2 h ago)". Above 30% it stays, with the latest report visible.

**Guarding against abuse.** A hospital reporting its rivals, or a wave of fake reports, is the main risk. Mitigations: signed-in reports only, one per person per day, a permanent "can't treat" needs at least three independent reporters or one ambulance crew plus one other, unusual bursts are held for review, and a hospital never disappears from the map, it only ranks lower with the reason shown. Labels say "reported unavailable", never "lying".

**How it's built.** Reports go into `vf_gold.facility_feedback` (or the free Lakebase Postgres database if writes from the App need to be faster); a view computes each belief on the fly, and `rank_facilities()` reads it. The EMR and patient tabs get a report button on every hospital card. For the video, we'll show an ambulance crew reporting "CT scanner down" and the next patient's top pick changing live. **Effort:** 2.5 h of Claude time in the App thread.

## 13. Twenty more ideas, rated

Every idea is in. Ten are core for the video (about 10 hours of Claude time, built in a third parallel thread), seven more are stretch, and the parts that need data or sign-ups we can't get by noon (live blood stock, SMS and offline, voice, ABHA, a heatwave feed) become the "what's next" slide. Video value means how much the idea adds to a 3 to 5 minute demo, not how much it matters in real life.

**Core: built before the video**

| Idea | What it does | Data source | Claude h | Video value |
| --- | --- | --- | --- | --- |
| Condition time windows, incl. snakebite | Stroke needs a CT that can support clot-busting within about 4.5 h, heart attack a cath lab within about 90 min, snakebite anti-venom; the filter checks for each | Claims text (CT, cath lab, anti-venom, ASV); snakebite kills about 58,000 Indians a year (Million Death Study, Suraweera et al., eLife 2020) | 1.5 | High |
| Synthetic evaluation | Run about 1,000 synthetic emergencies; report the share that reach a capable hospital first vs going to the nearest one, and minutes saved | Our tables + population grid | 2 | High: the measured headline |
| Confidence-aware catchments | Catchments drawn only from hospitals we're confident in; shows places that only think they're covered | Our confidence scores | 0.5 | High |
| "Reliability index" for regulators | Ranks districts by how unreliable claims are (quality flags, OSM disagreement, feedback reports) to target inspections | Silver flags, OSM check, section 12 | 1.5 | High |
| Backup hospital on the route | Always shows a second capable hospital, to avoid being bounced between hospitals | Ranking output | 1 | Medium |
| Night and Sunday availability | Marks claims as 24x7 or daytime-only; at night, daytime-only hospitals rank lower | Claims text ("24x7", "24 hours", "OPD timings") | 1 | Medium |
| Women and children | 102 shown for pregnancy and infants; paediatric-capable hospitals for children; female doctor flag | Claims text, service levels | 1 | Medium |
| Equity lens | Critical zones weight poverty, scheduled caste and tribe share, anaemia | NFHS-5 (already loaded) | 0.5 | Medium |
| Blood bank signal | Blood bank claim required for obstetric and trauma red cases | Claims text | 0.5 | Medium |
| Telehealth first for non-urgent | eSanjeevani before a trip in green cases (already in section 4, made explicit in the flow) | eSanjeevani link | 0.5 | Low |

**Stretch: only if ahead at 3 AM**

| Idea | What it does | Data source | Claude h | Video value |
| --- | --- | --- | --- | --- |
| Mass-casualty mode + capacity spread | Enter a crash or stampede and victim count; patients split across hospitals by beds and level | Bed counts in silver (incomplete), rungs | 3.5 | High |
| Upgrade counterfactual | "Give this CHC a blood bank: how many more people get golden-hour access?" | Placement model (item 10) | 2 | High |
| Pre-arrival handoff card | A QR code with age, allergies, conditions, insurance, made on the phone | Patient profile (on device) | 1.5 | Medium |
| Hospital-side confirm form | A hospital confirms or corrects its capabilities, earns "confirmed by hospital" | Feedback table (section 12) | 1.5 | Medium |
| Local language | Hindi and one more language for the patient tab's fixed text, translated once and checked | Translation of our own strings | 2 | Medium |
| Monsoon toggle | Longer travel times in flood-prone districts | Flood hazard district list, uploaded by the team | 2 | Medium |
| Ride-share and community transport | Ola / Uber links and known bike ambulance services as middle options | Deep links; bike ambulance list only for a few states | 1 | Low |

**After the datathon** (needs sign-ups, partners or data we can't get by noon)

| Idea | Why not now |
| --- | --- |
| Live blood stock from eRaktKosh | No bulk API; would mean scraping a government portal |
| SMS keyword ("DIALYSIS 560001") and offline mode | SMS needs a registered sender (TRAI DLT); offline needs an installable app; we can show a mock-up |
| Voice-first interface | Browser speech works for Hindi but needs careful testing with real users |
| ABHA health ID handoff | Needs registration with the Ayushman Bharat Digital Mission |
| Heatwave alerts | Needs IMD warnings feed; we can show a manual toggle mock-up |

## 14. A full justification for every recommendation

Every hospital card for a patient or an EMR carries its full reasoning, generated from the same data the ranking used, so the explanation can never say something the model didn't do. The assistant may rephrase it, but only from this record, and it must keep the quotes and links.

**What every recommendation explains**

| Part | What it shows | Where it comes from |
| --- | --- | --- |
| Can they treat it? | The required service and level, and the hospital's own words that support it, quoted exactly | `facility_claims.raw_text`, with the source record and website link |
| How sure are we? | High / medium / low, the reason in one line, and what lowers it (data quality flags, merged sources, no outside confirmation) | `facility_level` confidence and reason, quality score, OSM and government checks |
| What others report | Recent feedback, for example "ambulance crew: CT working, 3 h ago" | Section 12 |
| How long to get there | Minutes, the route, and whether it's live Google traffic or an estimate | Routes API or straight-line estimate |
| What it may cost | Expected range, covered or not, and the badge's status (verified or self-claimed) | Sections 2 and 5 |
| Why this rank | Each factor's score and weight, for example time 0.82 x 70 + confidence 0.60 x 30 | `rank_facilities()` output |
| Safety rules applied | For example "red case: cost ignored", "over 60 min: stabilising hospital added", "transit not offered: head injury" | Rule tables |
| Why not the others | The next options and why each ranked lower, plus nearby hospitals that were excluded and the reason ("no trauma signal", "daytime only", "reported CT down") | Ranking output, including filtered-out rows |
| What could change this | Plain statement of the biggest uncertainty, for example "if the NICU claim is wrong, the next option is 18 min further" | Difference between the top two |

**Depth per audience**

- **Patients:** one plain sentence on the card ("Closest hospital that can treat serious injuries; it says it has a 24-hour trauma unit"), then a "Why this hospital?" button that opens the rest in plain words, with the quotes kept but jargon explained. Nothing here slows the "Call 108" button.
- **EMRs:** a compact table on the card itself: service and level, confidence, ETA, latest report, rules applied, and the two alternatives with their scores, in clinical shorthand. Quotes and links one tap away.
- **Policy makers:** the same record aggregated per district (why an area is a critical zone, which inputs drove it).

**How it's built.** `rank_facilities()` returns an explanation record alongside each result, including the filtered-out hospitals and the rules that fired; the App renders it with fixed templates in each tab's language. No explanation text is generated freely by a language model. **Effort:** 2 h in the App thread, covered by the schedule's buffer.

## 15. Outside data: checks on our data, never a replacement

The Virtue Foundation facility data stays the primary source. Every outside source only confirms, contradicts or adds context to our own facility rows, through one column, `confirmed_by` (a list such as `["OSM", "PM-JAY", "NABH"]`), plus a matching `contradicted_by`. A hospital that exists only in an outside list is never added as a recommendation.

| Source | What it checks or adds | Feeds | Who gets it | Core or stretch |
| --- | --- | --- | --- | --- |
| PM-JAY empanelled hospitals (official finder, by state) | Verified empanelment and the specialties each hospital is approved for | `confirmed_by`, empanelment badge | Team, browser download for 2 to 3 demo states | Core |
| CGHS empanelled hospitals (AIKosh) | Verified CGHS empanelment | `confirmed_by`, badge | Team (AIKosh sign-in) | Core |
| NABH accredited hospitals list | Quality accreditation; a check that a hospital is as big as it claims | `confirmed_by`, confidence | Team, browser download | Core |
| India district boundaries (DataMeet or geoBoundaries) | District polygons for the map and zoom | Map | Team, download | Core |
| WorldPop or GHSL population grid (about 100 m) | People covered, critical zones, evaluation | Coverage, impact | Team, download | Core |
| eRaktKosh blood bank directory | Confirms blood bank claims for obstetric and trauma cases | `confirmed_by`, blood bank signal | Team, browser export if available | Stretch |
| National Health Facility Registry (ABDM HFR) | Government facility IDs and type, for matching | `confirmed_by`, matching | Team, if a public export is available | Stretch |
| Census 2011 district data, SECC deprivation | Equity lens beside NFHS-5 | Critical zones | Team, download | Stretch |
| Malaria Atlas Project travel-time raster | Access score for every facility without using Google's free allowance | Map, placement | Team, download | Stretch |
| State anti-snake-venom stocking lists | Confirms snakebite capability where published | `confirmed_by` | Team, where found | Stretch |
| Cath lab / stroke-ready lists (Cardiological Society of India, registries) | Confirms heart attack and stroke claims | `confirmed_by` | Team, where found | Stretch |
| IMD flood and heat data | Only if the monsoon or heatwave toggles get built | Toggles | Team | After the datathon |

**Skipped on purpose:** scraping Google reviews or live bed dashboards; they're unreliable or against terms, and would pull focus from the given dataset.

**How it's built.** Each list goes into its own folder in `vf_bronze.external`; notebook 06's matcher (name, pincode, distance) writes the match; a view gathers `confirmed_by` and `contradicted_by` per facility, which the confidence score and the explanation (section 14) both read. **Effort:** about 0.5 h per extra source once it's uploaded.

## Architecture on Databricks Free Edition

Everything fits inside Free Edition's limits if notebooks do the heavy work once and the surfaces only read small gold tables.

Architecture (diagram in the live doc):

| Inputs | Notebooks | Gold tables | Surfaces |
| --- | --- | --- | --- |
| Our tables (silver + gold) | Catchments (Voronoi, zones) | Catchment cells (one set per service) | AI/BI dashboard (map, cost, sites) |
| Map layers (boundaries, people) | Cost + badges (bands, list match) | Critical zones (district flags) | Databricks App (3 tabs, map, chat) |
| Official lists (PM-JAY, CGHS) | Ranking (rules + function) | Cost and badges (per facility) | Genie space (fallback Q&A) |
| Cost sources (NSS, HBP, CGHS) | Placement (greedy coverage) | Placement sites (top 10 per service) | |

New notebooks 07 to 09 sit next to 01 to 06 in the "CMU Datathon 2026" folder and log to the README change log, as we do now.

| Free Edition limit ([docs](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations)) | What it means for us |
| --- | --- |
| Serverless only, limited size; daily quota shuts compute for the day if exceeded | Compute Voronoi, H3 and placement once, write tables, never recompute on page load |
| One SQL warehouse, 2X-Small | Keep map tables small (simplify polygons, about 10k cells per service) so dashboard queries stay fast |
| Up to 3 Apps; each stops 24 h after start | One App is enough; restart it within 24 h of judging |
| Model serving: limited active endpoints, no GPU or provisioned throughput, some models missing | Use a pay-per-token Foundation Model API if listed; Genie as fallback |
| Outbound internet limited to trusted domains | Team downloads boundaries, population and lists in a browser and uploads them to the volume |
| One AI Search (vector) endpoint | Not needed: claims lookup is SQL text search |

AI/BI maps ([docs](https://docs.databricks.com/aws/en/dashboards/visualizations/maps)) support point maps and choropleths from Polygon / MultiPolygon geometry columns, which covers Voronoi cells and district zones. They don't support grouping by geometry, so each polygon is one row.

## Build order, effort and risks

This is the full version, for after the datathon; the re-scoped plan near the top is what we build before the video. About 55 to 70 hours of build in total, so with three or four people working in parallel this is one to two days. The first two phases (about 15 hours) already give a demo-able emergency map with ranking, cost and contacts, so each later phase is optional if time runs out.

| Phase | Delivers | Effort | Depends on |
| --- | --- | --- | --- |
| 1. Ranking core | `emergency_rule`, `rank_facilities()`, cost bands (NSS), self-claimed badges, contact table | 7 h | Nothing new |
| 2. Map | Voronoi per service, critical zones, "Emergency map" dashboard page | 8 h | India outline, district polygons, population grid uploaded |
| 3. Empanelment verified | PM-JAY / CGHS list match, verified badges | 4 h | Lists downloaded and uploaded |
| 4. App with three tabs | Policy maker, EMR and patient tabs, preloaded synthetic profiles, national map | 8 h | Phases 1 and 2 |
| 5. Google routing and district zoom | Live routes, Route Matrix per district, on-demand recompute | 6 h | Google billing account and API key |
| 6. Impact model | Lives and money ranges, parameter table, sensitivity chart | 7 h | Phase 2 tables, NSS tables |
| 7. Assistant | Genie space first (2 h), then chat in the App (6 to 8 h), pre-arrival message drafting | 8 to 10 h | Confirm a chat model is available in Serving |
| 8. Placement | Greedy maximal coverage, top 10 sites per service | 5 h | Phase 2 tables |
| 9. Polish | Travel-time raster, PM-JAY package rates per emergency, visual polish of the national map | 6 to 10 h | Time left |

| Risk | Likelihood | Fallback |
| --- | --- | --- |
| Free Edition daily compute quota hit while building H3 or Voronoi | Medium | Run per state, cache results, stay on small polygons |
| No chat model on Free Edition serving | Medium | Genie space as the assistant |
| PM-JAY list not obtainable in bulk | High | Verified badges for 2 to 3 demo states only; everything else "self-claimed" |
| App stopped at judging time (24 h limit) | Medium | Restart checklist item; dashboard works without it |
| Straight-line distance misleads in hilly regions | Certain | Label it; add the travel-time raster in phase 6 |
| A user reads the tool as medical advice | Low but serious | "Call 108" and "not medical advice" on every screen; red cases skip chat |

## What we need from the team

Approve or edit this plan, then answer these so the build can start.

- [ ] **Deadline:** how many hours are left, so we know whether to stop after phase 2, 4 or 6.
- [ ] **App or dashboard only:** the three tabs and Google routing need the Databricks App. Confirm you're happy with an App that needs a restart within 24 hours of judging, with the dashboard as the backup.
- [ ] **Uploads (browser, about 30 min):** India outline and district polygons, a population grid (Kontur H3 or WorldPop), the CGHS list from AIKosh, and PM-JAY hospital lists for 2 to 3 demo states. Also a Google Cloud billing account with a Maps API key; the free tier covers the datathon and we'll cap daily usage. We'll send exact links and file names once approved.
- [ ] **Check Serving:** open Serving in the workspace and tell us which pay-per-token chat models are listed.
- [ ] **Emergency types for the demo:** we suggest trauma, obstetric, newborn and dialysis (the four service levels we already score).
- [ ] **Weights:** agree the red / orange / green weights in section 3, or let us tune them.
- [ ] **Access token:** a short-lived Databricks token pasted in the thread when we start writing to the workspace, as before.

## Sources

- [Databricks Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations)
- [Databricks AI/BI map visualizations](https://docs.databricks.com/aws/en/dashboards/visualizations/maps)
- [NSS 75th round, Household social consumption: Health (PIB, Nov 2019)](https://pib.gov.in/PressReleasePage.aspx?PRID=1593246)
- [ThePrint on CAMS 2022-23 out-of-pocket hospitalisation costs](https://theprint.in/health/92-of-rural-households-hospitalisation-costs-are-out-of-pocket-77-for-urban-families-govt-data/2306247)
- [PM-JAY Health Benefit Package 2.2 manual (NHA)](https://nha.gov.in/img/resources/HBP-2.2-manual.pdf)
- [EMS response time research](https://claude.ai/artifact/9X3XAWgNqCTQoUJgHF3ZFE) and [care levels guide](https://claude.ai/artifact/42uCQscXbpaHLYQGY9r7PP)

* [Google Maps Platform pricing, India](https://developers.google.com/maps/billing-and-pricing/pricing-india), [Routes API usage and billing](https://developers.google.com/maps/documentation/routes/usage-and-billing), [Routes API policies](https://developers.google.com/maps/documentation/routes/policies)
* [MoRTH Road Accidents in India 2023, via PTI](https://www.theweek.in/wire-updates/business/2025/08/28/del82-biz-road-accidents-report.html)
* [SRS maternal mortality 2019-21, via Tribune](https://www.tribuneindia.com/news/india/maternal-mortality-ratio-declines-by-37-points)
* [Gaikwad et al., Golden hour in road traffic accident victims, Cureus 2025](https://assets.cureus.com/uploads/original_article/pdf/336199/20250209-885723-2s0rma.pdf)
* [Bedard et al., Prehospital time and trauma outcomes, scoping review, 2020](https://pmc.ncbi.nlm.nih.gov/articles/PMC7724615/)
