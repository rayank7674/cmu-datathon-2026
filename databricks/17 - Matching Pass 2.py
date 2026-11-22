# Databricks notebook source
# MAGIC %md
# MAGIC # 17 - Matching, second pass (distance + name words)
# MAGIC Task D's matcher (a teammate's notebook) compares whole name strings. It rejects true matches whose outside name carries extra words, for example "Jaslok Hospital And Research Centre, Blood Centre" against "Jaslok Hospital and Research Center" 20 m away. This pass re-reads Task D's **rejected** Wikidata and eRaktKosh candidates (all within 2 km) and compares the distinctive name words instead.
# MAGIC
# MAGIC Task D's tables are not changed. Results go to `vf_silver.ext_facility_matches_pass2` (same columns), and the view `vf_silver.ext_facility_matches_all` is Task D's rows with these decisions laid over them. Notebooks 09 and 13 read the view.
# MAGIC
# MAGIC **Rule**
# MAGIC - Noise words are dropped from both names: hospital, blood, centre, bank, research, ltd, trust, speciality words and similar, plus the facility's own city, district and state.
# MAGIC - `accepted_medium` (never high): the remaining words are the same on both sides (a spelling difference of one or two letters is allowed), and the two points are within 0.5 km, or within 0.3 km when only one distinctive word is left.
# MAGIC - `ambiguous` (review only): within 1 km and at least 60% of the words shared, but not the same.
# MAGIC - One outside record per facility and one facility per outside record; ties become ambiguous. Facilities that Task D already matched for that source are skipped.
# MAGIC - OpenStreetMap is not in this pass: it was matched in notebook 06 and its export has no bed counts (the capacity column is empty for all 139,664 rows).

# COMMAND ----------

import re, json, difflib
import pandas as pd

NOISE = {"hospital", "hospitals", "blood", "centre", "center", "bank", "bsu", "ltd", "limited", "pvt", "private", "trust", "society",
         "the", "and", "of", "for", "dept", "department", "transfusion", "medicine", "research", "multi", "super", "speciality",
         "specialty", "multispeciality", "multispecialty", "superspeciality", "superspecialty", "india", "unit", "a", "ms", "m", "s",
         "govt", "government", "new", "sri", "shri", "sree", "dr"}


def toks(s, place=()):
    s = re.sub(r"[^a-z0-9 ]", " ", str(s).lower().replace("&", " and ").replace("'", "").replace("’", ""))
    out = [t for t in s.split() if t not in NOISE and t not in place]
    return out


def place_words(*vals):
    w = set()
    for v in vals:
        if v is not None and str(v) != "None":
            w |= {t for t in re.sub(r"[^a-z ]", " ", str(v).lower()).split() if len(t) > 2}
    return w


def same(a, b):
    return a == b or (min(len(a), len(b)) >= 5 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.84)


def compare(a, b):
    """Returns (all words pair up, share of words that pair up)."""
    if not a or not b:
        return False, 0.0
    b_left = list(b); hit = 0
    for t in a:
        for u in b_left:
            if same(t, u):
                b_left.remove(u); hit += 1
                break
    share = hit / max(len(a), len(b))
    return (hit == len(a) and not b_left), share


cand = spark.sql("""
SELECT m.*, f.name AS vf_name, f.city, f.district, f.state
FROM workspace.vf_silver.ext_facility_matches m
JOIN workspace.vf_silver.facilities f USING (facility_id)
WHERE m.source IN ('wikidata', 'eraktkosh') AND m.match_status = 'rejected' AND m.distance_km <= 1.0
  AND NOT EXISTS (SELECT 1 FROM workspace.vf_silver.ext_facility_matches a
                  WHERE a.facility_id = m.facility_id AND a.source = m.source AND a.match_status IN ('accepted_high', 'accepted_medium', 'ambiguous'))
  AND NOT EXISTS (SELECT 1 FROM workspace.vf_silver.ext_facility_matches a
                  WHERE a.source_record_id = m.source_record_id AND a.source = m.source AND a.match_status IN ('accepted_high', 'accepted_medium'))
""").toPandas()

rows = []
for _, r in cand.iterrows():
    place = place_words(r["city"], r["district"], r["state"])
    a, b = toks(r["vf_name"], place), toks(r["ext_name"], place)
    if not a or not b:        # a name that is only a place plus "hospital": compare without dropping the place
        a, b = toks(r["vf_name"]), toks(r["ext_name"])
    equal, share = compare(a, b)
    d = float(r["distance_km"])
    if equal and (d <= 0.3 or (len(a) >= 2 and d <= 0.5)):
        status, reason, conf = "accepted_medium", "second pass: same distinctive name words within %.2f km; identity only, never validates a service" % d, "medium"
    elif share >= 0.6 and d <= 1.0:
        status, reason, conf = "ambiguous", "second pass: similar name within 1 km, but the distinctive words are not the same", "low"
    else:
        continue
    rows.append(dict(facility_id=r["facility_id"], source=r["source"], source_record_id=r["source_record_id"], ext_name=r["ext_name"],
                     match_score=round(0.6 * share + 0.4 * max(0.0, 1 - d), 3), name_sim=float(r["name_sim"]), distance_km=d,
                     match_method="pass2_name_words_distance", matched_fields=["name_words", "distance"], match_status=status,
                     match_reason=reason, source_type=r["source_type"], source_ref=r["source_ref"], confidence=conf))

m = pd.DataFrame(rows)
acc = m[m["match_status"] == "accepted_medium"]
dup = acc.duplicated(["facility_id", "source"], keep=False) | acc.duplicated(["source_record_id", "source"], keep=False)
m.loc[acc[dup].index, ["match_status", "match_reason", "confidence"]] = ["ambiguous", "second pass: more than one equally good candidate", "low"]

schema = ("facility_id string, source string, source_record_id string, ext_name string, match_score double, name_sim double, "
          "distance_km double, match_method string, matched_fields array<string>, match_status string, match_reason string, "
          "source_type string, source_ref string, confidence string")
spark.createDataFrame(m.astype(object).where(m.notna(), None), schema=schema) \
    .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("workspace.vf_silver.ext_facility_matches_pass2")

spark.sql("""
CREATE OR REPLACE VIEW workspace.vf_silver.ext_facility_matches_all AS
SELECT t.* FROM workspace.vf_silver.ext_facility_matches t
LEFT ANTI JOIN workspace.vf_silver.ext_facility_matches_pass2 p
  ON p.facility_id = t.facility_id AND p.source = t.source AND p.source_record_id = t.source_record_id
UNION ALL SELECT * FROM workspace.vf_silver.ext_facility_matches_pass2
""")

# COMMAND ----------

out = {
    "by_status": [r.asDict() for r in spark.sql("SELECT source, match_status, count(*) n FROM workspace.vf_silver.ext_facility_matches_pass2 GROUP BY 1, 2 ORDER BY 1, 2").collect()],
    "rows_all_vs_taskd": [r.asDict() for r in spark.sql("SELECT (SELECT count(*) FROM workspace.vf_silver.ext_facility_matches_all) all_rows, (SELECT count(*) FROM workspace.vf_silver.ext_facility_matches) taskd_rows").collect()],
    "accepted_sample": [r.asDict() for r in spark.sql("SELECT p.source, f.name vf_name, p.ext_name, round(p.distance_km, 2) km FROM workspace.vf_silver.ext_facility_matches_pass2 p JOIN workspace.vf_silver.facilities f USING (facility_id) WHERE p.match_status = 'accepted_medium' ORDER BY p.source DESC, f.name").collect()],
    "ambiguous_sample": [r.asDict() for r in spark.sql("SELECT p.source, f.name vf_name, p.ext_name, round(p.distance_km, 2) km FROM workspace.vf_silver.ext_facility_matches_pass2 p JOIN workspace.vf_silver.facilities f USING (facility_id) WHERE p.match_status = 'ambiguous' ORDER BY rand(3) LIMIT 40").collect()],
}
dbutils.notebook.exit(json.dumps(out))
