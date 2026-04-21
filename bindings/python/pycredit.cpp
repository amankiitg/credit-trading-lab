#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>

#include <cmath>
#include <memory>
#include <string>
#include <vector>

#include "credit/bond.hpp"
#include "credit/cds.hpp"
#include "credit/discount_curve.hpp"
#include "credit/survival_curve.hpp"
#include "credit/version.hpp"

namespace py = pybind11;
using namespace pybind11::literals;

// Concrete curve type used throughout.
using Curve = credit::DiscountCurve<credit::LogLinearDF, credit::Act365F>;

// ------------------------------------------------------------------
//  Opaque wrappers — prevent pybind11 from trying to copy/convert
//  these C++ objects.  Python sees them as opaque handles.
// ------------------------------------------------------------------
struct DiscountCurveHandle {
    std::shared_ptr<Curve> ptr;
};

struct SurvivalCurveHandle {
    std::shared_ptr<credit::SurvivalCurve> ptr;
};

// ------------------------------------------------------------------
//  Bootstrap helpers
// ------------------------------------------------------------------

static DiscountCurveHandle bootstrap_discount(
        py::array_t<double, py::array::c_style | py::array::forcecast> tenors,
        py::array_t<double, py::array::c_style | py::array::forcecast> par_yields) {
    auto n = tenors.size();
    if (n != par_yields.size()) {
        throw std::invalid_argument("tenors and par_yields must have the same length");
    }
    const double* tp = tenors.data();
    const double* yp = par_yields.data();
    std::vector<double> tv(tp, tp + n);
    std::vector<double> yv(yp, yp + n);

    auto curve = std::make_shared<Curve>(Curve::bootstrap(tv, yv));
    return {std::move(curve)};
}

static SurvivalCurveHandle bootstrap_survival(
        py::array_t<double, py::array::c_style | py::array::forcecast> tenors,
        py::array_t<double, py::array::c_style | py::array::forcecast> spreads,
        double recovery,
        const DiscountCurveHandle& disc) {
    auto n = tenors.size();
    if (n != spreads.size()) {
        throw std::invalid_argument("tenors and spreads must have the same length");
    }
    const double* tp = tenors.data();
    const double* sp = spreads.data();
    std::vector<double> tv(tp, tp + n);
    std::vector<double> sv(sp, sp + n);

    auto sc = std::make_shared<credit::SurvivalCurve>(
        credit::SurvivalCurve::bootstrap(tv, sv, recovery, *disc.ptr));
    return {std::move(sc)};
}

// ------------------------------------------------------------------
//  Bond batch pricing
// ------------------------------------------------------------------

static py::array price_bonds(
        const DiscountCurveHandle& disc,
        py::array_t<double, py::array::c_style | py::array::forcecast> coupons,
        py::array_t<int, py::array::c_style | py::array::forcecast> frequencies,
        py::array_t<double, py::array::c_style | py::array::forcecast> maturity_years,
        py::array_t<int, py::array::c_style | py::array::forcecast> day_count_codes) {

    auto n = static_cast<std::size_t>(coupons.size());
    if (frequencies.size() != static_cast<py::ssize_t>(n) ||
        maturity_years.size() != static_cast<py::ssize_t>(n) ||
        day_count_codes.size() != static_cast<py::ssize_t>(n)) {
        throw std::invalid_argument("all bond arrays must have the same length");
    }

    const double* cp  = coupons.data();
    const int*    fp  = frequencies.data();
    const double* mp  = maturity_years.data();
    const int*    dp  = day_count_codes.data();

    // Build bonds.
    credit::Date issue{2025, 1, 2};
    credit::Date settle{2025, 1, 2};

    std::vector<credit::FixedBond> bonds(n);
    for (std::size_t i = 0; i < n; ++i) {
        auto& b = bonds[i];
        b.notional = 100.0;
        b.coupon   = cp[i];
        b.frequency = fp[i];
        b.issue_date = issue;
        int mat_months = static_cast<int>(std::round(mp[i] * 12.0));
        b.maturity_date = credit::add_months(issue, mat_months);
        switch (dp[i]) {
            case 0:  b.day_count = credit::DayCountType::Act360;    break;
            case 1:  b.day_count = credit::DayCountType::Act365F;   break;
            default: b.day_count = credit::DayCountType::Thirty360; break;
        }
    }

    // Allocate output arrays.
    auto sn = static_cast<py::ssize_t>(n);
    auto out_price  = py::array_t<double>(sn);
    auto out_dv01   = py::array_t<double>(sn);
    auto out_dv01fd = py::array_t<double>(sn);
    auto out_acc    = py::array_t<double>(sn);
    auto out_ytm    = py::array_t<double>(sn);

    auto* p_price  = out_price.mutable_data();
    auto* p_dv01   = out_dv01.mutable_data();
    auto* p_dv01fd = out_dv01fd.mutable_data();
    auto* p_acc    = out_acc.mutable_data();
    auto* p_ytm    = out_ytm.mutable_data();

    const auto& curve = *disc.ptr;

    // Release GIL for the C++ computation loop.
    {
        py::gil_scoped_release release;
        for (std::size_t i = 0; i < n; ++i) {
            const auto& b = bonds[i];
            double dirty = credit::BondPricer::dirty(b, curve, settle);
            p_price[i]  = dirty;
            p_acc[i]    = credit::BondPricer::accrued(b, settle);
            p_ytm[i]    = credit::BondPricer::ytm(b, dirty, settle);
            p_dv01[i]   = credit::BondPricer::dv01(b, curve, settle);
            p_dv01fd[i] = credit::BondPricer::dv01_fd(b, curve, settle);
        }
    }

    // Build a structured numpy array via numpy.rec.fromarrays.
    auto np = py::module::import("numpy");
    py::list fields;
    fields.append(py::make_tuple("price",   "f8"));
    fields.append(py::make_tuple("dv01",    "f8"));
    fields.append(py::make_tuple("dv01_fd", "f8"));
    fields.append(py::make_tuple("accrued", "f8"));
    fields.append(py::make_tuple("ytm",     "f8"));
    auto dtype = py::dtype::from_args(fields);

    py::list arrays;
    arrays.append(out_price);
    arrays.append(out_dv01);
    arrays.append(out_dv01fd);
    arrays.append(out_acc);
    arrays.append(out_ytm);

    auto result = np.attr("rec").attr("fromarrays")(arrays, "dtype"_a = dtype);
    return result.cast<py::array>();
}

// ------------------------------------------------------------------
//  CDS batch pricing
// ------------------------------------------------------------------

static py::array price_cds(
        const SurvivalCurveHandle& surv,
        const DiscountCurveHandle& disc,
        py::array_t<double, py::array::c_style | py::array::forcecast> maturity_years,
        py::array_t<double, py::array::c_style | py::array::forcecast> coupons,
        py::array_t<double, py::array::c_style | py::array::forcecast> recoveries,
        py::array_t<double, py::array::c_style | py::array::forcecast> notionals) {

    auto n = static_cast<std::size_t>(maturity_years.size());
    if (coupons.size() != static_cast<py::ssize_t>(n) ||
        recoveries.size() != static_cast<py::ssize_t>(n) ||
        notionals.size() != static_cast<py::ssize_t>(n)) {
        throw std::invalid_argument("all CDS arrays must have the same length");
    }

    const double* mp = maturity_years.data();
    const double* cp = coupons.data();
    const double* rp = recoveries.data();
    const double* np_ = notionals.data();

    // Allocate output arrays.
    auto sn = static_cast<py::ssize_t>(n);
    auto out_mtm = py::array_t<double>(sn);
    auto out_ps  = py::array_t<double>(sn);
    auto out_cs  = py::array_t<double>(sn);
    auto out_rpv = py::array_t<double>(sn);

    auto* p_mtm = out_mtm.mutable_data();
    auto* p_ps  = out_ps.mutable_data();
    auto* p_cs  = out_cs.mutable_data();
    auto* p_rpv = out_rpv.mutable_data();

    const auto& sc = *surv.ptr;
    const auto& dc = *disc.ptr;

    // Release GIL for the C++ computation loop.
    {
        py::gil_scoped_release release;
        for (std::size_t i = 0; i < n; ++i) {
            double mat = mp[i];
            double cpn = cp[i];
            double rec = rp[i];
            double ntl = np_[i];

            p_mtm[i] = credit::CDSPricer::mtm(mat, cpn, rec, ntl, sc, dc);
            p_ps[i]  = credit::CDSPricer::par_spread(mat, rec, sc, dc);
            p_cs[i]  = credit::CDSPricer::cs01(mat, cpn, rec, ntl, sc, dc);
            p_rpv[i] = credit::CDSPricer::rpv01(mat, rec, sc, dc);
        }
    }

    // Build a structured numpy array via numpy.rec.fromarrays.
    auto np = py::module::import("numpy");
    py::list fields;
    fields.append(py::make_tuple("mtm",        "f8"));
    fields.append(py::make_tuple("par_spread", "f8"));
    fields.append(py::make_tuple("cs01",       "f8"));
    fields.append(py::make_tuple("rpv01",      "f8"));
    auto dtype = py::dtype::from_args(fields);

    py::list arrays;
    arrays.append(out_mtm);
    arrays.append(out_ps);
    arrays.append(out_cs);
    arrays.append(out_rpv);

    auto result = np.attr("rec").attr("fromarrays")(arrays, "dtype"_a = dtype);
    return result.cast<py::array>();
}

// ------------------------------------------------------------------
//  Curve query helpers
// ------------------------------------------------------------------

static py::array_t<double> discount_factors(
        const DiscountCurveHandle& disc,
        py::array_t<double, py::array::c_style | py::array::forcecast> times) {
    auto n = static_cast<std::size_t>(times.size());
    const double* tp = times.data();
    auto out = py::array_t<double>(static_cast<py::ssize_t>(n));
    auto* p = out.mutable_data();
    const auto& curve = *disc.ptr;
    for (std::size_t i = 0; i < n; ++i) {
        p[i] = curve.df(tp[i]);
    }
    return out;
}

static py::array_t<double> survival_probs(
        const SurvivalCurveHandle& surv,
        py::array_t<double, py::array::c_style | py::array::forcecast> times) {
    auto n = static_cast<std::size_t>(times.size());
    const double* tp = times.data();
    auto out = py::array_t<double>(static_cast<py::ssize_t>(n));
    auto* p = out.mutable_data();
    const auto& sc = *surv.ptr;
    for (std::size_t i = 0; i < n; ++i) {
        p[i] = sc.survival(tp[i]);
    }
    return out;
}

// ------------------------------------------------------------------
//  Module definition
// ------------------------------------------------------------------

PYBIND11_MODULE(pycredit, m) {
    m.doc() = "credit pricing C++ core (sprint v2)";

    // Smoke test.
    m.def("hello", []() -> std::string { return "ok"; },
          "smoke-test entry point; returns \"ok\"");
    m.def("version", []() { return std::string(credit::version()); },
          "underlying libcredit version string");

    // Opaque curve handles.
    py::class_<DiscountCurveHandle>(m, "DiscountCurve")
        .def("df", [](const DiscountCurveHandle& h, double t) {
            return h.ptr->df(t);
        }, py::arg("t"), "discount factor at time t (years)")
        .def("zero_rate", [](const DiscountCurveHandle& h, double t) {
            return h.ptr->zero_rate(t);
        }, py::arg("t"), "continuously-compounded zero rate at time t");

    py::class_<SurvivalCurveHandle>(m, "SurvivalCurve")
        .def("survival", [](const SurvivalCurveHandle& h, double t) {
            return h.ptr->survival(t);
        }, py::arg("t"), "survival probability at time t (years)")
        .def("hazard", [](const SurvivalCurveHandle& h, double t) {
            return h.ptr->hazard(t);
        }, py::arg("t"), "piecewise-constant hazard rate at time t");

    // Bootstrap.
    m.def("bootstrap_discount", &bootstrap_discount,
          py::arg("tenors"), py::arg("par_yields"),
          "Bootstrap a discount curve from par yields (decimal, e.g. 0.0425).\n"
          "Returns an opaque DiscountCurve handle.");

    m.def("bootstrap_survival", &bootstrap_survival,
          py::arg("tenors"), py::arg("spreads"),
          py::arg("recovery"), py::arg("discount"),
          "Bootstrap a survival curve from CDS par spreads (decimal).\n"
          "Returns an opaque SurvivalCurve handle.");

    // Batch pricing.
    m.def("price_bonds", &price_bonds,
          py::arg("curve"), py::arg("coupons"), py::arg("frequencies"),
          py::arg("maturity_years"), py::arg("day_count_codes"),
          "Batch-price bonds. Returns a structured numpy array with columns:\n"
          "price, dv01, dv01_fd, accrued, ytm.\n"
          "day_count_codes: 0=Act360, 1=Act365F, 2=Thirty360 (default).");

    m.def("price_cds", &price_cds,
          py::arg("survival"), py::arg("discount"),
          py::arg("maturity_years"), py::arg("coupons"),
          py::arg("recoveries"), py::arg("notionals"),
          "Batch-price CDS contracts. Returns a structured numpy array with columns:\n"
          "mtm, par_spread, cs01, rpv01.");

    // Curve query.
    m.def("discount_factors", &discount_factors,
          py::arg("curve"), py::arg("times"),
          "Vectorized discount factor query.");

    m.def("survival_probs", &survival_probs,
          py::arg("curve"), py::arg("times"),
          "Vectorized survival probability query.");
}
