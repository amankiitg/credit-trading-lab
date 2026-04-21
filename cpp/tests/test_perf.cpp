#include <catch2/catch_test_macros.hpp>
#include <catch2/benchmark/catch_benchmark.hpp>

#include "credit/bond.hpp"
#include "credit/cds.hpp"

#include <cmath>
#include <fstream>
#include <iomanip>
#include <random>
#include <sstream>
#include <string>
#include <vector>

using Curve = credit::DiscountCurve<credit::LogLinearDF, credit::Act365F>;

// Shared discount curve (FRED DGS 2025-01-02).
static Curve load_curve() {
    std::ifstream f(CREDIT_TEST_REF_DIR "/discount_curve_knots.csv");
    REQUIRE(f.is_open());
    std::vector<double> tenors, yields;
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

// ---- Parity dump: 20 bonds + 20 CDS priced in C++, saved to CSV ----

TEST_CASE("parity dump: 20 bonds + 20 CDS to CSV", "[perf][parity]") {
    auto disc = load_curve();
    credit::Date issue{2025, 1, 2};
    credit::Date settle{2025, 1, 2};

    // 20 bonds with deterministic parameters.
    std::mt19937 rng(42);
    std::uniform_real_distribution<double> cpn_dist(0.01, 0.10);
    std::uniform_int_distribution<int> freq_dist(1, 2);
    std::uniform_int_distribution<int> mat_dist(1, 30);

    std::ofstream out(CREDIT_TEST_REF_DIR "/parity_dump.csv");
    REQUIRE(out.is_open());
    out << std::setprecision(15);
    out << "# Parity dump: C++ reference values for Python cross-check\n";
    out << "# seed=42, settle=2025-01-02, discount=FRED DGS 2025-01-02\n";

    // Bond section.
    out << "# type,coupon,frequency,maturity_years,daycount_code,price,dv01,dv01_fd,accrued,ytm\n";

    for (int i = 0; i < 20; ++i) {
        credit::FixedBond b;
        b.notional = 100.0;
        b.coupon = cpn_dist(rng);
        b.frequency = freq_dist(rng);
        int mat_y = mat_dist(rng);
        b.issue_date = issue;
        b.maturity_date = credit::add_months(issue, mat_y * 12);
        b.day_count = credit::DayCountType::Thirty360;

        double dirty = credit::BondPricer::dirty(b, disc, settle);
        double acc   = credit::BondPricer::accrued(b, settle);
        double ytm   = credit::BondPricer::ytm(b, dirty, settle);
        double dv01  = credit::BondPricer::dv01(b, disc, settle);
        double dv01f = credit::BondPricer::dv01_fd(b, disc, settle);

        out << "bond," << b.coupon << "," << b.frequency << ","
            << mat_y << ",2,"
            << dirty << "," << dv01 << "," << dv01f << ","
            << acc << "," << ytm << "\n";
    }

    // CDS section: bootstrap a survival curve, then price 20 CDS.
    // Use flat 100 bps hazard for simplicity.
    credit::SurvivalCurve flat({10.0}, {0.01});
    std::vector<double> surv_tenors = {0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0};
    std::vector<double> par_spreads;
    for (double T : surv_tenors) {
        par_spreads.push_back(
            credit::CDSPricer::par_spread(T, 0.4, flat, disc));
    }
    auto surv = credit::SurvivalCurve::bootstrap(
        surv_tenors, par_spreads, 0.4, disc);

    // Dump survival curve bootstrap inputs so Python can replicate exactly.
    out << "# surv_bootstrap_tenors:";
    for (std::size_t i = 0; i < surv_tenors.size(); ++i) {
        out << (i ? "," : "") << surv_tenors[i];
    }
    out << "\n";
    out << "# surv_bootstrap_spreads:";
    for (std::size_t i = 0; i < par_spreads.size(); ++i) {
        out << (i ? "," : "") << par_spreads[i];
    }
    out << "\n";
    out << "# surv_recovery:0.4\n";
    out << "# type,maturity_years,coupon,recovery,notional,mtm,par_spread,cs01,rpv01\n";

    std::uniform_real_distribution<double> cds_cpn_dist(0.003, 0.012);
    std::uniform_int_distribution<int> cds_mat_dist(1, 10);

    for (int i = 0; i < 20; ++i) {
        double mat = static_cast<double>(cds_mat_dist(rng));
        double cpn = cds_cpn_dist(rng);
        double rec = 0.4;
        double ntl = 10'000'000.0;

        double mtm = credit::CDSPricer::mtm(mat, cpn, rec, ntl, surv, disc);
        double ps  = credit::CDSPricer::par_spread(mat, rec, surv, disc);
        double cs  = credit::CDSPricer::cs01(mat, cpn, rec, ntl, surv, disc);
        double rpv = credit::CDSPricer::rpv01(mat, rec, surv, disc);

        out << "cds," << mat << "," << cpn << ","
            << rec << "," << ntl << ","
            << mtm << "," << ps << "," << cs << "," << rpv << "\n";
    }

    out.close();
    REQUIRE(out.good());
}
