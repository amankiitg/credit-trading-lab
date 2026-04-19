#pragma once

#include <algorithm>
#include <cassert>
#include <cmath>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "credit/discount_curve.hpp"
#include "credit/rootfind.hpp"

namespace credit {

// Forward declaration for cds_pvs (used by bootstrap).
class SurvivalCurve;

namespace detail {

// PV components for a CDS contract.
struct CDSLegs {
    double pv_protection;    // (1-R) * sum of default-contingent payments
    double rpv01_scheduled;  // sum of scheduled premium payments
    double rpv01_accrual;    // accrual-on-default correction
    [[nodiscard]] double rpv01() const { return rpv01_scheduled + rpv01_accrual; }
};

// Compute CDS PV components on a quarterly (or other freq) grid.
//   maturity:  in year fractions from valuation date
//   recovery:  recovery rate (e.g., 0.40)
//   pay_freq:  premium payments per year (4 = quarterly)
//
// Protection leg uses midpoint discount factor.
// Accrual-on-default uses the ISDA "half-period" approximation:
//   aod_i = (Δ/2) * (S(t_{i-1}) − S(t_i)) * D(t_mid)
template <typename Interp, typename DC>
CDSLegs cds_pvs(double maturity,
                double recovery,
                const SurvivalCurve& surv,
                const DiscountCurve<Interp, DC>& disc,
                int pay_freq = 4);

}  // namespace detail

// -------------------------------------------------------------------
// SurvivalCurve — piecewise-constant hazard rate model.
//
//   λ(t) = λ_k  for t ∈ (T_{k-1}, T_k],  T_0 = 0
//   S(t) = exp(−∫₀ᵗ λ(u) du)
// -------------------------------------------------------------------
class SurvivalCurve {
public:
    // Build from known hazard rates.
    SurvivalCurve(std::vector<double> tenors, std::vector<double> hazards);

    // Survival probability at time t (years from valuation).
    [[nodiscard]] double survival(double t) const;

    // Piecewise-constant hazard rate at time t.
    [[nodiscard]] double hazard(double t) const;

    // Accessors.
    [[nodiscard]] const std::vector<double>& tenors()  const { return tenors_;  }
    [[nodiscard]] const std::vector<double>& hazards() const { return hazards_; }
    [[nodiscard]] std::size_t size() const { return tenors_.size(); }

    // Original bootstrap inputs (stored for V7 re-bootstrap / CS01).
    [[nodiscard]] const std::vector<double>& input_tenors()      const { return input_tenors_;      }
    [[nodiscard]] const std::vector<double>& input_par_spreads() const { return input_par_spreads_; }
    [[nodiscard]] double input_recovery() const { return input_recovery_; }

    // Bootstrap from a par-spread term structure.
    //   tenors:      maturities in years (ascending)
    //   par_spreads: in decimal (0.01 = 100 bps)
    //   recovery:    recovery rate
    //   discount:    risk-free discount curve
    //
    // Solves for λ_k sequentially (shortest → longest) so that
    // a CDS at each tenor T_k prices to zero under spread s_k.
    template <typename Interp, typename DC>
    static SurvivalCurve bootstrap(const std::vector<double>& tenors,
                                   const std::vector<double>& par_spreads,
                                   double recovery,
                                   const DiscountCurve<Interp, DC>& discount);

    // Re-bootstrap with all par spreads shifted by a parallel amount.
    template <typename Interp, typename DC>
    [[nodiscard]] SurvivalCurve parallel_shift(double shift,
                                               const DiscountCurve<Interp, DC>& discount) const {
        assert(!input_tenors_.empty());
        std::vector<double> shifted(input_par_spreads_.size());
        for (std::size_t i = 0; i < shifted.size(); ++i) {
            shifted[i] = input_par_spreads_[i] + shift;
        }
        return bootstrap(input_tenors_, shifted, input_recovery_, discount);
    }

private:
    std::vector<double> tenors_;   // T_1, T_2, …, T_n
    std::vector<double> hazards_;  // λ_1, λ_2, …, λ_n

    // Original bootstrap inputs.
    std::vector<double> input_tenors_;
    std::vector<double> input_par_spreads_;
    double input_recovery_ = 0.0;
};

// ===================================================================
//  Template implementations (must be in header)
// ===================================================================

// ---- detail::cds_pvs ----

template <typename Interp, typename DC>
detail::CDSLegs detail::cds_pvs(
    double maturity,
    double recovery,
    const SurvivalCurve& surv,
    const DiscountCurve<Interp, DC>& disc,
    int pay_freq)
{
    double lgd = 1.0 - recovery;
    int n = std::max(1, static_cast<int>(std::round(maturity * pay_freq)));
    double dt = maturity / n;

    double prot = 0.0;
    double sched = 0.0;
    double aod = 0.0;

    for (int i = 1; i <= n; ++i) {
        double t0    = (i - 1) * dt;
        double t1    = i * dt;
        double t_mid = 0.5 * (t0 + t1);

        double s0    = surv.survival(t0);
        double s1    = surv.survival(t1);
        double d_mid = disc.df(t_mid);
        double d1    = disc.df(t1);

        double dp = s0 - s1;   // P(default in [t0, t1])

        prot  += lgd * dp * d_mid;
        sched += dt * s1 * d1;
        aod   += 0.5 * dt * dp * d_mid;
    }

    return {prot, sched, aod};
}

// ---- SurvivalCurve::bootstrap ----

template <typename Interp, typename DC>
SurvivalCurve SurvivalCurve::bootstrap(
    const std::vector<double>& tenors,
    const std::vector<double>& par_spreads,
    double recovery,
    const DiscountCurve<Interp, DC>& discount)
{
    assert(tenors.size() == par_spreads.size());
    assert(!tenors.empty());

    std::vector<double> boot_tenors;
    std::vector<double> boot_hazards;

    for (std::size_t k = 0; k < tenors.size(); ++k) {
        double T = tenors[k];
        double s = par_spreads[k];

        // f(λ_k) = PV_prot(T) − s * RPV01(T)
        // We solve for the hazard rate on the newest segment.
        auto target = [&](double lam) -> double {
            auto t = boot_tenors;
            auto h = boot_hazards;
            t.push_back(T);
            h.push_back(lam);
            SurvivalCurve trial(t, h);

            auto legs = detail::cds_pvs(T, recovery, trial, discount);
            return legs.pv_protection - s * legs.rpv01();
        };

        // Numerical derivative (central FD).
        auto dtarget = [&](double lam) -> double {
            constexpr double bump = 1e-6;
            return (target(lam + bump) - target(lam - bump)) / (2.0 * bump);
        };

        // Initial guess: λ ≈ s / (1−R).
        double lam0 = s / (1.0 - recovery);
        double lam = newton(target, dtarget, lam0);

        if (lam < 0.0) {
            throw std::runtime_error(
                "SurvivalCurve::bootstrap: negative hazard rate at tenor "
                + std::to_string(T));
        }

        boot_tenors.push_back(T);
        boot_hazards.push_back(lam);
    }

    SurvivalCurve curve(std::move(boot_tenors), std::move(boot_hazards));
    curve.input_tenors_      = tenors;
    curve.input_par_spreads_ = par_spreads;
    curve.input_recovery_    = recovery;
    return curve;
}

}  // namespace credit
