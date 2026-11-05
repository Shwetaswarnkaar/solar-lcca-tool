"""
app.py  -  Rooftop Solar vs Grid: Life Cycle Cost Calculator (Streamlit web page)
CE725 Construction Economics and Finance, team course project.

Run:  pip install -r requirements.txt   then   streamlit run app.py
All calculations are in lcca_engine.py; this file only collects inputs and shows results.
"""
from __future__ import annotations

import math
from dataclasses import asdict
from io import BytesIO

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from lcca_engine import (Inputs, Tariff, MSEDCL_FY26, run, sizing, monthly_bill, slab_breakdown,
                         tornado, breakeven, npv_vs_rate, size_sweep, summary, central_subsidy)
from benchmarks import all_checks

st.set_page_config(page_title="Solar vs Grid LCCA", page_icon="☀️", layout="wide")

GRID = "#ef4444"     # red, as in the reference design
SOLAR = "#3b82f6"    # blue
GOOD = "#22c55e"
MUTED = "#9aa0a6"

st.markdown("""
<style>
.st-key-hero {background:#202124; border-radius:18px; padding:26px 30px 18px 30px;}
.st-key-hero p, .st-key-hero label, .st-key-hero span, .st-key-hero small {color:#e8eaed !important;}
.st-key-hero [data-testid="stMetricValue"] {color:#ffffff !important; font-size:1.7rem;}
.st-key-hero [data-testid="stMetricLabel"] p {color:#bdc1c6 !important;}
.hero-sub {text-align:center; color:#bdc1c6; font-size:1.05rem; margin:0;}
.hero-head {text-align:center; color:#ffffff; font-size:1.9rem; font-weight:600; margin:4px 0 6px 0;}
.pill {display:inline-block; padding:2px 14px; border-radius:999px; font-weight:700;}
.pill-good {background:rgba(34,197,94,.16); color:#4ade80;}
.pill-bad {background:rgba(239,68,68,.16); color:#f87171;}
.verdict {border-left:6px solid; padding:12px 18px; border-radius:8px; background:rgba(0,0,0,.03); margin:14px 0 6px 0;}
</style>
""", unsafe_allow_html=True)


# ------------------------------------------------------------------ helpers
@st.cache_data
def cached_checks() -> pd.DataFrame:
    return all_checks()


def indian(n: float) -> str:
    neg, s = n < 0, f"{abs(round(n)):.0f}"
    if len(s) > 3:
        head, tail, parts = s[:-3], s[-3:], []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return ("−" if neg else "") + s


def inr(x) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return ("−" if x < 0 else "") + "₹" + indian(abs(x))


def lakh(x) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    sign = "−" if x < 0 else ""
    if abs(x) >= 1e7:
        return f"{sign}₹{abs(x) / 1e7:.2f} crore"
    if abs(x) >= 1e5:
        return f"{sign}₹{abs(x) / 1e5:.2f} lakh"
    return inr(x)


def years(x) -> str:
    return "not within the period" if x is None else f"{x:.1f} years"


# ------------------------------------------------------------------ defaults = team Base_Inputs
BASE = Inputs()
DEFAULTS = dict(
    view="Today's money (discounted)", mode="Monthly bill (₹)", bill=5000, units=400, gross_cost=300000, esc=5.0,
    kw=5.0, _prev_kw=5.0, category="Residential", statecat="General category state",
    load=5.0, roof=600, area_kw=100, panel_wp=545, cuf=19.0, deg=0.5,
    sub_mode="PM Surya Ghar (automatic)", sub_manual=78000, topup=0, sub_year="Year 0 (at installation)",
    other_upfront=0, loan=False, loan_share=80, loan_rate=7.0, loan_years=5,
    om=3000, om_esc=3.0, ins=1000, ins_esc=3.0, inv_type="Single-phase (team base)", inv_cost=40000,
    inv_life=10, inv_esc=0.0, inv_credit=False, salvage=0,
    fixed=130.0, other_monthly=0.0, duty=16.0, export=2.82, export_esc=0.0, gsc=1.96,
    disc=8.0, years=25, ef=0.727, cprice=0,
    tariff_df=pd.DataFrame({"Slab upper limit (units/month)": [100.0, 300.0, 500.0, None],
                            "Rate ₹/kWh (energy + wheeling)": [5.67, 10.88, 14.07, 15.57]}),
)
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v.copy() if isinstance(v, pd.DataFrame) else v


def reset_all():
    for k, v in DEFAULTS.items():
        st.session_state[k] = v.copy() if isinstance(v, pd.DataFrame) else v
    st.session_state.pop("tariff_editor", None)


def on_size_change():
    """Keep the cost per kW when the size changes."""
    old, new = st.session_state._prev_kw, st.session_state.kw
    if old > 0 and new > 0:
        st.session_state.gross_cost = int(min(2_000_000, round(st.session_state.gross_cost / old * new / 5000) * 5000))
    st.session_state._prev_kw = new


def on_inv_type():
    st.session_state.inv_cost = 55000 if st.session_state.inv_type.startswith("Three") else 40000


def set_size(k: float):
    st.session_state.kw = k
    on_size_change()


def parse_tariff(df: pd.DataFrame):
    d = df.dropna(subset=[df.columns[1]]).copy()
    lim_col, rate_col = d.columns[0], d.columns[1]
    open_rows = d[d[lim_col].isna()]
    closed = d[d[lim_col].notna()].sort_values(lim_col)
    if len(open_rows) != 1:
        raise ValueError("Leave exactly one slab limit blank (the top, open-ended slab).")
    limits = tuple(float(x) for x in closed[lim_col])
    rates = tuple(float(x) for x in closed[rate_col]) + (float(open_rows[rate_col].iloc[0]),)
    return limits, rates


# ================================================================== SIDEBAR
with st.sidebar:
    st.markdown("### Inputs")
    st.button("↺ Reset to team base case", on_click=reset_all, width="stretch")
   

    with st.expander("🏠 Your home", expanded=True):
        st.selectbox("State · DISCOM", ["Maharashtra · MSEDCL (LT-I residential slabs)"],
                     help="Tariff slabs are editable under 'Grid tariff & net metering' for any other DISCOM.")
        st.selectbox("Consumer category", ["Residential", "Housing society (common areas)", "Commercial"], key="category",
                     help="Residential: PM Surya Ghar subsidy. Housing society: ₹18,000/kW. Commercial: no subsidy; "
                          "grid support charge above 10 kW; enter your commercial tariff below.")
        st.selectbox("Subsidy rate band", ["General category state", "Special category state (NE, hill states, UTs)"],
                     key="statecat")
        st.number_input("Sanctioned load (kW)", 0.0, 500.0, step=0.5, key="load",
                        help="Net-metered capacity is normally limited to the sanctioned load.")
        st.number_input("Shadow-free roof area (sq ft)", 0, 100000, step=50, key="roof")
        st.number_input("Roof area needed per kW (sq ft)", 40, 200, step=5, key="area_kw",
                        help="Assumption: about 100 sq ft per kW including spacing. Check with your installer.")

    with st.expander("☀️ Solar system", expanded=True):
        st.slider("System size (kW)", 0.0, 20.0, step=0.5, key="kw", on_change=on_size_change,
                  help="Changing the size keeps the cost per kW, so the system cost slider moves with it.")
        st.number_input("Panel rating (Wp)", 100, 800, step=5, key="panel_wp",
                        help="545 Wp = r-solar 5.45 kW / 10-panel quote (team ref R14).")
        st.slider("Capacity utilisation factor, CUF (%)", 10.0, 25.0, step=0.5, key="cuf",
                  help="MERC rooftop norm 19% (R20). 16% ≈ Mumbai monsoon case (~3.8 units/kW/day).")
        st.caption(f"= {st.session_state.cuf / 100 * 24:.2f} units per kW per day · "
                   f"{st.session_state.cuf / 100 * 8760:,.0f} kWh per kW per year")
        st.number_input("Panel degradation (%/yr)", 0.0, 5.0, step=0.1, key="deg", format="%.2f",
                        help="NREL median 0.5% (R17); meta-analysis 0.94% (R19).")

    with st.expander("💰 Subsidy, upfront costs & loan"):
        st.selectbox("Subsidy", ["PM Surya Ghar (automatic)", "Enter amount", "No subsidy"], key="sub_mode")
        if st.session_state.sub_mode == "Enter amount":
            st.number_input("Subsidy amount (₹)", 0, 10_000_000, step=1000, key="sub_manual")
        st.number_input("State top-up subsidy (₹)", 0, 1_000_000, step=1000, key="topup",
                        help="Maharashtra: ₹0. Delhi/UP give extra (team Subsidy sheet D).")
        st.radio("Subsidy received in", ["Year 0 (at installation)", "Year 1"], key="sub_year",
                 help="Paid by DBT about 30 working days after commissioning.")
        st.number_input("Other upfront costs (₹)", 0, 10_000_000, step=500, key="other_upfront",
                        help="e.g. upgraded mounting structure ₹7,500–15,000 (R27), net-meter fees.")
        st.toggle("Pay with a loan", key="loan")
        if st.session_state.loan:
            st.slider("Loan share of net cost (%)", 0, 100, step=5, key="loan_share")
            st.number_input("Loan interest (%/yr)", 0.0, 30.0, step=0.25, key="loan_rate",
                            help="PM Surya Ghar collateral-free loans ≈ 7%.")
            st.number_input("Loan tenure (years)", 1, 20, step=1, key="loan_years")

    with st.expander("🔧 Running costs"):
        st.number_input("O&M, year 1 (₹/yr)", 0, 1_000_000, step=500, key="om",
                        help="MERC norm ₹600/kW = ₹3,000 for 5 kW (R20).")
        st.number_input("O&M escalation (%/yr)", 0.0, 20.0, step=0.1, key="om_esc", format="%.2f")
        st.number_input("Insurance, year 1 (₹/yr)", 0, 1_000_000, step=100, key="ins",
                        help="Add-on ₹125–500; standalone 0.2–0.5% of cost (R26).")
        st.number_input("Insurance escalation (%/yr)", 0.0, 20.0, step=0.1, key="ins_esc", format="%.2f")
        st.selectbox("Inverter type", ["Single-phase (team base)", "Three-phase"], key="inv_type", on_change=on_inv_type)
        st.number_input("Inverter replacement cost (today's ₹)", 0, 2_000_000, step=1000, key="inv_cost",
                        help="5 kW single-phase ₹28–42k; three-phase ₹50–55k (R24, R41).")
        st.number_input("Inverter life (years, 0 = never replaced)", 0, 40, step=1, key="inv_life",
                        help="10 years → replaced in years 10 and 20 (R25).")
        st.number_input("Inverter price change (%/yr)", -20.0, 20.0, step=0.5, key="inv_esc")
        st.checkbox("Credit unused life of the last inverter at the end", key="inv_credit",
                    help="Off in the team base case (salvage set to 0).")
        st.number_input("Salvage value at end of life (₹)", 0, 10_000_000, step=1000, key="salvage")

    with st.expander("⚡ Grid tariff & net metering"):
        st.caption("Telescopic slabs: each block of monthly units at its own rate. Leave the top slab's limit blank.")
        edited_tariff = st.data_editor(st.session_state.tariff_df, key="tariff_editor", num_rows="dynamic",
                                       width="stretch", hide_index=True)
        st.number_input("Fixed charge (₹/month)", 0.0, 100000.0, step=5.0, key="fixed")
        st.number_input("Other monthly charges (₹)", 0.0, 100000.0, step=5.0, key="other_monthly",
                        help="Meter rent ₹35, municipal add-on ₹10 (excluded in team benchmark).")
        st.number_input("Electricity duty (%)", 0.0, 50.0, step=1.0, key="duty")
        st.number_input("Export rate for surplus (₹/kWh)", 0.0, 50.0, step=0.01, key="export",
                        help="MERC generic rooftop surplus tariff FY2025-26 and FY2026-27 (R06, R33).")
        st.number_input("Export rate change (%/yr)", -10.0, 20.0, step=0.5, key="export_esc")
        st.number_input("Grid support charge (₹/kWh, commercial > 10 kW)", 0.0, 10.0, step=0.01, key="gsc")

    with st.expander("📈 Finance & carbon"):
        st.slider("Discount rate (%/yr)", 0.0, 20.0, step=0.5, key="disc",
                  help="Team base 8% (FD ≈ 7%, solar loans 7–10%).")
        st.number_input("Analysis period (years)", 1, 40, step=1, key="years")
        st.number_input("Grid emission factor (tCO₂/MWh)", 0.0, 2.0, step=0.001, key="ef", format="%.3f",
                        help="CEA CO₂ Baseline Database, FY2023-24.")
        st.number_input("Carbon price (₹/tCO₂, 0 = not counted)", 0, 100000, step=100, key="cprice")


# ================================================================== tariff from the editor
tariff_df = edited_tariff
tariff_error = None
try:
    limits, rates = parse_tariff(tariff_df)
except Exception as e:  # fall back to the MSEDCL default, and say so
    tariff_error = str(e)
    limits, rates = MSEDCL_FY26.slab_limits, MSEDCL_FY26.slab_rates

CAT = {"Residential": "residential", "Housing society (common areas)": "housing_society", "Commercial": "commercial"}


def build_inputs() -> Inputs:
    s = st.session_state
    return Inputs(
        consumption_mode="bill" if s.mode.startswith("Monthly bill") else "units",
        monthly_bill=float(s.bill), monthly_units=float(s.units),
        category=CAT[s.category], state_category="special" if s.statecat.startswith("Special") else "general",
        tariff=Tariff(slab_limits=limits, slab_rates=rates, fixed_monthly=float(s.fixed),
                      other_monthly=float(s.other_monthly), duty=s.duty / 100),
        system_kw=float(s.kw), gross_cost=float(s.gross_cost), cuf=s.cuf / 100, degradation=s.deg / 100,
        analysis_years=int(s.years),
        subsidy_mode={"PM Surya Ghar (automatic)": "auto", "Enter amount": "manual", "No subsidy": "none"}[s.sub_mode],
        subsidy_manual=float(s.sub_manual), state_topup=float(s.topup),
        subsidy_year=0 if s.sub_year.startswith("Year 0") else 1, other_upfront=float(s.other_upfront),
        tariff_escalation=s.esc / 100, export_rate=float(s.export), export_escalation=s.export_esc / 100,
        gsc_rate=float(s.gsc), om_cost=float(s.om), om_escalation=s.om_esc / 100,
        insurance=float(s.ins), insurance_escalation=s.ins_esc / 100,
        inverter_cost=float(s.inv_cost), inverter_life=int(s.inv_life), inverter_escalation=s.inv_esc / 100,
        credit_unused_inverter=bool(s.inv_credit), salvage_value=float(s.salvage),
        discount_rate=s.disc / 100, loan_enabled=bool(s.loan), loan_share=s.loan_share / 100,
        loan_rate=s.loan_rate / 100, loan_years=int(s.loan_years),
        emission_factor=float(s.ef), carbon_price=float(s.cprice),
        roof_area_sqft=float(s.roof), area_per_kw=float(s.area_kw), panel_wp=float(s.panel_wp),
        sanctioned_load_kw=float(s.load),
    )


# ================================================================== HEADER
st.markdown("## ☀️ Rooftop Solar vs Grid — Life Cycle Cost Calculator")

if tariff_error:
    st.warning(f"Tariff table not used ({tariff_error}) — using MSEDCL FY2025-26 slabs.")

# ================================================================== HERO CARD (reference design)
with st.container(key="hero"):
    head_ph = st.container()
    kpi_ph = st.container()
    view = st.radio("View", ["Today's money (discounted)", "Plain rupees (not discounted)"], horizontal=True,
                    label_visibility="collapsed", key="view")
    chart_ph = st.container()
    st.radio("I know my", ["Monthly bill (₹)", "Monthly units (kWh)"], horizontal=True, key="mode")
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.session_state.mode.startswith("Monthly bill"):
            st.slider("Current monthly bill (₹/month)", 200, 30000, step=50, key="bill", format="₹%d")
        else:
            st.slider("Monthly consumption (units/month)", 0, 3000, step=10, key="units", format="%d units")
    with c2:
        st.slider("Solar system cost, gross (₹)", 0, 2_000_000, step=5000, key="gross_cost", format="₹%d",
                  help="Before subsidy, incl. GST. Team base ₹3,00,000 for 5 kW (R11–R16).")
    with c3:
        st.slider("Annual grid rate inflation (%)", 0.0, 12.0, step=0.5, key="esc", format="%.1f%%",
                  help="Team base 5% (CEEW 5%, PFC 5.85%); range 3–7%.")

inp = build_inputs()
problems = inp.check()
if problems:
    st.error("Please fix: " + " ".join(problems))
    st.stop()
res = run(inp)
df = res.table
sz = sizing(inp)
disc = view.startswith("Today")
N = inp.analysis_years

tot_grid = res.pw_grid if disc else res.total_grid_nominal
tot_solar = res.pw_solar if disc else res.total_solar_nominal
saving = tot_grid - tot_solar
pb = res.discounted_payback if disc else res.simple_payback

with head_ph:
    st.markdown(f"<p class='hero-sub' style='text-align:center;color:#bdc1c6;font-size:1.05rem;margin:0'>{N}-year lifecycle cost comparison · "
                f"{'in today’s money at ' + format(inp.discount_rate, '.1%') if disc else 'plain rupees, not discounted'}</p>",
                unsafe_allow_html=True)
    if saving >= 0:
        st.markdown(f"<p class='hero-head' style='text-align:center;color:#fff;font-size:2rem;font-weight:600;margin:6px 0 10px 0'>Solar power saves <span class='pill pill-good'>{lakh(saving)}</span></p>",
                    unsafe_allow_html=True)
    else:
        st.markdown(f"<p class='hero-head' style='text-align:center;color:#fff;font-size:2rem;font-weight:600;margin:6px 0 10px 0'>Solar costs <span class='pill pill-bad'>{lakh(-saving)}</span> more</p>",
                    unsafe_allow_html=True)
with kpi_ph:
    k1, k2, k3 = st.columns(3)
    k1.metric("Total grid cost", lakh(tot_grid))
    k2.metric("Total solar cost", lakh(tot_solar))
    k3.metric("Solar payback", "—" if pb is None else f"{pb:.1f} years")
with chart_ph:
    yg = (df.cum_pv_grid if disc else df.cum_grid) / 1e5
    ys = (df.cum_pv_solar if disc else df.cum_solar) / 1e5
    fig = go.Figure()
    fig.add_scatter(x=df.year, y=yg, name="Electricity grid", mode="lines", line=dict(color=GRID, width=3),
                    customdata=[inr(v * 1e5) for v in yg], hovertemplate="Year %{x}: %{customdata}<extra>Grid only</extra>")
    fig.add_scatter(x=df.year, y=ys, name="Solar power + grid", mode="lines", line=dict(color=SOLAR, width=3),
                    customdata=[inr(v * 1e5) for v in ys], hovertemplate="Year %{x}: %{customdata}<extra>Solar + grid</extra>")
    fig.add_scatter(x=[N, N], y=[yg.iloc[-1], ys.iloc[-1]], mode="markers", showlegend=False,
                    marker=dict(color=[GRID, SOLAR], size=9), hoverinfo="skip")
    if pb is not None and 0 < pb < N:
        fig.add_vline(x=pb, line_dash="dot", line_color=MUTED,
                     annotation_text=f"break-even ≈ year {pb:.1f}",
annotation_font_color="#e8eaed")
    fig.update_layout(height=380, margin=dict(l=70, r=20, t=40, b=55), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#e8eaed"),
                      legend=dict(orientation="h", y=1.08, x=0), hovermode="x unified",
                      xaxis=dict(title="Year", gridcolor="#3c4043", dtick=5, automargin=True, zeroline=False),
                      yaxis=dict(title="Cumulative cost (₹ lakh)", gridcolor="#3c4043", tickprefix="₹", ticksuffix=" L",
                                 automargin=True, rangemode="tozero", zeroline=False))
    st.plotly_chart(fig, width="stretch", theme=None, config={"displayModeBar": False})

# ================================================================== VERDICT + METRICS
if res.npv > 0:
    col, title = GOOD, "Rooftop solar is the lower-cost choice"
    body = (f"Over {N} years, the solar option costs <b>{lakh(res.pw_solar)}</b> in today's money against "
            f"<b>{lakh(res.pw_grid)}</b> for staying on the grid — a life-cycle saving (NPV) of <b>{lakh(res.npv)}</b>. "
            f"The <b>{lakh(res.net_cost)}</b> you pay after the subsidy comes back in <b>{years(res.simple_payback)}</b> "
            f"({years(res.discounted_payback)} counting the time value of money).")
else:
    col, title = GRID, "Staying on the grid is the lower-cost choice"
    body = (f"Over {N} years, solar would cost <b>{lakh(-res.npv)}</b> more than grid power in today's money "
            f"(PW {lakh(res.pw_solar)} vs {lakh(res.pw_grid)}).")
st.markdown(f"<div class='verdict' style='border-color:{col}'><div style='font-size:1.rem;font-weight:600;color:{col}'>"
            f"{title}</div><div style='margin-top:4px'>{body}</div></div>", unsafe_allow_html=True)

m = st.columns(6)
m[0].metric("NPV of going solar", lakh(res.npv))
m[1].metric("IRR", "—" if res.irr is None else f"{res.irr:.1%}")
m[2].metric("Net cost after subsidy", lakh(res.net_cost), help=f"Gross {inr(res.gross_cost)} − subsidy {inr(res.subsidy)}"
            + (f" + other {inr(inp.other_upfront)}" if inp.other_upfront else ""))
m[3].metric("Monthly bill with solar", inr(res.bill_y1_solar_month),
            delta=f"-{inr(res.bill_y1_grid_month - res.bill_y1_solar_month)} vs {inr(res.bill_y1_grid_month)}",
            delta_color="inverse", help="Year 1. Fixed charge and duty continue after solar.")
m[4].metric("Solar ₹/unit (LCOE)", "—" if res.lcoe is None else f"₹{res.lcoe:.2f}",
            help=f"vs levelised grid cost ₹{res.levelised_grid_cost:.2f}/unit")
m[5].metric("CO₂ avoided", f"{res.lifetime_co2_t:,.0f} t", help=f"over {N} years")

if inp.category == "commercial" and inp.tariff.slab_rates == MSEDCL_FY26.slab_rates:
    st.warning("Commercial selected: subsidy is set to zero, but the tariff is still the residential MSEDCL slabs. "
               "Enter your commercial tariff under 'Grid tariff & net metering' for a meaningful result.")
if inp.carbon_price > 0:
    st.info(f"NPV includes {lakh(res.pw_carbon)} of carbon value at ₹{inp.carbon_price:,.0f}/tCO₂.")
if inp.consumption_mode == "bill" and res.units_per_month == 0:
    st.warning("That bill is below the fixed charge + duty, so consumption is taken as zero.")

tabs = st.tabs(["🏠 Your system", "🧾 Bill & savings", "📊 Cash flows", "🎯 Sensitivity", "✅ Validation", "📘 Method & sources"])

# ------------------------------------------------------------------ tab 1: system
with tabs[0]:
    a, b = st.columns([1, 1.4])
    with a:
        st.markdown("#### System sizing")
        st.markdown(
            f"- Monthly consumption: **{res.units_per_month:,.0f} units** "
            f"({'worked back from your ₹' + indian(inp.monthly_bill) + ' bill on the slabs' if inp.consumption_mode == 'bill' else 'entered'})\n"
            f"- One kW here makes **{sz['yield_per_kw']:,.0f} units a year** ({sz['units_per_day_per_kw']:.2f} a day)\n"
            f"- Size that matches your use: **{sz['size_for_consumption']:.1f} kW**\n"
            f"- Largest size your roof allows: **{sz['max_by_roof']:.1f} kW**; sanctioned load: **{inp.sanctioned_load_kw:g} kW**\n"
            f"- **Recommended: {sz['recommended_kw']:g} kW** · you chose **{inp.system_kw:g} kW**")
        st.button(f"Use recommended size ({sz['recommended_kw']:g} kW)", on_click=set_size, args=(sz["recommended_kw"],),
                  disabled=abs(sz["recommended_kw"] - inp.system_kw) < 1e-9)
        st.markdown(f"- Panels: **{sz['panels']} × {inp.panel_wp:.0f} Wp** = {sz['panel_kwp']:.2f} kWp\n"
                    f"- Roof needed: **{sz['roof_needed']:,.0f} sq ft** of {inp.roof_area_sqft:,.0f}\n"
                    f"- Cost per kW: **{inr(inp.cost_per_kw)}** (team market range ₹40,000–76,000)\n"
                    f"- Subsidy: **{inr(res.subsidy)}**")
        if not sz["roof_ok"]:
            st.warning("The roof is too small for this size.")
        if not sz["load_ok"]:
            st.warning("Size is above the sanctioned load; net metering is normally limited to it.")
        if sz["coverage"] > 1.15:
            st.info(f"Year-1 generation is {sz['coverage']:.0%} of your use. Units above your use earn only "
                    f"₹{inp.export_rate:.2f}, so the system may be larger than you need — compare sizes in Sensitivity.")
        if res.loan_principal > 0:
            st.markdown("#### Loan")
            st.markdown(f"Loan **{inr(res.loan_principal)}** at {inp.loan_rate:.2%} for {inp.loan_years} years → "
                        f"EMI **{inr(res.loan_emi)}/month**, against a year-1 bill saving of "
                        f"**{inr(res.bill_y1_grid_month - res.bill_y1_solar_month)}/month**. "
                        f"You pay {inr(res.net_cost - res.loan_principal)} upfront.")
    with b:
        st.markdown("#### Generation vs consumption")
        yrs = df[df.year > 0]
        f2 = go.Figure()
        f2.add_bar(x=yrs.year, y=yrs.self_used_kwh, name="Used in the house", marker_color=SOLAR)
        f2.add_bar(x=yrs.year, y=yrs.export_kwh, name=f"Exported at ₹{inp.export_rate:.2f}", marker_color="#93c5fd")
        f2.add_scatter(x=yrs.year, y=yrs.consumption_kwh, name="Consumption", mode="lines", line=dict(color=GRID, dash="dot"))
        f2.update_layout(barmode="stack", height=380, margin=dict(l=10, r=10, t=10, b=10), xaxis_title="Year",
                         yaxis_title="kWh per year", legend=dict(orientation="h", y=-0.2))
        st.plotly_chart(f2, width="stretch")
        st.caption(f"Year-1 generation {df.generation_kwh.iloc[1]:,.0f} kWh, falling {inp.degradation:.1%} a year to "
                   f"{df.generation_kwh.iloc[-1]:,.0f} kWh in year {N}.")

# ------------------------------------------------------------------ tab 2: bill
with tabs[1]:
    st.markdown("#### Year-1 monthly bill, without and with solar")
    q_with = df.import_kwh.iloc[1] / 12
    bcols = st.columns(2)
    for colx, units_x, label in [(bcols[0], res.units_per_month, "Without solar"), (bcols[1], q_with, "With solar (net units)")]:
        with colx:
            bd = slab_breakdown(units_x, inp.tariff)
            energy = bd.charge.sum()
            sub = energy + inp.tariff.fixed_monthly + inp.tariff.other_monthly
            st.markdown(f"**{label}: {units_x:,.1f} units**")
            show = bd.rename(columns={"slab": "Slab", "units": "Units", "rate": "₹/kWh", "charge": "Charge ₹"})
            st.dataframe(show.style.format({"Units": "{:,.1f}", "₹/kWh": "{:.2f}", "Charge ₹": "{:,.2f}"}),
                         hide_index=True, width="stretch")
            st.markdown(f"Energy + wheeling {inr(energy)} · fixed {inr(inp.tariff.fixed_monthly)}"
                        f"{' · other ' + inr(inp.tariff.other_monthly) if inp.tariff.other_monthly else ''} · "
                        f"duty {inp.tariff.duty:.0%} {inr(sub * inp.tariff.duty)} → **{inr(sub * (1 + inp.tariff.duty))}/month**")
    y1 = df.iloc[1]
    st.markdown("#### Year-1 benefit")
    bc = st.columns(4)
    bc[0].metric("Avoided bill", inr(y1.grid_cost - y1.solar_bill))
    bc[1].metric("Export credit", inr(y1.export_credit), help=f"{y1.export_kwh:,.0f} surplus units × ₹{inp.export_rate:.2f}")
    bc[2].metric("Running costs", inr(-(y1.om + y1.insurance + y1.gsc)))
    bc[3].metric("Net year-1 benefit", inr(y1.grid_cost - y1.solar_bill + y1.export_credit - y1.om - y1.insurance - y1.gsc))
    naive = y1.generation_kwh * res.bill_y1_grid_month * 12 / max(1e-9, y1.consumption_kwh)
    st.caption(f"A quick 'units × average tariff' estimate would claim {inr(naive)} — too high, because surplus units "
               f"earn only the export rate and fixed charges continue (team Net_Metering sheet).")

    st.markdown("#### What each option costs over its life (today's money)")
    f3 = go.Figure()
    palette = {"Electricity bills": GRID, "Upfront cost after subsidy": SOLAR, "Remaining grid bills": "#fca5a5",
               "O&M": "#60a5fa", "Insurance": "#93c5fd", "Inverter replacement": "#1d4ed8", "Loan repayments": "#6366f1",
               "Grid support charge": "#f59e0b", "Export credit": GOOD, "End-of-life credit": "#86efac"}
    shown = set()
    for alt, parts in res.pw_parts.items():
        for name, val in parts.items():
            if abs(val) < 0.5:
                continue
            f3.add_bar(name=name, x=[alt], y=[val / 1e5], marker_color=palette.get(name, MUTED),
                       showlegend=name not in shown, customdata=[inr(val)],
                       hovertemplate=f"{name}: %{{customdata}}<extra>{alt}</extra>")
            shown.add(name)
    f3.update_layout(barmode="relative", height=400, margin=dict(l=10, r=10, t=10, b=10),
                     yaxis=dict(title="Present worth (₹ lakh)", tickprefix="₹", ticksuffix=" L"),
                     legend=dict(orientation="h", y=-0.15))
    st.plotly_chart(f3, width="stretch")

# ------------------------------------------------------------------ tab 3: cash flows
with tabs[2]:
    st.markdown("#### Cost each year (plain rupees)")
    yrs = df[df.year > 0]
    f4 = go.Figure()
    f4.add_bar(x=yrs.year, y=yrs.grid_cost, name="Grid only", marker_color=GRID)
    f4.add_bar(x=yrs.year, y=yrs.solar_cost, name="Solar + grid", marker_color=SOLAR)
    f4.update_layout(barmode="group", height=360, margin=dict(l=10, r=10, t=10, b=10), xaxis_title="Year",
                     yaxis_title="₹ per year", legend=dict(orientation="h", y=-0.2))
    st.plotly_chart(f4, width="stretch")
    st.caption(f"Year 0: solar pays {inr(df.solar_cost.iloc[0])}. Spikes in the blue bars are inverter replacements.")
    cols = {"year": "Year", "generation_kwh": "Generation kWh", "import_kwh": "Bought kWh", "export_kwh": "Exported kWh",
            "grid_cost": "Grid-only cost ₹", "solar_bill": "Bill with solar ₹", "export_credit": "Export credit ₹",
            "om": "O&M ₹", "insurance": "Insurance ₹", "inverter": "Inverter ₹", "loan": "Loan ₹",
            "solar_cost": "Solar+grid cost ₹", "saving": "Saving ₹", "discount_factor": "Discount factor",
            "pv_saving": "PW of saving ₹", "cum_pv_saving": "Cumulative PW ₹"}
    show = df[list(cols)].rename(columns=cols)
    fmt = {c: "{:,.0f}" for c in show.columns if c not in ("Year", "Discount factor")}
    fmt["Discount factor"] = "{:.5f}"
    st.dataframe(show.style.format(fmt), hide_index=True, width="stretch", height=460)

    def excel_bytes() -> bytes:
        buf = BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            d = asdict(inp)
            d.pop("tariff")
            d.update({"tariff_slab_limits": str(inp.tariff.slab_limits), "tariff_slab_rates": str(inp.tariff.slab_rates),
                      "fixed_monthly": inp.tariff.fixed_monthly, "other_monthly": inp.tariff.other_monthly,
                      "duty": inp.tariff.duty, "units_per_month_used": res.units_per_month})
            pd.DataFrame(list(d.items()), columns=["Input", "Value"]).to_excel(xw, sheet_name="Inputs", index=False)
            pd.DataFrame(list(summary(res).items()), columns=["Result", "Value"]).to_excel(xw, sheet_name="Results", index=False)
            df.to_excel(xw, sheet_name="Cash flows", index=False)
            tornado(inp).drop(columns="fmt").to_excel(xw, sheet_name="Sensitivity", index=False)
            cached_checks().to_excel(xw, sheet_name="Validation", index=False)
        return buf.getvalue()

    st.download_button("⬇ Download everything (Excel)", excel_bytes(), "solar_lcca_results.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ------------------------------------------------------------------ tab 4: sensitivity
with tabs[3]:
    st.markdown("#### Which assumptions move the answer? (low / high values from the team data workbook)")
    tor = tornado(inp)
    f5 = go.Figure()
    base = res.npv
    for _, row in tor.iterrows():
        lo_l, hi_l = row.fmt.format(row.low), row.fmt.format(row.high)
        f5.add_bar(y=[row.parameter], x=[(row.npv_low - base) / 1e5], base=base / 1e5, orientation="h",
                   marker_color="#94a3b8", showlegend=False, text=[lo_l], textposition="auto",
                   hovertemplate=f"{row.parameter} = {lo_l}: NPV {inr(row.npv_low)}<extra></extra>")
        f5.add_bar(y=[row.parameter], x=[(row.npv_high - base) / 1e5], base=base / 1e5, orientation="h",
                   marker_color=SOLAR, showlegend=False, text=[hi_l], textposition="auto",
                   hovertemplate=f"{row.parameter} = {hi_l}: NPV {inr(row.npv_high)}<extra></extra>")
    f5.add_vline(x=base / 1e5, line_color="#111827", annotation_text=f"base {lakh(base)}", annotation_position="top")
    f5.add_vline(x=0, line_color=GRID, line_dash="dot")
    f5.update_layout(barmode="overlay", height=560, margin=dict(l=10, r=10, t=20, b=10),
                     xaxis=dict(title="NPV of going solar (₹ lakh)", tickprefix="₹", ticksuffix=" L"))
    st.plotly_chart(f5, width="stretch")
    st.caption("Grey = input at its low value; blue = at its high value. Red dotted line = NPV of zero (decision flips).")

    a, b = st.columns(2)
    with a:
        st.markdown("#### NPV vs discount rate")
        nr = npv_vs_rate(inp)
        f6 = go.Figure()
        f6.add_scatter(x=nr.rate * 100, y=nr.npv / 1e5, mode="lines", line=dict(color=SOLAR, width=3),
                       hovertemplate="%{x:.0f}%: ₹%{y:.2f} L<extra></extra>")
        f6.add_hline(y=0, line_color=MUTED)
        f6.add_vline(x=inp.discount_rate * 100, line_dash="dash", annotation_text="chosen rate")
        if res.irr is not None and res.irr <= 0.30:
            f6.add_vline(x=res.irr * 100, line_dash="dot", line_color=GOOD, annotation_text=f"IRR {res.irr:.1%}")
        f6.update_layout(height=330, margin=dict(l=10, r=10, t=20, b=10), xaxis_title="Discount rate (%)",
                         yaxis=dict(title="NPV (₹ lakh)", tickprefix="₹", ticksuffix=" L"))
        st.plotly_chart(f6, width="stretch")
    with b:
        st.markdown("#### Break-even values (NPV = 0)")
        rows = []
        be_c = breakeven(inp, "gross_cost", 0, 50_000_000)
        rows.append(("Gross system cost", inr(be_c) if be_c else "beyond ₹5 crore", inr(inp.gross_cost)))
        be_cuf = breakeven(inp, "cuf", 0.0001, 0.6)
        rows.append(("CUF", f"{be_cuf:.1%}" if be_cuf else "outside 0–60%", f"{inp.cuf:.1%}"))
        be_e = breakeven(inp, "tariff_escalation", -0.5, 0.5)
        rows.append(("Grid tariff escalation", f"{be_e:.1%}" if be_e is not None else "outside ±50%", f"{inp.tariff_escalation:.1%}"))
        if inp.consumption_mode == "bill":
            be_b = breakeven(inp, "monthly_bill", 0, 1_000_000)
            rows.append(("Monthly bill", inr(be_b) if be_b else "outside range", inr(inp.monthly_bill)))
        else:
            be_u = breakeven(inp, "monthly_units", 0, 100_000)
            rows.append(("Monthly units", f"{be_u:,.0f}" if be_u else "outside range", f"{inp.monthly_units:,.0f}"))
        st.dataframe(pd.DataFrame(rows, columns=["Input", "Break-even value", "Your value"]), hide_index=True,
                     width="stretch")
        st.caption("How far an input can move before solar stops paying.")

    st.markdown("#### Compare system sizes (cost and inverter price scale with kW; subsidy follows its slabs)")
    sw = size_sweep(inp)
    f7 = go.Figure()
    f7.add_bar(x=[f"{k:g} kW" for k in sw.kw], y=sw.npv / 1e5,
               marker_color=[SOLAR if abs(k - inp.system_kw) < 1e-9 else "#94a3b8" for k in sw.kw],
               customdata=[inr(v) for v in sw.npv], hovertemplate="%{x}: NPV %{customdata}<extra></extra>")
    f7.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10),
                     yaxis=dict(title="NPV (₹ lakh)", tickprefix="₹", ticksuffix=" L"))
    st.plotly_chart(f7, width="stretch")
    st.dataframe(sw.assign(irr=sw.irr.map(lambda v: "—" if v is None or pd.isna(v) else f"{v:.1%}"),
                           payback=sw.payback.map(lambda v: "—" if v is None or pd.isna(v) else f"{v:.1f} yr"),
                           export_share=sw.export_share.map("{:.0%}".format),
                           net_cost=sw.net_cost.map(inr), subsidy=sw.subsidy.map(inr), npv=sw.npv.map(inr))
                 .rename(columns={"kw": "Size kW", "net_cost": "Net cost", "subsidy": "Subsidy", "npv": "NPV",
                                  "irr": "IRR", "payback": "Payback", "export_share": "Share exported"}),
                 hide_index=True, width="stretch")
    st.caption("The highest IRR and the highest NPV can be at different sizes: NPV is the rule for choosing between sizes.")

# ------------------------------------------------------------------ tab 5: validation
with tabs[4]:
    chk = cached_checks()
    n_pass = int((chk.result == "PASS").sum())
    st.markdown(f"#### Tool vs the team's manual benchmark: **{n_pass} / {len(chk)} PASS**")
    st.caption("Expected values are typed from the team's files and never from the tool: [VB] Validation Engineer's "
               "Manual Benchmark (13 slab-boundary bills); [DW] Data Lead's workbook tables. These checks run on fixed "
               "team cases, so they do not change with the sidebar.")
    st.dataframe(chk.assign(expected=chk.expected.map("{:,.2f}".format), tool=chk.tool.map("{:,.2f}".format),
                            difference=chk.difference.map("{:+,.3f}".format), tolerance=chk.tolerance.map("±{:g}".format))
                 .rename(columns={"check": "Check", "source": "Team file", "expected": "Expected", "tool": "Tool",
                                  "difference": "Difference", "tolerance": "Tolerance", "result": "Result"}),
                 hide_index=True, width="stretch", height=560)
    st.markdown("**Still to do by hand (Topic 6 validation):** hand-calculate the present worth of both alternatives for "
                "the base case and compare with *PW grid only* and *PW solar + grid* shown above.")

# ------------------------------------------------------------------ tab 6: method
with tabs[5]:
    st.markdown(r"""
#### Method
Two alternatives over a study period of $N$ years at discount rate $r$:

* **A — Grid only:** $\text{cost}_t = 12 \cdot B(Q)\,(1+e)^{t-1}$, where $B(q)$ is the monthly bill for $q$ units:
  telescopic slabs (energy + wheeling) + fixed charge, all × (1 + duty).
* **B — Rooftop solar + grid:** year 0: gross cost + other upfront − subsidy (− loan).
  Year $t$: $12 \cdot B\!\left(\max(0, Q - G_t/12)\right)(1+e)^{t-1}$ − export credit + O&M + insurance + inverter + loan − end credits.

Generation $G_t = kW \times 8760 \times CUF \times (1-d)^{t-1}$ · surplus $=\max(0, G_t - 12Q)$ paid at the export rate.

**PW** $=\sum_t \text{cost}_t/(1+r)^t$ · **NPV** $= PW_A - PW_B$ (positive → solar) · **IRR**: rate at which NPV = 0 ·
**payback**: year the cumulative saving turns positive (interpolated) · **LCOE** = PW of solar-side costs ÷ PW of generation ·
**EUAC** = PW × $\frac{r(1+r)^N}{(1+r)^N-1}$.

#### Simplifications
* Monthly-average net metering with year-end surplus settlement; no monsoon profile or solar-hour banking restriction (R03).
* Tariff slabs escalate together at one rate; FAC excluded (varies monthly).
* No battery, shading model or tax depreciation; commercial users must enter their own tariff.
""")
    st.markdown("#### Base-case data (team workbook, Base_Inputs)")
    st.dataframe(pd.DataFrame([
        ("Annual grid bill, year 1", "₹60,000 (≈400 units/month)", "Team, R01"),
        ("MSEDCL slabs FY2025-26", "₹5.67 / 10.88 / 14.07 / 15.57 per kWh; fixed ₹130; duty 16%", "R01, R32"),
        ("Tariff escalation", "5% (3–7%)", "R29, R30, R31, R54"),
        ("System size and gross cost", "5 kW, ₹3,00,000 (₹2.5–3.5 lakh)", "R11–R16"),
        ("PM Surya Ghar subsidy", "₹78,000", "R08, R09"),
        ("CUF", "19% (16–19%)", "R20, R23"),
        ("Degradation", "0.5%/yr (0.5–1.0%)", "R17, R18, R19"),
        ("O&M", "₹3,000/yr at 3% (₹3,000–8,000)", "R20, R27, R21"),
        ("Inverter", "₹40,000 in years 10 and 20", "R24, R25, R40, R41"),
        ("Insurance", "₹1,000/yr at 3%", "R26, R27"),
        ("Export rate", "₹2.82/kWh (₹2.24–3.50)", "R06, R33, R35"),
        ("Discount rate", "8% (7–10%) — team decision", "Team"),
        ("Grid emission factor", "0.727 tCO₂/MWh", "CEA CO₂ Baseline Database FY2023-24"),
    ], columns=["Input", "Value", "Ref"]), hide_index=True, width="stretch")
    st.markdown("""
#### Key references (full list in the team workbook, References sheet)
- **R01** MERC press note, MSEDCL MYT order FY2025-26 to FY2029-30 — merc.gov.in
- **R02** MERC order, Case 75 of 2025 (25 Mar 2026) — mahadiscom.in
- **R06 / R33** MERC surplus rooftop tariff ₹2.82/kWh — Energetica India; Mercom
- **R08 / R09** PM Surya Ghar subsidy slabs — pv magazine India; QuickEstimate
- **R11–R16** 2026 market quotes for 5 kW systems — QuickEstimate, SolarSquare, r-solar, SurgePV, Bajaj Finserv
- **R17–R19** PV degradation — Jordan & Kurtz (NREL); Straub-Mück et al. meta-analysis
- **R20** MERC rooftop CUF 19% and O&M ₹600/kW — Renewable Watch
- **R24, R25, R40, R41** inverter prices and life — SolarSquare, Eastman, Qbits, Luminous
- **R26, R27** insurance and maintenance — Qbits, Bridgeway Power
""")
