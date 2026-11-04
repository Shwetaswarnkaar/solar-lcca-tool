"""
benchmarks.py  -  the team's own manual numbers, used to validate the tool.

Every expected value below was typed from the team's files (no value comes from the tool):
  [VB]  CE725_Validation_Benchmark_and_Test_Log, sheet "Manual Benchmark" (Validation Engineer)
  [DW]  CE725_Solar_vs_Grid_Data (Data Lead), sheets named in each check
The tool passes a check when |tool - expected| <= tolerance (the team's tables are rounded to
whole rupees or to 2 decimals, so the tolerance is half of the last shown digit, or Rs 1).
"""
from __future__ import annotations

from typing import List, Dict

import pandas as pd

from lcca_engine import Inputs, run, monthly_bill, MSEDCL_FY26

# ---------------------------------------------------------------- [VB] bill test cases
BILL_CASES = [  # units/month, expected monthly bill Rs, test purpose
    (0, 150.80, "Zero consumption"),
    (1, 157.38, "Minimum positive consumption"),
    (99, 801.94, "Below first slab limit"),
    (100, 808.52, "Exact first slab boundary"),
    (101, 821.14, "First unit in second slab"),
    (299, 3320.06, "Below 300-unit boundary"),
    (300, 3332.68, "Exact second slab boundary"),
    (301, 3349.00, "First unit in third slab"),
    (400, 4964.80, "Reference case"),
    (499, 6580.60, "Below 500-unit boundary"),
    (500, 6596.92, "Exact third slab boundary"),
    (501, 6614.98, "First unit in fourth slab"),
    (600, 8403.04, "Higher consumption"),
]


def team_base_400() -> Inputs:
    """Team base case with consumption entered as 400 units/month (as in Net_Metering and Generation_CUF)."""
    return Inputs(consumption_mode="units", monthly_units=400)


def check_bills() -> pd.DataFrame:
    rows = []
    for units, expected, purpose in BILL_CASES:
        tool = monthly_bill(units, MSEDCL_FY26)
        rows.append(dict(check=f"Monthly bill at {units} units — {purpose}", source="[VB] Manual Benchmark",
                         expected=expected, tool=tool, tolerance=0.005))
    return pd.DataFrame(rows)


def check_workbook() -> pd.DataFrame:
    rows = []

    def add(name, source, expected, tool, tol=1.0):
        rows.append(dict(check=name, source=source, expected=expected, tool=tool, tolerance=tol))

    # Tariff_Escalation C: Rs 60,000 in year 1, +5%/yr, PV at 8%
    g = run(Inputs(consumption_mode="bill", monthly_bill=5000)).table
    add("Grid-only cost, year 1 (₹60,000 basis)", "[DW] Tariff_Escalation C", 60000, g.grid_cost.iloc[1])
    add("Grid-only cost, year 25", "[DW] Tariff_Escalation C", 193506, g.grid_cost.iloc[25])
    add("Grid-only cost, 25-year total", "[DW] Tariff_Escalation C", 2863626, g.grid_cost.sum())
    add("PW of grid-only cost at 8%", "[DW] Tariff_Escalation C", 1011063, g.pv_grid.sum())

    t = run(team_base_400()).table
    # Generation_CUF C: 5 kW, CUF 19%, 0.5%/yr, 4,800 kWh/yr
    add("Generation, year 1 (kWh)", "[DW] Generation_CUF C", 8322, t.generation_kwh.iloc[1])
    add("Generation, year 25 (kWh)", "[DW] Generation_CUF C", 7379, t.generation_kwh.iloc[25])
    add("Generation, 25-year total (kWh)", "[DW] Generation_CUF C", 196033, t.generation_kwh.sum())
    add("Surplus exported, 25-year total (kWh)", "[DW] Generation_CUF C", 76033, t.export_kwh.sum())
    # Net_Metering B: Year-1 savings check at 400 units
    add("Year-1 bill without solar (₹)", "[DW] Net_Metering B", 59578, t.grid_cost.iloc[1])
    add("Year-1 bill with solar, fixed charge + duty only (₹)", "[DW] Net_Metering B", 1810, t.solar_bill.iloc[1])
    add("Year-1 surplus exported (kWh)", "[DW] Net_Metering B", 3522, t.export_kwh.iloc[1])
    add("Year-1 export credit (₹)", "[DW] Net_Metering B", 9932, t.export_credit.iloc[1])
    add("Year-1 avoided bill (₹)", "[DW] Net_Metering B", 57768, t.grid_cost.iloc[1] - t.solar_bill.iloc[1])
    add("Year-1 total benefit (₹)", "[DW] Net_Metering B", 67700,
        t.grid_cost.iloc[1] - t.solar_bill.iloc[1] + t.export_credit.iloc[1])
    # Fixed_Charges B: fixed charge + duty after solar, escalated 5%
    add("Fixed charge after solar, 25-year total (₹)", "[DW] Fixed_Charges B", 86367, t.solar_bill.sum())
    # OandM C
    add("O&M, 25-year total (₹)", "[DW] OandM C", 109378, t.om.sum())
    add("PW of O&M at 8% (₹)", "[DW] OandM C", 41656, (t.om * t.discount_factor).sum())
    # Insurance C
    add("Insurance, 25-year total (₹)", "[DW] Insurance C", 36459, t.insurance.sum())
    # Inverter C
    add("PW of inverter replacement, year 10 (₹)", "[DW] Inverter C", 18528, t.inverter.iloc[10] * t.discount_factor.iloc[10])
    add("PW of inverter replacement, year 20 (₹)", "[DW] Inverter C", 8582, t.inverter.iloc[20] * t.discount_factor.iloc[20])
    add("Number of inverter replacements in 25 years", "[DW] Inverter C", 2, float((t.inverter > 0).sum()), 0)
    # Base_Inputs: net capital cost
    r0 = run(Inputs())
    add("Net capital cost after subsidy (₹)", "[DW] Base_Inputs", 222000, r0.net_cost)
    return pd.DataFrame(rows)


def all_checks() -> pd.DataFrame:
    df = pd.concat([check_bills(), check_workbook()], ignore_index=True)
    df["difference"] = df.tool - df.expected
    df["result"] = ["PASS" if abs(d) <= tol + 1e-9 else "FAIL" for d, tol in zip(df.difference, df.tolerance)]
    return df


if __name__ == "__main__":
    df = all_checks()
    pd.set_option("display.width", 200)
    print(df[["check", "expected", "tool", "difference", "result"]].to_string())
    print("\nPASS:", (df.result == "PASS").sum(), "/", len(df))
