# ANTAR — अंतर

### *Only the difference counts.*

[![CI](https://github.com/Vidhyan11/antar/actions/workflows/ci.yml/badge.svg)](https://github.com/Vidhyan11/antar/actions/workflows/ci.yml)

**Track 03 — AI Revenue Recovery · Razorpay AI Buildathon**

A payment-recovery agent whose objective is **incremental** recovery — money that
came in *because of it* — not gross recovery. It holds back a randomised control
group, targets only customers whose behaviour it can actually change, stays
silent when the failure isn't the customer's fault, and reports a P&L with a
confidence interval.

---

## The problem

Recovery tools are scored on **gross rupees recovered**, and that number is
inflated. Last-touch attribution
[overstates dunning impact by 30–60%](https://www.y.uno/en/blog/how-to-actually-measure-failed-payment-recovery)
in enterprise stacks. In India the gap is likely wider: UPI failure is normalised,
so a large share of customers simply retry within minutes — and the tool takes
credit for it.

The damage isn't only a wrong dashboard. Optimising for *"did payment succeed
after I messaged?"* pushes the system toward **bank timeouts** — the highest
success rate and the **lowest causal effect** — and starves the cohorts that
genuinely need help.

> **The best-looking recovery rate is produced by the worst policy.**

## Run it

```bash
pip install -e ".[dev]"
python scripts/run_day1.py
pytest -q
```

Everything is seeded from `config/antar.yaml`, so runs are byte-for-byte
reproducible and the demo needs no API keys.

## What day 1 produces

```
class                     n   share      p0      p1   uplift  rank p1  rank up
D_DEAD_INSTRUMENT       774   7.6%    0.01    0.32    0.308        4        1  <- most valuable
C_FUNDS               1,686  16.6%    0.12    0.38    0.254        3        2
F_INTENT_LOSS         1,023  10.1%    0.10    0.28    0.182        5        3
B_AUTH_DROPOFF        1,787  17.6%    0.56    0.73    0.173        2        4
E_RISK_DECLINE          624   6.2%    0.09    0.21    0.117        6        5
A_TRANSIENT_RAIL      4,233  41.8%    0.92    0.93    0.013        1        6  <- best rate, no value
```

`p0` = recovers with no intervention · `p1` = recovers if we act · `uplift` = the
difference, which is the only column that is actually money.

Rank correlation between treated success rate and true uplift: **−0.26**. And
only **12.6%** of failures are *persuadable* — for the other 87%, contacting is
cost without value.

## Repository layout

| Path | Contents |
|---|---|
| `antar/taxonomy.py` | The six decline classes and the reason-code mapping |
| `antar/ledger.py` | Append-only hash-chained decision ledger + verifier |
| `antar/models.py` | Domain objects, including both potential outcomes |
| `antar/simulator/engine.py` | The simulator that manufactures ground truth |
| `config/antar.yaml` | Every assumption, in one auditable place |
| `scripts/run_day1.py` | End-to-end day-1 demo |
| `tests/` | Invariants, including property-based tests |

## Why a simulator

To validate a causal estimator you need ground truth, and only a simulator has
it: for every failed payment we know **both** potential outcomes — what happens
if we intervene and what happens if we don't. Real payment data can never tell
you that, which is exactly why the industry's recovery numbers are unfalsifiable.

The headline magnitude depends on assumptions we chose, so those assumptions are
config knobs rather than constants, and a sensitivity sweep re-runs the pipeline
across a range of them to show the **policy ranking holds** even where the
magnitude doesn't. Payment plumbing runs against Razorpay **test-mode** APIs.

## Status

| Day | Date | Deliverable | |
|---|---|---|---|
| 1 | Sun 30 Aug | Ground-truth simulator · hash-chained ledger · CI | ✅ |
| 2 | Mon 31 Aug | Sensorium · naive baseline bot | ⬜ |
| 3 | Tue 1 Sep | Holdout assignment · ATE with always-valid confidence sequences · *console: holdout + ATE panel* | ⬜ |
| 4 | Wed 2 Sep | Uplift/CATE model · Qini · sensitivity sweep · *console: Qini + sweep panel* | ⬜ |
| 5 | Thu 3 Sep | Triage agent · incident freeze · Razorpay test-mode actions · *console: incident timeline* | ⬜ |
| 6 | Fri 4 Sep | Actuator · stopping rules · compliance linter · Counterfactual P&L · *console: P&L panel* | ⬜ |
| 7 | Sat 5 Sep | Console polish · pitch video · architecture doc · submission | ⬜ |

The console is a **Streamlit** app built one panel per evening, never as a
final-day task. The forensic beats — ledger tamper detection, stopping rules
firing — stay in the terminal, where they read as evidence rather than
decoration. The pipeline also emits a self-contained `report.html` so the P&L
and charts are visible without running anything.

See [`ANTAR-proposal.md`](ANTAR-proposal.md) for the full design.
