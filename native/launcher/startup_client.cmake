add_library(valmax_startup_client STATIC startup_client.cpp startup_client.h)
target_compile_definitions(valmax_startup_client PUBLIC WIN32_LEAN_AND_MEAN NOMINMAX)
target_include_directories(valmax_startup_client PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")
target_compile_options(valmax_startup_client PRIVATE /W4 /WX /permissive- /utf-8)
add_executable(valmax_startup_client_test ../tests/launcher/startup_client_test.cpp)
target_link_libraries(valmax_startup_client_test PRIVATE valmax_startup_client)
target_compile_options(valmax_startup_client_test PRIVATE /W4 /WX /permissive- /utf-8)
add_test(NAME startup_receipt_synthetic_abi COMMAND valmax_startup_client_test)

# Optional, explicit build-only C++ -> Rust compatibility fixture. Its Python
# driver supplies one new synthetic root and also tests old receipt compatibility.
set(VALMAX_STARTUP_TEST_CORE_LIBRARY "" CACHE FILEPATH "Pinned core import library for receipt ABI fixture")
if(VALMAX_STARTUP_TEST_CORE_LIBRARY)
    add_executable(valmax_startup_integration_test ../tests/launcher/startup_integration_test.cpp)
    target_link_libraries(valmax_startup_integration_test PRIVATE valmax_startup_client "${VALMAX_STARTUP_TEST_CORE_LIBRARY}")
    target_compile_options(valmax_startup_integration_test PRIVATE /W4 /WX /permissive- /utf-8)
endif()
