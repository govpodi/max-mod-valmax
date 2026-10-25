add_library(valmax_media_draft_dispatcher STATIC
    "${CMAKE_CURRENT_LIST_DIR}/draft_dispatcher.cpp" "${CMAKE_CURRENT_LIST_DIR}/draft_dispatcher.h")
target_include_directories(valmax_media_draft_dispatcher PUBLIC "${CMAKE_CURRENT_LIST_DIR}")
target_link_libraries(valmax_media_draft_dispatcher PUBLIC valmax_media_engine)
target_compile_definitions(valmax_media_draft_dispatcher PRIVATE QT_NO_CAST_FROM_ASCII QT_NO_CAST_TO_ASCII)
if(MSVC)
  target_compile_options(valmax_media_draft_dispatcher PRIVATE /W4 /WX /permissive- /utf-8)
endif()
