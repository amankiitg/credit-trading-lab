cmake_minimum_required(VERSION 3.20)
project(credit LANGUAGES CXX VERSION 0.1.0)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

# Workaround for an Apple CommandLineTools install where
# /Library/Developer/CommandLineTools/usr/include/c++/v1 contains only
# the 3-file legacy compatibility stub and shadows the full libc++ that
# actually ships in the SDK. Force the SDK's libc++ to win the search.
if(APPLE)
    if(NOT CMAKE_OSX_SYSROOT)
        execute_process(COMMAND xcrun --show-sdk-path
            OUTPUT_VARIABLE CMAKE_OSX_SYSROOT
            OUTPUT_STRIP_TRAILING_WHITESPACE)
    endif()
    set(_credit_sdk_libcxx "${CMAKE_OSX_SYSROOT}/usr/include/c++/v1")
    if(EXISTS "${_credit_sdk_libcxx}/chrono"
       AND NOT EXISTS "/Library/Developer/CommandLineTools/usr/include/c++/v1/chrono")
        message(STATUS "credit: forcing SDK libc++ at ${_credit_sdk_libcxx}")
        add_compile_options(-nostdinc++ -isystem ${_credit_sdk_libcxx})
        add_link_options(-stdlib=libc++)
    endif()
endif()

option(CREDIT_BUILD_PYTHON "Build pybind11 Python module" ON)
option(CREDIT_BUILD_TESTS  "Build Catch2 unit tests"     ON)
option(CREDIT_USE_SANITIZERS
    "Enable AddressSanitizer + UBSan (forced ON in Debug)" OFF)

# Warnings applied only to first-party targets (not FetchContent deps).
add_library(credit_warnings INTERFACE)
target_compile_options(credit_warnings INTERFACE
    -Wall -Wextra -Wpedantic -Werror
)

add_library(credit_sanitizers INTERFACE)
if(CMAKE_BUILD_TYPE STREQUAL "Debug" OR CREDIT_USE_SANITIZERS)
    target_compile_options(credit_sanitizers INTERFACE
        -fsanitize=address,undefined -fno-omit-frame-pointer -g)
    target_link_options(credit_sanitizers INTERFACE
        -fsanitize=address,undefined)
endif()

# ----- credit static library ------------------------------------------------
add_library(credit STATIC
    src/version.cpp
    src/bond.cpp
    src/schedule.cpp
    src/survival_curve.cpp
    src/cds.cpp
)
target_include_directories(credit PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
)
target_link_libraries(credit
    PRIVATE credit_warnings
    PUBLIC  credit_sanitizers
)

# ----- third-party deps via FetchContent ------------------------------------
include(FetchContent)

if(CREDIT_BUILD_TESTS)
    FetchContent_Declare(
        Catch2
        GIT_REPOSITORY https://github.com/catchorg/Catch2.git
        GIT_TAG        v3.5.4
        GIT_SHALLOW    TRUE
    )
    FetchContent_MakeAvailable(Catch2)
    list(APPEND CMAKE_MODULE_PATH ${catch2_SOURCE_DIR}/extras)

    enable_testing()
    add_subdirectory(tests)
endif()

if(CREDIT_BUILD_PYTHON)
    # Force pybind11 to use the modern FindPython (CMP0148-clean) and pick
    # up whichever interpreter the user passes in -DPython_EXECUTABLE.
    set(PYBIND11_FINDPYTHON ON CACHE BOOL "" FORCE)
    FetchContent_Declare(
        pybind11
        GIT_REPOSITORY https://github.com/pybind/pybind11.git
        GIT_TAG        v2.11.1
        GIT_SHALLOW    TRUE
    )
    FetchContent_MakeAvailable(pybind11)
    add_subdirectory(
        ${CMAKE_CURRENT_SOURCE_DIR}/../bindings/python
        ${CMAKE_BINARY_DIR}/bindings_python
    )
endif()
