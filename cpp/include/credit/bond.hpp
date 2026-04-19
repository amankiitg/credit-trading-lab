#pragma once

#include <vector>

#include "credit/date.hpp"
#include "credit/daycount.hpp"
#include "credit/discount_curve.hpp"
#include "credit/rootfind.hpp"

namespace credit {

// Runtime day-count selector — lets FixedBond be a plain data struct
// while still dispatching to the compile-time policy classes.
enum class DayCountType { Act360, Act365F, Thirty360 };

inline double year_fraction(DayCountType dc, const Date& d1, const Date& d2) {
    switch (dc) {
        case DayCountType::Act360:    return Act360::year_fraction(d1, d2);
        case DayCountType::Act365F:   return Act365F::year_fraction(d1, d2);
        case DayCountType::Thirty360: return Thirty360::year_fraction(d1, d2);
    }
    return 0.0;  // unreachable
}

struct FixedBond {
    double notional    = 100.0;
    double coupon;           // annual rate in decimal (0.05 = 5%)
    int    frequency;        // coupons per year: 1, 2, or 4
    Date   issue_date;
    Date   maturity_date;
    DayCountType day_count = DayCountType::Thirty360;
};

// Generate coupon dates from first coupon to maturity (ascending).
// Steps backward from maturity by 12/frequency months.
inline std::vector<Date> coupon_schedule(const FixedBond& bond) {
    int step = 12 / bond.frequency;
    std::vector<Date> dates;
    Date d = bond.maturity_date;
    while (d > bond.issue_date) {
        dates.push_back(d);
        d = add_months(d, -step);
    }
    std::reverse(dates.begin(), dates.end());
    return dates;
}

struct BondPricer {
    // Dirty price at a given yield.
    static double dirty_at_yield(const FixedBond& bond,
                                 double yield,
                                 const Date& settle);

    // Analytic derivative dDirty/dy (needed by Newton solver & DV01).
    static double dirty_deriv(const FixedBond& bond,
                              double yield,
                              const Date& settle);

    // Accrued interest at settlement date.
    static double accrued(const FixedBond& bond, const Date& settle);

    // Model dirty price from a discount curve.
    // Templated on the curve's interpolation/day-count policies.
    template <typename Interp, typename DC>
    static double dirty(const FixedBond& bond,
                        const DiscountCurve<Interp, DC>& curve,
                        const Date& settle) {
        auto sched = coupon_schedule(bond);
        double pv = 0.0;
        Date prev = bond.issue_date;
        for (const auto& cpn_date : sched) {
            if (cpn_date <= settle) { prev = cpn_date; continue; }
            double delta = year_fraction(bond.day_count, prev, cpn_date);
            double t = year_fraction(bond.day_count, settle, cpn_date);
            pv += bond.coupon * delta * bond.notional * curve.df(t);
            prev = cpn_date;
        }
        double t_mat = year_fraction(bond.day_count, settle, bond.maturity_date);
        pv += bond.notional * curve.df(t_mat);
        return pv;
    }

    // Clean price = dirty − accrued.
    template <typename Interp, typename DC>
    static double clean(const FixedBond& bond,
                        const DiscountCurve<Interp, DC>& curve,
                        const Date& settle) {
        return dirty(bond, curve, settle) - accrued(bond, settle);
    }

    // Yield to maturity: solve Dirty(y) = target dirty price.
    // Newton with Brent fallback on [1e-6, 1.0].
    static double ytm(const FixedBond& bond,
                      double dirty_price,
                      const Date& settle);

    // ---- V5: Risk measures ----

    // Analytic DV01: -dP/dy * 0.0001 at the bond's YTM.
    // This is the dollar change in dirty price per 1 bp rise in yield.
    template <typename Interp, typename DC>
    static double dv01(const FixedBond& bond,
                       const DiscountCurve<Interp, DC>& curve,
                       const Date& settle) {
        double dp = dirty(bond, curve, settle);
        double y  = ytm(bond, dp, settle);
        return -dirty_deriv(bond, y, settle) * 1e-4;
    }

    // Finite-difference DV01 via ±1bp shift of the bond's YTM.
    // Should agree with analytic DV01 to within ~1% (validates the derivative).
    template <typename Interp, typename DC>
    static double dv01_fd(const FixedBond& bond,
                          const DiscountCurve<Interp, DC>& curve,
                          const Date& settle) {
        constexpr double bump = 1e-4;  // 1 bp
        double dp = dirty(bond, curve, settle);
        double y  = ytm(bond, dp, settle);
        double p_up   = dirty_at_yield(bond, y + bump, settle);
        double p_down = dirty_at_yield(bond, y - bump, settle);
        return (p_down - p_up) / 2.0;
    }

    // Curve-based parallel DV01: re-bootstrap with all par yields shifted ±1bp.
    template <typename Interp, typename DC>
    static double dv01_parallel(const FixedBond& bond,
                                const DiscountCurve<Interp, DC>& curve,
                                const Date& settle) {
        constexpr double bump = 1e-4;  // 1 bp
        auto curve_up   = curve.parallel_shift(+bump);
        auto curve_down = curve.parallel_shift(-bump);
        double p_up   = dirty(bond, curve_up, settle);
        double p_down = dirty(bond, curve_down, settle);
        return (p_down - p_up) / 2.0;
    }

    // Key-rate DV01: sensitivity to each input tenor shifted individually.
    // Returns a vector with one entry per curve input tenor.
    template <typename Interp, typename DC>
    static std::vector<double> krdv01(const FixedBond& bond,
                                      const DiscountCurve<Interp, DC>& curve,
                                      const Date& settle) {
        constexpr double bump = 1e-4;  // 1 bp
        std::size_t n = curve.num_tenors();
        std::vector<double> kr(n);
        for (std::size_t i = 0; i < n; ++i) {
            auto curve_up   = curve.key_rate_shift(i, +bump);
            auto curve_down = curve.key_rate_shift(i, -bump);
            double p_up   = dirty(bond, curve_up, settle);
            double p_down = dirty(bond, curve_down, settle);
            kr[i] = (p_down - p_up) / 2.0;
        }
        return kr;
    }

    // Dirty price discounted with a constant Z-spread over the curve.
    //   DF_shifted(t) = DF(t) * exp(-z * t)
    template <typename Interp, typename DC>
    static double dirty_with_zspread(const FixedBond& bond,
                                     const DiscountCurve<Interp, DC>& curve,
                                     double zspread,
                                     const Date& settle) {
        auto sched = coupon_schedule(bond);
        double pv = 0.0;
        Date prev = bond.issue_date;
        for (const auto& cpn_date : sched) {
            if (cpn_date <= settle) { prev = cpn_date; continue; }
            double delta = year_fraction(bond.day_count, prev, cpn_date);
            double t = year_fraction(bond.day_count, settle, cpn_date);
            pv += bond.coupon * delta * bond.notional
                  * curve.df_with_zspread(t, zspread);
            prev = cpn_date;
        }
        double t_mat = year_fraction(bond.day_count, settle, bond.maturity_date);
        pv += bond.notional * curve.df_with_zspread(t_mat, zspread);
        return pv;
    }

    // Z-spread: the constant zero-rate shift that makes the model dirty
    // price match the market dirty price.  Newton-solved.
    template <typename Interp, typename DC>
    static double zspread(const FixedBond& bond,
                          const DiscountCurve<Interp, DC>& curve,
                          double mkt_dirty,
                          const Date& settle) {
        auto f = [&](double z) {
            return dirty_with_zspread(bond, curve, z, settle) - mkt_dirty;
        };
        auto df = [&](double z) {
            // dP/dz = sum(-t_i * CF_i * DF(t_i) * exp(-z * t_i))
            auto sched = coupon_schedule(bond);
            double deriv = 0.0;
            Date prev = bond.issue_date;
            for (const auto& cpn_date : sched) {
                if (cpn_date <= settle) { prev = cpn_date; continue; }
                double delta = year_fraction(bond.day_count, prev, cpn_date);
                double t = year_fraction(bond.day_count, settle, cpn_date);
                deriv += bond.coupon * delta * bond.notional
                         * (-t) * curve.df_with_zspread(t, z);
                prev = cpn_date;
            }
            double t_mat = year_fraction(bond.day_count, settle, bond.maturity_date);
            deriv += bond.notional * (-t_mat) * curve.df_with_zspread(t_mat, z);
            return deriv;
        };
        return newton(f, df, 0.0);
    }

    // Spread convexity: central finite difference on Z-spread.
    //   d²P/dz² ≈ (P(z+h) - 2P(z) + P(z-h)) / h²
    template <typename Interp, typename DC>
    static double spread_convexity(const FixedBond& bond,
                                   const DiscountCurve<Interp, DC>& curve,
                                   double zspd,
                                   const Date& settle) {
        constexpr double h = 1e-4;  // 1 bp
        double p_up   = dirty_with_zspread(bond, curve, zspd + h, settle);
        double p_mid  = dirty_with_zspread(bond, curve, zspd, settle);
        double p_down = dirty_with_zspread(bond, curve, zspd - h, settle);
        return (p_up - 2.0 * p_mid + p_down) / (h * h);
    }
};

}  // namespace credit
