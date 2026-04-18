#pragma once

#include <cstdint>

namespace credit {

// Minimal calendar date for day-count and schedule math.
// Stores year/month/day; converts to a serial (Julian day number) for
// date arithmetic.  No timezone, no intraday — settlement-date precision.
struct Date {
    int year;
    int month;
    int day;

    // Julian Day Number — days since a fixed epoch.  Used only for
    // subtraction (d2.serial() - d1.serial() == actual calendar days).
    [[nodiscard]] int serial() const {
        int a = (14 - month) / 12;
        int y = year + 4800 - a;
        int m = month + 12 * a - 3;
        return day + (153 * m + 2) / 5 + 365 * y + y / 4 - y / 100 + y / 400 - 32045;
    }
};

inline bool operator==(const Date& a, const Date& b) {
    return a.year == b.year && a.month == b.month && a.day == b.day;
}

inline bool operator<(const Date& a, const Date& b) {
    return a.serial() < b.serial();
}

inline bool operator<=(const Date& a, const Date& b) {
    return !(b < a);
}

inline bool operator>(const Date& a, const Date& b) {
    return b < a;
}

inline bool operator>=(const Date& a, const Date& b) {
    return !(a < b);
}

inline int days_between(const Date& d1, const Date& d2) {
    return d2.serial() - d1.serial();
}

// Shift a date by +/- months.  Day is clamped to end-of-month when the
// target month is shorter (e.g., Jan-31 + 1m → Feb-28/29).
inline Date add_months(const Date& d, int months) {
    int total = d.year * 12 + (d.month - 1) + months;
    int y = total / 12;
    int m = total % 12 + 1;
    if (m <= 0) { m += 12; --y; }

    static constexpr int kDays[] = {0,31,28,31,30,31,30,31,31,30,31,30,31};
    int max_day = kDays[m];
    if (m == 2 && (y % 4 == 0 && (y % 100 != 0 || y % 400 == 0)))
        max_day = 29;
    return {y, m, (d.day > max_day) ? max_day : d.day};
}

}  // namespace credit
