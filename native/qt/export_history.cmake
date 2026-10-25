set(VALMAX_EXPORT_HISTORY_RUNTIME_ROOT "" CACHE PATH "Exact native runtime for export-only EOF guards")
set(VALMAX_EXPORT_GUM_DIRECTORY "C:/ValMAXBuild/gum/17.23.3" CACHE PATH "Pinned C-only Gum devkit")
set(VALMAX_BUILD_PYTHON "D:/CodexLabs/max-mod-26.35.1/venv/Scripts/python.exe" CACHE FILEPATH "Build-only Python")
if(VALMAX_EXPORT_HISTORY_RUNTIME_ROOT)
    if(NOT VALMAX_CORE_LIBRARY)
        message(FATAL_ERROR "Export history host requires Rust Runtime")
    endif()
    add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/export_history_pins.generated.h" "${CMAKE_CURRENT_BINARY_DIR}/export-history-pins.json"
        COMMAND "${VALMAX_BUILD_PYTHON}" "${CMAKE_CURRENT_SOURCE_DIR}/export_history_generate_pins.py"
            --runtime-root "${VALMAX_EXPORT_HISTORY_RUNTIME_ROOT}" --output "${CMAKE_CURRENT_BINARY_DIR}/export_history_pins.generated.h"
            --proof "${CMAKE_CURRENT_BINARY_DIR}/export-history-pins.json"
        DEPENDS export_history_generate_pins.py ../../modkit/chat_export_end_specs.py
            "${VALMAX_EXPORT_HISTORY_RUNTIME_ROOT}/max.exe" "${VALMAX_EXPORT_HISTORY_RUNTIME_ROOT}/Qt6Core.dll" VERBATIM)
    target_sources(valmaxnativeplugin PRIVATE export_history_host.cpp export_history_host.h
        ../hooks/ghost_module.cpp ../hooks/hook_set.cpp "${CMAKE_CURRENT_BINARY_DIR}/export_history_pins.generated.h")
    target_include_directories(valmaxnativeplugin PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
    target_include_directories(valmaxnativeplugin SYSTEM PRIVATE "${VALMAX_EXPORT_GUM_DIRECTORY}")
    target_link_libraries(valmaxnativeplugin PRIVATE "${VALMAX_EXPORT_GUM_DIRECTORY}/frida-gum.lib" bcrypt)
    target_compile_definitions(valmaxnativeplugin PRIVATE VALMAX_HAS_EXPORT_HISTORY=1)
else()
    target_compile_definitions(valmaxnativeplugin PRIVATE VALMAX_HAS_EXPORT_HISTORY=0)
endif()
enable_language(ASM_MASM)
add_executable(valmax_export_history_host_test ../tests/qt/export_history_host_test.cpp ../tests/qt/export_history_owned_call.asm
    export_history_host.cpp export_history_host.h ../hooks/ghost_module.cpp ../hooks/hook_set.cpp)
target_include_directories(valmax_export_history_host_test PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}" "${CMAKE_CURRENT_SOURCE_DIR}/../core" "${CMAKE_CURRENT_SOURCE_DIR}/../hooks")
target_include_directories(valmax_export_history_host_test SYSTEM PRIVATE "${VALMAX_EXPORT_GUM_DIRECTORY}")
target_compile_definitions(valmax_export_history_host_test PRIVATE VALMAX_EXPORT_HISTORY_TESTING WIN32_LEAN_AND_MEAN NOMINMAX QT_NO_CAST_FROM_ASCII QT_NO_CAST_TO_ASCII)
target_compile_options(valmax_export_history_host_test PRIVATE $<$<COMPILE_LANGUAGE:CXX>:/W4;/WX;/permissive->)
target_link_libraries(valmax_export_history_host_test PRIVATE Qt6::Core "${VALMAX_EXPORT_GUM_DIRECTORY}/frida-gum.lib" bcrypt)
set_target_properties(valmax_export_history_host_test PROPERTIES MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
