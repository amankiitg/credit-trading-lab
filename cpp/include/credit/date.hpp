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

inline int days_between(const Date& d1, const Date& d2) {
    return d2.serial() - d1.serial();
}

}  // namespace credit
