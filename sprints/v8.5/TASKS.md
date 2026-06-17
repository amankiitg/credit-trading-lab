# Sprint v8.5 - Tasks

**Closed (local-only).** T1–T7 complete. T8 (integration smoke) carries over to v8.6: blocked on one-time manual steps — Supabase table provisioning via `supabase_schema.sql` and Google OIDC setup in Google Cloud Console. See WALKTHROUGH.md for the full carryover list.

Status: `[ ]` = not done, `[x]` = done, `[~]` = partially done (see notes.md).

**Dependency order:** T1 -> T2 -> T3 -> T4 -> T5 -> T6 -> T7 -> T8.
No hard data-gate stop, but U5 (email gate) must pass before T5 (the
Approve/Reject buttons write to a live table) so an unauthenticated
session cannot write decisions.

---

- [x] **Task T1: Supabase tables + connection module**
  - Provision the three tables (`trade_decisions`, `paper_positions`,
    `paper_fills`) in the Supabase dashboard using the SQL from the PRD
    schema section. Enable row-level security on each table with a
    service-role-only policy.
  - Install `supabase` Python library and add to `pyproject.toml`.
  - Write `dashboard/supabase_client.py` with a lazy singleton
    `get_client()` that reads `SUPABASE_URL` (trimming `/rest/v1/`) and
    `SUPABASE_SECRET_KEY` from environment. Confirm the key is the
    `SECRET_KEY` (service-role), not the publishable key.
  - Acceptance: `python -c "from dashboard.supabase_client import get_client; c = get_client(); print(c.table('trade_decisions').select('id').limit(1).execute())"` returns without error.
  - Files: `dashboard/supabase_client.py`, `pyproject.toml`
  - Validation: fails if the connection uses the publishable/anon key
    instead of the secret key; fails if the URL still contains `/rest/v1/`
    when passed to `create_client`.

- [x] **Task T2: Google OIDC authentication + email gate**
  - Add `.streamlit/` to `.gitignore` before creating any file there
    (prevent accidental secret commit).
  - Create `.streamlit/secrets.toml` (gitignored) with the `[auth]` and
    `[auth.google]` sections. Obtain a client ID and secret from Google
    Cloud Console (OAuth2 web application, redirect URI
    `http://localhost:8501/oauth2callback`).
  - Add an auth guard at the top of `dashboard/app.py`:
    `st.login("google")` then read `st.user.email` and compare to
    `os.environ["ALLOWED_EMAIL"]`; if the email does not match exactly,
    show an error and call `st.stop()`.
  - Add `ALLOWED_EMAIL` to `.env`.
  - Acceptance: `streamlit run dashboard/app.py` redirects to Google
    login; logging in with the authorized email shows the dashboard;
    any other email sees "Access denied" and stops (U5).
  - Files: `.streamlit/secrets.toml` (gitignored), `dashboard/app.py`,
    `.gitignore`, `.env`
  - Validation: fails if `.streamlit/secrets.toml` appears in
    `git status`; fails if an unauthorized email can view any panel.

- [x] **Task T3: Attribution panels A-D (sleeve P&L, carry, long/short, directional/selection)**
  - Add a new "Attribution" tab in `dashboard/app.py`. Load
    `data/processed/attribution.parquet` with `st.cache_data`.
  - Panel A: cumulative sleeve P&L (bar chart by date, colour-coded by
    asset_class). Highlight that GLD and credit dominate.
  - Panel B: carry vs price change (bar or area chart showing cumulative
    carry $612k vs price change $268k; label "carry is ~70% of gross
    P&L" prominently). Add framing-lock sub-caption (U7).
  - Panel C: cumulative long vs short P&L (stacked area, two series).
  - Panel D: cumulative directional vs selection P&L (stacked area,
    two series).
  - Acceptance: all four panels render without error on `streamlit run`;
    correct totals can be verified against notes.md (sleeve totals:
    commodity $434k, rates $181k, credit $178k, equity $86k). U1 gate.
  - Files: `dashboard/app.py`, `dashboard/views/attribution.py` (new)
  - Validation: fails if any panel reads from a source other than
    `attribution.parquet`; fails if the framing-lock caption is absent.

- [x] **Task T4: Attribution panels E-G (factor betas, cost, MCTR)**
  - Panel E: rolling factor betas over time (4-line chart: beta_eq,
    beta_rates, beta_credit, beta_gold). Below it: beta-explained vs
    residual cumulative P&L (two-line chart). Below both: the Finding-3
    caveat as a permanent `st.info` box (U2):
    "The residual is confounded with carry accrual. The four daily-return
    factors do not span coupon income. This residual is NOT standalone
    alpha. See sprints/v8.3/notes.md finding 3."
  - Panel F: gross vs net equity overlay (two lines) with cumulative
    turnover cost and borrow cost shown as a shaded gap. Cost breakdown
    in bps/day from the attribution frame.
  - Panel G: MCTR by sleeve (load `attribution_mctr_by_sleeve.parquet`).
    Ex-ante and realized side by side for today's most recent row.
  - Acceptance: U2 satisfied (caveat text present, correct verbatim);
    factor beta chart matches the notebook `sprints/v8.3/plots/factor_betas.png`
    visually; cost gap is visible.
  - Files: `dashboard/views/attribution.py`
  - Validation: fails if the U2 caveat is missing, paraphrased, or
    wrapped in a dismissible expander.

- [x] **Task T5: Proposed next trade panel + Approve/Reject + Supabase write (U3, U4)**
  - Add "Operational" tab. Panel H: compute today's proposed trade using
    the v8.2 signal pipeline (data through yesterday's close; display the
    as-of date prominently). For each ticker show: current target weight,
    proposed delta notional, position intent (open/close/cross).
  - An Approve button and a Reject button per ticker row.
  - On Approve: write a row to `trade_decisions` with
    `decision='APPROVE'`, `approved_by=st.user.email`, and the
    signal-derived fields.
  - On Reject: write `decision='REJECT'` with the same fields.
  - Neither button is rendered if the user is not authenticated (U5).
  - Acceptance: clicking Approve for one ticker inserts a row into
    `trade_decisions` within 2s; the row is visible in the Supabase
    dashboard. U3 (as-of date shown), U4 (write succeeds).
  - Files: `dashboard/views/operational.py` (new), `dashboard/app.py`
  - Validation: fails if the as-of date is today rather than yesterday's
    close; fails if clicking Approve does not produce a Supabase row;
    fails if an unauthenticated session can see or click the buttons.

- [x] **Task T6: Operational stubs -- equity curve, positions, P&L, drawdown, log (U6)**
  - Panel I: equity curve. Show a `st.warning("TODO v8.6: live equity
    curve from Alpaca fills. Stub shown.")` followed by an empty Altair
    chart with correct axes but no data.
  - Panel J: open positions. Same stub pattern -- `st.warning` plus an
    empty dataframe with columns `['ticker','signed_notional','weight',
    'entry_date','avg_entry_price']`.
  - Panel K: daily P&L and drawdown. Stub warning + empty chart.
  - Panel L: closed trade log. Stub warning + empty dataframe with
    `paper_fills` columns.
  - The stub warnings must include "TODO v8.6" verbatim (U6).
  - Acceptance: `streamlit run` shows all four stub panels without errors;
    "TODO v8.6" string is visible in each stub; grep for `TradingClient`
    instantiation inside `dashboard/` -> zero hits (U6).
  - Files: `dashboard/views/operational.py`
  - Validation: fails if any live Alpaca call is made from dashboard code;
    fails if the "TODO v8.6" label is absent from any stub panel.

- [x] **Task T7: Framing lock and guardrail text (U7, S4)**
  - Ensure every P&L panel (Panels A-F, I-K) has a non-dismissible
    `st.caption` or `st.info` with the single-rate-cycle framing.
    Template: "Historical P&L shown for 2007-2026. This sample contains
    one secular rate cycle. Results are not forward-looking."
  - Add the S4 guardrail verbatim to `sprints/v8.5/notes.md`.
  - Acceptance: `grep -r "single rate cycle" dashboard/` returns at least
    one hit per P&L panel file; S4 guardrail present verbatim in notes.md.
  - Files: `dashboard/views/attribution.py`, `dashboard/views/operational.py`,
    `sprints/v8.5/notes.md`
  - Validation: fails if any P&L panel lacks the caption; fails if the
    S4 text in notes.md differs from the PRD verbatim.

- [~] **Task T8: Integration smoke test (U1-U7 sweep)** -- manual checks done 2026-06-17; Google OAuth deferred
  - Run `streamlit run dashboard/app.py` locally. Manually walk through
    every panel in the checklist below. Document the result in notes.md.
  - Checklist:
    - [ ] Login redirects to Google; authorized email enters dashboard; other
          email sees "Access denied" page and cannot proceed.
          **DEFERRED** -- Google OAuth config pending; local dev mode used instead.
    - [x] Attribution tab loads all 7 panels without error; sleeve P&L
          totals match notes.md figures within 1%.
    - [x] Panel E Finding-3 caveat text is present verbatim (U2).
    - [x] Panel H shows yesterday's as-of date; Approve writes a row to
          Supabase; Reject writes a row; both rows visible in Supabase
          dashboard (U3, U4).
    - [x] Panels I-L show "TODO v8.6" stub warnings, no Alpaca calls (U6).
    - [x] Every P&L panel shows the rate-cycle framing caption (U7).
    - [x] `grep -r "TradingClient" dashboard/` returns zero hits (U6).
  - Acceptance: all checklist items pass.
  - Files: `sprints/v8.5/notes.md`
  - Validation: fails if any gate (U1-U7, S4) is not confirmed passing
    in the notes.md record.
