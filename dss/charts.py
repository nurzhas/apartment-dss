"""Step 7: plotly figures. Built with the light palette; the web page swaps the light hex
values for their dark steps when the dark theme is on (see LIGHT_TO_DARK in app.js)."""
from __future__ import annotations

import json
from typing import Dict, List, Optional, Sequence

import pandas as pd
import plotly.graph_objects as go

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SEQUENTIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#104281"]
INK, INK_2, MUTED, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"


def _layout(fig: go.Figure, height: int, **extra) -> go.Figure:
    extra.setdefault("margin", dict(l=8, r=16, t=8, b=8))
    fig.update_layout(
        height=height, paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)", font=dict(family=FONT, size=12, color=INK_2),
        hoverlabel=dict(font=dict(family=FONT, size=12)), legend=dict(orientation="h", y=1.02, x=0, yanchor="bottom",
                                                                         font=dict(color=INK_2)),
        **extra)
    fig.update_xaxes(gridcolor=GRID, linecolor=AXIS, zerolinecolor=AXIS, tickfont=dict(color=MUTED), automargin=True)
    fig.update_yaxes(gridcolor=GRID, linecolor=AXIS, zerolinecolor=AXIS, tickfont=dict(color=MUTED), automargin=True)
    return fig


def scores_bar(table: pd.DataFrame, labels: Dict[str, str], top: int = 15) -> go.Figure:
    """Grouped horizontal bars: the score of each method (WSM, TOPSIS, WPM - whichever the table has)
    for the leading alternatives. table: compare_methods()['table'] or compare_rankings()['table']."""
    t = table.sort_values(["wsm_rank", "topsis_rank"]).head(top).iloc[::-1]
    y = [labels.get(a, a) for a in t.index]
    fig = go.Figure()
    methods = [(n, c, s) for n, c, s in (("WSM", "wsm_score", SERIES[0]), ("TOPSIS", "topsis_score", SERIES[1]),
                                         ("WPM", "wpm_score", SERIES[2])) if c in t.columns]
    for name, col, color in methods:
        rank = t[col.replace("score", "rank")]
        fig.add_bar(name=name, y=y, x=t[col], orientation="h", marker=dict(color=color, line=dict(width=0)),
                    customdata=rank, hovertemplate=f"<b>%{{x:.4f}}</b> · место %{{customdata}}<br>%{{y}}<extra>{name}</extra>")
    fig.update_layout(barmode="group", bargap=0.3, bargroupgap=0.1)
    fig.update_xaxes(range=[0, 1.0], title=dict(text="Итоговый балл (0–1)", font=dict(color=MUTED)))
    return _layout(fig, max(280, (14 + 10 * len(methods)) * len(t) + 70))


def contribution_bar(contrib: pd.DataFrame, names: Dict[str, str], labels: Dict[str, str],
                     order: Sequence[str], top: int = 15, max_series: int = 7) -> go.Figure:
    """Stacked horizontal bars: what each criterion added to the WSM score (w_j * r_ij).
    order: criterion keys in their fixed colour order; past max_series they fold into "Прочие"."""
    c = contrib.loc[contrib.sum(axis=1).sort_values(ascending=False).index].head(top).iloc[::-1]
    keys = [k for k in order if k in c.columns]
    shown, rest = keys[:max_series], keys[max_series:]
    y = [labels.get(a, a) for a in c.index]
    fig = go.Figure()
    for i, k in enumerate(shown):
        fig.add_bar(name=names.get(k, k), y=y, x=c[k], orientation="h",
                    marker=dict(color=SERIES[i % len(SERIES)], line=dict(color=SURFACE, width=2)),
                    hovertemplate=f"<b>%{{x:.4f}}</b><br>%{{y}}<extra>{names.get(k, k)}</extra>")
    if rest:
        fig.add_bar(name="Прочие критерии", y=y, x=c[rest].sum(axis=1), orientation="h",
                    marker=dict(color=MUTED, line=dict(color=SURFACE, width=2)),
                    hovertemplate="<b>%{x:.4f}</b><br>%{y}<extra>Прочие</extra>")
    fig.update_layout(barmode="stack", bargap=0.35)
    fig.update_xaxes(title=dict(text="Вклад в балл WSM", font=dict(color=MUTED)))
    fig = _layout(fig, max(300, 30 * len(c) + 120))
    fig.update_layout(legend=dict(traceorder="normal"))
    return fig


def rank_slope(table: pd.DataFrame, labels: Dict[str, str], top: int = 15) -> go.Figure:
    """Slope chart: place by WSM on the left, by TOPSIS on the right. Blue - TOPSIS puts the
    flat higher, orange - lower, grey - the same place."""
    t = table[(table["wsm_rank"] <= top) | (table["topsis_rank"] <= top)]
    groups = [("Место то же", MUTED, t["shift"] == 0), ("TOPSIS выше", SERIES[0], t["shift"] < 0),
              ("TOPSIS ниже", SERIES[1], t["shift"] > 0)]
    fig = go.Figure()
    for name, color, mask in groups:                 # grey first, colours on top
        first = True
        for a, r in t[mask].iterrows():
            fig.add_scatter(x=["WSM", "TOPSIS"], y=[r.wsm_rank, r.topsis_rank], mode="lines+markers+text",
                            line=dict(color=color, width=2), marker=dict(size=9, color=color,
                                                                          line=dict(color=SURFACE, width=2)),
                            text=[a, a], textposition=["middle left", "middle right"],
                            textfont=dict(color=INK_2, size=11), name=name, legendgroup=name, showlegend=first,
                            hovertemplate=f"{labels.get(a, a)}<br>WSM: {int(r.wsm_rank)} → TOPSIS: "
                                          f"{int(r.topsis_rank)}<extra></extra>")
            first = False
    n = int(max(t["wsm_rank"].max(), t["topsis_rank"].max())) if len(t) else 1
    fig.update_yaxes(autorange=False, range=[n + 0.6, 0.4], dtick=1, title=dict(text="Место", font=dict(color=MUTED)))
    fig.update_xaxes(showgrid=False)
    return _layout(fig, max(300, 26 * n + 60), margin=dict(l=40, r=40, t=8, b=8))


def heatmap(normalized: pd.DataFrame, raw: pd.DataFrame, names: Dict[str, str], labels: Dict[str, str],
            order: Sequence[str], top_index: Sequence[str]) -> go.Figure:
    """Normalized decision matrix (1 = the best value among alternatives) for the leaders."""
    keys = [k for k in order if k in normalized.columns]
    n = normalized.loc[list(top_index), keys]
    r = raw.loc[list(top_index), keys]
    fig = go.Figure(go.Heatmap(
        z=n.values, x=[names.get(k, k) for k in keys], y=[labels.get(a, a) for a in n.index],
        customdata=r.values, zmin=0, zmax=1, xgap=2, ygap=2,
        colorscale=[[i / (len(SEQUENTIAL) - 1), c] for i, c in enumerate(SEQUENTIAL)],
        colorbar=dict(title=dict(text="норм.", font=dict(color=MUTED)), tickfont=dict(color=MUTED), thickness=10),
        hovertemplate="%{y}<br>%{x}: <b>%{customdata:,.2f}</b><br>нормализовано: %{z:.3f}<extra></extra>"))
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(showgrid=False, side="top", tickangle=-35)
    return _layout(fig, max(280, 28 * len(n) + 90))


def sensitivity_lines(scores: pd.DataFrame, labels: Dict[str, str], criterion_name: str, current: float,
                      thresholds: List[dict], max_series: int = 5) -> go.Figure:
    """Score of each leading alternative as the weight of one criterion goes 0 -> 1."""
    cols = list(scores.columns)[:max_series]
    fig = go.Figure()
    for i, a in enumerate(cols):
        fig.add_scatter(x=scores.index, y=scores[a], mode="lines", name=labels.get(a, a),
                        line=dict(color=SERIES[i], width=2),
                        hovertemplate="вес %{x:.2f}: <b>%{y:.4f}</b><extra>" + labels.get(a, a) + "</extra>")
    fig.add_vline(x=current, line=dict(color=MUTED, width=1))
    fig.add_annotation(x=current, y=1, yref="paper", text=f"текущий вес {current:.2f}", showarrow=False,
                       xanchor="left", yanchor="top", font=dict(color=MUTED, size=11), xshift=4)
    for th in thresholds[:4]:
        fig.add_vline(x=th["weight"], line=dict(color=AXIS, width=1))
    fig.update_xaxes(range=[0, 1], title=dict(text=f"Вес критерия «{criterion_name}»", font=dict(color=MUTED)))
    fig.update_yaxes(title=dict(text="Балл", font=dict(color=MUTED)))
    fig.update_layout(hovermode="x unified")
    return _layout(fig, 340)


def figure_json(fig: Optional[go.Figure]) -> Optional[dict]:
    # plotly's own encoder: numpy arrays and pandas indexes become plain JSON lists
    return None if fig is None else json.loads(fig.to_json())
