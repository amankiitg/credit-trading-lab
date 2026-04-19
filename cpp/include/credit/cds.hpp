#pragma once

#include <cmath>
#include <vector>

#include "credit/date.hpp"
#include "credit/daycount.hpp"
#include "credit/discount_curve.hpp"
#include "credit/schedule.hpp"
#include "credit/survival_curve.hpp"

namespace credit {

struct CDSContract {
    double notional = 10'000'000.0;
    double coupon;          // running spread in decimal (0.01 = 100 bps)
    double recovery;        // recovery rate (e.g. 0.40)
    Date   effective_date;
    Date   maturity_date;
    int    pay_freq = 4;    // quarterly
};

namespace detail {

// Numerical accrual-on-default via fine grid (10 buckets per day).
// Used only for validation against the closed-form approximation.
template <typename Interp, typename DC>
double aod_numerical(double maturity,
                     double /*recovery*/,
                     const SurvivalCurve& surv,
                     const DiscountCurve<Interp, DC>& disc,
                     int pay_freq = 4) {
    int n_periods = std::max(1,
        static_cast<int>(std::round(maturity * pay_freq)));
    double dt = maturity / n_periods;

    double total = 0.0;
    for (int i = 1; i <= n_periods; ++i) {
        double t_start = (i - 1) * dt;

        // 10 buckets per day ≈ 3650 per year
        int n_fine = std::max(10,
            static_cast<int>(std::round(dt * 365.0 * 10)));
        double dt_fine = dt / n_fine;

        for (int j = 1; j <= n_fine; ++j) {
            double u0 = t_start + (j - 1) * dt_fine;
            double u1 = t_start + j * dt_fine;
            double u_mid = 0.5 * (u0 + u1);

            double accrued_time = u_mid - t_start;  // time into period
            double s0 = surv.survival(u0);
            double s1 = surv.survival(u1);
            double d_m = disc.df(u_mid);

            total += accrued_time * (s0 - s1) * d_m;
        }
    }
    return total;
}

}  // namespace detail

struct CDSPricer {
    // ---- Year-fraction API (used in tests, bootstrap) ----

    // Par spread = PV_prot / RPV01.
    template <typename Interp, typename DC>
    static double par_spread(double maturity_yf,
                             double recovery,
                             const SurvivalCurve& surv,
                             const DiscountCurve<Interp, DC>& disc,
                             int pay_freq = 4) {
        auto legs = detail::cds_pvs(maturity_yf, recovery, surv, disc, pay_freq);
        return legs.pv_protection / legs.rpv01();
    }

    // Protection leg PV (buyer receives on default).
    template <typename Interp, typename DC>
    static double pv_protection(double maturity_yf,
                                double recovery,
                                const SurvivalCurve& surv,
                                const DiscountCurve<Interp, DC>& disc,
                                int pay_freq = 4) {
        return detail::cds_pvs(maturity_yf, recovery, surv, disc, pay_freq)
            .pv_protection;
    }

    // Risky PV01 (annuity per unit spread).
    template <typename Interp, typename DC>
    static double rpv01(double maturity_yf,
                        double recovery,
                        const SurvivalCurve& surv,
                        const DiscountCurve<Interp, DC>& disc,
                        int pay_freq = 4) {
        return detail::cds_pvs(maturity_yf, recovery, surv, disc, pay_freq)
            .rpv01();
    }

    // ---- Date-based API (for CDSContract) ----

    template <typename Interp, typename DC>
    static double par_spread(const CDSContract& cds,
                             const SurvivalCurve& surv,
                             const DiscountCurve<Interp, DC>& disc) {
        double mat = Act365F::year_fraction(cds.effective_date, cds.maturity_date);
        return par_spread(mat, cds.recovery, surv, disc, cds.pay_freq);
    }

    template <typename Interp, typename DC>
    static double rpv01(const CDSContract& cds,
                        const SurvivalCurve& surv,
                        const DiscountCurve<Interp, DC>& disc) {
        double mat = Act365F::year_fraction(cds.effective_date, cds.maturity_date);
        return rpv01(mat, cds.recovery, surv, disc, cds.pay_freq);
    }
};

}  // namespace credit
