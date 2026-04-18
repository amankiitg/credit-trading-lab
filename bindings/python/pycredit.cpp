#include <pybind11/pybind11.h>

#include <string>

#include "credit/version.hpp"

namespace py = pybind11;

namespace {

std::string hello() {
    return "ok";
}

}  // namespace

PYBIND11_MODULE(pycredit, m) {
    m.doc() = "credit pricing C++ core (sprint v2)";
    m.def("hello", &hello, "smoke-test entry point; returns \"ok\"");
    m.def("version", []() { return std::string(credit::version()); },
          "underlying libcredit version string");
}
