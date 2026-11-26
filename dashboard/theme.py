"""The page's look: warm paper, ink rules, one acid accent.

One stylesheet, injected once, plus the three pieces of markup the theme cannot
express: the masthead, a section's window bar, and a ruled table. Everything else
is a rule over Streamlit's stable data-testid attributes, so a Streamlit upgrade
that renames a generated class cannot silently strip the page back to default.

The tokens are the EFB dashboard's (web/src/style.css): paper #f3f0e8, paper-2
#e9e5dc for bars and zebra rows, ink #191713 for text and 1px rules, muted
#6e6a61 for secondary text, acid #e6ff4f for the one accent, orange #ff5a36 for
alerts and exceptions. Words are Atkinson Hyperlegible, self-hosted from
dashboard/static; labels and figures are the system monospace.

This module is display only. It reads no data and changes no number.
"""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st

# The page's palette, in one place, for the module that draws charts with it.
PAPER = "#f3f0e8"
PAPER_2 = "#e9e5dc"
INK = "#191713"
MUTED = "#6e6a61"
LINE = "rgba(25,23,19,.22)"
ACID = "#e6ff4f"
ORANGE = "#ff5a36"
ORANGE_FILL = "#ffe4d9"

FONT_SANS = '"Atkinson Hyperlegible", "Arial Narrow", ui-sans-serif, system-ui, sans-serif'
FONT_MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

# Served by Streamlit itself (server.enableStaticServing) out of dashboard/static.
FONT_DIR = "/app/static/fonts"

CSS = """
@font-face {
  font-family: "Atkinson Hyperlegible";
  src: url("/app/static/fonts/atkinson-hyperlegible-latin-400-normal.woff2") format("woff2");
  font-weight: 400;
  font-style: normal;
  font-display: swap;
}
@font-face {
  font-family: "Atkinson Hyperlegible";
  src: url("/app/static/fonts/atkinson-hyperlegible-latin-400-italic.woff2") format("woff2");
  font-weight: 400;
  font-style: italic;
  font-display: swap;
}
@font-face {
  font-family: "Atkinson Hyperlegible";
  src: url("/app/static/fonts/atkinson-hyperlegible-latin-700-normal.woff2") format("woff2");
  font-weight: 700;
  font-style: normal;
  font-display: swap;
}

:root {
  --cl-paper: #f3f0e8;
  --cl-paper-2: #e9e5dc;
  --cl-ink: #191713;
  --cl-muted: #6e6a61;
  --cl-line: rgba(25, 23, 19, 0.22);
  --cl-acid: #e6ff4f;
  --cl-orange: #ff5a36;
  --cl-orange-fill: #ffe4d9;
  --cl-sans: "Atkinson Hyperlegible", "Arial Narrow", ui-sans-serif, system-ui, sans-serif;
  --cl-mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --cl-max: 110rem;
  --cl-pad: clamp(0.75rem, 3vw, 3rem);
}

/* ---------------------------------------------------------------- the page */

html, body, .stApp, [data-testid="stAppViewContainer"] {
  background-color: var(--cl-paper);
  color: var(--cl-ink);
  font-family: var(--cl-sans);
  -webkit-font-smoothing: antialiased;
}

.stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea, .stApp select,
.stApp button, .stApp [data-testid="stMarkdownContainer"],
.stApp [data-testid="stCaptionContainer"], .stApp [data-testid="stMetricValue"],
.stApp [data-testid="stMetricLabel"], .stApp h1, .stApp h2, .stApp h3, .stApp h4,
.stApp h5, .stApp h6 {
  font-family: var(--cl-sans);
}

/* Icons are a font of their own; the word face must not reach them. */
.stApp [data-testid="stIconMaterial"], .stApp .material-symbols-rounded {
  font-family: "Material Symbols Rounded", sans-serif;
}

::selection { background: var(--cl-acid); color: var(--cl-ink); }
:focus-visible { outline: 2px solid var(--cl-ink); outline-offset: 2px; }

/* The hosted chrome: a paper bar with nothing on it. */
[data-testid="stHeader"] {
  background: transparent;
  height: 0;
  min-height: 0;
  border: 0;
}
[data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer {
  display: none;
}

/* Content is capped and centred; the masthead is the one full width block. */
[data-testid="stMainBlockContainer"], .block-container {
  max-width: var(--cl-max);
  margin-inline: auto;
  padding-inline: var(--cl-pad);
  /* The masthead is fixed to the window (a sticky element inside Streamlit's
     scroll pane is bounded by its own one-line parent and scrolls away), so this
     is the space it occupies. */
  padding-top: 3.5rem;
  padding-bottom: 3rem;
  overflow-x: clip;
}
[data-testid="stMain"] { overflow-x: clip; }

/* ---------------------------------------------------------------- type */

.stApp h1, .stApp h2, .stApp h3, .stApp h4 {
  font-weight: 700;
  letter-spacing: -0.03em;
  color: var(--cl-ink);
}
.stApp h3 { font-size: 1.15rem; margin: 0.15rem 0 0.35rem; }
.stApp a { color: var(--cl-ink); text-decoration: underline; text-underline-offset: 2px; }
.stApp code {
  font-family: var(--cl-mono);
  background: var(--cl-paper-2);
  color: var(--cl-ink);
  border-radius: 0;
}
.stApp hr { border-color: var(--cl-line); }

[data-testid="stCaptionContainer"] p,
[data-testid="stCaptionContainer"] {
  font-size: 0.75rem;
  line-height: 1.45;
  color: var(--cl-muted);
}

/* ---------------------------------------------------------------- masthead */

.cl-masthead {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 60;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.5rem max(var(--cl-pad), calc((100vw - var(--cl-max)) / 2));
  border-bottom: 1px solid var(--cl-ink);
  background: rgba(243, 240, 232, 0.94);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
}
.cl-brand { display: flex; align-items: center; gap: 0.7rem; min-width: 0; }
.cl-mark {
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  width: 2.2rem;
  height: 2.2rem;
  border-radius: 9999px;
  background: var(--cl-ink);
  color: var(--cl-acid);
  font-family: var(--cl-mono);
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.02em;
}
.cl-brand-text { min-width: 0; }
.cl-masthead .cl-brand-name {
  display: block;
  margin: 0;
  font-size: 1rem;
  font-weight: 700;
  line-height: 1.15;
  letter-spacing: -0.01em;
  color: var(--cl-ink);
  white-space: nowrap;
}
.cl-brand-sub {
  display: block;
  font-family: var(--cl-mono);
  font-size: 0.66rem;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--cl-muted);
  white-space: nowrap;
}

/* ---------------------------------------------------------------- tabs */

/* Streamlit draws a tab as [data-testid="stTab"] with role="tab" and a
   data-selected attribute, and tints the selected tab's own paragraph. Both are
   overridden here: the cell carries the acid, the word stays ink. */
[data-testid="stTabs"] [role="tablist"] {
  gap: 0;
  width: 100%;
  justify-content: flex-start;
  background: transparent;
  border-bottom: 1px solid var(--cl-ink);
}
[data-testid="stTab"] {
  display: flex;
  align-items: center;
  min-height: 2.5rem;
  padding: 0 1rem;
  border: 1px solid var(--cl-line);
  border-bottom: 0;
  border-radius: 0;
  background: transparent;
}
[data-testid="stTab"] p {
  margin: 0;
  background: transparent;
  font-family: var(--cl-mono);
  font-size: 0.72rem;
  font-weight: 500;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--cl-ink);
}
[data-testid="stTab"]:hover { background: var(--cl-paper-2); }
[data-testid="stTab"][data-selected="true"] {
  background: var(--cl-acid);
  border-color: var(--cl-ink);
  box-shadow: none;
}
[data-testid="stTab"] .react-aria-SelectionIndicator { display: none; }

/* ---------------------------------------------------------------- window bar */

.cl-window {
  height: 1.7rem;
  margin: 0.75rem 0 0.25rem;
  border: 1px solid var(--cl-ink);
  border-radius: 0;
  background:
    radial-gradient(circle at 0.85rem 50%, var(--cl-orange) 0 0.22rem, transparent 0.24rem),
    radial-gradient(circle at 1.6rem 50%, transparent 0 0.17rem, var(--cl-ink) 0.18rem 0.24rem, transparent 0.26rem),
    radial-gradient(circle at 2.35rem 50%, transparent 0 0.17rem, var(--cl-ink) 0.18rem 0.24rem, transparent 0.26rem),
    var(--cl-paper-2);
  display: flex;
  align-items: center;
  justify-content: flex-end;
  padding: 0 0.6rem;
}
.cl-window::after {
  content: attr(data-window);
  font-family: var(--cl-mono);
  font-size: 0.62rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--cl-ink);
  white-space: nowrap;
}

/* ---------------------------------------------------------------- charts */

[data-testid="stPlotlyChart"] {
  background: var(--cl-paper);
  border: 1px solid var(--cl-ink);
  border-radius: 0;
}
[data-testid="stPlotlyChart"] .modebar { background: transparent; }
[data-testid="stPlotlyChart"] .modebar-btn path { fill: var(--cl-muted); }

/* ---------------------------------------------------------------- figures */

[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] [data-testid="stMetric"]) {
  gap: 0;
  margin-bottom: 0.6rem;
  border-top: 1px solid var(--cl-ink);
  border-left: 1px solid var(--cl-ink);
}
[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] [data-testid="stMetric"]) > [data-testid="stColumn"] {
  padding: 0.55rem 0.85rem 0.6rem;
  border-right: 1px solid var(--cl-ink);
  border-bottom: 1px solid var(--cl-ink);
  border-radius: 0;
  background: var(--cl-paper);
}
/* st.container(border=True) is a plain stVerticalBlock carrying the border in
   this Streamlit release. Inside a figure grid the cell's own rules are the only
   frame, so the nested one is dropped. */
[data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) [data-testid="stVerticalBlock"] {
  border: 0;
  background: transparent;
}
[data-testid="stMetric"] { background: transparent; padding: 0; }
[data-testid="stMetricLabel"] {
  font-family: var(--cl-mono);
  text-transform: uppercase;
  letter-spacing: 0.07em;
  font-size: 0.68rem;
  color: var(--cl-muted);
}
[data-testid="stMetricLabel"] p {
  font-family: var(--cl-mono);
  font-size: 0.68rem;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--cl-muted);
}
[data-testid="stMetricValue"] {
  font-family: var(--cl-sans);
  font-weight: 700;
  font-size: clamp(1.75rem, 1.2rem + 1.4vw, 2.6rem);
  line-height: 1.02;
  letter-spacing: -0.045em;
  font-variant-numeric: tabular-nums;
  color: var(--cl-ink);
}
[data-testid="stMetricDelta"] { font-family: var(--cl-mono); font-size: 0.7rem; }

/* ---------------------------------------------------------------- tables */

.cl-table-wrap {
  margin: 0.35rem 0 0.5rem;
  border: 1px solid var(--cl-ink);
  border-radius: 0;
  background: var(--cl-paper);
  overflow-x: auto;
}
table.cl-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: auto;
  font-family: var(--cl-sans);
  font-size: 0.8rem;
  color: var(--cl-ink);
}
table.cl-table thead th {
  position: sticky;
  top: 0;
  z-index: 2;
  padding: 0.4rem 0.55rem;
  border-bottom: 1px solid var(--cl-ink);
  background: var(--cl-ink);
  color: var(--cl-paper);
  font-family: var(--cl-mono);
  font-size: 0.7rem;
  font-weight: 500;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  text-align: left;
  white-space: nowrap;
}
table.cl-table tbody td {
  padding: 0.38rem 0.55rem;
  border-bottom: 1px solid var(--cl-line);
  vertical-align: top;
}
table.cl-table tbody tr:nth-child(even) { background: var(--cl-paper-2); }
table.cl-table tbody tr:last-child td { border-bottom: 0; }
table.cl-table td.cl-num, table.cl-table th.cl-num {
  text-align: right;
  font-family: var(--cl-mono);
  font-variant-numeric: tabular-nums;
}
table.cl-table td.cl-num { font-size: 0.75rem; }

/* Pills and dots stay round; everything else on this page is square. */
.cl-pill {
  display: inline-block;
  max-width: 100%;
  padding: 0.05rem 0.5rem;
  border: 1px solid var(--cl-line);
  border-radius: 9999px;
  background: var(--cl-paper-2);
  color: var(--cl-ink);
  font-family: var(--cl-mono);
  font-size: 0.68rem;
  letter-spacing: 0.04em;
  white-space: normal;
  overflow-wrap: anywhere;
}
.cl-pill-alert {
  border-color: var(--cl-orange);
  background: var(--cl-orange-fill);
  color: var(--cl-ink);
}

/* ---------------------------------------------------------------- alerts */

[data-testid="stAlert"] {
  border-radius: 0;
  border: 1px solid var(--cl-line);
  background: transparent;
  color: var(--cl-ink);
}
[data-testid="stAlert"] [data-testid="stMarkdownContainer"] p,
[data-testid="stAlert"] [data-testid="stMarkdownContainer"] li {
  color: var(--cl-ink);
  font-size: 0.82rem;
}
[data-testid="stAlert"]:has([data-testid="stAlertContentWarning"]),
[data-testid="stAlert"]:has([data-testid="stAlertContentError"]) {
  border: 1px solid var(--cl-orange);
  border-left-width: 0.45rem;
  background: var(--cl-orange-fill);
}
/* The alert's icon stays ink: orange on the orange fill is 2.6:1, under the 3:1 a
   meaningful graphic needs. The orange border and the heavy orange edge carry the
   state; the glyph carries the meaning and has to be readable. */
[data-testid="stAlert"]:has([data-testid="stAlertContentWarning"]) svg,
[data-testid="stAlert"]:has([data-testid="stAlertContentError"]) svg {
  fill: var(--cl-ink);
  color: var(--cl-ink);
}
[data-testid="stAlert"]:has([data-testid="stAlertContentSuccess"]) {
  border: 1px solid var(--cl-line);
  border-left: 0.45rem solid var(--cl-ink);
  background: transparent;
}
[data-testid="stAlert"]:has([data-testid="stAlertContentSuccess"]) svg {
  fill: var(--cl-ink);
  color: var(--cl-ink);
}
[data-testid="stAlert"]:has([data-testid="stAlertContentInfo"]) {
  border: 1px solid var(--cl-line);
  border-left: 0.45rem solid var(--cl-line);
  background: transparent;
}

/* ---------------------------------------------------------------- controls */

.stApp button[data-testid^="stBaseButton"],
.stApp [data-testid="stBaseButton-primary"],
.stApp [data-testid="stBaseButton-secondary"] {
  border-radius: 0;
  font-family: var(--cl-mono);
  font-size: 0.72rem;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  border: 1px solid var(--cl-ink);
  box-shadow: none;
}
.stApp [data-testid="stBaseButton-primary"] {
  background: var(--cl-acid);
  color: var(--cl-ink);
}
.stApp [data-testid="stBaseButton-primary"] p { color: var(--cl-ink); }
.stApp [data-testid="stBaseButton-secondary"] {
  background: transparent;
  color: var(--cl-ink);
}
.stApp [data-testid="stBaseButton-secondary"] p { color: var(--cl-ink); }
.stApp button[data-testid^="stBaseButton"]:hover {
  background: var(--cl-paper-2);
  color: var(--cl-ink);
  border-color: var(--cl-ink);
}
.stApp [data-testid="stBaseButton-primary"]:hover { background: var(--cl-acid); }

/* The one thing that must stay round: a switch knob and its track. */
[data-testid="stToggle"] [data-baseweb="checkbox"] div[role="switch"],
[data-testid="stToggle"] [role="switch"] {
  border-radius: 9999px;
}
[data-testid="stToggle"] [role="switch"] > div,
[data-testid="stToggle"] [role="switch"] span {
  border-radius: 9999px;
}

.stApp input, .stApp textarea, .stApp [data-baseweb="select"] > div,
[data-testid="stDateInput"] > div {
  border-radius: 0;
  background: var(--cl-paper);
  color: var(--cl-ink);
  font-family: var(--cl-sans);
}
[data-testid="stDateInput"] label, [data-testid="stWidgetLabel"] p {
  font-family: var(--cl-mono);
  font-size: 0.68rem;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--cl-muted);
}

/* ---------------------------------------------------------------- expanders */

[data-testid="stExpander"] {
  border: 1px solid var(--cl-ink);
  border-radius: 0;
  background: var(--cl-paper);
}
[data-testid="stExpander"] summary {
  font-family: var(--cl-mono);
  font-size: 0.72rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--cl-ink);
}
[data-testid="stExpander"] summary:hover { background: var(--cl-paper-2); }
[data-testid="stExpander"] [data-testid="stExpanderDetails"] {
  border-top: 1px solid var(--cl-line);
  padding-top: 0.5rem;
}

/* ---------------------------------------------------------------- phones */

@media (max-width: 640px) {
  .cl-masthead { padding: 0.45rem 0.75rem; }
  .cl-mark { width: 1.9rem; height: 1.9rem; font-size: 0.66rem; }
  .cl-masthead .cl-brand-name { font-size: 0.9rem; }
  .cl-brand-sub { display: none; }
  .cl-window { margin-top: 0.6rem; }
  table.cl-table { font-size: 0.76rem; }
}
"""


def apply() -> None:
    """Inject the stylesheet. Called once, above every other element."""
    st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)


def masthead(*, name: str, subtitle: str, mark: str = "CL") -> None:
    """The full width sticky bar: an ink mark, the app name, one mono line."""
    st.markdown(
        '<header class="cl-masthead">'
        '<div class="cl-brand">'
        f'<span class="cl-mark" aria-hidden="true">{html.escape(mark)}</span>'
        '<div class="cl-brand-text">'
        f'<h1 class="cl-brand-name">{html.escape(name)}</h1>'
        f'<span class="cl-brand-sub">{html.escape(subtitle)}</span>'
        "</div>"
        "</div>"
        "</header>",
        unsafe_allow_html=True,
    )


def window_bar(section: str) -> None:
    """A section's window bar: three dots on the left, the mono label on the right.

    Drawn by the stylesheet from the label it is given, so the bar is a rule and not
    layout: it cannot push the section's own content around.
    """
    label = "CREDIT LAB / " + section.upper()
    st.markdown(
        f'<div class="cl-window" role="presentation" data-window="{html.escape(label)}"></div>',
        unsafe_allow_html=True,
    )


def _cell(value) -> str:
    """One table cell as text, without touching the value it shows."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, float):
        if float(value).is_integer():
            return f"{int(value):,}"
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    return str(value)


def html_table(
    frame: pd.DataFrame,
    *,
    numeric: tuple[str, ...] = (),
    pill_column: str | None = None,
    pill_alert: tuple[str, ...] = (),
) -> None:
    """A ruled table: ink header, paper rows, zebra body, mono tabular numbers.

    Values and column order are the frame's own. A column named in the numeric
    argument is set flush right in the monospace face, because a column of figures
    is read down its decimal point. The column named by pill_column draws its text
    as a round pill, and a value listed in pill_alert takes the orange pill.
    """
    if frame is None or frame.empty:
        return

    columns = [str(column) for column in frame.columns]
    numbers = {str(name) for name in numeric}
    numbers |= {str(name) for name in frame.select_dtypes(include="number").columns}

    head = "".join(
        '<th class="' + ("cl-num" if name in numbers else "") + '">'
        + html.escape(name) + "</th>"
        for name in columns
    )
    body: list[str] = []
    for _, row in frame.iterrows():
        cells: list[str] = []
        for name in columns:
            text = _cell(row[name])
            if name in numbers:
                cells.append('<td class="cl-num">' + html.escape(text) + "</td>")
            elif pill_column is not None and name == pill_column:
                alert = (
                    " cl-pill-alert"
                    if any(text.startswith(state) for state in pill_alert)
                    else ""
                )
                cells.append(
                    '<td><span class="cl-pill' + alert + '">'
                    + html.escape(text) + "</span></td>"
                )
            else:
                cells.append("<td>" + html.escape(text) + "</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")

    st.markdown(
        '<div class="cl-table-wrap"><table class="cl-table">'
        "<thead><tr>" + head + "</tr></thead>"
        "<tbody>" + "".join(body) + "</tbody>"
        "</table></div>",
        unsafe_allow_html=True,
    )
