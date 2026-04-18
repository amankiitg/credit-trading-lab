#pragma once

#include "credit/date.hpp"

namespace credit {

// Act/360: actual days / 360.  Used in money markets, LIBOR.
struct Act360 {
    static double year_fraction(const Date& d1, const Date& d2) {
        return static_cast<double>(days_between(d1, d2)) / 360.0;
    }
};

// Act/365 Fixed: actual days / 365.  Used for CDS, some UK bonds.
struct Act365F {
    static double year_fraction(const Date& d1, const Date& d2) {
        return static_cast<double>(days_between(d1, d2)) / 365.0;
    }
};

// 30/360 ISDA: pretend every month has 30 days.  Used for US corporates.
struct Thirty360 {
    static double year_fraction(const Date& d1, const Date& d2) {
        auto dd1 = d1.day;
        auto dd2 = d2.day;

        if (dd1 == 31) { dd1 = 30; }
        if (dd2 == 31 && dd1 >= 30) { dd2 = 30; }

        int days_30_360 = 360 * (d2.year - d1.year)
                        + 30 * (d2.month - d1.month)
                        + (dd2 - dd1);
        return static_cast<double>(days_30_360) / 360.0;
    }
};

}  // namespace credit
