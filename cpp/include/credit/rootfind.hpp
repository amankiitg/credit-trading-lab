#pragma once

#include <cmath>
#include <stdexcept>

namespace credit {

// Newton's method.  F returns f(x), DF returns f'(x).
// Returns x such that |f(x)| < tol, or throws.
template <typename F, typename DF>
double newton(F&& f, DF&& df, double x0,
              double tol = 1e-12, int max_iter = 50) {
    double x = x0;
    for (int i = 0; i < max_iter; ++i) {
        double fx = f(x);
        if (std::abs(fx) < tol) { return x; }
        double dfx = df(x);
        if (std::abs(dfx) < 1e-30) { break; }  // derivative vanished
        double dx = -fx / dfx;
        x += dx;
        if (std::abs(dx) < tol) { return x; }
    }
    throw std::runtime_error("newton: failed to converge");
}

// Brent's method.  F returns f(x).
// Requires f(a) and f(b) to have opposite signs (bracket the root).
template <typename F>
double brent(F&& f, double a, double b,
             double tol = 1e-12, int max_iter = 100) {
    double fa = f(a);
    double fb = f(b);
    if (fa * fb > 0.0) {
        throw std::runtime_error("brent: root not bracketed");
    }

    if (std::abs(fa) < std::abs(fb)) {
        std::swap(a, b);
        std::swap(fa, fb);
    }

    double c = a;
    double fc = fa;
    double d = b - a;
    bool mflag = true;

    for (int i = 0; i < max_iter; ++i) {
        if (std::abs(fb) < tol || std::abs(b - a) < tol) {
            return b;
        }

        double s;
        if (std::abs(fa - fc) > 1e-30 && std::abs(fb - fc) > 1e-30) {
            // Inverse quadratic interpolation
            s = a * fb * fc / ((fa - fb) * (fa - fc))
              + b * fa * fc / ((fb - fa) * (fb - fc))
              + c * fa * fb / ((fc - fa) * (fc - fb));
        } else {
            // Secant
            s = b - fb * (b - a) / (fb - fa);
        }

        // Bisection fallback conditions
        double mid = (a + b) / 2.0;
        double lo = std::min((3.0 * a + b) / 4.0, b);
        double hi = std::max((3.0 * a + b) / 4.0, b);
        bool bisect = false;
        if (s < lo || s > hi) { bisect = true; }
        else if (mflag && std::abs(s - b) >= std::abs(b - c) / 2.0) { bisect = true; }
        else if (!mflag && std::abs(s - b) >= std::abs(c - d) / 2.0) { bisect = true; }
        else if (mflag && std::abs(b - c) < tol) { bisect = true; }
        else if (!mflag && std::abs(c - d) < tol) { bisect = true; }

        if (bisect) {
            s = mid;
            mflag = true;
        } else {
            mflag = false;
        }

        double fs = f(s);
        d = c;
        c = b;
        fc = fb;

        if (fa * fs < 0.0) {
            b = s;
            fb = fs;
        } else {
            a = s;
            fa = fs;
        }

        if (std::abs(fa) < std::abs(fb)) {
            std::swap(a, b);
            std::swap(fa, fb);
        }
    }

    throw std::runtime_error("brent: failed to converge");
}

}  // namespace credit
