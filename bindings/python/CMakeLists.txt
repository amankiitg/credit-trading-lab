pybind11_add_module(pycredit MODULE
    pycredit.cpp
)
target_link_libraries(pycredit
    PRIVATE credit
            credit_warnings
)

# Drop the artifact next to the python package so `import pycredit` works
# from the source tree without an install step.
set_target_properties(pycredit PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY
        "${CMAKE_SOURCE_DIR}/../python/credit"
)
