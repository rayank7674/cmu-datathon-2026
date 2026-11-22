"""Validation without outcome labels.  Run from the app folder:

    python validation/validate.py                # full run, about 15 minutes on 4 cores
    python validation/validate.py --quick        # small run, about 1 minute

Reads validation_data/claims.parquet (what the app's load_claims() returns) and
validation_data/patients.csv (3,000 one-km cells sampled in proportion to
population, so each row is one equally likely patient). Writes
validation/results.json, validation/RESULTS.md and validation/stability.png.

What it measures
  1. Against the nearest facility: how often the nearest place makes no claim
     for the service the emergency needs, how often our pick differs, and what
     the extra travel buys.
  2. Stability: every uncertain number is redrawn from its error bar (claim
     reliability from the Beta posterior of the hand check, delay curves from
     the published confidence intervals, ambulance wait and speed) and the
     ranking is recomputed. Headline: how often the first choice stays first.
  3. Which group of numbers moves the answer most (each group redrawn alone).

It does NOT measure lives saved or whether a service really exists today.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from recommender import PIPELINE, Config, recommend, without  # noqa: E402
from recommender.knowledge import load  # noqa: E402
from recommender.steps import facilities_from_claims  # noqa: E402

# Emergency scenarios: the symptom phrases a person would enter. Drawn with equal
# probability (not by how common each emergency is), so every type is tested.
SCENARIOS = {
    "heart_attack": (["chest pain", "cold sweat", "pain in left arm"], {}),
    "stroke": (["face drooping", "slurred speech"], {}),
    "major_trauma": (["road accident", "heavy bleeding"], {}),
    "head_injury_serious": (["head injury", "vomiting", "confused"], {}),
    "obstetric_emergency": (["vaginal bleeding"], {"pregnant": True}),
    "newborn_emergency": (["baby not breathing"], {}),
    "poisoning_envenoming": (["snake bite"], {}),
    "sepsis": (["high fever", "confused", "breathing difficulty"], {}),
}
ONSETS = [0, 30, 60, 120]
FAST = without(without(PIPELINE, "explain"), "safety_rules")

G = {}  # per-process globals
BASE_CFG = Config()   # the model as shipped; edit here to validate a variant


def _init(data_dir):
    G["claims"] = pd.read_parquet(Path(data_dir) / "claims.parquet")
    G["fac"] = facilities_from_claims(G["claims"])
    G["kb"] = load(Config().knowledge_dir)
    G["have"] = G["claims"].groupby("facility_id")["service"].apply(set).to_dict()


def _needed_services(kb, condition):
    row = kb.conditions.set_index("condition").loc[condition]
    return [[name for name, _ in grp] for grp in row["needs"]]


def base_case(job):
    """One patient with the model as shipped, compared with two simple baselines."""
    i, lat, lon, scen, onset = job
    sym, patient = SCENARIOS[scen]
    r = recommend(sym, lat, lon, G["claims"], facilities=G["fac"], patient=patient, onset_min=onset,
                  kb=G["kb"], cfg=BASE_CFG, pipeline=FAST)
    rk = r.ranked
    if rk is None or rk.empty:
        return {"i": i, "scenario": scen, "empty": True}
    top = rk.iloc[0]
    nearest = rk.sort_values("km").iloc[0]
    cl = G["claims"]
    need = _needed_services(G["kb"], scen)

    def claims_all_needs(fid):
        have = G["have"].get(fid, set())
        return all(any(s in have for s in grp) for grp in need)

    full = rk[rk["facility_id"].map(claims_all_needs)].sort_values("km")
    nearest_full = full.iloc[0] if len(full) else None

    em = cl.loc[(cl["service"] == "emergency"), "facility_id"]
    rk_em = rk[rk["facility_id"].isin(em)].sort_values("km")
    nearest_em = rk_em.iloc[0] if len(rk_em) else nearest
    return {
        "i": i, "scenario": scen, "onset": onset, "empty": False,
        "inferred_top": r.conditions.iloc[0]["condition"], "is_red": bool(r.is_red),
        "n_candidates": int(len(rk)), "radius": float(r.radius_used_km),
        "top_id": top["facility_id"], "top_min": float(top["minutes"]), "top_km": float(top["km"]),
        "top_need_met": float(top["need_met"]), "top3": list(rk["facility_id"].head(3)),
        "nearest_id": nearest["facility_id"], "nearest_min": float(nearest["minutes"]),
        "nearest_km": float(nearest["km"]), "nearest_need_met": float(nearest["need_met"]),
        "nearest_claims_needs": bool(claims_all_needs(nearest["facility_id"])),
        "top_claims_needs": bool(claims_all_needs(top["facility_id"])),
        "nearest_em_id": nearest_em["facility_id"], "nearest_em_min": float(nearest_em["minutes"]),
        "nearest_em_need_met": float(nearest_em["need_met"]),
        "nearest_em_claims_needs": bool(claims_all_needs(nearest_em["facility_id"])),
        "nearest_full_id": None if nearest_full is None else nearest_full["facility_id"],
        "nearest_full_min": np.nan if nearest_full is None else float(nearest_full["minutes"]),
        "nearest_full_need_met": np.nan if nearest_full is None else float(nearest_full["need_met"]),
        "nearest_full_rank": -1 if nearest_full is None else int(nearest_full["rank"]),
    }


# ---- drawing every uncertain number from its error bar -----------------------------------
def draw_parameters(rng, kb, groups=("reliability", "curves", "transport")):
    """Returns (cfg, kb') with the named groups of numbers redrawn."""
    cfg = BASE_CFG
    if "reliability" in groups:
        # Beta posterior of the hand check (uniform prior); cautious mode is replaced by the draw itself
        t = {k: float(rng.beta(r["supported"] + 1, r["checked"] - r["supported"] + 1))
             for k, r in kb.reliability.items()}
        cfg = cfg.replace(p_service_by_strength=t, p_no_claim=float(rng.uniform(0.01, 0.08)))
    if "curves" in groups:
        curves = {}
        for name, c in kb.curves.items():
            c = dict(c)
            prm = dict(c["params"])
            if c["kind"] == "ratio":       # log-normal matching the published 95% interval
                sd = (math.log(c["hi"]) - math.log(c["lo"])) / 3.92
                prm["ratio"] = max(1.0005, math.exp(rng.normal(math.log(prm["ratio"]), sd)))
            elif c["kind"] == "linear":    # no interval published for the 3-hour point: +/- 30 min
                prm["zero_at"] = float(rng.uniform(150, 210))
                c["late_benefit"] = float(rng.uniform(0.10, 0.40))
            elif c["kind"] == "points":    # later-window benefit +/- 30% (the >4.5 h effect was not significant)
                pts = sorted(prm["points"])
                prm["points"] = [pts[0]] + [(x, float(np.clip(y * rng.uniform(0.7, 1.3), 0, 1))) for x, y in pts[1:]]
            c["params"] = prm
            curves[name] = c
        cond = kb.conditions.copy()        # assumed fall-back curves: tau x/÷ about 1.6
        cond["tau_min"] = cond["tau_min"] * np.exp(rng.normal(0, 0.5, len(cond)))
        kb = dataclasses.replace(kb, curves=curves, conditions=cond)
        cfg = cfg.replace(late_care_benefit=float(rng.uniform(0.10, 0.40)))
    if "transport" in groups:
        cfg = cfg.replace(transport_overrides={
            "ambulance_108": {"wait_min": float(np.clip(rng.normal(25, 8), 8, 50)),
                              "speed_kmh": float(rng.uniform(30, 50)), "circuity": float(rng.uniform(1.25, 1.6))},
            "auto_taxi": {"wait_min": float(rng.uniform(5, 25)), "speed_kmh": float(rng.uniform(18, 32)),
                          "circuity": float(rng.uniform(1.25, 1.6))}})
    return cfg, kb


def mc_case(job):
    """One patient, n redraws of one group of numbers: how often does the first choice hold?"""
    i, lat, lon, scen, onset, base_top, base_top3, groups, n, seed = job
    sym, patient = SCENARIOS[scen]
    rng = np.random.default_rng(seed)
    same = in3 = base_in_new3 = 0
    for _ in range(n):
        cfg, kb = draw_parameters(rng, G["kb"], groups)
        r = recommend(sym, lat, lon, G["claims"], facilities=G["fac"], patient=patient, onset_min=onset,
                      kb=kb, cfg=cfg, pipeline=FAST)
        new = list(r.ranked["facility_id"].head(3))
        same += new[0] == base_top
        in3 += new[0] in base_top3
        base_in_new3 += base_top in new
    return {"i": i, "scenario": scen, "groups": "+".join(groups), "n": n, "same": same, "in3": in3,
            "base_in_new3": base_in_new3}


def pct(x):
    return f"{100 * x:.0f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--data", default=str(APP / "validation_data"))
    ap.add_argument("--procs", type=int, default=4)
    a = ap.parse_args()
    n_base, n_mc, draws, draws_group = (200, 40, 10, 6) if a.quick else (3000, 300, 60, 30)

    rng = np.random.default_rng(20261122)
    pts = pd.read_csv(Path(a.data) / "patients.csv").head(n_base)
    scen = rng.choice(list(SCENARIOS), len(pts))
    onset = rng.choice(ONSETS, len(pts))
    jobs = [(i, float(r.lat), float(r.lon), scen[i], int(onset[i])) for i, r in enumerate(pts.itertuples())]

    with Pool(a.procs, initializer=_init, initargs=(a.data,)) as pool:
        base = pd.DataFrame(pool.map(base_case, jobs, chunksize=20))
        ok = base[~base["empty"]].copy()
        sub = ok.head(n_mc)
        mc_jobs = []
        for groups, n in ((("reliability", "curves", "transport"), draws), (("reliability",), draws_group),
                          (("curves",), draws_group), (("transport",), draws_group)):
            for r in sub.itertuples():
                j = jobs[r.i]
                mc_jobs.append((*j, r.top_id, r.top3, groups, n, 1000 * r.i + len(groups) + ord(groups[0][0])))
        mc = pd.DataFrame(pool.map(mc_case, mc_jobs, chunksize=4))

    # ---- summarise ----
    ok["differs"] = ok["top_id"] != ok["nearest_id"]
    ok["extra_min"] = ok["top_min"] - ok["nearest_min"]
    ok["differs_em"] = ok["top_id"] != ok["nearest_em_id"]
    res = {
        "patients": int(len(base)), "scored": int(len(ok)), "draws_per_patient": draws, "mc_patients": int(len(sub)),
        "symptoms_gave_intended_condition": float((ok["inferred_top"] == ok["scenario"]).mean()),
        "all_red": float(ok["is_red"].mean()),
        "nearest_lacks_needed_claim": float(1 - ok["nearest_claims_needs"].astype(bool).mean()),
        "nearest_emergency_lacks_needed_claim": float(1 - ok["nearest_em_claims_needs"].astype(bool).mean()),
        "pick_lacks_needed_claim": float(1 - ok["top_claims_needs"].astype(bool).mean()),
        "pick_differs_from_nearest": float(ok["differs"].mean()),
        "pick_differs_from_nearest_emergency": float(ok["differs_em"].mean()),
        "median_minutes_pick": float(ok["top_min"].median()), "median_minutes_nearest": float(ok["nearest_min"].median()),
        "median_extra_minutes_when_differs": float(ok.loc[ok["differs"], "extra_min"].median()),
        "p90_extra_minutes_when_differs": float(ok.loc[ok["differs"], "extra_min"].quantile(0.9)),
        "mean_need_met_pick": float(ok["top_need_met"].mean()),
        "mean_need_met_nearest": float(ok["nearest_need_met"].mean()),
        "mean_need_met_nearest_emergency": float(ok["nearest_em_need_met"].mean()),
        "pick_is_nearest_full": float((ok["top_id"] == ok["nearest_full_id"]).mean()),
        "nearest_full_in_top3": float(ok["nearest_full_rank"].between(1, 3).mean()),
        "no_full_claim_in_range": float(ok["nearest_full_id"].isna().mean()),
        "median_minutes_nearest_full": float(ok["nearest_full_min"].median()),
        "median_extra_vs_nearest_full": float((ok["top_min"] - ok["nearest_full_min"])[ok["top_id"] != ok["nearest_full_id"]].median()),
        "p90_extra_vs_nearest_full": float((ok["top_min"] - ok["nearest_full_min"])[ok["top_id"] != ok["nearest_full_id"]].quantile(0.9)),
        "mean_need_met_nearest_full": float(ok["nearest_full_need_met"].mean()),
        "pick_over_60_min": float((ok["top_min"] > 60).mean()), "pick_over_120_min": float((ok["top_min"] > 120).mean()),
    }
    stab = {}
    for g, d in mc.groupby("groups"):
        stab[g] = {"first_choice_unchanged": float(d["same"].sum() / d["n"].sum()),
                   "new_first_was_in_our_top3": float(d["in3"].sum() / d["n"].sum()),
                   "our_first_still_in_top3": float(d["base_in_new3"].sum() / d["n"].sum()),
                   "patients_never_changing": float((d["same"] == d["n"]).mean())}
    res["stability"] = stab
    allg = mc[mc["groups"] == "reliability+curves+transport"]
    by_s = allg.groupby("scenario").apply(lambda d: d["same"].sum() / d["n"].sum()).to_dict()
    res["stability_by_scenario"] = {k: float(v) for k, v in by_s.items()}
    by = ok.groupby("scenario").agg(patients=("i", "size"), nearest_lacks=("nearest_claims_needs", lambda s: 1 - s.astype(bool).mean()),
                                    differs=("differs", "mean"), med_min=("top_min", "median"),
                                    med_nearest=("nearest_min", "median"), need_pick=("top_need_met", "mean"),
                                    need_nearest=("nearest_need_met", "mean"))
    res["by_scenario"] = json.loads(by.to_json(orient="index"))

    out = APP / "validation"
    (out / "results.json").write_text(json.dumps(res, indent=1))
    base.drop(columns=["top3"], errors="ignore").to_csv(out / "base_cases.csv", index=False)

    full = stab["reliability+curves+transport"]
    lines = [
        "# Validation results (no outcome labels)", "",
        f"{res['scored']:,} synthetic emergency patients placed in proportion to population (WorldPop 1 km cells), "
        f"each given one of {len(SCENARIOS)} emergencies with equal chance and an onset time of 0 to 120 minutes. "
        "Travel time is the straight-line estimate with the 108 ambulance or an auto/taxi. "
        "Produced by `validation/validate.py`; every number below is in `results.json`.", "",
        "## Against going to the nearest facility", "",
        f"- The nearest facility makes **no claim for a service the emergency needs** for {pct(res['nearest_lacks_needed_claim'])} of patients. "
        f"Even the nearest facility that claims emergency care lacks one for {pct(res['nearest_emergency_lacks_needed_claim'])}. "
        f"Our pick lacks one for {pct(res['pick_lacks_needed_claim'])}.",
        f"- Our pick is a different facility from the nearest for {pct(res['pick_differs_from_nearest'])} of patients "
        f"(and from the nearest emergency-claiming facility for {pct(res['pick_differs_from_nearest_emergency'])}).",
        f"- When it differs, the extra trip is {res['median_extra_minutes_when_differs']:.0f} minutes at the median "
        f"and {res['p90_extra_minutes_when_differs']:.0f} minutes for one patient in ten. Median total time to our pick "
        f"is {res['median_minutes_pick']:.0f} minutes against {res['median_minutes_nearest']:.0f} to the nearest.",
        f"- By the model's own score (share of the need met, which counts both capability and delay), our pick averages "
        f"{res['mean_need_met_pick']:.2f}, the nearest facility {res['mean_need_met_nearest']:.2f} and the nearest "
        f"emergency-claiming facility {res['mean_need_met_nearest_emergency']:.2f}. This is the model marking its own homework: "
        "it shows the size of the trade-off, not a health outcome.",
        f"- **Against the nearest facility that claims every needed service** (a sensible simple rule): our pick is that same facility for "
        f"{pct(res['pick_is_nearest_full'])} of patients and it is in our top three for {pct(res['nearest_full_in_top3'])}. "
        f"When we pick something else, it is {res['median_extra_vs_nearest_full']:.0f} minutes further at the median "
        f"({res['p90_extra_vs_nearest_full']:.0f} for one in ten), chosen for stronger evidence, a higher care level or a 24x7 statement. "
        f"Median time to that facility is {res['median_minutes_nearest_full']:.0f} minutes; its mean score is {res['mean_need_met_nearest_full']:.2f}. "
        f"For {pct(res['no_full_claim_in_range'])} of patients no facility in range claims everything needed.",
        f"- {pct(res['pick_over_60_min'])} of picks are more than 60 minutes away and {pct(res['pick_over_120_min'])} more than 120.", "",
        "| Emergency | Patients | Nearest lacks a needed claim | Pick differs from nearest | Median min to pick | Median min to nearest | Need met: pick | Need met: nearest |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for k, v in res["by_scenario"].items():
        lines.append(f"| {k.replace('_', ' ')} | {v['patients']} | {pct(v['nearest_lacks'])} | {pct(v['differs'])} | "
                     f"{v['med_min']:.0f} | {v['med_nearest']:.0f} | {v['need_pick']:.2f} | {v['need_nearest']:.2f} |")
    lines += ["", "## Stability when every uncertain number is redrawn", "",
              f"{res['mc_patients']} patients, {draws} redraws each. Claim reliability is drawn from the Beta posterior of the "
              "hand check, the delay curves from the published 95% intervals, the assumed curves by a factor of about 1.6 "
              "either way, and the ambulance wait (25 ± 8 min), speed (30 to 50 km/h) and road factor (1.25 to 1.6).", "",
              f"- **The first choice stays first in {pct(full['first_choice_unchanged'])} of redraws.**",
              f"- When it changes, the new first choice was already in our top three in {pct(full['new_first_was_in_our_top3'])} of all redraws, "
              f"and our first choice is still in the top three in {pct(full['our_first_still_in_top3'])}.",
              f"- For {pct(full['patients_never_changing'])} of patients the first choice does not change in any of the redraws.", "",
              "| Numbers redrawn (others held fixed) | First choice unchanged |", "| --- | ---: |"]
    names = {"reliability": "Claim reliability (hand-check error bars)", "curves": "Time curves (published intervals and assumed curves)",
             "transport": "Ambulance wait, speed and road factor", "reliability+curves+transport": "All three together"}
    for g in ("reliability", "curves", "transport", "reliability+curves+transport"):
        lines.append(f"| {names[g]} | {pct(stab[g]['first_choice_unchanged'])} |")
    lines += ["", "| Emergency | First choice unchanged (all redrawn) |", "| --- | ---: |"]
    lines += [f"| {k.replace('_', ' ')} | {pct(v)} |" for k, v in res["stability_by_scenario"].items()]
    lines += ["", "## Checks", "",
              f"- The entered symptoms produced the intended emergency as the most likely possibility for {pct(res['symptoms_gave_intended_condition'])} of patients, "
              f"and {pct(res['all_red'])} were treated as red cases.",
              "- 25 automated tests (`python -m pytest -q tests`) cover the sanity properties: more delay never raises a score, "
              "every published curve starts at 1 and only falls, red-flag symptoms always go to hospital, a suspect location is never ranked.", "",
              "## What this does not show", "",
              "- No outcome data exists, so nothing here is a count of lives saved.",
              "- A claim is the facility's own text; 'claims the service' is not 'has the service today'.",
              "- Distances are straight-line to the roughly 10,000 facilities in this dataset; real road time and other facilities are not included.",
              "- Scenarios are equally weighted, not weighted by how common each emergency is.", ""]
    (out / "RESULTS.md").write_text("\n".join(lines))

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        order = ["transport", "curves", "reliability", "reliability+curves+transport"]
        vals = [100 * stab[g]["first_choice_unchanged"] for g in order]
        fig, ax = plt.subplots(figsize=(10, 3.4), dpi=200)
        bars = ax.barh([names[g] for g in order], vals, color=["#4a6fa5"] * 3 + ["#1f3b63"], height=0.6)
        for b, v in zip(bars, vals):
            ax.text(v - 2, b.get_y() + b.get_height() / 2, f"{v:.0f}%", va="center", ha="right", color="white",
                    fontweight="bold")
        ax.set_xlim(0, 100)
        ax.set_xlabel("Share of redraws where the first-choice hospital stays the same (%)")
        fig.suptitle("How stable is the recommendation when the numbers are varied?", x=0.01, ha="left", fontsize=12)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        fig.tight_layout()
        fig.savefig(out / "stability.png", bbox_inches="tight")
    except Exception as e:  # the chart is optional
        print("chart skipped:", e)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
