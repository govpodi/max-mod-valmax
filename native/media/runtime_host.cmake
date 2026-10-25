add_library(valmax_media_runtime STATIC
    "${CMAKE_CURRENT_LIST_DIR}/runtime_host.cpp" "${CMAKE_CURRENT_LIST_DIR}/runtime_host.h")
target_link_libraries(valmax_media_runtime PUBLIC valmax_media_runtime_prepare valmax_media_composer_adapter)
target_compile_definitions(valmax_media_runtime PRIVATE QT_NO_CAST_FROM_ASCII QT_NO_CAST_TO_ASCII)
if(MSVC)
    target_compile_options(valmax_media_runtime PRIVATE /W4 /WX /permissive- /utf-8)
endif()
