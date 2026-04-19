#pragma once

#include <vector>

#include "credit/date.hpp"

namespace credit {

// Generate CDS premium payment dates stepping forward from effective.
// Returns dates in ascending order; always includes maturity.
// period_months = 12 / pay_freq (default 3 = quarterly).
inline std::vector<Date> cds_payment_dates(const Date& effective,
                                            const Date& maturity,
                                            int period_months = 3) {
    std::vector<Date> dates;
    Date d = add_months(effective, period_months);
    while (d < maturity) {
        dates.push_back(d);
        d = add_months(d, period_months);
    }
    dates.push_back(maturity);
    return dates;
}

}  // namespace credit
