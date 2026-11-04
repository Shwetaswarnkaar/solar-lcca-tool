# Rooftop Solar vs Grid — Life Cycle Cost Calculator

CE725 Construction Economics and Finance · Team course project (Topic 6, LCCA decision tool)
Basis: Maharashtra, MSEDCL LT-I residential tariff. Defaults = team data workbook `CE725_Solar_vs_Grid_Data`, sheet Base_Inputs.

## What it does
Compares **staying on grid power** with **installing net-metered rooftop solar** over 25 years and tells a
non-technical owner which costs less, in today's money and in plain rupees.

- **Quick view** (top card): headline saving, total grid cost, total solar cost, payback, cumulative cost lines,
  and three sliders — monthly bill (or units), gross system cost, grid inflation.
- **Inputs like the reference calculators** (itsmysun, arka360): consumer category, sanctioned load, roof area,
  bill *or* units, panel rating, generation (CUF), subsidy, loan, inverter type, running costs, editable tariff slabs.
- **Results**: PW of each option, NPV, IRR, simple and discounted payback, LCOE, CO₂, year-1 bill before/after.
- **Tabs**: system sizing and recommended size · year-1 slab-wise bill · year-by-year cash flows with Excel download ·
  sensitivity (tornado on the team's Low/High ranges, NPV vs discount rate, break-even values, size comparison) ·
  live validation against the team's manual benchmark · method and sources.

## Files
| File | Purpose |
|---|---|
| `lcca_engine.py` | All calculations (no screen code) |
| `app.py` | Streamlit web page |
| `benchmarks.py` | The team's manual numbers (Validation Engineer + Data Lead files) and the checks |
| `test_lcca_engine.py` | 41 automated tests |
| `requirements.txt`, `.streamlit/config.toml` | Packages and theme |

## Run on a laptop
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py          # opens http://localhost:8501
python -m pytest -q           # expect: 41 passed
python benchmarks.py          # expect: PASS 35 / 35
```

## Put it online (Streamlit Community Cloud, free)
1. Upload this folder (including `.streamlit/`) to a GitHub repository.
2. share.streamlit.io → sign in with GitHub → **Create app** → choose the repository, branch `main`, file `app.py` → **Deploy**.
3. Open the link on a phone and a laptop and check the base case shows NPV ₹7.71 lakh.

## Base case (team Base_Inputs) → results
₹5,000/month bill (₹60,000/yr ≈ 402 units on MSEDCL slabs), 5% grid escalation, 5 kW for ₹3,00,000,
₹78,000 PM Surya Ghar subsidy, CUF 19%, 0.5%/yr degradation, O&M ₹3,000 at 3%, insurance ₹1,000 at 3%,
inverter ₹40,000 in years 10 and 20, fixed charge ₹130 + 16% duty, export ₹2.82/kWh, 25 years, 8% discount rate.

| Result | Value |
|---|---|
| PW grid only | ₹10,11,063 |
| PW solar + grid | ₹2,39,890 |
| NPV of going solar | ₹7,71,173 |
| IRR | 32.8% |
| Payback (simple / discounted) | 3.3 / 3.9 years |
| 25-year totals, plain rupees | grid ₹28.64 lakh vs solar ₹3.22 lakh |

The size comparison shows **3 kW gives a slightly higher NPV (₹7.95 lakh) than 5 kW**: above ~3 kW the extra
units are exported at only ₹2.82 and the subsidy is capped.

## Validation
`benchmarks.py` reproduces, within rounding, all 13 slab-boundary bills from the Validation Engineer's Manual
Benchmark and 22 figures from the Data Lead's workbook (grid projection and PV, generation, Year-1 net-metering
check, fixed charges after solar, O&M, insurance, inverter PVs, net capital cost): **35 / 35 PASS**.
Still to do by hand: present worth of both alternatives for the base case (Topic 6 validation).

## Conventions
Year 0 = installation; end-of-year flows; values entered for year 1 escalate as X(1+e)^(t−1);
generation = kW × 8,760 × CUF × (1−d)^(t−1); monthly-average net metering with year-end surplus at the export rate;
inverter replaced every L years before the last year; NPV = PW(grid only) − PW(solar + grid).

## Known limitations
Monthly-average generation (no monsoon profile); no solar-hour banking restriction; tariff slabs escalate together;
FAC excluded; no battery, shading or tax model; commercial users must enter their own tariff.
MSEDCL slab values are FY2025-26 from the March 2025 MYT order — verify against the post-review (Case 75 of 2025) booklet.
