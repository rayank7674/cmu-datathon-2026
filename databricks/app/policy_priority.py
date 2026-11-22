"""Policymaker view: where is each service most out of reach, and for how many people?

Priority = people x distance gap: a district's population times how far beyond 30 km its average
resident is from the nearest facility that claims the service (straight line). It is a way to
order districts for a closer look, not a measure of need or of lives affected.
Standing caveat: this dataset is mostly private facilities. Government facilities exist in almost
every district (Rural Health Statistics counts) but their locations are not in our data, so a
"far" district can still have care nearby.
"""
from __future__ import annotations

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

import ui_theme as ui

NEAR_KM = 30.0
RANKINGS = {
    "People × distance gap": ("gap_score", "Million person-km beyond 30 km"),
    "People beyond 30 km": ("people_beyond", "People living more than 30 km away"),
    "Average distance": ("km", "Average straight-line km to the nearest"),
}


def _fmt_people(v) -> str:
    if v is None or pd.isna(v):
        return "unknown"
    return f"{v / 1e6:,.1f} M" if v >= 1e6 else f"{v / 1e3:,.0f} k"


def build(d: pd.DataFrame, pct_col: str, pub: pd.DataFrame | None, zones: pd.DataFrame | None) -> pd.DataFrame:
    """One row per district for the chosen service and evidence tier."""
    t = d[["district_id", "district_name", "state_name", "population_2020", "km", pct_col, "facilities_located",
           "deprivation_tercile"]].copy()
    t = t.rename(columns={pct_col: "pct_within"})
    pop = t["population_2020"].fillna(0)
    t["people_beyond"] = pop * (1 - t["pct_within"].clip(0, 100) / 100)
    t["gap_km"] = (t["km"] - NEAR_KM).clip(lower=0)
    t["gap_score"] = pop * t["gap_km"] / 1e6
    if pub is not None and len(pub):
        t = t.merge(pub[["district_id", "govt_hospitals_chc_and_above"]], on="district_id", how="left")
    else:
        t["govt_hospitals_chc_and_above"] = np.nan
    if zones is not None and len(zones):
        z = zones[zones["zone_type"] == "critical"].groupby("district_id").agg(
            critical_zones=("zones", "sum"), critical_people=("population", "sum")).reset_index()
        t = t.merge(z, on="district_id", how="left")
    else:
        t["critical_zones"], t["critical_people"] = np.nan, np.nan
    return t


def render(d, service_label, tier_label, pct_col, pub, zones, access, districts, labels):
    t = build(d, pct_col, pub, zones)
    known = t[t["km"].notna()]
    far = known[known["km"] > NEAR_KM]
    has_zones = t["critical_zones"].notna().any()
    top20 = known.sort_values("gap_score", ascending=False).head(20)
    blind = int(((top20["facilities_located"].fillna(0) == 0) & (top20["govt_hospitals_chc_and_above"].fillna(0) > 0)).sum())
    govt_any = int((top20["govt_hospitals_chc_and_above"].fillna(0) > 0).sum())
    ui.kpis([
        (_fmt_people(known["people_beyond"].sum()), f"People more than 30 km from a facility claiming {service_label.lower()}",
         f"Straight line, WorldPop 2020, {tier_label.lower()}. Only the ~10,000 facilities in this dataset."),
        (f"{len(far)} of {len(known)}", "Districts where the average resident is over 30 km away",
         f"Home to {_fmt_people(far['population_2020'].sum())} people."),
        ((f"{int(t['critical_zones'].sum()):,}" if has_zones else "not defined"),
         "Critical 5 km blocks for this service",
         (f"{_fmt_people(t['critical_people'].sum())} people: populated, over 30 km away, in the most deprived third (NFHS-5)."
          if has_zones else "Critical blocks are defined for 8 of the 14 services.")),
        (f"{govt_any} of 20", "of the top 20 districts have government hospitals that are not in our data",
         "Counted in March 2022 without locations, so the real gap may be smaller than shown.", True),
    ])

    st.markdown("<h2>Districts to look at first</h2>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([3, 3, 2])
    rank_by = c1.radio("Rank districts by", list(RANKINGS), horizontal=True, key="g_rankby",
                       help="People × distance gap = population × (average km to the nearest claiming facility − 30), "
                            "floored at zero. It orders districts for a closer look; it is not a measure of need.")
    states = c2.multiselect("Limit to states", sorted(known["state_name"].dropna().unique()), key="g_states",
                            placeholder="All states")
    n_show = c3.select_slider("Show", [10, 15, 25, 50], value=15, key="g_nshow")
    col, col_label = RANKINGS[rank_by]
    v = known if not states else known[known["state_name"].isin(states)]
    v = v.sort_values(col, ascending=False).head(n_show).reset_index(drop=True)
    v["rank"] = v.index + 1
    if v.empty or float(v[col].max() or 0) <= 0:
        st.info("No district is beyond 30 km on this measure for the current selection.")
    else:
        v["District"] = v["rank"].astype(str) + ". " + v["district_name"] + ", " + v["state_name"]
        v["In our data"] = np.where(v["facilities_located"].fillna(0) == 0, "None listed in this dataset",
                                    "Has listed facilities")
        left, right = st.columns([6, 5], gap="large")
        with left:
            base = alt.Chart(v).encode(y=alt.Y("District:N", sort=None, title=None, axis=alt.Axis(labelLimit=280, labelFontSize=12)),
                                       x=alt.X(f"{col}:Q", title=col_label))
            bars = base.mark_bar().encode(
                color=alt.Color("In our data:N", scale=alt.Scale(domain=["Has listed facilities", "None listed in this dataset"],
                                                                 range=[ui.INDIGO, ui.AMBER]),
                                legend=alt.Legend(orient="bottom", title=None, labelLimit=320, columns=1)),
                tooltip=[alt.Tooltip("District:N"), alt.Tooltip("population_2020:Q", title="Population", format=","),
                         alt.Tooltip("km:Q", title="Avg km", format=".0f"),
                         alt.Tooltip("people_beyond:Q", title="People beyond 30 km", format=",.0f"),
                         alt.Tooltip("govt_hospitals_chc_and_above:Q", title="Govt CHCs + hospitals (2022)")])
            fmt = ".1f" if col == "gap_score" else (",.0f" if col == "people_beyond" else ".0f")
            text = base.mark_text(align="left", dx=4, fontSize=11, color=ui.ink()).encode(text=alt.Text(f"{col}:Q", format=fmt))
            st.altair_chart(alt.layer(bars, text).properties(height=alt.Step(24)), use_container_width=True)
        with right:
            first = v.iloc[0]
            st.markdown(
                f"<div class='vf-card'><div class='vf-note'>Highest on this measure</div>"
                f"<div class='name'>{ui.esc(first['district_name'])}, {ui.esc(first['state_name'])}</div>"
                f"<div class='vf-facts'>"
                f"<div class='vf-fact'><div class='v'>{_fmt_people(first['population_2020'])}</div><div class='l'>people</div></div>"
                f"<div class='vf-fact'><div class='v'>{first['km']:.0f} km</div><div class='l'>average to the nearest claim</div></div>"
                f"<div class='vf-fact'><div class='v'>{first['pct_within']:.0f}%</div><div class='l'>live within 30 km</div></div>"
                f"<div class='vf-fact'><div class='v'>{int(first['facilities_located']) if pd.notna(first['facilities_located']) else 0}</div><div class='l'>facilities in this dataset</div></div>"
                + (f"<div class='vf-fact'><div class='v'>{int(first['govt_hospitals_chc_and_above'])}</div><div class='l'>govt CHCs and hospitals (2022), not in our data</div></div>"
                   if pd.notna(first["govt_hospitals_chc_and_above"]) else "")
                + "</div></div>", unsafe_allow_html=True)
            st.markdown("<div class='vf-caveat'><b>Far in this dataset is not the same as no care.</b> Most high-priority "
                        "districts have government health centres or hospitals that this dataset does not list. Official "
                        "counts are shown, but not their locations, so check them before acting on this order.</div>",
                        unsafe_allow_html=True)
        table = v[["rank", "district_name", "state_name", "population_2020", "km", "pct_within", "people_beyond", "gap_score",
                   "deprivation_tercile", "facilities_located", "govt_hospitals_chc_and_above", "critical_zones"]].copy()
        table.columns = ["#", "District", "State", "Population (2020)", "Avg km to nearest", "% within 30 km",
                         "People beyond 30 km", "Gap (M person-km)", "Deprivation (NFHS-5)", "Facilities in dataset",
                         "Govt CHCs + hospitals (2022)", "Critical blocks"]
        with st.expander(f"The same {len(table)} districts as a table"):
            st.dataframe(table.round(1), hide_index=True, use_container_width=True,
                         column_config={"Population (2020)": st.column_config.NumberColumn(format="%,d"),
                                        "People beyond 30 km": st.column_config.NumberColumn(format="%,d")})
            st.download_button("Download as CSV", table.to_csv(index=False).encode(), "priority_districts.csv", "text/csv",
                               key="g_dl")
    st.caption(f"{service_label}, {tier_label.lower()}. Distances are straight-line from each 1 km population cell to the nearest "
               "facility in this dataset whose own text claims the service; nothing is verified. Government counts: Rural "
               "Health Statistics 2021-22. Deprivation: NFHS-5 district factsheets.")

    with st.expander("Every service by state: share of people more than 30 km from a claiming facility"):
        hm = _state_service(access, districts, labels)
        if hm is None:
            st.info("District access figures are not available.")
        else:
            order = hm.groupby("State")["Share beyond 30 km"].mean().sort_values(ascending=False).index.tolist()
            ch = alt.Chart(hm).mark_rect().encode(
                x=alt.X("Service:N", title=None, axis=alt.Axis(labelAngle=-40, labelLimit=180, orient="top", labelOverlap=False)),
                y=alt.Y("State:N", sort=order, title=None, axis=alt.Axis(labelLimit=220, labelOverlap=False)),
                color=alt.Color("Share beyond 30 km:Q", scale=alt.Scale(range=ui.ramp(), domain=[0, 100]),
                                legend=alt.Legend(title="% of people beyond 30 km", orient="bottom")),
                tooltip=["State", "Service", alt.Tooltip("Share beyond 30 km:Q", format=".0f"),
                         alt.Tooltip("People beyond 30 km:Q", format=",.0f")])
            st.altair_chart(ch.properties(height=alt.Step(20)), use_container_width=True)
            st.caption("Any claim, straight line. Darker means a larger share of the state's people live more than 30 km from "
                       "the nearest facility in this dataset that claims the service. States are ordered by their average "
                       "across services. The same caveat applies: government facilities are missing from this dataset.")


@st.cache_data(ttl=3600, show_spinner=False)
def _state_service(access, districts, labels):
    if access is None or districts is None:
        return None
    a = access[access["service"].isin(list(labels))][["district_id", "service", "pct_pop_within_30km_claimed"]].merge(
        districts[["district_id", "state_name", "population_2020"]], on="district_id", how="inner")
    a["pop"] = a["population_2020"].fillna(0)
    a["beyond"] = a["pop"] * (1 - a["pct_pop_within_30km_claimed"].clip(0, 100) / 100)
    g = a.dropna(subset=["pct_pop_within_30km_claimed"]).groupby(["state_name", "service"]).agg(
        beyond=("beyond", "sum"), pop=("pop", "sum")).reset_index()
    g = g[g["pop"] > 0]
    return pd.DataFrame({"State": g["state_name"], "Service": g["service"].map(labels),
                         "Share beyond 30 km": 100 * g["beyond"] / g["pop"], "People beyond 30 km": g["beyond"]})
