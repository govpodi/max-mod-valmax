add_library(valmax_media_composer_adapter STATIC
    "${CMAKE_CURRENT_LIST_DIR}/composer_adapter.cpp" "${CMAKE_CURRENT_LIST_DIR}/composer_adapter.h")
set_target_properties(valmax_media_composer_adapter PROPERTIES AUTOMOC ON)
target_include_directories(valmax_media_composer_adapter PUBLIC ${CMAKE_CURRENT_LIST_DIR})
target_link_libraries(valmax_media_composer_adapter PUBLIC valmax_media_composer valmax_media_composer_binding Qt6::Qml)
target_compile_definitions(valmax_media_composer_adapter PRIVATE QT_NO_CAST_FROM_ASCII QT_NO_CAST_TO_ASCII)
if(MSVC)
  target_compile_options(valmax_media_composer_adapter PRIVATE /W4 /WX /permissive- /utf-8)
endif()
