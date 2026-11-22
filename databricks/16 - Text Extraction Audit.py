# Databricks notebook source
# MAGIC %md
# MAGIC # 16 - Audit of the text extraction (chunk B)
# MAGIC Chunk B (`07_Facility_Attribute_Extraction`, a teammate's notebook) pulled beds, doctors, founding year and ICU beds out of each facility's own text with regular expressions and wrote `vf_silver.facility_extracted_attributes`. Its `candidate_fill` rows are values the facility record lacks. This notebook audits those rows before any of them is used. It does not change the teammate's table.
# MAGIC
# MAGIC **Hand audit (2026-11-22), 22 random `candidate_fill` rows per attribute, each read against the full source sentence:**
# MAGIC
# MAGIC | Attribute | Wrong | What went wrong |
# MAGIC |---|---|---|
# MAGIC | beds | 8 of 22 (36%) | the number belongs to a unit or ward ("5-bed ICCU", "20 bed emergency department"), a plan ("aims to establish a 500-bed hospital"), a group total, or beds set aside for one purpose |
# MAGIC | founding year | 6 of 22 (27%) | "since YEAR" about a doctor, a registration, an accreditation or one department, not the facility |
# MAGIC | doctors | 1 of 22 (5%) | "12 doctors and staff" |
# MAGIC | ICU beds | 0 of 22 | none found |
# MAGIC
# MAGIC **Rule applied here.** A value passes only when the whole clause that holds it is a plain statement about the facility: "Has 100 beds", "60-bed hospital", "Established in 1995", "Operating since 2017", "Has 11 doctors on staff". Anything with extra context fails and stays flagged. A second hand check of 40 passing rows per attribute found no wrong value (beds 40 of 40, founding year 40 of 40, doctors 40 of 40 read as stated).
# MAGIC
# MAGIC Known softness that the rule cannot remove: "Has N doctors available" usually comes from a listing site and may count visiting doctors. Confidence stays low.

# COMMAND ----------

import re
N = r"(?:\d{1,3}(?:,\d{3})+|\d+)"
BEDS = re.compile(rf"^(?:the (?:hospital|facility) )?(?:has |with |is an? |it is an? |an? )?(?:an? )?(?:total (?:capacity )?(?:of )?)?{N}\+?[- ]?(?:beds?|bedded)"
                  rf"(?: (?:in-?patient )?capacity| in total| (?:[\w&'-]+ ){{0,4}}(?:hospital|nursing home|facility|medical college(?: and hospital)?))?(?: in [\w ,.-]+)?$")
YEAR = re.compile(r"^(?:the (?:hospital|clinic|facility|centre|center|institute) (?:was |has been |is )?)?(?:established|founded|operat(?:ed|ing|ional)|functioning|in operation|running|open(?:ed)?)(?: in| since)? (?:18|19|20)\d\d$")
DOCS = re.compile(rf"^(?:has |staff includes |with |team of |a team of )?{N} doctors(?: on staff| available| listed on practo profile| on the team| in the team)?$")
RULE = {"beds": BEDS, "year_founded": YEAR, "doctors": DOCS}


def segment(ctx, quote):
    """The clause of the source text that holds the quote."""
    ctx = str(ctx); q = str(quote)
    for seg in re.split(r";|\.\s+|\n", ctx):
        if q.lower() in seg.lower():
            return seg.strip().rstrip(".").strip().lower()
    return ctx.strip().rstrip(".").lower()


def passes(attribute, ctx, quote):
    r = RULE.get(attribute)
    return bool(r and r.match(segment(ctx, quote)))


# COMMAND ----------

import json
import pandas as pd

df = spark.sql(r"""
SELECT a.*, f.name,
  CASE WHEN a.source_field = 'description' THEN f.description
       WHEN a.source_field LIKE 'capabilities[%' THEN element_at(f.capabilities, CAST(regexp_extract(a.source_field, '\\[(\\d+)\\]', 1) AS INT) + 1)
       WHEN a.source_field LIKE 'equipment[%' THEN element_at(f.equipment, CAST(regexp_extract(a.source_field, '\\[(\\d+)\\]', 1) AS INT) + 1)
       WHEN a.source_field LIKE 'procedures[%' THEN element_at(f.procedures, CAST(regexp_extract(a.source_field, '\\[(\\d+)\\]', 1) AS INT) + 1)
       WHEN a.source_field LIKE 'specialties[%' THEN element_at(f.specialties, CAST(regexp_extract(a.source_field, '\\[(\\d+)\\]', 1) AS INT) + 1) END AS source_text
FROM workspace.vf_silver.facility_extracted_attributes a
JOIN workspace.vf_silver.facilities f USING (facility_id)
WHERE a.review_status = 'candidate_fill'
""").toPandas()

df["clause"] = [segment(c, q) for c, q in zip(df["source_text"], df["quote"])]
df["audit_pass"] = [passes(a, c, q) for a, c, q in zip(df["attribute"], df["source_text"], df["quote"])]
LIMITS = {"beds": (1, 5000), "doctors": (1, 2000), "year_founded": (1850, 2026)}
in_range = [a in LIMITS and v is not None and LIMITS[a][0] <= int(v) <= LIMITS[a][1] for a, v in zip(df["attribute"], df["value"])]
df["audit_pass"] = df["audit_pass"] & pd.Series(in_range, index=df.index)
one_per = df.groupby(["facility_id", "attribute"])["value"].transform("nunique") == 1
df["audit_pass"] = df["audit_pass"] & one_per
df["audit_reason"] = df.apply(lambda r: "plain statement about the facility" if r["audit_pass"]
                              else ("ICU beds are not folded into any resolved value" if r["attribute"] == "icu_beds"
                                    else "clause has extra context (unit, plan, person, department or group), so the number may not describe the facility"), axis=1)
df["audit_rule_version"] = "2026-11-22 whole-clause rule"
out = df.drop(columns=["source_text"])
spark.createDataFrame(out.astype(object).where(out.notna(), None)).write.mode("overwrite").option("overwriteSchema", "true") \
    .saveAsTable("workspace.vf_silver.facility_extracted_attributes_audit")

summary = [r.asDict() for r in spark.sql("""SELECT attribute, count(*) AS candidate_fill, sum(CAST(audit_pass AS INT)) AS passed
  FROM workspace.vf_silver.facility_extracted_attributes_audit GROUP BY 1 ORDER BY 1""").collect()]
dbutils.notebook.exit(json.dumps({"summary": summary}))
