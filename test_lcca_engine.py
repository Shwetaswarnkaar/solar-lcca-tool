"""
Automatic tests. Run:  python -m pytest -q
Each test compares the engine with an answer obtained another way:
the team's manual tables, a closed-form engineering-economics factor, or an identity.
"""
import math
import pytest

from lcca_engine import (Inputs, Tariff, run, monthly_bill, units_from_bill, central_subsidy, emi,
                         irr, payback, crf, sizing, breakeven, tornado, size_sweep, MSEDCL_FY26)
from benchmarks import BILL_CASES, all_checks


def pa_geometric(i, g, n):
    """(P/A1, g, i, n): present worth of a series starting at 1 in year 1 and growing at g."""
    return n / (1 + i) if abs(i - g) < 1e-12 else (1 - ((1 + g) / (1 + i)) ** n) / (i - g)


# ------------------------------------------------------------ team's manual benchmark
@pytest.mark.parametrize("units,expected,_", BILL_CASES)
def test_bill_matches_validation_engineer_table(units, expected, _):
    assert monthly_bill(units, MSEDCL_FY26) == pytest.approx(expected, abs=0.005)


def test_all_team_benchmark_checks_pass():
    df = all_checks()
    failed = df[df.result != "PASS"]
    assert failed.empty, failed[["check", "expected", "tool"]].to_string()


# ------------------------------------------------------------ tariff mechanics
def test_bill_to_units_round_trip():
    for u in [0, 37, 100, 250, 400, 523.5, 1200]:
        b = monthly_bill(u, MSEDCL_FY26)
        assert units_from_bill(b, MSEDCL_FY26) == pytest.approx(u, abs=1e-6)


def test_bill_below_fixed_charge_gives_zero_units():
    assert units_from_bill(100, MSEDCL_FY26) == 0


def test_team_5000_bill_is_about_402_units():
    assert units_from_bill(5000, MSEDCL_FY26) == pytest.approx(400 + (5000 - 4964.80) / (14.07 * 1.16), abs=1e-6)


def test_custom_flat_tariff():
    t = Tariff(slab_limits=(), slab_rates=(8.0,), fixed_monthly=0, duty=0)
    assert monthly_bill(250, t) == pytest.approx(2000)


# ------------------------------------------------------------ subsidy, loan
@pytest.mark.parametrize("kw,exp", [(0, 0), (1, 30000), (2, 60000), (2.5, 69000), (3, 78000), (5, 78000), (10, 78000)])
def test_pm_surya_ghar_general(kw, exp):
    assert central_subsidy(kw) == pytest.approx(exp)


def test_subsidy_other_categories():
    assert central_subsidy(3, "residential", "special") == pytest.approx(33000 * 2 + 19800)
    assert central_subsidy(10, "housing_society") == pytest.approx(180000)
    assert central_subsidy(10, "commercial") == 0


def test_emi_formula():
    # 1,00,000 at 12% for 1 year -> standard EMI 8,884.88
    assert emi(100000, 0.12, 1) == pytest.approx(8884.88, abs=0.01)
    assert emi(120000, 0.0, 1) == pytest.approx(10000)


# ------------------------------------------------------------ closed-form checks
def test_grid_pw_equals_geometric_gradient_factor():
    inp = Inputs(consumption_mode="units", monthly_units=400)
    res = run(inp)
    a1 = 12 * monthly_bill(400, MSEDCL_FY26)
    assert res.pw_grid == pytest.approx(a1 * pa_geometric(0.08, 0.05, 25), rel=1e-12)


def test_solar_pw_closed_form_when_house_never_imports():
    """5 kW at 400 units: generation always exceeds use, so the with-solar bill is fixed charge + duty only."""
    inp = Inputs(consumption_mode="units", monthly_units=400)
    t = run(inp).table
    assert (t.import_kwh.iloc[1:] == 0).all()
    i = 0.08
    fixed = 12 * 130 * 1.16 * pa_geometric(i, 0.05, 25)
    om = 3000 * pa_geometric(i, 0.03, 25)
    ins = 1000 * pa_geometric(i, 0.03, 25)
    inv = 40000 / 1.08 ** 10 + 40000 / 1.08 ** 20
    g1 = 5 * 8760 * 0.19
    pw_gen = g1 * pa_geometric(i, -0.005, 25)
    export = 2.82 * (pw_gen - 4800 * pa_geometric(i, 0.0, 25))
    expected = 222000 + fixed + om + ins + inv - export
    assert run(inp).pw_solar == pytest.approx(expected, rel=1e-10)


def test_npv_identities():
    res = run(Inputs())
    assert res.npv == pytest.approx(res.pw_grid - res.pw_solar)
    assert res.npv == pytest.approx(res.table.pv_saving.sum())
    flows = list(res.table.saving)
    assert sum(cf / (1 + res.irr) ** k for k, cf in enumerate(flows)) == pytest.approx(0, abs=1e-3)
    assert res.euac_grid == pytest.approx(res.pw_grid * crf(0.08, 25))


# ------------------------------------------------------------ edge cases
def test_zero_size_means_no_difference():
    assert run(Inputs(system_kw=0, gross_cost=0)).npv == pytest.approx(0, abs=1e-6)


def test_no_subsidy_lowers_npv_by_exactly_the_subsidy():
    base, nosub = run(Inputs()), run(Inputs(subsidy_mode="none"))
    assert base.npv - nosub.npv == pytest.approx(78000)


def test_subsidy_in_year_one_is_discounted_once():
    a, b = run(Inputs()), run(Inputs(subsidy_year=1))
    assert a.npv - b.npv == pytest.approx(78000 - 78000 / 1.08)


def test_unused_inverter_credit():
    t = run(Inputs(credit_unused_inverter=True)).table
    assert t.residual.iloc[25] == pytest.approx(40000 * 5 / 10)


def test_loan_at_discount_rate_with_annual_payments_is_nearly_npv_neutral():
    a = run(Inputs())
    b = run(Inputs(loan_enabled=True, loan_rate=0.08, loan_years=5))
    assert b.loan_principal == pytest.approx(0.8 * 222000)
    # monthly EMIs counted at year end cost slightly more than the principal at 8%
    assert abs(a.npv - b.npv) < 0.03 * b.loan_principal


def test_commercial_over_10kw_pays_grid_support_charge():
    t = run(Inputs(category="commercial", system_kw=12, gross_cost=600000)).table
    assert t.gsc.iloc[1] == pytest.approx(12 * 8760 * 0.19 * 1.96)
    assert run(Inputs(category="commercial", system_kw=12, gross_cost=600000)).subsidy == 0


def test_payback_and_irr_helpers():
    assert payback([-100, -60, 20]) == pytest.approx(1.75)
    assert payback([-10, -5]) is None
    assert irr([-100, 110]) == pytest.approx(0.10, abs=1e-9)


def test_breakeven_gross_cost_gives_zero_npv():
    inp = Inputs()
    c = breakeven(inp, "gross_cost", 0, 5e6)
    assert run(inp.copy(gross_cost=c)).npv == pytest.approx(0, abs=1)


def test_sizing_recommendation_for_400_units():
    s = sizing(Inputs(consumption_mode="units", monthly_units=400, roof_area_sqft=600, sanctioned_load_kw=5))
    assert s["size_for_consumption"] == pytest.approx(4800 / (8760 * 0.19))
    assert s["recommended_kw"] == 3.0
    assert s["panels"] == math.ceil(5000 / 545)


def test_tornado_and_sweep_run():
    tor = tornado(Inputs())
    assert len(tor) == 14 and (tor.base_npv - run(Inputs()).npv).abs().max() < 1e-6
    sw = size_sweep(Inputs())
    assert sw.loc[sw.kw == 5, "npv"].iloc[0] == pytest.approx(run(Inputs()).npv)


def test_invalid_inputs_rejected():
    with pytest.raises(ValueError):
        run(Inputs(system_kw=-1))
    with pytest.raises(ValueError):
        run(Inputs(degradation=1.5))
