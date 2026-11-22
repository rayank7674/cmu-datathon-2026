"""Loads the editable knowledge tables in knowledge/ into one object."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pandas as pd


def _bool(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.lower().isin(["true", "1", "yes"])


@dataclass(frozen=True)
class Knowledge:
    symptoms: pd.DataFrame      # symptom, label, red_flag, leak (blank = config default), synonyms (list)
    conditions: pd.DataFrame    # condition, label, prior, severity, life_threatening, tau_min, min_rung, needs (list of lists)
    likelihood: pd.DataFrame    # condition x symptom matrix of P(symptom | condition), NaN where not listed
    transport: pd.DataFrame     # mode, label, wait_min, speed_kmh, circuity, max_km, allowed_when_life_threatening, free
    # curve name -> {kind, params, lo, hi, late_benefit, label, source, url}: share of benefit left after t minutes
    curves: dict = field(default_factory=dict)
    # claim_strength -> {supported, checked, source}: the hand-check counts behind P(service exists)
    reliability: dict = field(default_factory=dict)

    @property
    def services(self) -> list[str]:
        out = set()
        for groups in self.conditions["needs"]:
            for g in groups:
                out.update(name for name, _ in g)
        return sorted(out)


def parse_needs(text) -> list[list[tuple[str, float]]]:
    """'cardiac_cath|icu:0.6;emergency' -> [[('cardiac_cath', 1.0), ('icu', 0.6)], [('emergency', 1.0)]].

    ';' separates requirements that must ALL be met; '|' separates
    alternatives, any one of which meets that requirement. ':0.6' gives an
    alternative partial credit (an ICU can treat a heart attack, but less well
    than a cath lab).
    """
    if text is None or (isinstance(text, float) and pd.isna(text)) or not str(text).strip():
        return []
    out = []
    for grp in str(text).split(";"):
        alts = []
        for a in grp.split("|"):
            a = a.strip()
            if not a:
                continue
            name, _, credit = a.partition(":")
            alts.append((name.strip(), float(credit) if credit else 1.0))
        if alts:
            out.append(alts)
    return out


def beta_quantile(a: int, b: int, q: float) -> float:
    """Quantile of Beta(a, b) for whole-number a, b (bisection on the binomial identity
    I_x(a, b) = P(Binomial(a + b - 1, x) >= a)); no scipy needed."""
    n = a + b - 1

    def cdf(x):
        return sum(math.comb(n, j) * x ** j * (1 - x) ** (n - j) for j in range(a, n + 1))
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if cdf(mid) < q else (lo, mid)
    return (lo + hi) / 2


def reliability_estimate(supported: int, checked: int, quantile: float | None = None) -> float:
    """Beta-Binomial with a uniform prior: Beta(supported + 1, checked - supported + 1).
    Returns the mean, or the given quantile (e.g. 0.05 for a cautious lower bound)."""
    a, b = int(supported) + 1, int(checked) - int(supported) + 1
    return a / (a + b) if quantile is None else beta_quantile(a, b, quantile)


def parse_params(text) -> dict:
    """'ratio=1.075;per_min=30' -> {'ratio': 1.075, 'per_min': 30.0}; '90:1;225:0.53' -> {'points': [(90, 1), ...]}."""
    text = str(text)
    if "=" in text:
        return {a.split("=")[0].strip(): float(a.split("=")[1]) for a in text.split(";") if a.strip()}
    return {"points": [(float(a.split(":")[0]), float(a.split(":")[1])) for a in text.split(";") if a.strip()]}


@lru_cache(maxsize=8)
def load(knowledge_dir: str) -> Knowledge:
    d = Path(knowledge_dir)
    sym = pd.read_csv(d / "symptoms.csv")
    sym["red_flag"] = _bool(sym["red_flag"])
    sym["leak"] = pd.to_numeric(sym.get("leak"), errors="coerce") if "leak" in sym else float("nan")
    sym["synonyms"] = sym["synonyms"].fillna("").map(
        lambda s: [x.strip().lower() for x in s.split("|") if x.strip()])

    cond = pd.read_csv(d / "conditions.csv", keep_default_na=False, na_values=[""])
    cond["life_threatening"] = _bool(cond["life_threatening"])
    cond["needs"] = cond["needs"].map(parse_needs)
    cond["prior"] = cond["prior"] / cond["prior"].sum()

    lk = pd.read_csv(d / "likelihood.csv")
    unknown_c = set(lk["condition"]) - set(cond["condition"])
    unknown_s = set(lk["symptom"]) - set(sym["symptom"])
    if unknown_c or unknown_s:
        raise ValueError(f"likelihood.csv names unknown conditions {unknown_c} or symptoms {unknown_s}")
    mat = (lk.pivot_table(index="condition", columns="symptom", values="p", aggfunc="max")
             .reindex(index=cond["condition"], columns=sym["symptom"]))

    tr = pd.read_csv(d / "transport.csv")
    tr["allowed_when_life_threatening"] = _bool(tr["allowed_when_life_threatening"])
    tr["free"] = _bool(tr["free"])
    tr["max_km"] = pd.to_numeric(tr["max_km"], errors="coerce")
    for col in ("wait_min", "speed_kmh", "circuity"):
        tr[col] = tr[col].astype(float)   # so transport_overrides can set fractional values
    if "time_curve" not in cond:
        cond["time_curve"] = None
    cond["time_curve"] = cond["time_curve"].where(cond["time_curve"].notna(), None)

    curves, reliability = {}, {}
    if (d / "time_curves.csv").exists():
        for r in pd.read_csv(d / "time_curves.csv").to_dict("records"):
            r["params"] = parse_params(r["params"])
            curves[r["curve"]] = r
    unknown = set(cond["time_curve"].dropna()) - set(curves)
    if unknown:
        raise ValueError(f"conditions.csv names time curves not in time_curves.csv: {unknown}")
    if (d / "claim_reliability.csv").exists():
        reliability = {r["claim_strength"]: r for r in pd.read_csv(d / "claim_reliability.csv").to_dict("records")}
    return Knowledge(sym, cond.reset_index(drop=True), mat, tr, curves, reliability)
