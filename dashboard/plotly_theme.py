"""One Plotly template for every chart on the page.

Warm paper under the plot and the page, ink axes and gridlines, mono tick labels,
and the asset-class palette unchanged: equity #1d4ed8, rates #047857, credit
#7c3aed, commodity #d97706. A figure that goes through dashboard.views.operational.
_plot gets this template applied, so a new chart cannot arrive in Plotly's default
blue-grey look.

Display only: no series, no number and no axis range is set here.
"""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio

from dashboard.theme import ACID, FONT_MONO, FONT_SANS, INK, LINE, MUTED, ORANGE, PAPER, PAPER_2

TEMPLATE_NAME = "credit_lab_paper"

# The asset-class palette, unchanged: colour carries the sleeve and nothing else.
ASSET_CLASS_PALETTE: tuple[str, ...] = ("#1d4ed8", "#047857", "#7c3aed", "#d97706")

# Warm equivalents of the greys the charts used to hard-code, at the same lightness.
# The old neutral series grey was #8a8a8a; #8f897c sits at the same lightness on the
# warm side. The old grid was black at 6 percent; ink at 9 percent is the same weight
# on paper.
CHART_NEUTRAL = "#8f897c"
CHART_GRID = "rgba(25,23,19,0.10)"
CHART_LINE = LINE


def build_template() -> go.layout.Template:
    """The template, built once."""
    axis = dict(
        showline=True,
        linewidth=1,
        linecolor=INK,
        ticks="outside",
        ticklen=4,
        tickcolor=INK,
        tickfont=dict(family=FONT_MONO, size=10, color=MUTED),
        title=dict(font=dict(family=FONT_MONO, size=10, color=MUTED)),
        zeroline=True,
        zerolinewidth=1,
        zerolinecolor=LINE,
        gridcolor=CHART_GRID,
        gridwidth=1,
        automargin=True,
    )
    return go.layout.Template(
        layout=go.Layout(
            paper_bgcolor=PAPER,
            plot_bgcolor=PAPER,
            font=dict(family=FONT_SANS, size=12, color=INK),
            colorway=list(ASSET_CLASS_PALETTE),
            title=dict(font=dict(family=FONT_SANS, size=13, color=INK)),
            xaxis=dict(axis),
            yaxis=dict(axis),
            legend=dict(
                orientation="h",
                bgcolor="rgba(0,0,0,0)",
                bordercolor=LINE,
                borderwidth=0,
                font=dict(family=FONT_MONO, size=10, color=INK),
                title=dict(font=dict(family=FONT_MONO, size=10, color=MUTED)),
            ),
            hoverlabel=dict(
                bgcolor=INK,
                bordercolor=INK,
                font=dict(family=FONT_MONO, size=11, color=PAPER),
            ),
            hovermode="x unified",
            margin=dict(l=10, r=10, t=40, b=10),
            separators=".,",
            modebar=dict(bgcolor="rgba(0,0,0,0)", color=MUTED, activecolor=INK),
        ),
    )


TEMPLATE: go.layout.Template = build_template()

# Register it as the default too, so a figure drawn outside _plot still lands on
# paper rather than Plotly's own default.
pio.templates[TEMPLATE_NAME] = TEMPLATE
pio.templates.default = TEMPLATE_NAME


def apply(fig: go.Figure) -> go.Figure:
    """Put one figure on paper. The figure is returned for convenience."""
    fig.update_layout(template=TEMPLATE)
    return fig
