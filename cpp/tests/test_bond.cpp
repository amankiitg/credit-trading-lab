#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include "credit/bond.hpp"

#include <cmath>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

using Catch::Matchers::WithinAbs;
using credit::BondPricer;
using credit::Date;
using credit::DayCountType;
using credit::FixedBond;

// Parse YYYYMMDD integer into a Date.
static Date parse_date(const std::string& s) {
    int v = std::stoi(s);
    return {v / 10000, (v / 100) % 100, v % 100};
}

// One row from bond_ytm_vectors.csv.
struct BondRow {
    std::string name;
    FixedBond   bond;
    Date        settle;
    double      ref_dirty;
    double      ref_ytm;   // decimal
};

static std::vector<BondRow> load_bonds(const std::string& path) {
    std::ifstream f(path);
    REQUIRE(f.is_open());

    std::vector<BondRow> rows;
    std::string line;
    while (std::getline(f, line)) {
        if (line.empty() || line[0] == '#') { continue; }

        std::istringstream ss(line);
        std::string tok;
        auto next = [&]() { std::getline(ss, tok, ','); return tok; };

        BondRow r;
        r.name                = next();
        r.bond.notional       = std::stod(next());
        r.bond.coupon         = std::stod(next()) / 100.0;
        r.bond.frequency      = std::stoi(next());
        r.bond.issue_date     = parse_date(next());
        r.bond.maturity_date  = parse_date(next());
        r.settle              = parse_date(next());
        std::string dc        = next();
        r.bond.day_count      = DayCountType::Thirty360;  // all rows use 30/360
        r.ref_dirty           = std::stod(next());
        r.ref_ytm             = std::stod(next()) / 100.0;

        rows.push_back(r);
    }
    return rows;
}

// ---- C14: YTM accuracy within 1 bp ----

TEST_CASE("C14: dirty price matches Python reference within 1e-8", "[bond][C14]") {
    auto rows = load_bonds(CREDIT_TEST_REF_DIR "/bond_ytm_vectors.csv");
    REQUIRE(rows.size() == 10);

    for (const auto& r : rows) {
        double dirty = BondPricer::dirty_at_yield(r.bond, r.ref_ytm, r.settle);
        INFO(r.name << ": dirty=" << dirty << "  ref=" << r.ref_dirty
             << "  err=" << (dirty - r.ref_dirty));
        CHECK_THAT(dirty, WithinAbs(r.ref_dirty, 1e-8));
    }
}

TEST_CASE("C14: YTM round-trip error < 1.0 bp for all 10 bonds", "[bond][C14]") {
    auto rows = load_bonds(CREDIT_TEST_REF_DIR "/bond_ytm_vectors.csv");
    REQUIRE(rows.size() == 10);

    for (const auto& r : rows) {
        double ytm = BondPricer::ytm(r.bond, r.ref_dirty, r.settle);
        double err_bp = std::abs(ytm - r.ref_ytm) * 10000.0;
        INFO(r.name << ": ytm=" << ytm << "  ref=" << r.ref_ytm
             << "  error=" << err_bp << " bp");
        CHECK(err_bp < 1.0);
    }
}

// ---- Accrued interest sanity ----

TEST_CASE("accrued interest is non-negative and <= one coupon", "[bond]") {
    auto rows = load_bonds(CREDIT_TEST_REF_DIR "/bond_ytm_vectors.csv");

    for (const auto& r : rows) {
        double acc = BondPricer::accrued(r.bond, r.settle);
        double max_coupon = r.bond.coupon / r.bond.frequency * r.bond.notional;
        INFO(r.name << ": accrued=" << acc << "  max_coupon=" << max_coupon);
        CHECK(acc >= 0.0);
        CHECK(acc <= max_coupon + 1e-10);
    }
}

// ---- Analytical spot-checks ----

TEST_CASE("zero-coupon bond dirty price = N / (1+y)^T", "[bond]") {
    FixedBond zero;
    zero.notional      = 100.0;
    zero.coupon         = 0.0;
    zero.frequency      = 1;
    zero.issue_date     = {2020, 1, 1};
    zero.maturity_date  = {2030, 1, 1};
    zero.day_count      = DayCountType::Thirty360;

    Date settle{2025, 1, 1};
    double y = 0.05;

    double dirty = BondPricer::dirty_at_yield(zero, y, settle);

    // 30/360 from 2025-01-01 to 2030-01-01 = 5.0 years exactly
    double expected = 100.0 / std::pow(1.05, 5.0);
    INFO("dirty=" << dirty << "  expected=" << expected);
    CHECK_THAT(dirty, WithinAbs(expected, 1e-10));

    // YTM round-trip
    double solved = BondPricer::ytm(zero, dirty, settle);
    CHECK_THAT(solved, WithinAbs(y, 1e-10));
}

TEST_CASE("at-par bond on coupon date: dirty = notional", "[bond]") {
    // A bond where coupon rate = YTM, settled on a coupon date, prices to par.
    FixedBond par_bond;
    par_bond.notional      = 100.0;
    par_bond.coupon         = 0.06;
    par_bond.frequency      = 2;
    par_bond.issue_date     = {2020, 3, 15};
    par_bond.maturity_date  = {2030, 3, 15};
    par_bond.day_count      = DayCountType::Thirty360;

    // Settle on a coupon date (2025-03-15)
    Date settle{2025, 3, 15};
    double y = 0.06;

    double dirty = BondPricer::dirty_at_yield(par_bond, y, settle);
    INFO("dirty=" << dirty);
    CHECK_THAT(dirty, WithinAbs(100.0, 1e-8));

    double acc = BondPricer::accrued(par_bond, settle);
    CHECK_THAT(acc, WithinAbs(0.0, 1e-12));
}

TEST_CASE("clean = dirty - accrued", "[bond]") {
    auto rows = load_bonds(CREDIT_TEST_REF_DIR "/bond_ytm_vectors.csv");

    for (const auto& r : rows) {
        double dirty = BondPricer::dirty_at_yield(r.bond, r.ref_ytm, r.settle);
        double acc   = BondPricer::accrued(r.bond, r.settle);
        double clean = dirty - acc;
        INFO(r.name << ": dirty=" << dirty << " acc=" << acc << " clean=" << clean);
        CHECK(clean > 0.0);
        CHECK(clean < 200.0);  // sanity: no bond is worth > 200
    }
}
