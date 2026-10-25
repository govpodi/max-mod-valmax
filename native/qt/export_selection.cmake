# A selected native View is sufficient to start a BETA API2 archive. This
# host emits no history EOF observations, so the archive cannot claim complete.
if(VALMAX_CORE_LIBRARY AND NOT VALMAX_EXPORT_HISTORY_RUNTIME_ROOT)
    target_sources(valmaxnativeplugin PRIVATE export_selection_host.cpp export_selection_host.h)
    target_compile_definitions(valmaxnativeplugin PRIVATE VALMAX_HAS_EXPORT_SELECTION=1)
else()
    target_compile_definitions(valmaxnativeplugin PRIVATE VALMAX_HAS_EXPORT_SELECTION=0)
endif()

add_executable(valmax_export_selection_host_test
    ../tests/qt/export_selection_host_test.cpp
    export_selection_host.cpp export_selection_host.h)
target_include_directories(valmax_export_selection_host_test PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}" "${CMAKE_CURRENT_SOURCE_DIR}/../core")
target_compile_definitions(valmax_export_selection_host_test PRIVATE
    QT_NO_CAST_FROM_ASCII QT_NO_CAST_TO_ASCII WIN32_LEAN_AND_MEAN NOMINMAX)
target_compile_options(valmax_export_selection_host_test PRIVATE /W4 /WX /permissive-)
target_link_libraries(valmax_export_selection_host_test PRIVATE Qt6::Core)
set_target_properties(valmax_export_selection_host_test PROPERTIES MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
