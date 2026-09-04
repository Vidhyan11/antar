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
python scripts/run_day1.py     # simulator + ledger
python scripts/run_day2.py     # baseline bot: claimed vs caused
python scripts/run_day3.py     # holdout, ATE, peeking demo
python scripts/run_day4.py     # uplift model, Qini, sensitivity sweep
python scripts/run_day5.py     # triage, incident freeze, actions
python scripts/run_day6.py     # compliance, stopping rules, the P&L
pytest -q

streamlit run console/app.py   # the console reads what the scripts write
```

Everything is seeded from `config/antar.yaml`, so runs are byte-for-byte
reproducible **and the demo needs no API keys**. Model calls resolve from
committed fixtures, and the Razorpay client runs in recorded dry-run mode
unless `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` test-mode credentials are set.

## What day 1 produces

```
class                     n   share      p0      p1   uplift  rank p1  rank up
D_DEAD_INSTRUMENT     3,245   8.0%    0.01    0.31    0.301        4        1  <- most valuable
C_FUNDS               6,570  16.1%    0.12    0.38    0.256        3        2
F_INTENT_LOSS         3,925   9.6%    0.10    0.28    0.183        5        3
B_AUTH_DROPOFF        7,139  17.5%    0.56    0.74    0.175        2        4
E_RISK_DECLINE        2,477   6.1%    0.09    0.21    0.119        6        5
A_TRANSIENT_RAIL     17,351  42.6%    0.92    0.93    0.013        1        6  <- best rate, no value
```

`p0` = recovers with no intervention · `p1` = recovers if we act · `uplift` = the
difference, which is the only column that is actually money.

Rank correlation between treated success rate and true uplift: **−0.26**. And
only about **12%** of failures are *persuadable* — for the rest, contacting is
cost without value.

## Repository layout

| Path | Contents |
|---|---|
| `antar/taxonomy.py` | The six decline classes and the reason-code mapping |
| `antar/ledger.py` | Append-only hash-chained decision ledger + verifier |
| `antar/models.py` | Domain objects, including both potential outcomes |
| `antar/simulator/engine.py` | The simulator that manufactures ground truth |
| `antar/sensorium.py` | Normalises raw gateway failures into FailureRecords |
| `antar/evaluation.py` | The truth book — the only object allowed to hold ground truth |
| `antar/features.py` | Shared feature construction (baseline and uplift model) |
| `antar/policies/baseline.py` | The naive recovery bot — our control condition |
| `antar/holdout.py` | Keyed-hash arm assignment: random, deterministic, auditable |
| `antar/stats/sequential.py` | Always-valid confidence sequences (normal-mixture boundary) |
| `antar/stats/validation.py` | Coverage and peeking simulations that test the guarantee |
| `antar/pipeline.py` | One experiment end to end — shared by every day and the sweep |
| `antar/policies/uplift.py` | T-learner CATE targeting, and the Qini curve |
| `antar/sweep.py` | Sensitivity analysis over the self-recovery assumption |
| `antar/triage/detector.py` | Seasonality-aware Poisson detection of rail incidents |
| `antar/triage/agent.py` | The triage agent, verdict validation, and the freeze registry |
| `antar/llm/provider.py` | Fixture-first model layer — runs with no API key |
| `antar/actuator.py` | Bounded, gated, logged actions against Razorpay test mode |
| `antar/compliance.py` | The compliance linter and the self-pausing arm monitor |
| `antar/economics.py` | The Counterfactual P&L |
| `console/app.py` | Streamlit console — a viewer over the pipeline's artifacts |
| `config/antar.yaml` | Every assumption, in one auditable place |
| `scripts/run_day1.py` | End-to-end day-1 demo |
| `scripts/run_day2.py` | Baseline bot: what it claims vs what it caused |
| `scripts/run_day3.py` | Holdout, ATE, and the peeking demonstration |
| `scripts/run_day4.py` | Uplift model, Qini, and the sensitivity sweep |
| `scripts/run_day5.py` | Triage, the incident freeze, and Razorpay actions |
| `scripts/run_day6.py` | Compliance, stopping rules, and the P&L |
| `scripts/build_report.py` | Regenerates `RESULTS.md` from the artifacts |
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
| 2 | Mon 31 Aug | Sensorium · naive baseline bot | ✅ |
| 3 | Tue 1 Sep | Holdout assignment · ATE with always-valid confidence sequences · *console: holdout + ATE panel* | ✅ |
| 4 | Wed 2 Sep | Uplift/CATE model · Qini · sensitivity sweep · *console: Qini + sweep panel* | ✅ |
| 5 | Thu 3 Sep | Triage agent · incident freeze · Razorpay test-mode actions · *console: incident timeline* | ✅ |
| 6 | Fri 4 Sep | Actuator · stopping rules · compliance linter · Counterfactual P&L · *console: P&L panel* | ✅ |
| 7 | Sat 5 Sep | Console polish · pitch video · architecture doc · submission | ✅ |

The console is a **Streamlit** app built one panel per evening, never as a
final-day task. The forensic beats — ledger tamper detection, stopping rules
firing — stay in the terminal, where they read as evidence rather than
decoration. The pipeline also emits a self-contained `report.html` so the P&L
and charts are visible without running anything.

## What day 5 produces

```
rail outages                6 (6 above the detection floor)
detected                    5 of 6 detectable, 6 episodes, no false positives

transactions inside an open incident        331
baseline would contact, inside the incident   32
ANTAR contacts, inside the incident            0
```

Those messages would tell customers their payment failed during a window when
the bank was down and nothing they could do would have helped. **Staying silent
is the correct action, and no success-rate metric will ever reward it.**

Detection is a large-numbers problem, so recall is reported against outages that
were reachable at all. A rail carrying almost no traffic cannot have an outage
detected by any method, and scoring against those would be marking our own
homework generously.

## The Counterfactual P&L

Both policies run under the *same* compliance rules and the same incident freeze —
comparing a constrained system against an unconstrained one would flatter whichever
we left unbuckled.

```
                                        baseline         ANTAR
headline claim                         8,473,813     2,228,099
actually caused                          726,244     1,171,670
retention damage                         141,600       112,800
NET VALUE CREATED                        110,961       289,138
contacts                                   4,088         2,288
headline overstates by                     11.7x          1.9x
```

ANTAR creates **2.6x the net value on 44% fewer contacts.** The baseline wins the
headline and loses the P&L: more messages, more gross recovery booked, less money
actually caused, more customers burned doing it. On a conventional dashboard only
the second of those is visible.

The **retention-damage line is measured, not assumed** — a treated-vs-control
opt-out delta priced at merchant LTV. No vendor reports it, for the obvious reason
that a tool paid on gross recovery has no incentive to price the customers it burns.

**Read the interval before quoting the point estimate.** Net value carries a 95%
always-valid CI of [-1,493,042, 2,071,319], which is enormous and honest:
per-transaction net value runs from a few rupees of margin to minus a whole customer
lifetime. The defensible claim is the ordering and the efficiency, not the rupee figure.

## Documents

| File | What it is |
|---|---|
| [`RESULTS.md`](RESULTS.md) | **Every measured number**, generated from the pipeline's own artifacts — readable without running anything |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | How it is built, where the AI is and is not, and the known limitations |
| [`VIDEO.md`](VIDEO.md) | Pitch-video shot list and narration |
| [`ANTAR-proposal.md`](ANTAR-proposal.md) | The full design and the strategic reasoning behind it |

## Reproducing this

```bash
pip install -e ".[dev]"
python scripts/run_day1.py     # …through run_day6.py
python scripts/build_report.py # regenerates RESULTS.md
streamlit run console/app.py
pytest -q                      # 143 tests
```

**No API keys and no network required.** Model calls replay from committed
fixtures; the Razorpay client refuses non-test credentials and records dry-run
requests otherwise.
