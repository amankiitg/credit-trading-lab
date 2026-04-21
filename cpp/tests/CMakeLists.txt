add_executable(credit_tests
    test_smoke.cpp
    test_daycount.cpp
    test_interp.cpp
    test_discount_curve.cpp
    test_bond.cpp
    test_cds.cpp
    test_perf.cpp
)
target_link_libraries(credit_tests
    PRIVATE credit
            credit_warnings
            Catch2::Catch2WithMain
)
target_compile_definitions(credit_tests PRIVATE
    CREDIT_TEST_REF_DIR="${CMAKE_CURRENT_SOURCE_DIR}/ref"
)

include(Catch)
catch_discover_tests(credit_tests)
