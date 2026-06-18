# v8.6 Deployment Checklist

Run these in order before declaring the sprint closed. Record pass/fail and timestamp
in `sprints/v8.6/notes.md`. The sprint cannot close until D12 passes with a SCHEDULED
(not manually triggered) live cycle.

---

## A. Pre-deployment (local)

- [ ] A1  `python -m pytest tests/test_paper_execution.py tests/test_calendar_utils.py` -- all pass
- [ ] A2  `grep -r "ALPACA_PAPER_API_KEY\|ALPACA_PAPER_SECRET_KEY" render.yaml` -- zero value hits (key names only)
- [ ] A3  `grep -r "TradingClient" dashboard/` -- zero hits (dashboard never calls Alpaca)
- [ ] A4  `git grep "ALPACA_PAPER" .streamlit/ dashboard/` -- zero hits

## B. Render deployment

- [ ] B1  Push to main branch. Verify Render auto-deploy triggered for all three services.
- [ ] B2  Render dashboard -> credit-lab-dashboard -> Environment: confirm ALPACA_PAPER_API_KEY and ALPACA_PAPER_SECRET_KEY are ABSENT.
- [ ] B3  Render dashboard -> credit-lab-signal -> Environment: confirm ALPACA_PAPER_API_KEY present.
- [ ] B4  Render dashboard -> credit-lab-execution -> Environment: confirm ALPACA_PAPER_API_KEY present.
- [ ] B5  All three services show "Live" status in the Render dashboard.

## C. Dashboard service

- [ ] C1  Open the Render URL in a browser. Confirm it redirects to Google OIDC login.
- [ ] C2  Sign in with the authorized email (amank.iitg@gmail.com). Confirm you land on the dashboard with attribution panels loading. (U5a)
- [ ] C3  Sign in with a DIFFERENT Google account. Confirm "Access denied" -- no panels visible. (U5b)
- [ ] C4  Attribution panels A-G load without error. Sleeve P&L totals match local smoke figures.
- [ ] C5  Panel H shows yesterday's as-of date. Approve/Reject buttons visible.

## D. Supabase connectivity (both cron services)

- [ ] D1  Manually trigger credit-lab-signal in Render dashboard.
         Check Render logs: should see "wrote decision='proposed'" with today's date.
         Check Supabase decisions table: new row with decision='proposed'.
- [ ] D2  Dashboard panel H shows the proposed trade from the manual signal run.
- [ ] D3  Approve the proposed trade in the dashboard.
         Check Supabase: row updated to decision='approve'.

## E. Execution cron (manual trigger)

- [ ] E1  Set DRY_RUN_DEFAULT=True in execution/alpaca_paper.py (safety: keep dry_run for first check).
          Manually trigger credit-lab-execution. Check logs: should see DRY_RUN status for all orders.
          No Alpaca orders submitted.
- [ ] E2  Verify idempotency: trigger credit-lab-execution a second time.
          Logs should say "already ran for YYYY-MM-DD -- exit 0".
          No orders submitted.
- [ ] E3  Delete the cron_runs row for today in Supabase (to reset idempotency).
          Set DRY_RUN_DEFAULT=False.
          Trigger credit-lab-execution again. Verify live orders placed in Alpaca paper account.
- [ ] E4  Check Supabase positions table: rows for today.
- [ ] E5  Check Supabase pnl_log table: row for today.
- [ ] E6  Check Supabase settings: live_nav key updated.
- [ ] E7  Dashboard panels I-L: refresh. Equity curve, positions, P&L log should show data.

## F. NYSE calendar check (offline test)

- [ ] F1  Modify run_signal.py temporarily to pass a Saturday date to is_trading_day.
          Confirm log says "skipping: NYSE closed".
          Revert the change.

## G. Scheduled live cycle (D12 gate -- sprint close gate)

- [ ] G1  Wait for the next scheduled signal cron (21:30 UTC).
          Confirm it fires automatically. Check Supabase decisions for today's proposal.
- [ ] G2  Approve or auto-approve in the dashboard.
- [ ] G3  Wait for the next scheduled execution cron (14:30 UTC next trading day).
          Confirm it fires automatically. Check Alpaca paper account for filled orders.
          Check Supabase positions, pnl_log, settings for updated rows.
- [ ] G4  Dashboard panels I-L show the new cycle's data.

Sprint close: G4 pass = sprint v8.6 closed.
Record the order IDs from G3 in sprints/v8.6/notes.md.
