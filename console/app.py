"""ANTAR console.

A viewer, never a compute engine. It reads the artifacts the pipeline writes to
data/ and renders them. Panels are added one per build day; the pipeline stays
runnable with no console, and the console stays instant with no pipeline.

    streamlit run console/app.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

st.set_page_config(page_title="ANTAR", page_icon=":bar_chart:", layout="wide")

# -- theme tokens -----------------------------------------------------------
# Both modes are selected, not flipped: the dark column is the same two hues
# re-stepped for the dark surface. Validated with the palette checker.
_DARK = st.get_option("theme.base") == "dark"

SURFACE = "#1a1a19" if _DARK else "#fcfcfb"
TEXT_PRIMARY = "#ffffff" if _DARK else "#0b0b0b"
TEXT_SECONDARY = "#c3c2b7" if _DARK else "#52514e"
GRID = "#33322f" if _DARK else "#e6e5e1"
SERIES_1 = "#3987e5" if _DARK else "#2a78d6"   # estimate
SERIES_2 = "#d95926" if _DARK else "#eb6834"   # ground truth
MUTED = "#7a7973" if _DARK else "#8a8983"


def base_layout(fig: go.Figure, height: int, xtitle: str = "", ytitle: str = "") -> go.Figure:
    fig.update_layout(
        height=height,
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(color=TEXT_SECONDARY, size=13),
        margin=dict(l=10, r=20, t=30, b=10),
        hoverlabel=dict(bgcolor=SURFACE, font_size=13),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        xaxis_title=xtitle,
        yaxis_title=ytitle,
    )
    fig.update_xaxes(gridcolor=GRID, zeroline=False, linecolor=GRID)
    fig.update_yaxes(gridcolor=GRID, zeroline=False, linecolor=GRID)
    return fig


def load(name: str) -> dict | None:
    path = DATA / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- header
st.title("ANTAR — अंतर")
st.caption("Only the difference counts. · Track 03 — AI Revenue Recovery")

day3 = load("day3_results.json")
if day3 is None:
    st.warning("No results yet. Run `python scripts/run_day3.py` first.")
    st.stop()

cfg, a, ate = day3["config"], day3["assignment"], day3["ate"]

# ------------------------------------------------------- panel: holdout
st.subheader("The control group")
st.caption(
    "Two numbers and a checkmark — not a chart. The point of this panel is that "
    "the arms are recomputable by anyone holding the salt, so no transaction can "
    "be quietly moved after its outcome is known."
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Treated", f"{a['treatment']:,}")
c2.metric("Held out", f"{a['control']:,}", f"{a['realised_holdout']:.2%} of failures")
c3.metric("Salt", cfg["salt"])
c4.metric("Recomputable", "yes" if a["recomputable"] else "NO")

# ----------------------------------------------------------- panel: ATE
st.divider()
st.subheader("Average treatment effect")

av, fh = ate["always_valid"], ate["fixed_horizon"]
m1, m2, m3 = st.columns(3)
m1.metric(
    "Incremental recovery rate",
    f"{av['point']:+.4f}",
    f"95% CS [{av['lower']:+.4f}, {av['upper']:+.4f}]",
    delta_color="off",
)
m2.metric("Ground truth", f"{ate['truth']:+.4f}", "covered" if ate["covered"] else "MISSED",
          delta_color="normal" if ate["covered"] else "inverse")
m3.metric("Width vs fixed-horizon", f"{ate['width_ratio']:.2f}x",
          "the price of peeking", delta_color="off")

st.caption(
    f"Treated arm recovers at {ate['treated_rate']:.1%}, control at {ate['control_rate']:.1%}. "
    "The always-valid interval is wider than an ordinary one on purpose: it stays "
    "correct no matter how often ANTAR looks, which is what lets a stopping rule "
    "exist at all."
)

# -------------------------------------------------- panel: forest plot
st.divider()
st.subheader("Effect by decline class")

rows = list(reversed(day3["by_class"]))
labels = [r["class"].replace("_", " ").title() for r in rows]

fig = go.Figure()
fig.add_vline(x=0, line_width=2, line_dash="dash", line_color=MUTED)

fig.add_trace(go.Scatter(
    x=[r["point"] for r in rows],
    y=labels,
    error_x=dict(
        type="data",
        symmetric=False,
        array=[r["upper"] - r["point"] for r in rows],
        arrayminus=[r["point"] - r["lower"] for r in rows],
        color=SERIES_1,
        thickness=2,
        width=6,
    ),
    mode="markers",
    marker=dict(size=11, color=SERIES_1, line=dict(width=2, color=SURFACE)),
    name="Estimate (95% confidence sequence)",
    hovertemplate="<b>%{y}</b><br>estimate %{x:+.3f}<extra></extra>",
))

fig.add_trace(go.Scatter(
    x=[r["truth"] for r in rows],
    y=labels,
    mode="markers",
    marker=dict(size=10, color=SERIES_2, symbol="diamond",
                line=dict(width=2, color=SURFACE)),
    name="Ground truth",
    hovertemplate="<b>%{y}</b><br>true uplift %{x:+.3f}<extra></extra>",
))

st.plotly_chart(base_layout(fig, 380, xtitle="Incremental recovery rate"),
                use_container_width=True)

undetected = [r["class"] for r in day3["by_class"] if not r["detected"]]
st.caption(
    "An interval crossing the dashed line cannot be distinguished from doing "
    "nothing. Transient rail failures sit there — and that is where a "
    "conventional bot spends most of its budget. "
    + (
        f"**{', '.join(c.replace('_', ' ').title() for c in undetected)}** are *not* null: "
        "their true effects are real, but their control arms are too thin to resolve "
        "an effect this size under a time-uniform bound. That is a power limit, and "
        "it is reported as one."
        if undetected else ""
    )
)

with st.expander("Table view"):
    st.dataframe(
        [
            {
                "Class": r["class"],
                "Estimate": round(r["point"], 4),
                "Lower": round(r["lower"], 4),
                "Upper": round(r["upper"], 4),
                "Truth": round(r["truth"], 4),
                "Verdict": "detected" if r["detected"] else "indistinguishable",
                "n treated": r["n_treated"],
                "n control": r["n_control"],
            }
            for r in day3["by_class"]
        ],
        use_container_width=True,
        hide_index=True,
    )

# ------------------------------------------------------ panel: peeking
st.divider()
st.subheader("Why the interval is wide")

p = day3["peeking"]
methods = ["Fixed-horizon<br>(recomputed each peek)", "Always-valid<br>(confidence sequence)"]
rates = [p["fixed_horizon_fpr"], p["always_valid_fpr"]]

bars = go.Figure()
bars.add_trace(go.Bar(
    x=methods,
    y=rates,
    marker=dict(color=SERIES_1, line=dict(width=0)),
    width=0.5,
    text=[f"{r:.1%}" for r in rates],
    textposition="outside",
    textfont=dict(color=TEXT_PRIMARY, size=15),
    hovertemplate="%{x}<br>false positive rate %{y:.1%}<extra></extra>",
    showlegend=False,
))
bars.add_hline(
    y=p["alpha"],
    line_width=2,
    line_dash="dash",
    line_color=MUTED,
    annotation_text=f"nominal {p['alpha']:.0%}",
    annotation_position="right",
    annotation_font_color=TEXT_SECONDARY,
)
bars.update_yaxes(tickformat=".0%", range=[0, max(rates) * 1.25 + 0.02])
st.plotly_chart(base_layout(bars, 340, ytitle="False positive rate"),
                use_container_width=True)

st.caption(
    f"{p['n_experiments']} experiments where the true effect is **exactly zero**, "
    f"peeked {p['peeks_per_experiment']} times each, stopping on the first "
    "interval that excluded zero. Every alarm is false by construction. An agent "
    "that monitors continuously and stops on significance needs the right-hand bar, "
    "or its stopping rule manufactures the effect it stops for."
)

day4 = load("day4_results.json")
if day4 is None:
    st.divider()
    st.info("Run `python scripts/run_day4.py` to add the targeting panels.")
    st.stop()

# ------------------------------------------------------- panel: targeting
st.divider()
st.subheader("Targeting: same data, same budget, different objective")

b, an = day4["policies"]["baseline"], day4["policies"]["antar"]
t1, t2, t3 = st.columns(3)
t1.metric("Baseline — actually caused", f"₹{b['true_incremental_inr']:,.0f}",
          f"claims ₹{b['gross_claimed_inr']:,.0f}", delta_color="off")
t2.metric("ANTAR — actually caused", f"₹{an['true_incremental_inr']:,.0f}",
          f"claims ₹{an['gross_claimed_inr']:,.0f}", delta_color="off")
t3.metric("Incremental revenue",
          f"{an['true_incremental_inr'] / max(b['true_incremental_inr'], 1e-9):.2f}x",
          f"{b['contacts']:,} contacts either way", delta_color="off")

st.caption(
    f"Persuadables reached: **{b['persuadable_share']:.1%}** → **{an['persuadable_share']:.1%}**. "
    f"Contacts that changed nothing: **{b['wasted_share']:.1%}** → **{an['wasted_share']:.1%}**. "
    "The baseline reports the larger headline number and creates less money."
)

# ------------------------------------------------------------ panel: qini
st.divider()
st.subheader("Qini — is the ranking any good?")

q = day4["qini"]
qfig = go.Figure()
qfig.add_trace(go.Scatter(
    x=q["fractions"], y=q["random"], mode="lines", name="Random targeting",
    line=dict(color=MUTED, width=2, dash="dash"),
    hovertemplate="random: %{y:.0f}<extra></extra>",
))
qfig.add_trace(go.Scatter(
    x=q["fractions"], y=q["naive"], mode="lines", name="Baseline (success rate)",
    line=dict(color=SERIES_2, width=2),
    hovertemplate="baseline: %{y:.0f} incremental<extra></extra>",
))
qfig.add_trace(go.Scatter(
    x=q["fractions"], y=q["antar"], mode="lines", name="ANTAR (treatment effect)",
    line=dict(color=SERIES_1, width=2),
    hovertemplate="ANTAR: %{y:.0f} incremental<extra></extra>",
))
qfig.update_xaxes(tickformat=".0%")
st.plotly_chart(
    base_layout(qfig, 400, xtitle="Share of failures targeted",
                ytitle="Incremental responders"),
    use_container_width=True,
)

qa, qn = day4["model"]["qini_antar"], day4["model"]["qini_naive"]
st.caption(
    f"Qini coefficient — ANTAR **{qa:+.3f}**, baseline **{qn:+.3f}**. "
    "Above the dashed line means the ranking finds uplift that random targeting "
    "would miss; below it means the ranking is actively worse than picking at "
    "random. Accuracy would not reveal this: a model can rank *outcomes* "
    "perfectly and *uplift* terribly, which is exactly what the baseline does."
)

# --------------------------------------------------------- panel: sweep
st.divider()
st.subheader("Does this survive moving the assumption?")

sw = day4["sweep"]
sfig = go.Figure()
sfig.add_trace(go.Scatter(
    x=[p["mean_p0"] for p in sw], y=[p["baseline_inr"] for p in sw],
    mode="lines+markers", name="Baseline",
    line=dict(color=SERIES_2, width=2),
    marker=dict(size=9, line=dict(width=2, color=SURFACE)),
    hovertemplate="self-recovery %{x:.2f}<br>baseline ₹%{y:,.0f}<extra></extra>",
))
sfig.add_trace(go.Scatter(
    x=[p["mean_p0"] for p in sw], y=[p["antar_inr"] for p in sw],
    mode="lines+markers", name="ANTAR",
    line=dict(color=SERIES_1, width=2),
    marker=dict(size=9, line=dict(width=2, color=SURFACE)),
    hovertemplate="self-recovery %{x:.2f}<br>ANTAR ₹%{y:,.0f}<extra></extra>",
))
st.plotly_chart(
    base_layout(sfig, 380, xtitle="Mean self-recovery rate (assumption under test)",
                ytitle="Revenue actually caused (INR)"),
    use_container_width=True,
)

adv = [p["advantage"] for p in sw]
st.caption(
    f"ANTAR's advantage runs **{adv[0]:.2f}x → {adv[-1]:.2f}x**, rising with "
    "self-recovery and never falling behind. Read it in two parts. The "
    "**magnitude does not survive** — at low self-recovery the policies converge "
    "to a tie, because when almost nobody pays unaided everything has uplift and "
    "ranking by success rate is nearly the same ordering. The **direction does** — "
    "the more customers pay without being asked, which is the Indian case, the "
    "more targeting by treatment effect is worth."
)

with st.expander("Sweep table"):
    st.dataframe(
        [
            {
                "Self-recovery scale": p["scale"],
                "Mean p0": round(p["mean_p0"], 3),
                "Baseline (INR)": round(p["baseline_inr"]),
                "ANTAR (INR)": round(p["antar_inr"]),
                "Advantage": f"{p['advantage']:.2f}x",
                "Baseline wasted": f"{p['baseline_wasted']:.1%}",
                "ANTAR wasted": f"{p['antar_wasted']:.1%}",
            }
            for p in sw
        ],
        use_container_width=True,
        hide_index=True,
    )

st.divider()
st.caption(f"seed {cfg['seed']} · n={cfg['n']:,} failures · generated {day3['generated_at'][:19]}Z")
