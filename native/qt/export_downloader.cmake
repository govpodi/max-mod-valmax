target_sources(valmaxnativeplugin PRIVATE export_downloader.cpp export_downloader.h)
target_link_libraries(valmaxnativeplugin PRIVATE Qt6::Network)
add_executable(valmax_export_downloader_test ../tests/qt/export_downloader_test.cpp export_downloader.cpp export_downloader.h)
target_include_directories(valmax_export_downloader_test PRIVATE "${CMAKE_CURRENT_LIST_DIR}" "${CMAKE_CURRENT_LIST_DIR}/../core")
target_compile_definitions(valmax_export_downloader_test PRIVATE VALMAX_HAS_RUST_CORE=1 VALMAX_EXPORT_DOWNLOAD_TESTING
    QT_NO_CAST_FROM_ASCII QT_NO_CAST_TO_ASCII)
target_compile_options(valmax_export_downloader_test PRIVATE /W4 /WX /permissive- /utf-8)
target_link_libraries(valmax_export_downloader_test PRIVATE Qt6::Core Qt6::Network)
set_target_properties(valmax_export_downloader_test PROPERTIES MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
