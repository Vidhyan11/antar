# ANTAR — architecture

*Companion to [`README.md`](README.md) (what it is) and [`RESULTS.md`](RESULTS.md)
(what it measured). This is how it is built and why each piece is shaped that way.*

---

## The one idea

Every component below exists to support a single claim: **the only rupee worth
counting is the one that would not have arrived otherwise.**

That claim needs a counterfactual, a counterfactual needs a control group, and a
control group needs an agent willing to deliberately not act. Everything else —
the taxonomy, the incident freeze, the compliance linter, the stopping rules —
follows from taking that seriously.

---

## Data flow

```
  Razorpay webhook  ┐
  (or simulator)    ├──► RawGatewayEvent ──► SENSORIUM ──► FailureRecord
                    ┘         │                                  │
                        [ THE AIRLOCK ]                          │
                    ground truth cannot                          ▼
                    cross this boundary            ┌─────────────────────────┐
                                                   │ TRIAGE                  │
                                                   │ Poisson detector, hourly│
                                                   │ vs same hour prior days │
                                                   │        │                │
                                                   │   agent verdict         │
                                                   │   (schema-validated)    │
                                                   └────────┬────────────────┘
                                            systemic ◄──────┴──────► individual
                                               │                        │
                                        FREEZE COHORT                   ▼
                                        (nobody contacted)   ┌──────────────────────┐
                                                             │ ALLOCATOR            │
                                                             │ 10% hashed holdout   │
                                                             │ T-learner CATE       │
                                                             │ rank by Δ × amount   │
                                                             └──────────┬───────────┘
                                                                        ▼
                                                             ┌──────────────────────┐
                                                             │ ACTUATOR             │
                                                             │ compliance linter    │
                                                             │ allow-listed actions │
                                                             │ rupee ceiling        │
                                                             │ Razorpay test mode   │
                                                             └──────────┬───────────┘
                                                                        ▼
                                                             ┌──────────────────────┐
                                                             │ TRIBUNAL             │
                                                             │ hash-chained ledger  │
                                                             │ always-valid CIs     │
                                                             │ arm monitor (pause)  │
                                                             │ Counterfactual P&L   │
                                                             └──────────────────────┘
```

---

## The airlock

The single most important structural decision in the codebase.

| Object | Carries | Who may hold it |
|---|---|---|
| `FailureEvent` | both potential outcomes (`y0`,`y1`,`o0`,`o1`) | simulator only |
| `RawGatewayEvent` | what a gateway actually delivers | anyone |
| `FailureRecord` | normalised + customer features | every policy |
| `TruthBook` | ground truth, keyed by txn | evaluator only |

`FailureEvent.to_raw_event()` is the boundary. `RawGatewayEvent` has **nowhere to
put an answer** — the leak is prevented by the type, not by discipline.

Why it matters: if ground truth reached a policy, every number the project
produces would be worthless *and would still look correct*. That is the worst
class of bug available here, so it is guarded three ways — the type boundary, a
leakage test over every field name, and an AST-parsing test asserting no module
under `antar/policies/` imports `antar.evaluation`.

---

## Module map

| Module | Responsibility | Load-bearing detail |
|---|---|---|
| `taxonomy.py` | six decline classes, reason-code map | unmapped codes raise rather than defaulting — a misclassified failure is a wrong action |
| `simulator/engine.py` | manufactures ground truth | one uniform per transaction ⇒ `y1 ≥ y0` always; contact can never stop someone paying |
| `sensorium.py` | normalise, enrich, log | batched ledger write: identical chain, one commit |
| `holdout.py` | arm assignment | keyed SHA-256: random, deterministic, and recomputable by anyone with the salt |
| `stats/sequential.py` | always-valid confidence sequences | normal-mixture boundary; validity holds for *any* ρ, so a bad ρ costs power not correctness |
| `policies/baseline.py` | the control condition | built properly — same features, real classifier. It must lose on *objective*, not information |
| `policies/uplift.py` | T-learner CATE targeting | no hardcoded class filter; the estimator subsumes the rule |
| `triage/detector.py` | correlated-failure detection | hourly buckets vs the *same hour on previous days* — seasonality, not a rolling mean |
| `triage/agent.py` | systemic-vs-individual verdict | schema+range validated; unusable output fails closed to *release*, not freeze |
| `llm/provider.py` | model access | fixture-first: the demo runs with no API key |
| `compliance.py` | linter + arm monitor | quiet hours in IST, not UTC |
| `actuator.py` | decisions → actions | allow-listed per class, rupee ceiling, logged *before* the network call |
| `economics.py` | the Counterfactual P&L | retention damage measured from the control arm, not assumed |

---

## Where the AI is, and where it deliberately is not

**Is:** the triage agent's systemic-vs-individual verdict and its incident note.

**Is not:** authorising money, computing uplift, choosing budgets, overriding
compliance, or selecting who to contact.

The boundary is mechanical, not aspirational. `TriageAgent._validate()` parses
the model's output into a fixed schema and range-checks it; anything malformed
becomes `FAIL_CLOSED`, which releases the cohort to normal targeting rather than
freezing the book on unvalidated output. The model writes the reasoning; a
deterministic policy writes the cheque.

The agent's task is narrow on purpose. It receives a pre-computed summary and
returns a verdict — it does not roam the ledger. Free-tier models are reliable at
a bounded classify-and-explain task and unreliable at open-ended tool loops, so
the task was shaped to the thing that works rather than the thing that demos well.

**Provider resolution:** fixture → live model (if a key exists) → deterministic
fallback. Whichever answered is recorded and surfaced in the console. A demo
silently running on canned rules while presenting itself as agentic would be the
same dishonesty this project criticises.

---

## Four decisions worth defending

**A control group costs money and buys everything.** Ten percent of failures are
deliberately untouched. Without them there is no `P(recover | not contacted)`,
so no uplift, no P&L, and no defensible number anywhere.

**Time-uniform intervals, because the agent has stopping rules.** ANTAR watches
its own effect and pauses arms on it. Under that protocol a 95% t-interval is not
a 95% interval — measured at 30.2% false positives on null data. The confidence
sequence is wider on purpose; that width is what buys the right to stop.

**Silence is an action.** The freeze registry sits between targeting and the
actuator. During a rail outage the correct move is to say nothing, and no
success-rate metric will ever reward it.

**The ledger records refusals.** Vetoes and blocks are appended with the rule
that fired. An audit trail containing only what happened is half a trail.

---

## Reproducibility

Every run is seeded from `config/antar.yaml`. Model calls replay from committed
fixtures. The Razorpay client refuses non-test keys and records dry-run requests
otherwise. **A judge can clone the repo and reproduce every figure in
`RESULTS.md` with no API keys and no network.**

```bash
pip install -e ".[dev]"
python scripts/run_day1.py   # …through run_day6.py
python scripts/build_report.py
streamlit run console/app.py
```

---

## Known limitations

Stated here rather than discovered by a reviewer.

| Limitation | Detail |
|---|---|
| **Volume floor** | Uplift estimation on a 10% holdout is noise below a few thousand failures/month. Works for large merchants, degrades on the long tail. |
| **Detection floor** | A rail with almost no traffic cannot have an outage detected by any method. Recall is scored only against outages above the floor. |
| **Wide net-value interval** | Per-transaction net value spans a few rupees of margin to minus a customer lifetime, so the time-uniform bound is loose. The ordering is defensible; the point estimate is not. |
| **Simulator-dependent magnitudes** | Every multiple follows from a self-recovery rate we chose. The sensitivity sweep is the answer: the ranking survives, the magnitude does not. |
| **Mocked channels** | DLT registration takes weeks. Content is real, delivery is simulated, costs are accounted. |
| **Contaminated holdouts in production** | A real control customer still receives the merchant's own marketing, so a live estimate would be a lower bound. |
| **Some contacts are not optional** | Mandate pre-debit notifications are legally required regardless of uplift and would need carving out of the optimisation. |
