"""
lcca_engine.py  -  all the calculations for the Rooftop Solar vs Grid LCCA tool.
CE725 Construction Economics and Finance, team course project (Topic 6).

No screen code lives here, so the same functions are used by the web app (app.py),
the automatic tests (test_lcca_engine.py) and the validation checks (benchmarks.py).

Base case = the team's data workbook "CE725_Solar_vs_Grid_Data", sheet Base_Inputs
(Maharashtra, MSEDCL LT-I residential, 5 kW, compiled 03-Nov-2026).

CONVENTIONS (identical to the team's data workbook)
----------------------------------------------------
1. Year 0 = installation. Gross cost and other upfront costs are paid in year 0;
   the subsidy is received in year 0 by default (option: year 1).
2. Every other cash flow happens at the END of its year: discount factor = 1/(1+r)^t.
3. Grid bill = telescopic MSEDCL slabs: each block of monthly units at its own rate
   (energy + wheeling), plus fixed charge, plus electricity duty on the subtotal.
4. Everything entered "for year 1" escalates as X * (1+e)^(t-1)
   (tariff incl. fixed charge at the grid escalation rate; O&M and insurance at their own rates).
5. Generation in year t = kW x 8,760 h x CUF x (1 - degradation)^(t-1).
6. Net metering, monthly-average simplification: each month the house imports
   max(0, consumption - generation/12), billed on the slabs; the year's surplus
   max(0, generation - consumption) is paid at the export rate (year-end settlement).
7. Inverter replaced every L years strictly before the last year (L=10 -> years 10 and 20).
8. NPV of going solar = PW(grid only) - PW(solar + grid). Positive -> choose solar.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

import pandas as pd

HOURS_PER_YEAR = 8760


# =============================================================================
# 1. Grid tariff (telescopic slabs)
# =============================================================================
@dataclass
class Tariff:
    """Monthly tariff. slab_limits are cumulative upper limits in units/month;
    slab_rates has one more entry than slab_limits (the last slab is open-ended)."""
    slab_limits: Tuple[float, ...] = (100, 300, 500)
    slab_rates: Tuple[float, ...] = (5.67, 10.88, 14.07, 15.57)   # Rs/kWh, energy + wheeling
    fixed_monthly: float = 130.0                                   # Rs/connection/month
    other_monthly: float = 0.0                                     # meter rent etc. (excluded in team benchmark)
    duty: float = 0.16                                             # on (energy + fixed + other)
    name: str = "MSEDCL LT-I Residential, FY2025-26 (MERC MYT order)"

    def check(self) -> List[str]:
        p = []
        if len(self.slab_rates) != len(self.slab_limits) + 1:
            p.append("Tariff needs one more rate than slab limits.")
        if any(b <= a for a, b in zip((0,) + tuple(self.slab_limits), self.slab_limits)):
            p.append("Slab limits must increase.")
        if any(r < 0 for r in self.slab_rates) or self.fixed_monthly < 0 or self.duty < 0:
            p.append("Tariff values cannot be negative.")
        return p


MSEDCL_FY26 = Tariff()


def energy_charge(units: float, t: Tariff) -> float:
    """Telescopic energy + wheeling charge for one month (Rs)."""
    units = max(0.0, units)
    charge, lower = 0.0, 0.0
    for limit, rate in zip(t.slab_limits, t.slab_rates):
        block = min(units, limit) - lower
        if block <= 0:
            return charge
        charge += block * rate
        lower = limit
    if units > lower:
        charge += (units - lower) * t.slab_rates[-1]
    return charge


def slab_breakdown(units: float, t: Tariff) -> pd.DataFrame:
    """Rows = slabs, as in the team's Grid_Tariff 'Monthly bill calculator'."""
    rows, lower = [], 0.0
    bounds = list(t.slab_limits) + [math.inf]
    for upper, rate in zip(bounds, t.slab_rates):
        block = max(0.0, min(units, upper) - lower)
        label = f"{int(lower) + 1}–{int(upper)}" if upper != math.inf else f"Above {int(lower)}"
        rows.append(dict(slab=label, units=block, rate=rate, charge=block * rate))
        lower = upper
    return pd.DataFrame(rows)


def monthly_bill(units: float, t: Tariff, scale: float = 1.0) -> float:
    """Monthly bill incl. fixed charge and duty. scale = tariff escalation factor for that year."""
    subtotal = energy_charge(units, t) + t.fixed_monthly + t.other_monthly
    return subtotal * (1.0 + t.duty) * scale


def units_from_bill(bill: float, t: Tariff) -> float:
    """Monthly units that produce a given monthly bill (the bill rises with units, so bisection works)."""
    if bill <= monthly_bill(0, t):
        return 0.0
    lo, hi = 0.0, 1000.0
    while monthly_bill(hi, t) < bill:
        hi *= 2
        if hi > 1e8:
            raise ValueError("Bill is too large for this tariff.")
    for _ in range(200):
        mid = (lo + hi) / 2
        if monthly_bill(mid, t) < bill:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# =============================================================================
# 2. Subsidy (PM Surya Ghar: Muft Bijli Yojana) and loan
# =============================================================================
def central_subsidy(kw: float, category: str = "residential", state_category: str = "general") -> float:
    """Central financial assistance (team Subsidy sheet, refs R08, R09, R48).
    residential, general states: Rs 30,000/kW for the first 2 kW + Rs 18,000 for the 3rd kW (max Rs 78,000)
    residential, special-category states: Rs 33,000/kW + Rs 19,800 for the 3rd kW
    housing society / RWA common facilities: Rs 18,000/kW up to 500 kW
    commercial / industrial: nothing."""
    kw = max(0.0, kw)
    if category == "residential":
        a, b = (33000.0, 19800.0) if state_category == "special" else (30000.0, 18000.0)
        return a * min(kw, 2.0) + b * max(0.0, min(kw, 3.0) - 2.0)
    if category == "housing_society":
        return 18000.0 * min(kw, 500.0)
    return 0.0


def emi(principal: float, annual_rate: float, years: int) -> float:
    """Monthly instalment of a reducing-balance loan."""
    n = int(round(years * 12))
    if principal <= 0 or n <= 0:
        return 0.0
    r = annual_rate / 12
    if r == 0:
        return principal / n
    return principal * r * (1 + r) ** n / ((1 + r) ** n - 1)


# =============================================================================
# 3. Inputs (defaults = team Base_Inputs, column B)
# =============================================================================
@dataclass
class Inputs:
    # consumption
    consumption_mode: str = "bill"          # "bill" or "units"
    monthly_bill: float = 5000.0            # Rs/month, year 1 (team: Rs 60,000/yr)
    monthly_units: float = 400.0            # kWh/month (used when mode = "units")
    category: str = "residential"           # residential | housing_society | commercial
    state_category: str = "general"         # general | special (for the subsidy rate)
    tariff: Tariff = field(default_factory=Tariff)
    # system
    system_kw: float = 5.0
    gross_cost: float = 300000.0            # Rs incl. GST, before subsidy
    cuf: float = 0.19                       # MERC rooftop norm
    degradation: float = 0.005
    analysis_years: int = 25
    # subsidy
    subsidy_mode: str = "auto"              # auto | manual | none
    subsidy_manual: float = 0.0
    state_topup: float = 0.0                # Maharashtra: Rs 0
    subsidy_year: int = 0                   # 0 or 1
    other_upfront: float = 0.0              # mounting upgrade, net-meter fees ...
    # grid and export
    tariff_escalation: float = 0.05
    export_rate: float = 2.82
    export_escalation: float = 0.0
    gsc_rate: float = 1.96                  # Rs/kWh of solar generation, LT commercial > 10 kW only
    # running costs
    om_cost: float = 3000.0
    om_escalation: float = 0.03
    insurance: float = 1000.0
    insurance_escalation: float = 0.03
    inverter_cost: float = 40000.0          # today's Rs
    inverter_life: int = 10                 # 0 = never replaced
    inverter_escalation: float = 0.0
    credit_unused_inverter: bool = False
    salvage_value: float = 0.0              # Rs received in the final year
    # finance
    discount_rate: float = 0.08
    loan_enabled: bool = False
    loan_share: float = 0.80                # of net cost (gross - subsidy + other upfront)
    loan_rate: float = 0.07
    loan_years: int = 5
    # carbon
    emission_factor: float = 0.727          # tCO2/MWh (CEA CO2 Baseline Database FY2023-24)
    carbon_price: float = 0.0               # Rs/tCO2; 0 = not added to NPV
    # sizing checks (do not change the cash flows)
    roof_area_sqft: float = 600.0
    area_per_kw: float = 100.0
    panel_wp: float = 545.0
    sanctioned_load_kw: float = 5.0

    def copy(self, **kw) -> "Inputs":
        return replace(self, **kw)

    def check(self) -> List[str]:
        p = list(self.tariff.check())
        if self.system_kw < 0: p.append("System size cannot be negative.")
        if self.gross_cost < 0: p.append("System cost cannot be negative.")
        if not (0 <= self.cuf <= 0.6): p.append("CUF must be between 0% and 60%.")
        if not (0 <= self.degradation < 1): p.append("Degradation must be between 0% and 100%.")
        if not (1 <= self.analysis_years <= 50): p.append("Analysis period must be 1–50 years.")
        if self.discount_rate <= -1: p.append("Discount rate must be above −100%.")
        if self.inverter_life < 0: p.append("Inverter life cannot be negative.")
        if self.consumption_mode == "bill" and self.monthly_bill < 0: p.append("Bill cannot be negative.")
        if self.consumption_mode == "units" and self.monthly_units < 0: p.append("Units cannot be negative.")
        return p

    # ---- derived quantities -------------------------------------------------
    @property
    def units_per_month(self) -> float:
        if self.consumption_mode == "bill":
            return units_from_bill(self.monthly_bill, self.tariff)
        return self.monthly_units

    @property
    def subsidy(self) -> float:
        if self.subsidy_mode == "none":
            s = 0.0
        elif self.subsidy_mode == "manual":
            s = self.subsidy_manual
        else:
            s = central_subsidy(self.system_kw, self.category, self.state_category) + self.state_topup
        return max(0.0, min(s, self.gross_cost))

    @property
    def cost_per_kw(self) -> float:
        return self.gross_cost / self.system_kw if self.system_kw > 0 else 0.0


# =============================================================================
# 4. Sizing (like the reference calculators)
# =============================================================================
def sizing(inp: Inputs) -> Dict[str, float]:
    yield_per_kw = HOURS_PER_YEAR * inp.cuf                       # kWh per kW per year
    annual_units = 12 * inp.units_per_month
    by_consumption = annual_units / yield_per_kw if yield_per_kw > 0 else 0.0
    by_roof = inp.roof_area_sqft / inp.area_per_kw if inp.area_per_kw > 0 else math.inf
    by_load = inp.sanctioned_load_kw if inp.sanctioned_load_kw > 0 else math.inf
    need = round(by_consumption * 2) / 2                              # nearest 0.5 kW that covers the use
    cap = min(by_roof, by_load)                                       # roof and sanctioned-load limits
    cap = math.floor(cap * 2) / 2 if cap != math.inf else math.inf
    rec = min(max(need, 1.0), cap)                                    # 1 kW minimum where the roof allows
    panels = math.ceil(inp.system_kw * 1000 / inp.panel_wp) if inp.panel_wp > 0 and inp.system_kw > 0 else 0
    return dict(
        yield_per_kw=yield_per_kw, units_per_day_per_kw=yield_per_kw / 365,
        annual_units=annual_units, size_for_consumption=by_consumption,
        max_by_roof=by_roof, max_by_load=by_load, recommended_kw=rec,
        panels=panels, panel_kwp=panels * inp.panel_wp / 1000,
        roof_needed=inp.system_kw * inp.area_per_kw,
        roof_ok=inp.system_kw * inp.area_per_kw <= inp.roof_area_sqft + 1e-9,
        load_ok=inp.system_kw <= by_load + 1e-9,
        coverage=(inp.system_kw * yield_per_kw / annual_units) if annual_units > 0 else math.inf,
    )


# =============================================================================
# 5. The life-cycle model
# =============================================================================
@dataclass
class Results:
    inputs: Inputs
    table: pd.DataFrame
    units_per_month: float
    gross_cost: float
    subsidy: float
    net_cost: float
    loan_principal: float
    loan_emi: float
    pw_grid: float
    pw_solar: float
    pw_carbon: float
    npv: float
    irr: Optional[float]
    simple_payback: Optional[float]
    discounted_payback: Optional[float]
    total_grid_nominal: float
    total_solar_nominal: float
    lcoe: Optional[float]
    levelised_grid_cost: float
    euac_grid: float
    euac_solar: float
    bill_y1_grid_month: float
    bill_y1_solar_month: float
    lifetime_generation: float
    lifetime_co2_t: float
    pw_parts: Dict[str, Dict[str, float]]

    @property
    def nominal_saving(self) -> float:
        return self.total_grid_nominal - self.total_solar_nominal

    @property
    def decision(self) -> str:
        return "solar" if self.npv > 0 else ("grid" if self.npv < 0 else "equal")


def crf(r: float, n: int) -> float:
    if n <= 0:
        return float("nan")
    if abs(r) < 1e-12:
        return 1.0 / n
    return r * (1 + r) ** n / ((1 + r) ** n - 1)


def irr(flows: List[float], lo: float = -0.99, hi: float = 10.0) -> Optional[float]:
    """Rate at which the NPV of the flows is zero (bisection). None if no sign change."""
    f = lambda rate: sum(cf / (1 + rate) ** t for t, cf in enumerate(flows))
    f_lo, f_hi = f(lo), f(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(300):
        mid = (lo + hi) / 2
        f_mid = f(mid)
        if f_lo * f_mid <= 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def payback(cumulative: List[float]) -> Optional[float]:
    """First year a cumulative series (starting below zero) reaches zero, interpolated."""
    if cumulative[0] >= 0:
        return 0.0
    for t in range(1, len(cumulative)):
        if cumulative[t] >= 0 > cumulative[t - 1]:
            return (t - 1) + (-cumulative[t - 1]) / (cumulative[t] - cumulative[t - 1])
    return None


def run(inp: Inputs) -> Results:
    problems = inp.check()
    if problems:
        raise ValueError(" ".join(problems))

    N, r, T = inp.analysis_years, inp.discount_rate, inp.tariff
    kw = inp.system_kw
    q_m = inp.units_per_month
    q_y = 12 * q_m
    S = inp.subsidy
    C = inp.gross_cost
    net = C + inp.other_upfront - S
    has_solar = kw > 0
    gsc_applies = inp.category == "commercial" and kw > 10

    principal = max(0.0, inp.loan_share * net) if (inp.loan_enabled and has_solar) else 0.0
    m_emi = emi(principal, inp.loan_rate, inp.loan_years)

    year0_solar = C + inp.other_upfront - (S if inp.subsidy_year == 0 else 0.0) - principal
    rows = [dict(year=0, tariff_factor=1.0, generation_kwh=0.0, consumption_kwh=0.0, self_used_kwh=0.0,
                 import_kwh=0.0, export_kwh=0.0, grid_cost=0.0, solar_bill=0.0, export_credit=0.0,
                 om=0.0, insurance=0.0, inverter=0.0, gsc=0.0, loan=0.0, subsidy=-(S if inp.subsidy_year == 0 else 0.0),
                 upfront=C + inp.other_upfront - principal, residual=0.0, solar_cost=year0_solar,
                 co2_t=0.0, carbon_value=0.0)]

    last_inv_year, last_inv_cost = None, 0.0
    for t in range(1, N + 1):
        f = (1 + inp.tariff_escalation) ** (t - 1)
        G = kw * HOURS_PER_YEAR * inp.cuf * (1 - inp.degradation) ** (t - 1)
        net_month = max(0.0, q_m - G / 12)
        grid_cost = 12 * monthly_bill(q_m, T, f)
        solar_bill = 12 * monthly_bill(net_month, T, f)
        surplus = max(0.0, G - q_y)
        export_credit = surplus * inp.export_rate * (1 + inp.export_escalation) ** (t - 1)
        om = inp.om_cost * (1 + inp.om_escalation) ** (t - 1) if has_solar else 0.0
        ins = inp.insurance * (1 + inp.insurance_escalation) ** (t - 1) if has_solar else 0.0
        inv = 0.0
        if has_solar and inp.inverter_life and t % inp.inverter_life == 0 and t < N:
            inv = inp.inverter_cost * (1 + inp.inverter_escalation) ** (t - 1)
            last_inv_year, last_inv_cost = t, inv
        gsc = G * inp.gsc_rate * f if gsc_applies else 0.0
        loan = 12 * m_emi if (principal > 0 and t <= inp.loan_years) else 0.0
        sub = -S if (inp.subsidy_year == 1 and t == 1) else 0.0
        residual = 0.0
        if t == N and has_solar:
            residual = inp.salvage_value
            if inp.credit_unused_inverter and last_inv_year is not None:
                residual += last_inv_cost * max(0.0, (inp.inverter_life - (N - last_inv_year)) / inp.inverter_life)
        solar_cost = solar_bill - export_credit + om + ins + inv + gsc + loan + sub - residual
        co2 = G / 1000 * inp.emission_factor
        rows.append(dict(year=t, tariff_factor=f, generation_kwh=G, consumption_kwh=q_y,
                         self_used_kwh=min(G, q_y), import_kwh=12 * net_month, export_kwh=surplus,
                         grid_cost=grid_cost, solar_bill=solar_bill, export_credit=export_credit,
                         om=om, insurance=ins, inverter=inv, gsc=gsc, loan=loan, subsidy=sub,
                         upfront=0.0, residual=residual, solar_cost=solar_cost,
                         co2_t=co2, carbon_value=co2 * inp.carbon_price))

    df = pd.DataFrame(rows)
    df["discount_factor"] = 1 / (1 + r) ** df.year
    df["saving"] = df.grid_cost - df.solar_cost + df.carbon_value
    df["pv_grid"] = df.grid_cost * df.discount_factor
    df["pv_solar"] = df.solar_cost * df.discount_factor
    df["pv_saving"] = df.saving * df.discount_factor
    df["cum_grid"] = df.grid_cost.cumsum()
    df["cum_solar"] = df.solar_cost.cumsum()
    df["cum_pv_grid"] = df.pv_grid.cumsum()
    df["cum_pv_solar"] = df.pv_solar.cumsum()
    df["cum_saving"] = df.saving.cumsum()
    df["cum_pv_saving"] = df.pv_saving.cumsum()

    pw_grid, pw_solar = df.pv_grid.sum(), df.pv_solar.sum()
    pw_carbon = (df.carbon_value * df.discount_factor).sum()
    npv = pw_grid - pw_solar + pw_carbon
    flows = list(df.saving)
    the_irr = irr(flows) if flows[0] < 0 else None

    dfac = df.discount_factor
    pw_gen = (df.generation_kwh * dfac).sum()
    own_costs = df.upfront + df.subsidy + df.om + df.insurance + df.inverter + df.gsc + df.loan - df.residual
    lcoe = (own_costs * dfac).sum() / pw_gen if pw_gen > 0 else None
    pw_cons = (df.consumption_kwh * dfac).sum()

    parts = {
        "Grid only": {"Electricity bills": pw_grid},
        "Solar + grid": {
            "Upfront cost after subsidy": ((df.upfront + df.subsidy) * dfac).sum(),
            "Remaining grid bills": (df.solar_bill * dfac).sum(),
            "O&M": (df.om * dfac).sum(),
            "Insurance": (df.insurance * dfac).sum(),
            "Inverter replacement": (df.inverter * dfac).sum(),
            "Loan repayments": (df.loan * dfac).sum(),
            "Grid support charge": (df.gsc * dfac).sum(),
            "Export credit": -(df.export_credit * dfac).sum(),
            "End-of-life credit": -(df.residual * dfac).sum(),
        },
    }

    return Results(
        inputs=inp, table=df, units_per_month=q_m, gross_cost=C, subsidy=S,
        net_cost=C + inp.other_upfront - S, loan_principal=principal, loan_emi=m_emi,
        pw_grid=pw_grid, pw_solar=pw_solar, pw_carbon=pw_carbon, npv=npv, irr=the_irr,
        simple_payback=payback(list(df.cum_saving)),
        discounted_payback=payback(list(df.cum_pv_saving)),
        total_grid_nominal=df.grid_cost.sum(), total_solar_nominal=df.solar_cost.sum(),
        lcoe=lcoe, levelised_grid_cost=pw_grid / pw_cons if pw_cons > 0 else 0.0,
        euac_grid=pw_grid * crf(r, N), euac_solar=pw_solar * crf(r, N),
        bill_y1_grid_month=monthly_bill(q_m, T), bill_y1_solar_month=monthly_bill(df.import_kwh.iloc[1] / 12, T) if N >= 1 else 0.0,
        lifetime_generation=df.generation_kwh.sum(), lifetime_co2_t=df.co2_t.sum(), pw_parts=parts,
    )


# =============================================================================
# 6. Sensitivity  (Low / High = team Base_Inputs columns C and D)
# =============================================================================
# field, label, low, high, display format
TEAM_RANGES: List[Tuple[str, str, float, float, str]] = [
    ("monthly_bill", "Monthly bill, year 1 (₹)", 40000 / 12, 80000 / 12, "₹{:,.0f}"),
    ("monthly_units", "Monthly consumption (kWh)", 300, 600, "{:,.0f}"),
    ("tariff_escalation", "Grid tariff escalation", 0.03, 0.07, "{:.1%}"),
    ("system_kw", "System size (kW)", 3, 5, "{:g} kW"),
    ("gross_cost", "Gross upfront cost (₹)", 250000, 350000, "₹{:,.0f}"),
    ("cuf", "Capacity utilisation factor", 0.16, 0.19, "{:.0%}"),
    ("degradation", "Annual degradation", 0.005, 0.010, "{:.1%}"),
    ("om_cost", "O&M, year 1 (₹)", 3000, 8000, "₹{:,.0f}"),
    ("om_escalation", "O&M escalation", 0.03, 0.0384, "{:.2%}"),
    ("inverter_cost", "Inverter replacement (₹)", 30000, 55000, "₹{:,.0f}"),
    ("inverter_life", "Inverter life (years)", 10, 12, "{:.0f} yr"),
    ("insurance", "Insurance, year 1 (₹)", 125, 1500, "₹{:,.0f}"),
    ("fixed_monthly", "Fixed charge (₹/month)", 130, 145, "₹{:,.0f}"),
    ("export_rate", "Export rate (₹/kWh)", 2.24, 3.50, "₹{:.2f}"),
    ("discount_rate", "Discount rate", 0.07, 0.10, "{:.1%}"),
]


def with_value(inp: Inputs, fld: str, value) -> Inputs:
    """Copy of the inputs with one value changed. A size change keeps the cost per kW."""
    if fld == "fixed_monthly":
        return inp.copy(tariff=replace(inp.tariff, fixed_monthly=value))
    if fld == "system_kw":
        per_kw = inp.cost_per_kw
        return inp.copy(system_kw=value, gross_cost=per_kw * value)
    if fld == "inverter_life":
        return inp.copy(inverter_life=int(round(value)))
    return inp.copy(**{fld: value})


def current_value(inp: Inputs, fld: str):
    return inp.tariff.fixed_monthly if fld == "fixed_monthly" else getattr(inp, fld)


def tornado(inp: Inputs, ranges=None) -> pd.DataFrame:
    ranges = ranges or TEAM_RANGES
    base = run(inp).npv
    out = []
    for fld, label, lo, hi, fmt in ranges:
        if fld == "monthly_bill" and inp.consumption_mode != "bill":
            continue
        if fld == "monthly_units" and inp.consumption_mode != "units":
            continue
        b = current_value(inp, fld)
        lo, hi = min(lo, b), max(hi, b)          # always include the current value
        n_lo, n_hi = run(with_value(inp, fld, lo)).npv, run(with_value(inp, fld, hi)).npv
        out.append(dict(field=fld, parameter=label, low=lo, high=hi, base_value=b,
                        npv_low=n_lo, npv_high=n_hi, base_npv=base, swing=abs(n_hi - n_lo), fmt=fmt))
    return pd.DataFrame(out).sort_values("swing").reset_index(drop=True)


def breakeven(inp: Inputs, fld: str, lo: float, hi: float) -> Optional[float]:
    """Value of one input at which NPV = 0 (bisection); None if NPV keeps its sign in [lo, hi]."""
    # Illinois (regula falsi) method: always keeps the root bracketed, needs ~10 runs instead of ~60.
    f = lambda v: run(with_value(inp, fld, v)).npv
    a, b = lo, hi
    fa, fb = f(a), f(b)
    if fa * fb > 0:
        return None
    side = 0
    for _ in range(60):
        c = (a * fb - b * fa) / (fb - fa) if fb != fa else (a + b) / 2
        fc = f(c)
        if abs(fc) < 0.01 or abs(b - a) < 1e-9 * max(1.0, abs(c)):
            return c
        if fc * fb > 0:
            b, fb = c, fc
            if side == -1:
                fa /= 2
            side = -1
        else:
            a, fa = c, fc
            if side == 1:
                fb /= 2
            side = 1
    return c


def npv_vs_rate(inp: Inputs, rates=None) -> pd.DataFrame:
    rates = rates if rates is not None else [i / 100 for i in range(0, 31)]
    return pd.DataFrame({"rate": rates, "npv": [run(inp.copy(discount_rate=x)).npv for x in rates]})


def size_sweep(inp: Inputs, sizes=None) -> pd.DataFrame:
    """NPV for other system sizes. Cost and inverter price scale with kW; subsidy follows its rule."""
    sizes = sizes or [1, 2, 3, 4, 5, 6, 7, 8, 10]
    base_kw = inp.system_kw if inp.system_kw > 0 else 1.0
    per_kw, inv_per_kw = inp.gross_cost / base_kw, inp.inverter_cost / base_kw
    rows = []
    for k in sizes:
        res = run(inp.copy(system_kw=k, gross_cost=per_kw * k, inverter_cost=inv_per_kw * k))
        rows.append(dict(kw=k, net_cost=res.net_cost, subsidy=res.subsidy, npv=res.npv, irr=res.irr,
                         payback=res.simple_payback, export_share=res.table.export_kwh.sum() / max(1e-9, res.lifetime_generation)))
    return pd.DataFrame(rows)


def summary(res: Results) -> Dict[str, float]:
    nan = float("nan")
    return {
        "Monthly consumption (kWh)": res.units_per_month,
        "Gross cost (₹)": res.gross_cost,
        "Subsidy (₹)": res.subsidy,
        "Net upfront cost (₹)": res.net_cost,
        "PW grid only (₹)": res.pw_grid,
        "PW solar + grid (₹)": res.pw_solar,
        "NPV of going solar (₹)": res.npv,
        "IRR": res.irr if res.irr is not None else nan,
        "Simple payback (years)": res.simple_payback if res.simple_payback is not None else nan,
        "Discounted payback (years)": res.discounted_payback if res.discounted_payback is not None else nan,
        "Total grid cost, 25 yr nominal (₹)": res.total_grid_nominal,
        "Total solar cost, 25 yr nominal (₹)": res.total_solar_nominal,
        "LCOE of solar (₹/kWh)": res.lcoe if res.lcoe is not None else nan,
        "Levelised grid cost (₹/kWh)": res.levelised_grid_cost,
        "EUAC grid only (₹/yr)": res.euac_grid,
        "EUAC solar + grid (₹/yr)": res.euac_solar,
        "Lifetime generation (kWh)": res.lifetime_generation,
        "Lifetime CO2 avoided (t)": res.lifetime_co2_t,
    }


if __name__ == "__main__":
    res = run(Inputs())
    for k, v in summary(res).items():
        print(f"{k:40s} {v:,.4f}")
