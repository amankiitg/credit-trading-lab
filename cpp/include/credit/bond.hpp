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
};

}  // namespace credit
