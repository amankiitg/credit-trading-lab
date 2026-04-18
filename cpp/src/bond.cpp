#include "credit/bond.hpp"

#include <cmath>
#include <stdexcept>

namespace credit {

double BondPricer::dirty_at_yield(const FixedBond& bond,
                                  double yield,
                                  const Date& settle) {
    auto sched = coupon_schedule(bond);
    int f = bond.frequency;
    double v_inv = 1.0 + yield / f;  // 1 + y/f
    double pv = 0.0;

    Date prev = bond.issue_date;
    for (const auto& cpn_date : sched) {
        if (cpn_date <= settle) { prev = cpn_date; continue; }
        double delta = year_fraction(bond.day_count, prev, cpn_date);
        double t     = year_fraction(bond.day_count, settle, cpn_date);
        double n     = f * t;  // number of periods from settle
        pv += bond.coupon * delta * bond.notional / std::pow(v_inv, n);
        prev = cpn_date;
    }

    double t_mat = year_fraction(bond.day_count, settle, bond.maturity_date);
    double n_mat = f * t_mat;
    pv += bond.notional / std::pow(v_inv, n_mat);
    return pv;
}

double BondPricer::dirty_deriv(const FixedBond& bond,
                               double yield,
                               const Date& settle) {
    auto sched = coupon_schedule(bond);
    int f = bond.frequency;
    double v_inv = 1.0 + yield / f;
    double deriv = 0.0;

    // d/dy [CF / (1+y/f)^n] = CF * (-n/f) / (1+y/f)^(n+1)
    Date prev = bond.issue_date;
    for (const auto& cpn_date : sched) {
        if (cpn_date <= settle) { prev = cpn_date; continue; }
        double delta = year_fraction(bond.day_count, prev, cpn_date);
        double t     = year_fraction(bond.day_count, settle, cpn_date);
        double n     = f * t;
        double cf    = bond.coupon * delta * bond.notional;
        deriv += cf * (-n / f) / std::pow(v_inv, n + 1.0);
        prev = cpn_date;
    }

    double t_mat = year_fraction(bond.day_count, settle, bond.maturity_date);
    double n_mat = f * t_mat;
    deriv += bond.notional * (-n_mat / f) / std::pow(v_inv, n_mat + 1.0);
    return deriv;
}

double BondPricer::accrued(const FixedBond& bond, const Date& settle) {
    auto sched = coupon_schedule(bond);

    // Find the bracket: last coupon on or before settle, next coupon after.
    Date prev = bond.issue_date;
    Date next = sched.front();
    for (const auto& d : sched) {
        if (d <= settle) {
            prev = d;
        } else {
            next = d;
            break;
        }
    }

    double yf_elapsed = year_fraction(bond.day_count, prev, settle);
    double yf_period  = year_fraction(bond.day_count, prev, next);
    if (yf_period <= 0.0) { return 0.0; }

    // Coupon for this period = c/f * N (regular) or c * delta * N (stub)
    double cpn_amount = bond.coupon * yf_period * bond.notional;
    return cpn_amount * (yf_elapsed / yf_period);
}

double BondPricer::ytm(const FixedBond& bond,
                       double dirty_price,
                       const Date& settle) {
    auto f  = [&](double y) { return dirty_at_yield(bond, y, settle) - dirty_price; };
    auto df = [&](double y) { return dirty_deriv(bond, y, settle); };

    // Try Newton first with a reasonable initial guess.
    double y0 = bond.coupon;  // par-ish guess
    if (y0 < 1e-4) { y0 = 0.05; }  // fallback for zero-coupon

    try {
        return newton(f, df, y0);
    } catch (const std::runtime_error&) {
        // Newton failed — fall back to Brent on [1e-6, 1.0].
        return brent(f, 1e-6, 1.0);
    }
}

}  // namespace credit
