# Pitch video — shot list and narration

Five minutes is **about 700 spoken words**. This script is ~720. Read it at a
normal pace; do not rush to fit more in.

**Do not read it word for word.** Verbatim reading is audible and it flattens
everything. Learn the beats, then say them in your own words — you built this,
so you already know what each screen shows. The exceptions are marked **[VERBATIM]**
below: those are precision claims where the wording is doing real work, and an
approximation would either overclaim or lose the point.

**Record the demo footage first, narrate over it second.** Trying to talk and
drive the terminal at the same time is where takes get lost.

---

## Before you record

- [ ] `python scripts/run_day1.py` … `run_day6.py` — all six green
- [ ] `python scripts/build_report.py`
- [ ] `streamlit run console/app.py` — light theme is pinned in .streamlit/config.toml;
      browser zoom ~125%, and Ctrl+R once so it picks the theme up
- [ ] Terminal font large enough to read at 720p — this is the usual mistake
- [ ] Razorpay dashboard open on the test-mode Payment Links page
- [ ] `slides/title.html` open in a second tab, F11 fullscreen (frames 1 and 2)
- [ ] Water. You will do more takes than you expect.

---

## 0:00–0:30 · The number that looks great

**Screen:** console, P&L panel, baseline column only.

> "This is a payment recovery bot. Over one month it recovered eighty-four lakh
> rupees for a merchant, from failed payments it chased down. That is the number
> it reports, and it is the number every recovery tool in the market reports.
>
> It is mostly fiction. Let me show you why."

---

## 0:30–1:15 · The control group

**Screen:** console → *The control group* panel, then the P&L waterfall.

> "In India, when a UPI payment fails, most people just try again. Nobody has to
> ask them. So if you message everyone whose payment failed and then take credit
> for whoever pays, you are taking credit for money that was already coming.
>
> The only way to know the difference is to deliberately not message some of
> them. We hold back ten percent of failures at random — permanently. That
> control group is the entire product.
>
> Against it, that eighty-four lakh headline is seven lakh of actual recovery.
> The rest was arriving anyway."

---

## 1:15–2:00 · The inversion — *the intellectual core, do not rush*

**Screen:** `RESULTS.md` section 2, or the console forest plot.

> **[VERBATIM from here to the end of this beat]**
>
> "It gets worse than a wrong dashboard. Look at what the metric does to
> targeting.
>
> When a bank's UPI goes down, payments fail — and those customers retry on their
> own about ninety-two percent of the time. Message them and they pay, so on a
> success-rate metric they look like your best cohort. Our measured effect for
> them is statistically indistinguishable from doing nothing.
>
> Now expired cards. Almost none of those recover on their own — the card cannot
> work. Success rate around thirty percent, so the bot avoids them. But every one
> of those thirty percent is *yours*.
>
> The bot chases the cohort it cannot help and ignores the one that needs it. The
> best-looking recovery rate is produced by the worst policy."

---

## 2:00–2:45 · Silence during an outage

**Screen:** run `python scripts/run_day5.py`, let section 3 land. Then the
console incident panel.

**Then cut to the Razorpay dashboard → Payment Links (Test Mode).**

> "Second problem. When an issuer degrades, thousands of payments fail at once.
> That is *one* incident about a bank, not thousands about customers.
>
> ANTAR runs a Poisson test on each rail against the same hour on previous days —
> simple enough that you can check it by hand. When a burst is systemic, it
> freezes the whole cohort.
>
> Inside the incident window, the baseline would message thirty-one customers to
> tell them a payment failed during a window when the bank was down and nothing
> they could do would have helped. ANTAR messages zero.
>
> Silence is the correct action here, and no success-rate metric will ever reward
> it.
>
> And these aren't simulated. Here are the payment links the agent created, in
> Razorpay's own test dashboard. Every write carries an idempotency key — I ran
> it twice, and the second run created nothing new. Replaying this agent cannot
> double-charge anyone."

---

## 2:45–3:30 · It works, and we can prove it

**Screen:** console Qini curve, then the sweep chart.

> "The targeting model learns two functions — one per arm — and ranks by the
> difference. It never sees both outcomes for the same transaction, yet its
> predictions correlate zero-point-eight-eight with the true effect it was never
> shown.
>
> On Qini, ANTAR scores plus zero-four-three. The baseline scores *minus*
> zero-two-one — worse than random at finding incremental responders.
>
> **[VERBATIM]** And the obvious objection: our headline depends on how often customers
> self-recover, which is a number we chose. So we swept it. The multiple moves a
> lot. The ordering does not — ANTAR is never behind, and its advantage grows as
> self-recovery rises, which is the Indian case. We claim the direction, not the
> multiple."

---

## 3:30–4:15 · The parts that stop it

**Screen:** `run_day6.py` sections 1 and 2, then terminal tamper demo from
`run_day1.py` section 5.

> "Three things can stop this agent.
>
> Compliance vetoed twenty-eight hundred of five thousand planned actions —
> quiet hours in local time, missing consent, contact caps. Every refusal is
> logged with the rule that fired.
>
> Second, arms stop themselves. Transient rail failures paused automatically:
> effect indistinguishable from zero. That is the cohort the baseline spends most
> of its budget on, and we stop it without being told to.
>
> Third, the ledger. Every decision is hash-chained. Watch — I tamper with one
> entry, and verification names the exact row that moved."

---

## 4:15–5:00 · The P&L, and the honest part

**Screen:** console P&L waterfall, both columns.

> **[VERBATIM to the end]**
>
> "So here is the statement nobody else produces. Headline claim. Minus what the
> control group says was coming anyway. Minus channel cost, discounts — and
> retention damage, measured from the same control arm, because contacting people
> costs you some of them.
>
> The baseline: one-one-one thousand net. ANTAR: two-eight-nine thousand net, on
> forty-four percent fewer contacts.
>
> Two things I want to say plainly. The confidence interval on that bottom line is
> enormous, and we print it, because one lost customer swamps a lot of margin. And
> the magnitudes come from a simulator, which is exactly why the sweep exists.
>
> Everyone in this track will tell you how much money their agent recovered. We
> are the only ones who can tell you how much of it we actually caused —
> including on our own numbers."

---

## Cutting to time

If you overrun, cut **2:45–3:30 down to the Qini line only** (keep the sweep
sentence). Never cut the inversion at 1:15 or the P&L at 4:15 — those are the
argument.

## Do not say

- ~~"nobody has ever done this"~~ — uplift modelling and holdouts are textbook.
  Say: *"this is known best practice that almost nobody implements, and nobody
  has built it for Indian rails."*
- ~~"we recovered X"~~ — always *"we caused X"*.
- ~~quoting our overstatement ratio as Yuno's 30–60%~~ — different quantities.
