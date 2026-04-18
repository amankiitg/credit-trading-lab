#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include "credit/interp.hpp"

using Catch::Matchers::WithinAbs;

static const std::vector<double> kTenors = {1.0, 2.0, 3.0, 5.0, 7.0, 10.0};

TEST_CASE("LinearYield round-trips on knots", "[interp]") {
    std::vector<double> yields = {0.04, 0.042, 0.043, 0.045, 0.046, 0.048};

    for (std::size_t i = 0; i < kTenors.size(); ++i) {
        INFO("knot " << i << " at t=" << kTenors[i]);
        CHECK_THAT(credit::LinearYield::interp(kTenors, yields, kTenors[i]),
                   WithinAbs(yields[i], 1e-12));
    }
}

TEST_CASE("LinearYield interpolates linearly between knots", "[interp]") {
    std::vector<double> yields = {0.04, 0.05};
    std::vector<double> xs = {1.0, 2.0};

    // Midpoint should be exact average
    CHECK_THAT(credit::LinearYield::interp(xs, yields, 1.5),
               WithinAbs(0.045, 1e-15));
}

TEST_CASE("LogLinearDF round-trips on knots", "[interp]") {
    // Discount factors: must be in (0, 1]
    std::vector<double> dfs = {0.96, 0.92, 0.88, 0.80, 0.73, 0.62};

    for (std::size_t i = 0; i < kTenors.size(); ++i) {
        INFO("knot " << i << " at t=" << kTenors[i]);
        CHECK_THAT(credit::LogLinearDF::interp(kTenors, dfs, kTenors[i]),
                   WithinAbs(dfs[i], 1e-12));
    }
}

TEST_CASE("LogLinearDF interpolates in log-space", "[interp]") {
    std::vector<double> xs = {1.0, 2.0};
    std::vector<double> dfs = {0.96, 0.92};

    double mid = credit::LogLinearDF::interp(xs, dfs, 1.5);

    // In log-space, midpoint = exp((log(0.96) + log(0.92)) / 2)
    // = sqrt(0.96 * 0.92) = geometric mean
    double expected = std::sqrt(0.96 * 0.92);
    CHECK_THAT(mid, WithinAbs(expected, 1e-12));
}

TEST_CASE("PiecewiseConstantHazard round-trips on knots", "[interp]") {
    std::vector<double> lambdas = {0.02, 0.025, 0.03, 0.028, 0.032, 0.035};

    for (std::size_t i = 0; i < kTenors.size(); ++i) {
        INFO("knot " << i << " at t=" << kTenors[i]);
        CHECK_THAT(
            credit::PiecewiseConstantHazard::interp(kTenors, lambdas, kTenors[i]),
            WithinAbs(lambdas[i], 1e-12));
    }
}

TEST_CASE("PiecewiseConstantHazard is flat between knots", "[interp]") {
    std::vector<double> xs = {1.0, 2.0, 5.0};
    std::vector<double> lambdas = {0.02, 0.03, 0.04};

    // Any point in (1, 2] should return lambda[1] = 0.03
    CHECK_THAT(credit::PiecewiseConstantHazard::interp(xs, lambdas, 1.5),
               WithinAbs(0.03, 1e-15));
    CHECK_THAT(credit::PiecewiseConstantHazard::interp(xs, lambdas, 1.999),
               WithinAbs(0.03, 1e-15));

    // Any point in (2, 5] should return lambda[2] = 0.04
    CHECK_THAT(credit::PiecewiseConstantHazard::interp(xs, lambdas, 3.0),
               WithinAbs(0.04, 1e-15));
}

TEST_CASE("all interpolators flat-extrapolate outside the grid", "[interp]") {
    std::vector<double> xs = {1.0, 5.0, 10.0};
    std::vector<double> ys = {0.04, 0.045, 0.05};

    // Left of first knot → first value
    CHECK_THAT(credit::LinearYield::interp(xs, ys, 0.5),
               WithinAbs(0.04, 1e-15));
    CHECK_THAT(credit::LogLinearDF::interp(xs, ys, 0.5),
               WithinAbs(0.04, 1e-15));
    CHECK_THAT(credit::PiecewiseConstantHazard::interp(xs, ys, 0.5),
               WithinAbs(0.04, 1e-15));

    // Right of last knot → last value
    CHECK_THAT(credit::LinearYield::interp(xs, ys, 15.0),
               WithinAbs(0.05, 1e-15));
    CHECK_THAT(credit::LogLinearDF::interp(xs, ys, 15.0),
               WithinAbs(0.05, 1e-15));
    CHECK_THAT(credit::PiecewiseConstantHazard::interp(xs, ys, 15.0),
               WithinAbs(0.05, 1e-15));
}
