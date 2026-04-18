#pragma once

#include <cassert>
#include <cmath>
#include <stdexcept>
#include <vector>

#include "credit/daycount.hpp"
#include "credit/interp.hpp"

namespace credit {

// Discount curve bootstrapped from par yields.
//
// Template parameters are policy classes (V2):
//   Interp — how to interpolate between knot DFs (e.g., LogLinearDF)
//   DC     — day-count convention for year fractions (e.g., Act365F)
template <typename Interp, typename DC = Act365F>
class DiscountCurve {
public:
    // Bootstrap from par yield term structure.
    //   tenors:     maturities in years (sorted ascending, e.g., {1,2,3,5,7,10,20,30})
    //   par_yields: par coupon rates in decimal (e.g., 0.0417 for 4.17%)
    //
    // For each tenor T, solves for DF(T) such that a par bond with
    // coupon = par_yield prices to 1.0 on the curve.  Intermediate
    // coupon DFs that fall between the last known knot and T depend on
    // DF(T) via LogLinearDF interpolation, so we use Newton's method.
    static DiscountCurve bootstrap(const std::vector<double>& tenors,
                                   const std::vector<double>& par_yields) {
        assert(tenors.size() == par_yields.size());
        assert(!tenors.empty());

        std::vector<double> knot_tenors;
        std::vector<double> knot_dfs;

        knot_tenors.push_back(0.0);
        knot_dfs.push_back(1.0);

        for (std::size_t k = 0; k < tenors.size(); ++k) {
            double T = tenors[k];
            double c = par_yields[k];
            int n_coupons = static_cast<int>(T);

            // PV of coupons at dates that already have known DFs
            // (dates <= last bootstrapped knot)
            double t_prev = knot_tenors.back();
            double df_prev = knot_dfs.back();
            double known_pv = 0.0;

            for (int j = 1; j <= n_coupons; ++j) {
                double t_j = static_cast<double>(j);
                if (t_j <= t_prev) {
                    known_pv += c * Interp::interp(knot_tenors, knot_dfs, t_j);
                }
            }

            // Newton's method: solve for x = DF(T) such that
            //   f(x) = known_pv + dependent_pv(x) + x - 1 = 0
            //
            // For coupon dates t_j in (t_prev, T], DF(t_j) depends on x
            // via LogLinearDF:
            //   DF(t_j) = df_prev^(1 - alpha_j) * x^(alpha_j)
            // where alpha_j = (t_j - t_prev) / (T - t_prev)

            // Collect the alpha exponents for dependent coupon dates
            std::vector<double> alphas;
            bool maturity_is_coupon = false;
            for (int j = 1; j <= n_coupons; ++j) {
                double t_j = static_cast<double>(j);
                if (t_j > t_prev && t_j < T) {
                    double alpha = (t_j - t_prev) / (T - t_prev);
                    alphas.push_back(alpha);
                } else if (t_j == T) {
                    maturity_is_coupon = true;
                }
            }

            // f(x) = known_pv + sum_j(c * df_prev^(1-a_j) * x^a_j) + (1+c)*x - 1
            //         if maturity date is a coupon date; otherwise last term is just x
            // f'(x) = sum_j(c * df_prev^(1-a_j) * a_j * x^(a_j-1)) + (1+c) or 1

            double x = df_prev * std::exp(-c * (T - t_prev));  // initial guess

            constexpr int kMaxIter = 50;
            constexpr double kTol = 1e-14;

            for (int iter = 0; iter < kMaxIter; ++iter) {
                double dep_pv = 0.0;
                double dep_deriv = 0.0;

                for (double alpha : alphas) {
                    double xa = std::pow(x, alpha);
                    double dp = std::pow(df_prev, 1.0 - alpha);
                    dep_pv += c * dp * xa;
                    dep_deriv += c * dp * alpha * xa / x;
                }

                double mat_coeff = maturity_is_coupon ? (1.0 + c) : 1.0;
                double f = known_pv + dep_pv + mat_coeff * x - 1.0;
                double fp = dep_deriv + mat_coeff;

                double dx = -f / fp;
                x += dx;

                if (std::abs(dx) < kTol) { break; }
            }

            if (x <= 0.0 || x > 1.0) {
                throw std::runtime_error(
                    "bootstrap: invalid discount factor at tenor "
                    + std::to_string(T));
            }

            knot_tenors.push_back(T);
            knot_dfs.push_back(x);
        }

        return DiscountCurve(std::move(knot_tenors), std::move(knot_dfs));
    }

    // Discount factor at time t (in years).
    [[nodiscard]] double df(double t) const {
        if (t <= 0.0) { return 1.0; }
        return Interp::interp(tenors_, dfs_, t);
    }

    // Continuously-compounded zero rate at time t.
    [[nodiscard]] double zero_rate(double t) const {
        if (t <= 0.0) { return 0.0; }
        return -std::log(df(t)) / t;
    }

    [[nodiscard]] const std::vector<double>& tenors() const { return tenors_; }
    [[nodiscard]] const std::vector<double>& dfs() const { return dfs_; }
    [[nodiscard]] std::size_t size() const { return tenors_.size(); }

private:
    DiscountCurve(std::vector<double> tenors, std::vector<double> dfs)
        : tenors_(std::move(tenors)), dfs_(std::move(dfs)) {}

    std::vector<double> tenors_;
    std::vector<double> dfs_;
};

}  // namespace credit
