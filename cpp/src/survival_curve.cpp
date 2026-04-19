#include "credit/survival_curve.hpp"

#include <cassert>
#include <cmath>

namespace credit {

SurvivalCurve::SurvivalCurve(std::vector<double> tenors,
                             std::vector<double> hazards)
    : tenors_(std::move(tenors)), hazards_(std::move(hazards)) {
    assert(tenors_.size() == hazards_.size());
}

double SurvivalCurve::survival(double t) const {
    if (t <= 0.0) { return 1.0; }

    double cum = 0.0;
    double prev = 0.0;
    for (std::size_t k = 0; k < tenors_.size(); ++k) {
        if (t <= tenors_[k]) {
            cum += hazards_[k] * (t - prev);
            return std::exp(-cum);
        }
        cum += hazards_[k] * (tenors_[k] - prev);
        prev = tenors_[k];
    }
    // Flat extrapolation beyond the last tenor.
    cum += hazards_.back() * (t - prev);
    return std::exp(-cum);
}

double SurvivalCurve::hazard(double t) const {
    if (tenors_.empty()) { return 0.0; }
    if (t <= 0.0) { return hazards_.front(); }
    for (std::size_t k = 0; k < tenors_.size(); ++k) {
        if (t <= tenors_[k]) { return hazards_[k]; }
    }
    return hazards_.back();  // flat extrapolation
}

}  // namespace credit
