add_library(valmax_media_composer STATIC
    "${CMAKE_CURRENT_LIST_DIR}/composer_frontend.cpp" "${CMAKE_CURRENT_LIST_DIR}/composer_frontend.h")
set_target_properties(valmax_media_composer PROPERTIES AUTOMOC ON)
target_include_directories(valmax_media_composer PUBLIC ${CMAKE_CURRENT_LIST_DIR})
target_link_libraries(valmax_media_composer PUBLIC valmax_media_service valmax_media_draft_dispatcher)
target_compile_definitions(valmax_media_composer PRIVATE QT_NO_CAST_FROM_ASCII QT_NO_CAST_TO_ASCII)
if(MSVC)
  target_compile_options(valmax_media_composer PRIVATE /W4 /WX /permissive- /utf-8)
endif()
