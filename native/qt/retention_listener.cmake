set(VALMAX_RETENTION_RUNTIME_ROOT "${VALMAX_EXPORT_HISTORY_RUNTIME_ROOT}" CACHE PATH
    "Exact verified MAX runtime for passive retention pins")
set(VALMAX_RETENTION_GUM_DIRECTORY "C:/ValMAXBuild/gum/17.23.3" CACHE PATH
    "Pinned C-only Gum devkit for passive retention")

if(VALMAX_RETENTION_RUNTIME_ROOT)
    if(NOT VALMAX_CORE_LIBRARY)
        message(FATAL_ERROR "Passive retention requires the Rust Runtime library")
    endif()
    add_custom_command(
        OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/retention_pins.generated.h"
               "${CMAKE_CURRENT_BINARY_DIR}/retention-pins.json"
        COMMAND "${VALMAX_BUILD_PYTHON}" "${CMAKE_CURRENT_SOURCE_DIR}/../hooks/retention_generate_pins.py"
            --runtime-root "${VALMAX_RETENTION_RUNTIME_ROOT}"
            --output "${CMAKE_CURRENT_BINARY_DIR}/retention_pins.generated.h"
            --proof "${CMAKE_CURRENT_BINARY_DIR}/retention-pins.json"
        DEPENDS ../hooks/retention_generate_pins.py ../hooks/ghost_generate_pins.py
            ../../modkit/message_retention_native.py ../../modkit/message_retention_monitor.js
            "${VALMAX_RETENTION_RUNTIME_ROOT}/core.dll"
            "${VALMAX_RETENTION_RUNTIME_ROOT}/storage.dll"
        VERBATIM)
    add_library(valmax_retention_native STATIC
        ../hooks/retention_passive_host.cpp ../hooks/retention_passive_host.h
        ../hooks/retention_async_worker.cpp ../hooks/retention_async_worker.h
        ../hooks/retention_row_codec.cpp ../hooks/retention_row_codec.h
        "${CMAKE_CURRENT_BINARY_DIR}/retention_pins.generated.h")
    target_include_directories(valmax_retention_native PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/../hooks" "${CMAKE_CURRENT_BINARY_DIR}")
    target_include_directories(valmax_retention_native SYSTEM PRIVATE
        "${VALMAX_RETENTION_GUM_DIRECTORY}")
    target_compile_definitions(valmax_retention_native PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
    target_compile_options(valmax_retention_native PRIVATE /W4 /WX /permissive-)
    set_target_properties(valmax_retention_native PROPERTIES MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
    target_link_libraries(valmax_retention_native PUBLIC
        "${VALMAX_RETENTION_GUM_DIRECTORY}/frida-gum.lib" bcrypt "${VALMAX_CORE_LIBRARY}")
    if(NOT VALMAX_EXPORT_HISTORY_RUNTIME_ROOT)
        target_sources(valmaxnativeplugin PRIVATE ../hooks/ghost_module.cpp ../hooks/hook_set.cpp)
        target_include_directories(valmaxnativeplugin SYSTEM PRIVATE
            "${VALMAX_RETENTION_GUM_DIRECTORY}")
    endif()
    target_sources(valmaxnativeplugin PRIVATE
        retention_host_bridge.cpp retention_host_bridge.h)
    target_link_libraries(valmaxnativeplugin PRIVATE valmax_retention_native)
    target_compile_definitions(valmaxnativeplugin PRIVATE VALMAX_HAS_RETENTION=1)
    add_executable(valmax_retention_host_bridge_test
        ../tests/qt/retention_host_bridge_test.cpp
        retention_host_bridge.cpp retention_host_bridge.h)
    target_include_directories(valmax_retention_host_bridge_test PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}" "${CMAKE_CURRENT_SOURCE_DIR}/../core"
        "${CMAKE_CURRENT_SOURCE_DIR}/../hooks")
    target_compile_definitions(valmax_retention_host_bridge_test PRIVATE
        QT_NO_CAST_FROM_ASCII QT_NO_CAST_TO_ASCII)
    target_compile_options(valmax_retention_host_bridge_test PRIVATE /W4 /WX /permissive-)
    target_link_libraries(valmax_retention_host_bridge_test PRIVATE Qt6::Core)
    set_target_properties(valmax_retention_host_bridge_test PROPERTIES
        MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
else()
    target_compile_definitions(valmaxnativeplugin PRIVATE VALMAX_HAS_RETENTION=0)
endif()
