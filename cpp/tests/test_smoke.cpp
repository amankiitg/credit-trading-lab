#include <catch2/catch_test_macros.hpp>

#include "credit/version.hpp"

TEST_CASE("arithmetic smoke", "[smoke]") {
    REQUIRE(1 + 1 == 2);
}

TEST_CASE("library exposes version string", "[smoke]") {
    REQUIRE(credit::version() == "0.1.0");
}
