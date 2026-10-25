cmake_minimum_required(VERSION 3.28)
project(ValMAXHooks VERSION 0.1.0 LANGUAGES CXX)
if(NOT WIN32 OR NOT MSVC OR NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
    message(FATAL_ERROR "ValMAX hooks require Windows x64 MSVC")
endif()

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY MultiThreaded)
enable_testing()

# First retention stage: routing-only queue/ACK contract. No MAX hook can be
# enabled by this library; ready/applied remain false until the guarded gates
# and durable Rust worker have their separate proof.
add_library(valmax_retention_queue SHARED retention_queue.cpp)
target_compile_definitions(valmax_retention_queue PRIVATE VALMAX_RETENTION_BUILD WIN32_LEAN_AND_MEAN NOMINMAX)
target_link_libraries(valmax_retention_queue PRIVATE bcrypt)
target_compile_options(valmax_retention_queue PRIVATE /W4 /WX /permissive-)
add_executable(valmax_retention_queue_test ../tests/hooks/retention_queue_test.cpp retention_queue.cpp)
target_compile_definitions(valmax_retention_queue_test PRIVATE VALMAX_RETENTION_BUILD VALMAX_RETENTION_TESTING WIN32_LEAN_AND_MEAN NOMINMAX)
target_include_directories(valmax_retention_queue_test PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(valmax_retention_queue_test PRIVATE bcrypt)
target_compile_options(valmax_retention_queue_test PRIVATE /W4 /WX /permissive-)
add_test(NAME retention_queue COMMAND valmax_retention_queue_test)

# Isolated synthetic layout/owner proof. This codec is not linked into the
# shipping queue DLL and installs no MAX hook.
add_executable(valmax_retention_row_codec_test
    ../tests/hooks/retention_row_codec_test.cpp retention_row_codec.cpp)
target_include_directories(valmax_retention_row_codec_test PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
target_compile_options(valmax_retention_row_codec_test PRIVATE /W4 /WX /permissive-)
add_test(NAME retention_row_codec COMMAND valmax_retention_row_codec_test)

# Passive bounded producer worker. Its synthetic backend proves FIFO capture,
# deletion receipts, route isolation, backpressure, revocation and shutdown.
add_executable(valmax_retention_async_worker_test
    ../tests/hooks/retention_async_worker_test.cpp
    retention_async_worker.cpp retention_row_codec.cpp)
target_include_directories(valmax_retention_async_worker_test PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
target_compile_definitions(valmax_retention_async_worker_test PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
target_compile_options(valmax_retention_async_worker_test PRIVATE /W4 /WX /permissive-)
add_test(NAME retention_async_worker COMMAND valmax_retention_async_worker_test)

set(VALMAX_GUM_DIRECTORY "C:/ValMAXBuild/gum/17.23.3" CACHE PATH "Pinned C-only Gum devkit")
add_executable(valmax_retention_passive_host_test
    ../tests/hooks/retention_passive_host_test.cpp
    retention_passive_host.cpp retention_async_worker.cpp retention_row_codec.cpp
    ghost_module.cpp hook_set.cpp)
target_include_directories(valmax_retention_passive_host_test PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
target_include_directories(valmax_retention_passive_host_test SYSTEM PRIVATE "${VALMAX_GUM_DIRECTORY}")
target_compile_definitions(valmax_retention_passive_host_test PRIVATE
    VALMAX_RETENTION_HOST_TESTING WIN32_LEAN_AND_MEAN NOMINMAX)
target_compile_options(valmax_retention_passive_host_test PRIVATE /W4 /WX /permissive-)
target_link_libraries(valmax_retention_passive_host_test PRIVATE "${VALMAX_GUM_DIRECTORY}/frida-gum.lib" bcrypt)
set_target_properties(valmax_retention_passive_host_test PROPERTIES MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
add_test(NAME retention_passive_host COMMAND valmax_retention_passive_host_test)
add_library(valmax_hooks STATIC hook_set.cpp hook_set.h)
target_include_directories(valmax_hooks PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_include_directories(valmax_hooks SYSTEM PRIVATE ${VALMAX_GUM_DIRECTORY})
target_link_libraries(valmax_hooks PUBLIC "${VALMAX_GUM_DIRECTORY}/frida-gum.lib")
target_compile_options(valmax_hooks PRIVATE /W4 /WX /permissive-)
add_executable(valmax_hooks_test ../tests/hooks/hook_set_test.cpp)
target_link_libraries(valmax_hooks_test PRIVATE valmax_hooks)
target_compile_options(valmax_hooks_test PRIVATE /W4 /WX /permissive-)
add_test(NAME hooks COMMAND valmax_hooks_test)

set(VALMAX_GHOST_RUNTIME_ROOT "" CACHE PATH "Exact verified MAX runtime for generated production ghost pins")
set(VALMAX_BUILD_PYTHON "D:/CodexLabs/max-mod-26.35.1/venv/Scripts/python.exe" CACHE FILEPATH "Build-time Python (not a runtime dependency)")
if(VALMAX_GHOST_RUNTIME_ROOT)
    add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/ghost_pins.generated.h" "${CMAKE_CURRENT_BINARY_DIR}/ghost-pins.json"
        COMMAND "${VALMAX_BUILD_PYTHON}" "${CMAKE_CURRENT_SOURCE_DIR}/ghost_generate_pins.py"
        --runtime-root "${VALMAX_GHOST_RUNTIME_ROOT}"
        --output "${CMAKE_CURRENT_BINARY_DIR}/ghost_pins.generated.h"
        --proof "${CMAKE_CURRENT_BINARY_DIR}/ghost-pins.json"
        DEPENDS ghost_generate_pins.py ../hooks/ghost_internal.h ../../modkit/ghost_native.py ../../modkit/ghost_unread_native.py
                "${VALMAX_GHOST_RUNTIME_ROOT}/core.dll" "${VALMAX_GHOST_RUNTIME_ROOT}/max.exe"
        VERBATIM)
    set(GHOST_SOURCES ghost_runtime.cpp ghost_module.cpp ghost_ledger.cpp "${CMAKE_CURRENT_BINARY_DIR}/ghost_pins.generated.h")
    add_library(valmax_ghost SHARED ${GHOST_SOURCES})
    target_compile_definitions(valmax_ghost PRIVATE VALMAX_GHOST_BUILD WIN32_LEAN_AND_MEAN NOMINMAX)
    target_include_directories(valmax_ghost PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
    target_include_directories(valmax_ghost SYSTEM PRIVATE "${VALMAX_GUM_DIRECTORY}")
    target_link_libraries(valmax_ghost PRIVATE valmax_hooks bcrypt)
    target_compile_options(valmax_ghost PRIVATE /W4 /WX /permissive-)
    add_executable(valmax_ghost_test ../tests/hooks/ghost_test.cpp ${GHOST_SOURCES})
    target_compile_definitions(valmax_ghost_test PRIVATE VALMAX_GHOST_BUILD VALMAX_GHOST_TESTING WIN32_LEAN_AND_MEAN NOMINMAX)
    target_include_directories(valmax_ghost_test PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
    target_include_directories(valmax_ghost_test SYSTEM PRIVATE "${VALMAX_GUM_DIRECTORY}")
    target_link_libraries(valmax_ghost_test PRIVATE valmax_hooks bcrypt)
    target_compile_options(valmax_ghost_test PRIVATE /W4 /WX /permissive-)
    add_test(NAME ghost COMMAND valmax_ghost_test)
endif()

set(VALMAX_NOTIFICATION_RUNTIME_ROOT "${VALMAX_GHOST_RUNTIME_ROOT}" CACHE PATH "Exact verified MAX runtime for notification pins")
if(VALMAX_NOTIFICATION_RUNTIME_ROOT)
    add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/notification_pins.generated.h" "${CMAKE_CURRENT_BINARY_DIR}/notification-pins.json"
        COMMAND "${VALMAX_BUILD_PYTHON}" "${CMAKE_CURRENT_SOURCE_DIR}/notification_generate_pins.py"
        --runtime-root "${VALMAX_NOTIFICATION_RUNTIME_ROOT}"
        --output "${CMAKE_CURRENT_BINARY_DIR}/notification_pins.generated.h"
        --proof "${CMAKE_CURRENT_BINARY_DIR}/notification-pins.json"
        DEPENDS notification_generate_pins.py ghost_generate_pins.py ../../modkit/notification_native.py
                "${VALMAX_NOTIFICATION_RUNTIME_ROOT}/max.exe" "${VALMAX_NOTIFICATION_RUNTIME_ROOT}/notifications.dll"
        VERBATIM)
    set(NOTIFICATION_SOURCES notification_runtime.cpp notification_policy.cpp notification_clone.cpp ghost_module.cpp
        "${CMAKE_CURRENT_BINARY_DIR}/notification_pins.generated.h")
    add_library(valmax_notifications SHARED ${NOTIFICATION_SOURCES})
    target_compile_definitions(valmax_notifications PRIVATE VALMAX_NOTIFICATION_BUILD WIN32_LEAN_AND_MEAN NOMINMAX)
    target_include_directories(valmax_notifications PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
    target_include_directories(valmax_notifications SYSTEM PRIVATE "${VALMAX_GUM_DIRECTORY}")
    target_link_libraries(valmax_notifications PRIVATE valmax_hooks bcrypt)
    target_compile_options(valmax_notifications PRIVATE /W4 /WX /permissive-)
    add_executable(valmax_notification_test ../tests/hooks/notification_test.cpp ${NOTIFICATION_SOURCES})
    target_compile_definitions(valmax_notification_test PRIVATE VALMAX_NOTIFICATION_BUILD VALMAX_NOTIFICATION_TESTING WIN32_LEAN_AND_MEAN NOMINMAX)
    target_include_directories(valmax_notification_test PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
    target_include_directories(valmax_notification_test SYSTEM PRIVATE "${VALMAX_GUM_DIRECTORY}")
    target_link_libraries(valmax_notification_test PRIVATE valmax_hooks bcrypt)
    target_compile_options(valmax_notification_test PRIVATE /W4 /WX /permissive-)
    add_test(NAME notifications COMMAND valmax_notification_test)
endif()

set(VALMAX_PRIVACY_RUNTIME_ROOT "${VALMAX_GHOST_RUNTIME_ROOT}" CACHE PATH "Exact verified runtime with static privacy patches")
if(VALMAX_PRIVACY_RUNTIME_ROOT)
    add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/privacy_pins.generated.h" "${CMAKE_CURRENT_BINARY_DIR}/privacy-pins.json"
        COMMAND "${VALMAX_BUILD_PYTHON}" "${CMAKE_CURRENT_SOURCE_DIR}/privacy_generate_pins.py"
        --runtime-root "${VALMAX_PRIVACY_RUNTIME_ROOT}"
        --output "${CMAKE_CURRENT_BINARY_DIR}/privacy_pins.generated.h"
        --proof "${CMAKE_CURRENT_BINARY_DIR}/privacy-pins.json"
        DEPENDS privacy_generate_pins.py ghost_generate_pins.py ../../modkit/privacy.py ../../modkit/accounts.py ../../modkit/isolation.py ../../modkit/build_mod.py
                "${VALMAX_PRIVACY_RUNTIME_ROOT}/core.dll" "${VALMAX_PRIVACY_RUNTIME_ROOT}/config.dll"
                "${VALMAX_PRIVACY_RUNTIME_ROOT}/tracernative.dll" "${VALMAX_PRIVACY_RUNTIME_ROOT}/max-service/max-service.exe"
                "${VALMAX_PRIVACY_RUNTIME_ROOT}/max-service/config.dll" "${VALMAX_PRIVACY_RUNTIME_ROOT}/max-service/tracernative.dll"
        VERBATIM)
    add_library(valmax_privacy SHARED privacy_runtime.cpp "${CMAKE_CURRENT_BINARY_DIR}/privacy_pins.generated.h")
    target_compile_definitions(valmax_privacy PRIVATE VALMAX_PRIVACY_BUILD WIN32_LEAN_AND_MEAN NOMINMAX)
    target_include_directories(valmax_privacy PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
    target_link_libraries(valmax_privacy PRIVATE bcrypt)
    target_compile_options(valmax_privacy PRIVATE /W4 /WX /permissive-)
    add_executable(valmax_privacy_test ../tests/hooks/privacy_test.cpp privacy_runtime.cpp "${CMAKE_CURRENT_BINARY_DIR}/privacy_pins.generated.h")
    target_compile_definitions(valmax_privacy_test PRIVATE VALMAX_PRIVACY_BUILD VALMAX_PRIVACY_TESTING WIN32_LEAN_AND_MEAN NOMINMAX)
    target_include_directories(valmax_privacy_test PRIVATE "${CMAKE_CURRENT_BINARY_DIR}" "${CMAKE_CURRENT_SOURCE_DIR}")
    target_link_libraries(valmax_privacy_test PRIVATE bcrypt)
    target_compile_options(valmax_privacy_test PRIVATE /W4 /WX /permissive-)
    add_test(NAME privacy COMMAND valmax_privacy_test)
endif()
