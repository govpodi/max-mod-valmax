if(NOT TARGET valmax_closed_clients)
    include("${CMAKE_CURRENT_LIST_DIR}/../transfer/closed_clients.cmake")
endif()
include("${CMAKE_CURRENT_LIST_DIR}/../common/startup_context.cmake")
add_library(valmax_launch_session STATIC launch_session.cpp launch_session.h)
target_link_libraries(valmax_launch_session PUBLIC valmax_preflight_support valmax_activation valmax_profile_client valmax_environment valmax_closed_clients)
target_compile_options(valmax_launch_session PRIVATE /W4 /WX /permissive- /utf-8)
set(VALMAX_LAUNCHER_RUNTIME_ROOT "" CACHE PATH "Final native runtime tree; build-only inventory input")
if(VALMAX_LAUNCHER_RUNTIME_ROOT)
    enable_language(RC)
    if(NOT VALMAX_BUILD_PYTHON)
        message(FATAL_ERROR "VALMAX_BUILD_PYTHON is required only for pin generation at build time")
    endif()
    file(GLOB_RECURSE VALMAX_LAUNCHER_PAYLOAD CONFIGURE_DEPENDS "${VALMAX_LAUNCHER_RUNTIME_ROOT}/*")
    add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/launcher_pins.generated.h" "${CMAKE_CURRENT_BINARY_DIR}/launcher-pins.json"
        COMMAND "${VALMAX_BUILD_PYTHON}" "${CMAKE_CURRENT_SOURCE_DIR}/launcher_generate_pins.py"
            --runtime-root "${VALMAX_LAUNCHER_RUNTIME_ROOT}" --output "${CMAKE_CURRENT_BINARY_DIR}/launcher_pins.generated.h"
            --receipt "${CMAKE_CURRENT_BINARY_DIR}/launcher-pins.json"
        DEPENDS launcher_generate_pins.py ${VALMAX_LAUNCHER_PAYLOAD} VERBATIM)
    set(VALMAX_LAUNCHER_ICON_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/stock-icon")
    add_custom_command(OUTPUT "${VALMAX_LAUNCHER_ICON_DIRECTORY}/launcher-stock.rc"
        "${VALMAX_LAUNCHER_ICON_DIRECTORY}/launcher-stock.ico" "${VALMAX_LAUNCHER_ICON_DIRECTORY}/launcher-icon.json"
        COMMAND "${VALMAX_BUILD_PYTHON}" "${CMAKE_CURRENT_SOURCE_DIR}/launcher_icon.py"
            --runtime-root "${VALMAX_LAUNCHER_RUNTIME_ROOT}" --pins "${CMAKE_CURRENT_BINARY_DIR}/launcher-pins.json"
            --output "${VALMAX_LAUNCHER_ICON_DIRECTORY}"
        DEPENDS launcher_icon.py "${CMAKE_CURRENT_BINARY_DIR}/launcher-pins.json"
            "${VALMAX_LAUNCHER_RUNTIME_ROOT}/mod/max.exe" VERBATIM)
    add_executable(ValMAX WIN32 launcher_main.cpp "${CMAKE_CURRENT_BINARY_DIR}/launcher_pins.generated.h"
        "${VALMAX_LAUNCHER_ICON_DIRECTORY}/launcher-stock.rc")
    target_include_directories(ValMAX PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
    target_link_libraries(ValMAX PRIVATE valmax_launch_session valmax_startup_context shell32 ole32 uuid)
    target_compile_options(ValMAX PRIVATE $<$<COMPILE_LANGUAGE:CXX>:/W4;/WX;/permissive-;/utf-8>)
endif()

add_executable(valmax_launch_session_test ../tests/launcher/launch_session_test.cpp)
target_link_libraries(valmax_launch_session_test PRIVATE valmax_launch_session)
target_compile_options(valmax_launch_session_test PRIVATE /W4 /WX /permissive- /utf-8)
add_test(NAME ordinary_launcher_policy COMMAND valmax_launch_session_test)

add_executable(valmax_launcher_child WIN32 ../tests/launcher/launcher_child.cpp)
set_target_properties(valmax_launcher_child PROPERTIES OUTPUT_NAME "max")
target_compile_options(valmax_launcher_child PRIVATE /W4 /WX /permissive- /utf-8)
add_library(valmax_launcher_test_core SHARED ../tests/launcher/launcher_test_core.cpp)
set_target_properties(valmax_launcher_test_core PROPERTIES OUTPUT_NAME "valmax_core")
target_compile_options(valmax_launcher_test_core PRIVATE /W4 /WX /permissive- /utf-8)
target_link_options(valmax_launcher_test_core PRIVATE /EXPORT:valmax_core_abi_version /EXPORT:valmax_launcher_prepare
    /EXPORT:valmax_runtime_open /EXPORT:valmax_runtime_close /EXPORT:valmax_core_buffer_free)

add_executable(valmax_launcher_owned_test ../tests/launcher/launcher_owned_test.cpp launch_session.cpp transfer_mutex.cpp profile_client.cpp)
target_compile_definitions(valmax_launcher_owned_test PRIVATE VALMAX_TRANSFER_TESTING WIN32_LEAN_AND_MEAN NOMINMAX)
target_include_directories(valmax_launcher_owned_test PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(valmax_launcher_owned_test PRIVATE valmax_preflight_support valmax_activation valmax_environment valmax_closed_clients bcrypt)
target_compile_options(valmax_launcher_owned_test PRIVATE /W4 /WX /permissive- /utf-8)
