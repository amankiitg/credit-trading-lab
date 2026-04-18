#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include "credit/discount_curve.hpp"

#include <fstream>
#include <sstream>
#include <string>

using Catch::Matchers::WithinAbs;
using Curve = credit::DiscountCurve<credit::LogLinearDF, credit::Act365F>;

// Load the reference CSV: tenor,par_yield_pct
static void load_ref(const std::string& path,
                     std::vector<double>& tenors,
                     std::vector<double>& par_yields) {
    std::ifstream f(path);
    REQUIRE(f.is_open());

    std::string line;
    while (std::getline(f, line)) {
        if (line.empty() || line[0] == '#' || line.substr(0, 5) == "tenor") {
            continue;
        }
        std::istringstream ss(line);
        std::string tok;
        std::getline(ss, tok, ',');
        double tenor = std::stod(tok);
        std::getline(ss, tok, ',');
        double yield_pct = std::stod(tok);
        tenors.push_back(tenor);
        par_yields.push_back(yield_pct / 100.0);  // percent → decimal
    }
}

TEST_CASE("C12: discount curve knot reprice within 1e-10", "[discount][C12]") {
    std::vector<double> tenors, par_yields;
    load_ref(CREDIT_TEST_REF_DIR "/discount_curve_knots.csv", tenors, par_yields);
    REQUIRE(tenors.size() == 8);

    auto curve = Curve::bootstrap(tenors, par_yields);

    // For each input tenor, reprice a par bond and check it prices to 1.0
    // Uses the same schedule logic as DiscountCurve::bootstrap:
    //   coupon dates step backward from T by 1.0; accrual = gap between dates.
    for (std::size_t k = 0; k < tenors.size(); ++k) {
        double T = tenors[k];
        double c = par_yields[k];

        // Build coupon schedule (ascending)
        std::vector<double> cpn_dates;
        for (double t = T; t > 1e-12; t -= 1.0) {
            cpn_dates.push_back(t);
        }
        std::reverse(cpn_dates.begin(), cpn_dates.end());

        double price = 0.0;
        double prev = 0.0;
        for (double t_j : cpn_dates) {
            double accrual = t_j - prev;
            price += c * accrual * curve.df(t_j);
            prev = t_j;
        }
        price += curve.df(T);  // principal repayment

        INFO("tenor=" << T << "  par_yield=" << c
             << "  repriced=" << price << "  error=" << (price - 1.0));
        CHECK_THAT(price, WithinAbs(1.0, 1e-10));
    }
}

TEST_CASE("C12: discount factors monotone-positive", "[discount][C12]") {
    std::vector<double> tenors, par_yields;
    load_ref(CREDIT_TEST_REF_DIR "/discount_curve_knots.csv", tenors, par_yields);

    auto curve = Curve::bootstrap(tenors, par_yields);

    // Check DF(0) = 1
    CHECK_THAT(curve.df(0.0), WithinAbs(1.0, 1e-15));

    // Check monotone decreasing over a fine grid
    double prev_df = 1.0;
    for (double t = 0.5; t <= 30.0; t += 0.5) {
        double d = curve.df(t);
        INFO("t=" << t << "  DF=" << d);
        CHECK(d > 0.0);
        CHECK(d <= 1.0);
        CHECK(d < prev_df);
        prev_df = d;
    }
}

TEST_CASE("discount curve zero rates are positive", "[discount]") {
    std::vector<double> tenors, par_yields;
    load_ref(CREDIT_TEST_REF_DIR "/discount_curve_knots.csv", tenors, par_yields);

    auto curve = Curve::bootstrap(tenors, par_yields);

    for (double t = 0.5; t <= 30.0; t += 0.5) {
        double r = curve.zero_rate(t);
        INFO("t=" << t << "  zero_rate=" << r);
        CHECK(r > 0.0);
    }
}
