#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include "credit/daycount.hpp"

using namespace credit;
using Catch::Matchers::WithinAbs;

// Reference table: 15 date pairs with hand-computed year fractions.
// Each row: {d1, d2, Act360, Act365F, Thirty360}
struct DayCountRow {
    Date d1, d2;
    double act360, act365f, thirty360;
};

// clang-format off
static const DayCountRow kTable[] = {
    // Row 1: basic 60-day span
    {{2024,1,1}, {2024,3,1}, 60.0/360, 60.0/365, 60.0/360},
    // Row 2: single day
    {{2024,1,1}, {2024,1,2}, 1.0/360, 1.0/365, 1.0/360},
    // Row 3: same day → zero
    {{2024,6,15}, {2024,6,15}, 0.0, 0.0, 0.0},
    // Row 4: leap year Feb (2024 is leap)
    // 30/360: d1=31→30, d2=1, days = 30*(3-1)+(1-30) = 31
    {{2024,1,31}, {2024,3,1}, 30.0/360, 30.0/365, 31.0/360},
    // Row 5: non-leap year Feb (2023)
    {{2023,1,31}, {2023,3,1}, 29.0/360, 29.0/365, 31.0/360},
    // Row 6: full year
    {{2023,1,1}, {2024,1,1}, 365.0/360, 365.0/365, 360.0/360},
    // Row 7: leap year full year
    {{2024,1,1}, {2025,1,1}, 366.0/360, 366.0/365, 360.0/360},
    // Row 8: 30/360 special — both 31st, d1=31 triggers d2 clamp
    {{2024,1,31}, {2024,3,31}, 60.0/360, 60.0/365, 60.0/360},
    // Row 9: 30/360 — d2=31 but d1 < 30 so no clamp on d2
    {{2024,1,15}, {2024,3,31}, 76.0/360, 76.0/365, 76.0/360},
    // Row 10: end-of-month February to June
    {{2024,2,29}, {2024,6,30}, 122.0/360, 122.0/365,
     // 30/360: 360*(0) + 30*(6-2) + (30-29) = 121
     121.0/360},
    // Row 11: cross-year boundary
    {{2023,12,15}, {2024,1,15}, 31.0/360, 31.0/365, 30.0/360},
    // Row 12: 6-month span
    {{2024,1,1}, {2024,7,1}, 182.0/360, 182.0/365, 180.0/360},
    // Row 13: 30/360 tricky — Jan 30 to Feb 28 (non-leap)
    {{2023,1,30}, {2023,2,28}, 29.0/360, 29.0/365,
     // 30/360: 360*0 + 30*(2-1) + (28-30) = 28
     28.0/360},
    // Row 14: 10-year span
    {{2015,3,20}, {2025,3,20}, 3653.0/360, 3653.0/365, 3600.0/360},
    // Row 15: short month-end to month-end
    {{2024,4,30}, {2024,5,31}, 31.0/360, 31.0/365,
     // 30/360: d1=30 (clamped from 30→30 no-op), d2=31→30 (d1>=30)
     // 360*0 + 30*(5-4) + (30-30) = 30
     30.0/360},
};
// clang-format on

TEST_CASE("day-count 15-row reference table", "[daycount]") {
    constexpr double tol = 1e-15;  // exact to the nanosecond

    for (std::size_t row = 0; row < 15; ++row) {
        const auto& r = kTable[row];
        INFO("Row " << row + 1 << ": ("
             << r.d1.year << "-" << r.d1.month << "-" << r.d1.day << " → "
             << r.d2.year << "-" << r.d2.month << "-" << r.d2.day << ")");

        CHECK_THAT(Act360::year_fraction(r.d1, r.d2),
                   WithinAbs(r.act360, tol));
        CHECK_THAT(Act365F::year_fraction(r.d1, r.d2),
                   WithinAbs(r.act365f, tol));
        CHECK_THAT(Thirty360::year_fraction(r.d1, r.d2),
                   WithinAbs(r.thirty360, tol));
    }
}

TEST_CASE("day-count conventions have no state", "[daycount]") {
    // Verify calling twice with same inputs gives identical results
    Date d1{2024, 3, 15}, d2{2024, 9, 15};
    REQUIRE(Act360::year_fraction(d1, d2) == Act360::year_fraction(d1, d2));
    REQUIRE(Act365F::year_fraction(d1, d2) == Act365F::year_fraction(d1, d2));
    REQUIRE(Thirty360::year_fraction(d1, d2) == Thirty360::year_fraction(d1, d2));
}
