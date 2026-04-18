#pragma once

#include <cassert>
#include <cmath>
#include <cstddef>
#include <vector>

namespace credit {

// Linear interpolation on continuously-compounded zero yields.
//   yield(t) = lerp between neighbouring knot yields.
struct LinearYield {
    static double interp(const std::vector<double>& xs,
                         const std::vector<double>& ys,
                         double x) {
        assert(xs.size() == ys.size() && xs.size() >= 2);

        // Flat extrapolation outside the grid
        if (x <= xs.front()) { return ys.front(); }
        if (x >= xs.back())  { return ys.back();  }

        // Find the right interval: xs[i-1] <= x < xs[i]
        std::size_t i = 1;
        while (i < xs.size() && xs[i] < x) { ++i; }

        double t = (x - xs[i - 1]) / (xs[i] - xs[i - 1]);
        return ys[i - 1] + t * (ys[i] - ys[i - 1]);
    }
};

// Linear interpolation in log(discount factor).
//   log(DF(t)) = lerp between neighbouring log(DF) values.
//   Equivalent to piecewise-flat forward rates — guarantees
//   positive forwards (no free money).
struct LogLinearDF {
    static double interp(const std::vector<double>& xs,
                         const std::vector<double>& ys,
                         double x) {
        assert(xs.size() == ys.size() && xs.size() >= 2);

        if (x <= xs.front()) { return ys.front(); }
        if (x >= xs.back())  { return ys.back();  }

        std::size_t i = 1;
        while (i < xs.size() && xs[i] < x) { ++i; }

        double log_y0 = std::log(ys[i - 1]);
        double log_y1 = std::log(ys[i]);
        double t = (x - xs[i - 1]) / (xs[i] - xs[i - 1]);
        return std::exp(log_y0 + t * (log_y1 - log_y0));
    }
};

// Piecewise-constant hazard rate (ISDA default for CDS).
//   λ(t) = λ_k for t ∈ (T_{k-1}, T_k].
//   Here xs = tenor knots, ys = hazard rates on each segment.
//   Returns the hazard rate for the segment containing x.
struct PiecewiseConstantHazard {
    static double interp(const std::vector<double>& xs,
                         const std::vector<double>& ys,
                         double x) {
        assert(xs.size() == ys.size() && !xs.empty());

        if (x <= xs.front()) { return ys.front(); }

        for (std::size_t i = 1; i < xs.size(); ++i) {
            if (x <= xs[i]) { return ys[i]; }
        }
        return ys.back();
    }
};

}  // namespace credit
