# ANTAR — अंतर

### *Only the difference counts.*

> **Track 03 — AI Revenue Recovery** | Razorpay AI Buildathon
>
> **Antar** (Hindi/Sanskrit: *the difference, the gap*) is a payment-recovery agent whose objective is **incremental** recovery — money that came in *because of it* — not gross recovery. It holds back a control group, targets only customers whose behaviour it can actually change, stays silent when the failure isn't the customer's fault, and reports a P&L with a confidence interval.

---

## 0. TL;DR for a judge with 40 seconds

Recovery tools are scored on **gross rupees recovered**. That number is inflated, and the inflation is documented: last-touch attribution **overstates dunning impact by 30–60%** in enterprise stacks ([Yuno](https://www.y.uno/en/blog/how-to-actually-measure-failed-payment-recovery)). In India the gap is likely wider, because UPI failure is normalised — a large share of customers simply retry within minutes, and the tool takes credit for it.

The damage isn't just a wrong dashboard. Optimising for *"did payment succeed after I messaged?"* pushes the system toward **bank timeouts** — the highest success rate and the **lowest causal effect** — and starves the cohorts that genuinely need help (expired cards, revoked mandates, empty balance). **The best-looking recovery rate is produced by the worst policy.**

Antar makes the incremental rupee the objective function, live and per transaction. It runs a permanent randomised holdout, estimates who is actually persuadable, screens for portfolio-wide incidents *before* contacting anyone, executes real recovery actions against Razorpay test-mode APIs, and publishes a counterfactual P&L that subtracts self-recovery, channel cost, discounts, **and the retention damage it caused**.

Headline demo: on identical data, a conventional dunning bot reports **~53% more recovery than it caused**, at **4x the contact volume**. Antar shows the gap — from its own control arm.

---

## 1. Why Track 03

| Track                   | Market heat                                                                      | Expected crowding                                            | Rubric measurability                                                          | Verdict                                                              |
| ----------------------- | -------------------------------------------------------------------------------- | ------------------------------------------------------------ | ----------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| 01 Agentic Commerce     | Highest                                                                          | **Highest** — everyone builds a conversational checkout demo | Soft ("explainable, bounded, gated")                                          | Great space, terrible odds. You'd be submission #200 of the same bot. |
| 02 Risk Manager         | High                                                                             | High                                                         | You have no real fraud data — everyone's precision/recall is unfalsifiable    | Crowded and hollow                                                   |
| **03 Revenue Recovery** | **High** — Indian failure rates are structurally worse; a live merchant P&L line | **Moderate**                                                 | **Denominated in rupees.** Their bar names *stopping rules* and *audit trail* | **This one**                                                         |
| 04 Finance Controller   | Medium                                                                           | Low                                                          | Very measurable                                                               | Safe but ceiling-limited; "match rate" isn't a thesis                |
| 05 Open                 | —                                                                                | —                                                            | —                                                                             | Only worth it with a thesis; ours fits 03 exactly                    |

**The tell in their own copy.** Track 03 is the only bar that names **"stopping rules"** and **"compliant escalation."** That is a payments company that has watched recovery tooling spam customers and inflate its own numbers. They are asking, in advance, for restraint and honest accounting. Antar's entire premise is restraint and honest accounting.

**Framing note for the pitch:** say *"your merchants"*, not *"you"*. A gateway's own recovery levers are technical — routing, retry timing, rail health. The attribution problem bites hardest in the **outbound** layer, which is the merchant's pain. Track 03's wording ("payment failures, checkout abandonment, **or receivables**") puts you squarely inside the brief, but don't tell a Razorpay panel this is their daily problem when it is their customers'.

### Rubric cross-check

| Their criterion                           | Antar's answer                                                                             |
| ----------------------------------------- | ------------------------------------------------------------------------------------------ |
| Execution quality and reliability         | Deterministic money-policy layer; real actions against Razorpay test APIs; LLM never authorises |
| Meaningful AI implementation              | Causal core + agentic incident diagnosis + multilingual generation under a compliance linter |
| Evidence of real value creation           | Net rupees, after channel cost, discounts **and measured retention damage**                  |
| **Honest metrics and exception handling** | The holdout *is* the product. Always-valid CIs. Inbound-reply exception loop.                |
| Proper audit trails and compliance        | Hash-chained append-only ledger; every rupee-affecting action carries a rationale             |

---

## 2. The problem, end to end

### 2.1 How a payment normally works

```
Customer                Merchant           Razorpay              Bank / UPI rail
   │                       │                   │                       │
   │──"Pay ₹999"──────────>│                   │                       │
   │                       │──create order────>│                       │
   │<────checkout page─────────────────────────│                       │
   │──picks UPI / card────────────────────────>│                       │
   │                       │                   │──route to rail───────>│
   │<────────── enter UPI PIN / OTP ───────────────────────────────────│
   │──────────── PIN entered ─────────────────────────────────────────>│
   │                       │                   │                       │ balance? limits?
   │                       │                   │                       │ risk?
   │                       │                   │<───── APPROVED ───────│
   │                       │<──webhook: paid───│                       │
   │<──"Order confirmed"───│                   │                       │
```

1. Customer clicks **Pay** → 2. merchant creates an **order** → 3. customer picks a method → 4. Razorpay routes to the **rail** → 5. customer **authenticates** (UPI PIN / OTP / 3DS) → 6. the **bank decides** → 7. approval returns via **webhook** → 8. order confirmed, funds settle later.

### 2.2 How it fails — six different problems, not one

Steps 3–6 all break, and the failures are **not interchangeable**:

| Class                     | Example                                       | Breaks at | Self-recovers?             | Right action                              | Uplift        |
| ------------------------- | --------------------------------------------- | --------- | -------------------------- | ----------------------------------------- | ------------- |
| **A. Transient rail**     | PSP timeout, NPCI throttle, issuer bank down  | Step 4    | **Almost always**          | Wait, retry, re-route. **Do not contact.** | ~Zero         |
| **B. Auth drop-off**      | OTP/UPI PIN not entered, app-switch failed    | Step 5    | Often                      | Sub-5-minute same-session nudge only      | Low, decays fast |
| **C. Funds**              | Insufficient balance, limit exceeded          | Step 6    | Rarely                     | Retry timed to inflow; offer part-pay     | **High**      |
| **D. Dead instrument**    | Expired card, revoked/paused mandate          | Step 6    | **Never** — it cannot work | Must contact; needs a new instrument      | **Highest**   |
| **E. Risk decline**       | Issuer risk block, velocity rule              | Step 6    | Rarely                     | Re-route rail; never re-hammer            | Moderate      |
| **F. Intent loss**        | Abandoned before auth                         | Step 3    | Rarely                     | Reminder or offer — but fatigue bites     | Moderate, noisy |

### 2.3 What today's tools do, and the three things wrong with it

```
Payment fails ──> "Your payment failed, please retry" ──> Customer pays ──> "Recovered ₹999!" ✅
```

**Problem 1 — Credit inflation.** Attribution is post-hoc and last-touch. No counterfactual, so the category's reported ROI is unfalsifiable. Published estimate: **30–60% overstatement**.

**Problem 2 — Adverse targeting.** Scored on *P(recovery | contacted)*, the system gravitates to Class A — the biggest bucket with the best-looking success rate and near-zero incremental effect — while C and D look "hard" and get under-served. **This consequence holds regardless of the exact inflation figure**, which is what makes the argument robust.

**Problem 3 — Correlated-failure blindness.** When an issuer degrades, 4,000 failures are **one** incident. The tool sends 4,000 messages, teaching customers the merchant is broken, while the real fix — re-route, or retry after recovery — goes untaken.

**Unpriced cost throughout:** contact fatigue, opt-outs and retention damage never appear on the tool's scorecard, because the tool is paid on gross recovery.

> **The real problem:** *recovery systems cannot distinguish money they recovered from money that recovered itself — so they optimise toward the customers who need them least, and cannot be held accountable in rupees.*

---

## 3. Prior art — read this before you pitch

Do **not** claim this is unprecedented. It isn't, and a knowledgeable panel will catch it in one question. The honest positioning is stronger than the overclaim.

### What already exists

- **The incrementality critique is published and mainstream.** [Yuno](https://www.y.uno/en/blog/how-to-actually-measure-failed-payment-recovery) documents the 30–60% last-touch overstatement and prescribes holdout testing plus Shapley allocation as the fix.
- **It's standard procurement advice.** A [2026 buyer's guide](https://www.revaly.co/resources/failed-payment-recovery-software-buyers-guide) tells purchasers to explicitly ask vendors whether they can *"isolate incremental lift with control groups rather than reporting gross recovered payments."*
- **Agentic recovery already ships commercially.** [Butter Payments](https://www.butterpayments.com/solution/recover) markets an agentic AI recovery platform analysing 128 data points per transaction, selecting per-failure-class strategies across email / SMS / **silent recovery** / call.
- **Uplift modelling** (persuadables vs. sure-things vs. lost-causes, Qini curves, T-learners) is textbook marketing science with mature libraries.
- **Smart retries and decline taxonomies** are mature — Razorpay Optimizer, Stripe, Adyen all do this.
- **Comms circuit-breakers during incidents** are normal ops hygiene at mature companies, if usually manual.

### What is actually differentiated

| Element                                                                       | Status                                                        |
| ----------------------------------------------------------------------------- | ------------------------------------------------------------- |
| Incrementality as a critique                                                  | ❌ Known and published                                        |
| Uplift / CATE targeting                                                       | ❌ Textbook                                                    |
| Agentic recovery with per-class strategy                                      | ❌ Commercially shipped                                       |
| Systemic-vs-individual **triage as a hard gate on outbound**, agent-driven    | 🟡 Uncommon in productised form                               |
| **Retention damage measured from the same control arm and charged to the agent's own P&L** | 🟢 Genuinely rare — most systems don't price annoyance at all |
| **Always-valid sequential inference as an auto-pause kill-switch**            | 🟢 Rare outside dedicated experimentation platforms           |
| **The whole loop built for Indian rails** — UPI, Autopay, e-mandate, AFA, DLT | 🟢 Unoccupied. Existing vendors are US/card/subscription-shaped |

### The claim to actually make

> *"Incrementality isn't our invention — buyer's guides already tell merchants to demand it. The problem is almost nobody implements it, nobody has built it for Indian rails, and where it does exist it's a quarterly measurement report. We made it the agent's live objective function — and we let it stop us."*

That claim is true, survives scrutiny, and is more impressive than a false world-first.

---

## 4. Where the white space actually is: Indian rails

This is the strongest part of the positioning, and the part existing vendors cannot copy quickly.

- **UPI dominates and fails differently from cards.** Technical declines, PSP throttling and beneficiary-bank downtime produce huge Class-A volume with near-total self-recovery. A US-shaped recovery tool has no concept of this and will message all of it.
- **Mandatory additional-factor authentication** makes Class B (auth drop-off) far larger in India than in card-native markets. The right intervention is *seconds*, not a next-day email.
- **UPI Autopay and e-mandate failures** are a genuinely under-tooled category: revoked mandates, paused mandates, failed pre-debit notifications, AFA thresholds by category. These are Class D — highest uplift, worst served.
- **Salary-cycle timing.** Class C recovery in India is strongly calendar-driven. Retry timing against inflow dates is a real, cheap, high-uplift lever.
- **Compliance is different**: TRAI DLT registration for transactional SMS, WhatsApp template and session-window rules, DNC quiet hours, mandate pre-debit notification timing.

Build the taxonomy, the timing model and the compliance linter around these, and the project stops being "dunning with statistics" and becomes something specific to Razorpay's market.

---

## 5. The system

```
                    ┌─────────────────────────────────────────────────┐
  webhooks /        │  1. SENSORIUM                                   │
  txn feed  ───────>│  normalise failures -> canonical class A–F       │
                    │  attach: rail, issuer, MID, amount, instrument   │
                    └────────────────────┬────────────────────────────┘
                                         v
                    ┌─────────────────────────────────────────────────┐
                    │  2. TRIAGE  (differential diagnosis)            │
                    │  correlated-failure detection across portfolio  │
                    │  systemic?      -> incident, FREEZE cohort       │
                    │  idiosyncratic? -> release to targeting          │
                    └────────────────────┬────────────────────────────┘
                                         v
                    ┌─────────────────────────────────────────────────┐
                    │  3. ALLOCATOR  (the control group lives here)   │
                    │  10% randomised stratified holdout               │
                    │  CATE / uplift model -> persuadables only        │
                    │  rank by  Δmargin / (cost + λ · fatigue)         │
                    └────────────────────┬────────────────────────────┘
                                         v
                    ┌─────────────────────────────────────────────────┐
                    │  4. ACTUATOR   (bounded, deterministic)         │
                    │  REAL actions on Razorpay test APIs:            │
                    │  timed retry · rail re-route · payment link ·   │
                    │  mandate re-auth · inflow-day schedule · human  │
                    │  compliance linter + quiet hours + caps         │
                    └────────────────────┬────────────────────────────┘
                                         v
                    ┌─────────────────────────────────────────────────┐
                    │  5. TRIBUNAL  (honesty layer)                   │
                    │  hash-chained ledger · always-valid CIs ·       │
                    │  auto-pause when uplift CI crosses zero ·       │
                    │  Counterfactual P&L                             │
                    └─────────────────────────────────────────────────┘
```

### Step by step

**Step 1 — Classify the failure.** Not "payment failed" but A–F. A dead card and a bank timeout are different problems with opposite correct responses.

**Step 2 — Ask whether this is one problem or many.** Scan for over-dispersion in failures conditioned on `(issuer × rail × instrument × MID × time-bucket)` against a rolling baseline. An LLM agent forms a hypothesis, queries the ledger to confirm, then either **opens an incident and freezes outbound for the whole cohort** (scheduling a re-route or post-recovery sweep, and writing a human-readable incident note), or releases the transactions as genuinely individual.

**Step 3 — Hold back a control group.** 10%, stratified by class × amount band × tenure, assigned by deterministic hash of the transaction ID so it is reproducible and auditable. This is the load-bearing component.

**Step 4 — Estimate who is persuadable.** Because *we* randomise, CATE estimation is unconfounded — a clean setting for a T-learner or uplift forest. Evaluate with **Qini / uplift-at-k, not AUC**: a model can rank *outcome* perfectly and *uplift* terribly. Class A ranks top on outcome and bottom on uplift; that single fact is the thesis.

**Step 5 — Spend a bounded budget on the best targets only.** Rank by `expected incremental margin / (channel cost + λ · fatigue units)` and take greedily under budget — the Lagrangian relaxation of the budgeted allocation problem, with λ as the shadow price of a contact. Thompson sampling over `(class × channel × timing)` cells keeps the system exploring.

**Step 6 — Execute the right action, for real.** Not "send a message":

| Class | Action                                                             |
| ----- | ------------------------------------------------------------------ |
| A     | Silent retry after rail recovery, or re-route                       |
| B     | Same-session nudge inside 5 minutes                                 |
| C     | Retry scheduled to inflow date; part-payment link                   |
| D     | Instrument-update / mandate re-auth link — the one that truly needs contact |
| E     | Alternate rail or instrument                                        |
| F     | Reminder or bounded offer                                           |

All inside hard rules: consent state, quiet hours, rolling per-customer caps, template compliance, rupee ceilings, idempotency keys. **The AI proposes; a deterministic rulebook approves.**

**Step 7 — Log immutably.** Append-only hash-chained ledger: `prev_hash`, inputs, model version, policy version, action, cost, outcome, and a plain-language rationale. Tamper with a row in the demo and let the verifier break — cheap to build, disproportionately convincing.

**Step 8 — Publish the honest scorecard**, and stop when it says to.

---

## 6. Where the AI is — and where it deliberately is not

**Is:** the diagnostic agent (hypothesis generation and tool-using investigation over correlated failures); the causal core (uplift/CATE); constrained multilingual generation (Hindi / Tamil / Telugu / Bengali / English) inside approved templates behind a compliance linter that can veto; the exception loop (inbound "I already paid" / "wrong amount" / "STOP" classified and resolved or escalated with context); and the explanation layer attached to every ledger entry.

**Is not:** authorising money movement, computing uplift, setting budgets, or overriding compliance. **The LLM writes the reasoning; a deterministic policy writes the cheque.** Say this sentence in the video — Razorpay's Track 01 bar ("*explainable, bounded and gated*") shows how much they care.

---

## 7. The output artifact: a Counterfactual P&L

```
RECOVERY P&L — Merchant #4412 — batch of 5,000 failed payments
──────────────────────────────────────────────────────────────
  Last-touch claim (what a normal tool reports)   INR 12,40,000
  Less: control-implied self-recovery            (INR  4,30,000)
  ─────────────────────────────────────────────────────────────
  INCREMENTAL RECOVERY                            INR  8,10,000
  Less: channel cost (SMS / WA / voice / human)      (INR 12,400)
  Less: discounts and concessions granted            (INR 41,000)
  Less: measured retention damage *                  (INR 18,700)
  ─────────────────────────────────────────────────────────────
  NET VALUE CREATED                               INR  7,37,900
                                95% always-valid CI  +/- INR 46,000

  Contacts sent: 1,140 of 4,500 eligible (75% deliberately not contacted)
  Naive tool's overstatement: 53%  (published range 30–60%, Yuno)

  * treated-vs-control delta in 30-day opt-out and repeat-purchase rate,
    priced at merchant ARPU. The agent is charged for annoying people.
```

**Stopping rules — the trial's safety board:**

- *per customer* — opt-out, refusal detected, contact cap hit, or expected uplift below cost;
- *per cohort* — a systemic incident is open;
- *per arm* — uplift CI crosses zero, or measured retention damage exceeds incremental margin. The arm **pauses itself** and files a note.

Monitoring uses **always-valid inference** (e-values / mSPRT) so you can peek continuously without inflating false positives. Fixed-horizon p-values would be quietly wrong here, and saying so signals real rigour.

---

## 8. Build plan

### 8.1 Data — the simulator is a strength, but guard its weakness

You cannot get real failed-payment data. Every team will hand-wave synthetic data. Turn it around: **validating a causal estimator requires ground truth, and only a simulator has it.**

Build a generative simulator where each customer has a hidden true type — *sure-thing / persuadable / lost-cause / unreachable* — plus an inflow calendar, a fatigue state and an instrument state. Inject issuer outages, throttle windows and mandate revocations. Because true uplift is known by construction, you can show your estimator **recovers it**.

**The circularity trap — your single biggest exposure.** Your headline number depends on a self-recovery rate *you chose*. A sharp judge will say you assumed your conclusion. Pre-empt it, on a slide:

- Run a **sensitivity sweep** across self-recovery rates from conservative to aggressive.
- Show that the **policy ranking is invariant** — Antar beats the naive bot on net value across the whole range, even where the inflation is small.
- State plainly: *"the mechanism is what we demonstrate; the magnitude depends on the merchant."*

Then run the same policy against **Razorpay test-mode APIs** — orders, payment links, subscriptions and mandates, webhooks, idempotency, real retry paths. **Simulator for causal truth, test APIs for operational truth.**

### 8.2 Tech stack

Principle: **keep the plumbing boring.** Spend the complexity budget on the causal core, the triage gate and the ledger — the three things that are the idea. Everything else should be the most obvious tool that works.

#### Language and service layer

| Component      | Choice                    | Why                                                                                            |
| -------------- | ------------------------- | ---------------------------------------------------------------------------------------------- |
| Language       | **Python 3.11+**          | Non-negotiable: the causal-inference ecosystem (CausalML, EconML) is Python-only                |
| Web / webhooks | **FastAPI + Uvicorn**     | Async webhook receiver for Razorpay events; auto OpenAPI docs are a free repo-quality signal    |
| Schemas        | **Pydantic v2**           | Doubles as the enforcement layer — a proposed action that fails validation cannot reach the actuator |
| Dependencies   | **uv** (or Poetry)        | Fast, lockfile-based, reproducible installs                                                    |

#### Storage and the ledger

| Component         | Choice                             | Why                                                                                        |
| ----------------- | ---------------------------------- | ------------------------------------------------------------------------------------------- |
| Operational state | **SQLite**                         | Customers, transactions, fatigue counters, consent state, mandate state. Zero setup.        |
| Analytics         | **DuckDB**                         | Cohort aggregation, uplift feature tables, P&L computation. Embedded OLAP, reads Parquet directly. |
| Event archive     | **Parquet**                        | Columnar, compresses well, DuckDB queries it in place                                       |
| **Ledger**        | **Hand-rolled SHA-256 hash chain** (`hashlib`) | No dependency needed. Each row = `sha256(prev_hash + canonical_json(entry))` with sorted keys. A verifier walks the chain; mutate any row and it breaks. Cheap to build, very convincing on camera. |

> Say in the README: *Postgres is the production answer; SQLite + DuckDB is the ten-day answer.* Naming the tradeoff reads as judgment, not ignorance.

#### Scheduling

**APScheduler** — timed actions are core to the product, not an afterthought: 5-minute same-session nudges (Class B), inflow-day retries (Class C), post-outage sweeps (Class A). In-process, no broker. Note Celery + Redis as the scale path.

#### Simulator

**NumPy + plain Python**, with `numpy.random.default_rng(seed)` seeded everywhere so runs are reproducible and the sensitivity sweep is deterministic. SimPy is available if you want true discrete-event simulation, but a generator loop over a virtual clock is enough and easier to audit.

#### Causal inference — the core

| Component            | Choice                                        | Why                                                                                   |
| -------------------- | --------------------------------------------- | --------------------------------------------------------------------------------------- |
| Uplift / CATE        | **CausalML** (Uber)                           | Uplift trees and forests, plus **Qini curve and uplift-at-k built in** — the metrics you need |
| Second estimator     | **EconML** (Microsoft), optional              | DR-learner / DML as a robustness check — agreement between two estimators is a strong slide |
| Base learner         | **LightGBM**                                  | Gradient boosting beats neural nets on tabular data at this scale, trains in seconds, handles categoricals natively |
| Framework            | **scikit-learn**                              | Pipelines, cross-fitting, calibration                                                 |
| Numerics             | **NumPy · pandas · SciPy · statsmodels**      | Standard                                                                              |

#### Always-valid inference (the auto-pause kill-switch)

**`confseq`** (Howard & Ramdas confidence sequences) for always-valid CIs, **or ~50 lines of hand-rolled mSPRT / e-values** on SciPy. I'd write it yourself and unit-test it — it's short, it's the most statistically sophisticated thing in the repo, and owning the code lets you explain it in the video. This is what allows continuous monitoring without p-hacking.

#### Triage — correlated-failure detection

**SciPy `stats`** (Poisson / negative-binomial over-dispersion tests), **statsmodels** (EWMA baselines), optionally **`ruptures`** for changepoint detection.

Deliberately simple and explainable: a judge can follow a Poisson test against a rolling baseline. Nobody can audit an autoencoder, and "the anomaly model said so" is exactly the kind of unaccountable AI this project is arguing against.

#### AI layer — zero-cost, multi-provider

**Design constraint: the entire project runs on free tiers.** The three LLM jobs in this system have wildly different volume profiles, and routing each to the provider that fits is both the cheapest and the most defensible engineering choice.

| Job                              | Volume     | Provider                                      | Why                                                                       |
| -------------------------------- | ---------- | --------------------------------------------- | ------------------------------------------------------------------------- |
| **Diagnostic agent** (tool use)  | ~200 calls | **Google Gemini Flash** (AI Studio free tier) | Low volume, needs reliable function calling and reasoning. Free tier fits comfortably. No card required. |
| **Message generation**           | 1,140+     | **Ollama, local** (Llama 3.x 8B / Qwen, quantized) | This volume exceeds *every* free daily cap. Local inference is unlimited, offline and genuinely free. Runs on 16 GB RAM. |
| **Inbound-reply classification** | Medium     | **Groq** (`llama-3.3-70b-versatile`)          | Simple task, and Groq's free tier is generous (≈30 RPM / 1,000 RPD) and extremely fast. |

**Fallbacks:** Cerebras and Mistral both maintain permanent free tiers. OpenRouter is a poor primary — only ~50 free-model requests/day until you buy $10 of credit — but it's useful for one-off model comparison.

> **Verify quotas live.** Most providers stopped publishing fixed free-tier tables during 2026, and Gemini's Pro models left the free tier in April 2026. Check your actual project quota in AI Studio / the Groq console before you plan around a number.

**Everything runs behind one provider interface.** A single `LLMProvider` protocol — `complete()`, `classify()`, `call_with_tools()` — with a thin adapter per backend. Three reasons, and the third is the one that matters to a judge:

1. Free-tier limits shift without warning; you can swap providers mid-build without touching business logic.
2. It's what lets you route each job independently, as above.
3. **Provider-agnosticism is a repo-quality signal.** It also reinforces the thesis: the intelligence in this system is the causal core and the policy layer, not the model vendor. Any competent LLM can fill the slot.

**Design choices that map to rubric lines, and are provider-independent:**

- **Tool use for the diagnostic agent** — a narrow, bounded tool surface: `query_failures`, `get_rolling_baseline`, `open_incident`, `freeze_cohort`. Deliberately *not* open-ended exploration; the agent forms one hypothesis and makes two or three calls. Narrow scope is what keeps weaker free-tier models reliable.
- **Schema-validated outputs** — every LLM output is parsed into a Pydantic model before it goes anywhere. This is the *technical* boundary between "the AI proposes" and "the policy executes": a proposal that fails validation never reaches the actuator. Say that on camera.
- **Fixture record-and-replay** — every LLM call is cached to JSON on first execution and replayed from disk thereafter. This keeps you inside daily rate limits, makes the demo deterministic, and makes the whole submission reproducible from a fixed seed with no API keys at all. Judges can clone and run it.
- **Template-constrained generation** — messages are generated into approved templates, then passed through the compliance linter. Constraining the output shape is what makes an 8B local model perfectly adequate here.

**Does the free stack weaken the submission?** No. The rubric says *meaningful* AI implementation, not expensive. The differentiator is the causal core, the triage gate and the honest P&L; the LLM is an interface layer over them. The only real tradeoff is slightly less polished prose in incident notes and outbound messages — and both are template-constrained and linter-checked, so the gap barely shows.

#### Payments — Razorpay test mode

**`razorpay`** Python SDK. Surfaces used: **Orders**, **Payment Links** (instrument-update and part-pay flows), **Subscriptions / mandates** (UPI Autopay, e-mandate), **Webhooks** with HMAC signature verification, and **idempotency keys** on every write.

Practical note that saves you a day: use **ngrok** or a **Cloudflare Tunnel** to receive webhooks on localhost during development.

#### Messaging channels — be honest here

You cannot get TRAI DLT registration in ten days. So:

- **Mock channel adapter** for SMS/WhatsApp/voice — real message *content*, simulated delivery, full cost accounting.
- Optionally wire **one** live send through a WhatsApp/Twilio sandbox for the video, purely to prove the plumbing.
- Document the production path: **Gupshup / MSG91 / Karix** (DLT-registered Indian providers).

State the mock plainly in the README. A judge who spots an unacknowledged fake channel discounts everything else; a judge who reads "channels are mocked because DLT registration takes weeks — here's the adapter interface and the provider we'd use" reads it as competence.

#### Console — and how the whole thing gets demonstrated

**There is no website deliverable.** The submission is a public repo, a five-minute video, and the architecture. Nobody visits a URL. The console is a *prop for the video*, which means legibility in twenty seconds of screen recording beats visual sophistication every time.

**Stack: Streamlit**, with Plotly for charts. Not Next.js. On a compressed timeline with evening-sized work slots the trade is clear:

| | Next.js + Recharts | Streamlit + Plotly |
| ------------------------------- | ------------------ | ---------------------------------------- |
| Needs a FastAPI layer to serve data | Yes            | No — reads the ledger and DuckDB directly |
| Separate build and design pass  | Yes                | No                                       |
| Realistic cost                  | A full day or more | 3–4 hours                                |
| Looks better                    | Somewhat           | Adequate with care                       |

That "somewhat" is not worth a day. A console competing with the pitch video on the final day is how people end up shipping neither.

**Build it incrementally — one panel per day, as each capability lands.** Never as a final-day task.

| Day | Panel that appears                                                   |
| --- | -------------------------------------------------------------------- |
| 3   | Holdout split, and the ATE with its always-valid confidence interval |
| 4   | Qini curve and the sensitivity sweep                                 |
| 5   | **Incident timeline** — outage injected, baseline flooding, ANTAR frozen |
| 6   | **Counterfactual P&L** — the money shot                              |
| 7   | Polish, then record                                                  |

Each panel is 30–45 minutes on top of work already being done that evening. By the final day the console exists and the only remaining task is recording.

**Put each beat where it lands hardest.** Not everything belongs in the UI:

- **Ledger tamper demo → terminal.** `chain BROKEN at seq=2` reads as forensic evidence. The same message in a styled red box reads as decoration.
- **Stopping rules firing → terminal logs.**
- **P&L, Qini, sensitivity sweep, incident timeline → console.** These need to look like a financial statement and real charts.

**Free footage worth thirty seconds of the video: the Razorpay dashboard itself.** Test-mode payment links, orders and webhook delivery logs all appear in the real dashboard. Razorpay's own product confirming the agent is genuinely executing is more persuasive than any interface you could build.

**One cheap hedge:** have the pipeline emit a static `report.html` (Plotly, self-contained) and commit it. A judge who will not clone and run anything still sees the P&L and the charts by clicking one file in the repo. Roughly an hour of work.

At submission, the Streamlit app can also go to Streamlit Community Cloud for a free live URL — but only once the repo goes public.

#### Testing and reproducibility

| Component     | Choice                     | Why                                                                                                  |
| ------------- | -------------------------- | ------------------------------------------------------------------------------------------------------ |
| Tests         | **pytest**                 | Baseline                                                                                             |
| Property tests| **Hypothesis**             | The standout repo signal. Assert invariants: the ledger never verifies after mutation; the same action applied twice has one effect (idempotency); spend never exceeds the budget cap; a frozen cohort emits zero contacts. |
| Lint / types  | **ruff + mypy**            | Fast, and visible in CI                                                                              |
| CI            | **GitHub Actions**         | Green badge on a public repo; judges do look                                                         |
| Repro         | **Docker Compose + Makefile** | `make demo` runs the full pipeline end to end on a fixed seed                                     |
| Config        | Single **YAML** file       | Every knob — holdout %, budget, λ, self-recovery rates for the sweep — in one auditable place        |

#### What we deliberately did *not* use

Worth a short README section, because omissions are judgment:

- **No vector DB / RAG** — nothing here is a retrieval problem.
- **No agent framework (LangChain, CrewAI)** — the loop is ~40 lines with the SDK tool runner. A framework would obscure the decision trail, and the decision trail is the product.
- **No deep learning for uplift** — gradient boosting wins on tabular data at this scale and stays explainable. An unexplainable model would contradict the entire thesis.
- **No Kafka, no microservices** — the volume doesn't justify either, and both would cost days that belong to the causal core.

### 8.3 Ten-day plan

| Day | Deliverable                                                                  |
| --- | ---------------------------------------------------------------------------- |
| 1   | Simulator v0: customer types, A–F taxonomy, self-recovery dynamics           |
| 2   | Sensorium, hash-chained ledger, tamper verifier                              |
| 3   | Razorpay test-mode integration: orders, links, mandates, webhook loop        |
| 4   | Naive baseline bot — build the enemy properly and honestly                   |
| 5   | Holdout assignment + ATE with always-valid CIs                               |
| 6   | Uplift model + Qini; prove recovery of known ground truth                    |
| 7   | Triage agent: correlated-failure detection, incident freeze                  |
| 8   | Actuator: budgeted allocation, real actions, compliance linter, stopping rules |
| 9   | Counterfactual P&L, sensitivity sweep, console, inbound exception loop       |
| 10  | Pitch video, README, architecture doc, one-command repro                     |

**Cut order:** multilingual generation → Thompson sampling → console polish.
**Never cut:** the holdout, the triage gate, the P&L, or **visibly-real actions on Razorpay test APIs**. The track asks for an agent that *executes recovery workflows* — if the demo is mostly statistics you lose to a scrappier team whose bot obviously does things.

---

## 9. The five-minute pitch video

| Time      | Beat                                                                                                                                                                          |
| --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0:00–0:30 | "Recovery tools report a number they can't defend." Show the naive bot's dashboard: **INR 12.4L recovered, 76% success rate.** Let it look great.                              |
| 0:30–1:00 | Reveal the control arm. Real incremental: INR 8.1L. Cite the published 30–60% overstatement so it isn't just your claim.                                                       |
| 1:00–1:45 | The A–F table. **Highest success rate = lowest uplift.** This is the intellectual core; spend the time here.                                                                   |
| 1:45–2:45 | Live: inject an issuer outage. Baseline fires 4,000 messages. Antar opens an incident, **freezes the cohort**, schedules a re-route sweep, writes the incident note. Silence as a feature. |
| 2:45–3:30 | Uplift targeting: 1,140 contacts instead of 4,500, higher net value. Qini curve. Ground-truth recovery. **Sensitivity sweep — the ranking holds.**                             |
| 3:30–4:15 | Real Razorpay test-mode actions firing: payment link created, mandate re-auth sent, retry scheduled to inflow day, webhooks landing. Then stopping rules firing, ledger tamper demo, compliance veto. |
| 4:15–5:00 | The Counterfactual P&L with its CI and its retention-damage line. Close on the line in §12.                                                                                    |

---

## 10. Answering the hard questions

- **"Isn't the holdout lost revenue?"** It costs a measurable slice of one cohort and is the only thing making every other rupee credible. We report its cost as a line item. Merchants who won't pay for measurement pay far more for illusion.
- **"Hasn't Butter/Yuno done this?"** Yuno *documents* the problem. Butter ships agentic recovery with silent-recovery strategies. Neither exposes uplift as the live objective with a self-pausing kill-switch, neither prices retention damage into its own scorecard, and neither is built for UPI, Autopay or e-mandate. We're not claiming the idea; we're claiming the implementation and the market.
- **"This is dunning with extra steps."** Dunning maximises contacts × success rate. We maximise net value and deliberately skip 75% of eligible customers. Watch what each does during an outage.
- **"Your data is synthetic."** Deliberately — ground truth is required to validate a causal estimator. Payment plumbing runs on Razorpay test APIs. Here's the estimator recovering known true uplift, and here's the sensitivity sweep showing the conclusion doesn't depend on our assumptions.
- **"Where's the AI?"** Diagnostic agent, causal core, constrained generation, exception handling. And we'll tell you exactly where it *isn't*: it never authorises money.

---

## 11. Limitations and risks — state these before a judge does

| Risk / limitation                                | Reality                                                                                                                                                       |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Needs volume**                                 | Uplift estimation on a 10% holdout is noise below a few thousand failures/month. Works for large merchants; degrades badly on the long tail. Say so.           |
| **Merchants resist holdouts**                    | "Don't even try on 10%?" is a real commercial objection. Answer: it's the price of a defensible number, and we report it.                                      |
| **Control arms leak**                            | The holdout customer still gets the merchant's own marketing. Real holdouts are contaminated; the estimate is a lower bound.                                   |
| **Retention damage is the hardest line to measure** | Needs 30-day follow-up and the effect is small against noise. Conceptually the best part, practically the first to break. Present it with wide error bars.  |
| **Some contacts aren't optional**                | Mandate pre-debit notifications are legally required regardless of uplift. Carve them out of the optimisation explicitly.                                      |
| **Circularity of simulator results**             | Mitigated by the sensitivity sweep (§8.1). This is the question you must be ready for.                                                                        |
| **Reads as analytics, not an agent**             | Mitigated by making real test-API actions the visual centre of the demo.                                                                                      |
| **Uplift model may underperform on small data**  | That's a *finding*, not a failure — report it with its CI. A team that pre-registers a test and reports a null honestly is exactly what that rubric is fishing for. |

---

## 12. Verify before you pitch

Do not assert regulatory specifics from memory — a payments panel will catch it. Confirm current values for: e-mandate **pre-debit notification timing**; **AFA thresholds** for recurring debits (they differ by category); **TRAI DLT** rules for transactional SMS; **WhatsApp Business** template and session-window rules; **DNC quiet hours** for voice. Cite them in the README as *constraints implemented*, with sources. Cheap to get right; getting them wrong collapses the compliance pillar.

---

## 13. The line to lead with

> **"Everyone in this track will show you how much money their agent recovered. We're the only ones who can show you how much of it they actually caused — including on our own numbers."**

**अंतर — only the difference counts.**
