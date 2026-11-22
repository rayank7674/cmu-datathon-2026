"""Loads the editable knowledge tables in knowledge/ into one object."""
from __future__ import annotations

from dataclasses import dataclass
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
    return Knowledge(sym, cond.reset_index(drop=True), mat, tr)
