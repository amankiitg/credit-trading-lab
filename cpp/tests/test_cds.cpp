#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include "credit/cds.hpp"

#include <cmath>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

using Catch::Matchers::WithinAbs;
using credit::CDSPricer;
using credit::SurvivalCurve;

// ---- Discount curve helper (same as test_bond.cpp) ----

using Curve = credit::DiscountCurve<credit::LogLinearDF, credit::Act365F>;

static Curve load_curve() {
    std::ifstream f(CREDIT_TEST_REF_DIR "/discount_curve_knots.csv");
    REQUIRE(f.is_open());

    std::vector<double> tenors;
    std::vector<double> yields;
    std::string line;
    while (std::getline(f, line)) {
        if (line.empty() || line[0] == '#' || line[0] == 't') { continue; }
        std::istringstream ss(line);
        std::string tok;
        std::getline(ss, tok, ',');
        tenors.push_back(std::stod(tok));
        std::getline(ss, tok, ',');
        yields.push_back(std::stod(tok) / 100.0);
    }
    return Curve::bootstrap(tenors, yields);
}

// ---- Helpers for test cases ----

// Known hazard structures for each reference case.
static SurvivalCurve make_curve(const std::string& case_name) {
    if (case_name == "flat_100") {
        return SurvivalCurve({10.0}, {0.01});
    }
    if (case_name == "flat_200") {
        return SurvivalCurve({10.0}, {0.02});
    }
    if (case_name == "pw1") {
        return SurvivalCurve({2.0, 5.0, 10.0}, {0.008, 0.012, 0.020});
    }
    FAIL("unknown case: " << case_name);
    return SurvivalCurve({1.0}, {0.01});  // unreachable
}

// CSV row.
struct CDSRef {
    std::string case_name;
    double maturity;
    double recovery;
    double ref_bps;
};

static std::vector<CDSRef> load_cds_vectors(const std::string& path) {
    std::ifstream f(path);
    REQUIRE(f.is_open());

    std::vector<CDSRef> rows;
    std::string line;
    while (std::getline(f, line)) {
        if (line.empty() || line[0] == '#') { continue; }

        std::istringstream ss(line);
        std::string tok;
        auto next = [&]() { std::getline(ss, tok, ','); return tok; };

        CDSRef r;
        r.case_name = next();
        r.maturity  = std::stod(next());
        r.recovery  = std::stod(next());
        r.ref_bps   = std::stod(next());
        rows.push_back(r);
    }
    return rows;
}

// ---- C13: par spread vs ISDA reference ----

TEST_CASE("C13: par spread matches reference within 0.5 bps", "[cds][C13]") {
    auto disc = load_curve();
    auto refs = load_cds_vectors(CREDIT_TEST_REF_DIR "/isda_cds_vectors.csv");
    REQUIRE(refs.size() >= 20);

    for (const auto& r : refs) {
        auto surv = make_curve(r.case_name);
        double ps = CDSPricer::par_spread(r.maturity, r.recovery, surv, disc);
        double ps_bps = ps * 10000.0;

        double err = std::abs(ps_bps - r.ref_bps);
        INFO(r.case_name << " T=" << r.maturity
             << ": computed=" << ps_bps << " bps"
             << "  ref=" << r.ref_bps << " bps"
             << "  err=" << err << " bps");
        CHECK(err < 0.5);
    }
}

// ---- C13: bootstrap round-trip ----

TEST_CASE("C13: flat hazard bootstrap round-trip", "[cds][C13]") {
    auto disc = load_curve();

    // Create a flat-hazard survival curve and compute par spreads.
    SurvivalCurve flat({10.0}, {0.01});
    std::vector<double> tenors = {0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0};
    std::vector<double> orig_spreads;
    for (double T : tenors) {
        orig_spreads.push_back(
            CDSPricer::par_spread(T, 0.4, flat, disc));
    }

    // Bootstrap from those par spreads.
    auto boot = SurvivalCurve::bootstrap(tenors, orig_spreads, 0.4, disc);

    // Recompute — must round-trip to within 1e-10.
    for (std::size_t i = 0; i < tenors.size(); ++i) {
        double ps = CDSPricer::par_spread(tenors[i], 0.4, boot, disc);
        double err = std::abs(ps - orig_spreads[i]);
        double err_bps = err * 10000.0;
        INFO("T=" << tenors[i]
             << ": orig=" << orig_spreads[i] * 10000.0 << " bps"
             << "  round-trip=" << ps * 10000.0 << " bps"
             << "  err=" << err_bps << " bps");
        CHECK(err_bps < 1e-6);
    }
}

TEST_CASE("C13: piecewise hazard bootstrap round-trip", "[cds][C13]") {
    auto disc = load_curve();

    // Piecewise hazard: [80, 120, 200] bps on [0-2y, 2-5y, 5-10y].
    SurvivalCurve pw({2.0, 5.0, 10.0}, {0.008, 0.012, 0.020});
    std::vector<double> tenors = {0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0};
    std::vector<double> orig_spreads;
    for (double T : tenors) {
        orig_spreads.push_back(
            CDSPricer::par_spread(T, 0.4, pw, disc));
    }

    // Bootstrap from those par spreads.
    auto boot = SurvivalCurve::bootstrap(tenors, orig_spreads, 0.4, disc);

    // Recompute — must round-trip.
    for (std::size_t i = 0; i < tenors.size(); ++i) {
        double ps = CDSPricer::par_spread(tenors[i], 0.4, boot, disc);
        double err_bps = std::abs(ps - orig_spreads[i]) * 10000.0;
        INFO("T=" << tenors[i]
             << ": orig=" << orig_spreads[i] * 10000.0 << " bps"
             << "  round-trip=" << ps * 10000.0 << " bps"
             << "  err=" << err_bps << " bps");
        CHECK(err_bps < 1e-6);
    }
}

// ---- Hazard rate sanity ----

TEST_CASE("C13: survival probability is monotone decreasing", "[cds][C13]") {
    SurvivalCurve flat({10.0}, {0.01});
    double prev = 1.0;
    for (double t = 0.25; t <= 12.0; t += 0.25) {
        double s = flat.survival(t);
        INFO("t=" << t << " S(t)=" << s << " S(prev)=" << prev);
        CHECK(s < prev);
        CHECK(s > 0.0);
        prev = s;
    }
}

TEST_CASE("C13: hazard rate is piecewise constant", "[cds][C13]") {
    SurvivalCurve pw({2.0, 5.0, 10.0}, {0.008, 0.012, 0.020});

    CHECK_THAT(pw.hazard(0.5), WithinAbs(0.008, 1e-15));
    CHECK_THAT(pw.hazard(1.999), WithinAbs(0.008, 1e-15));
    CHECK_THAT(pw.hazard(2.0), WithinAbs(0.008, 1e-15));
    CHECK_THAT(pw.hazard(2.001), WithinAbs(0.012, 1e-15));
    CHECK_THAT(pw.hazard(5.0), WithinAbs(0.012, 1e-15));
    CHECK_THAT(pw.hazard(5.001), WithinAbs(0.020, 1e-15));
    CHECK_THAT(pw.hazard(10.0), WithinAbs(0.020, 1e-15));
    CHECK_THAT(pw.hazard(15.0), WithinAbs(0.020, 1e-15));  // flat extrap
}

TEST_CASE("C13: negative hazard bootstrap throws", "[cds][C13]") {
    auto disc = load_curve();

    // Severely inverted spread curve: 1y=500 bps, 2y=600 bps, 5y=10 bps.
    // The 5y segment would need a negative hazard to match 10 bps par spread
    // after 600 bps for 2y.
    std::vector<double> tenors = {1.0, 2.0, 5.0};
    std::vector<double> bad_spreads = {0.05, 0.06, 0.001};

    CHECK_THROWS(SurvivalCurve::bootstrap(tenors, bad_spreads, 0.4, disc));
}

// ---- Accrual-on-default: closed form vs numerical ----

TEST_CASE("C13: accrual-on-default closed form vs numerical integral", "[cds][C13]") {
    auto disc = load_curve();

    // Test on both flat and piecewise hazard curves.
    struct Case {
        std::string name;
        SurvivalCurve surv;
        double recovery;
    };

    std::vector<Case> cases = {
        {"flat_100", SurvivalCurve({10.0}, {0.01}), 0.40},
        {"flat_200", SurvivalCurve({10.0}, {0.02}), 0.40},
        {"pw1", SurvivalCurve({2.0, 5.0, 10.0}, {0.008, 0.012, 0.020}), 0.40},
    };

    for (const auto& c : cases) {
        for (double T : {1.0, 3.0, 5.0, 10.0}) {
            auto legs = credit::detail::cds_pvs(T, c.recovery, c.surv, disc);
            double aod_closed = legs.rpv01_accrual;
            double aod_num = credit::detail::aod_numerical(
                T, c.recovery, c.surv, disc);

            double rpv = legs.rpv01();
            double diff_ps = std::abs(aod_closed - aod_num) / rpv;
            double diff_bps = diff_ps * 10000.0;

            INFO(c.name << " T=" << T
                 << ": closed=" << aod_closed
                 << "  numerical=" << aod_num
                 << "  diff=" << diff_bps << " bps");
            CHECK(diff_bps < 0.1);
        }
    }
}

// ---- Survival probability spot-checks ----

TEST_CASE("survival at knots matches exp(-cumulative hazard)", "[cds]") {
    SurvivalCurve pw({2.0, 5.0, 10.0}, {0.008, 0.012, 0.020});

    double cum = 0.0;
    cum += 0.008 * 2.0;
    CHECK_THAT(pw.survival(2.0), WithinAbs(std::exp(-cum), 1e-14));

    cum += 0.012 * 3.0;
    CHECK_THAT(pw.survival(5.0), WithinAbs(std::exp(-cum), 1e-14));

    cum += 0.020 * 5.0;
    CHECK_THAT(pw.survival(10.0), WithinAbs(std::exp(-cum), 1e-14));
}
