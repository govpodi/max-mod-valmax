# Independent std/Win32 host binding; no Qt, network or module loading.
add_library(valmax_media_staging_client STATIC
    "${CMAKE_CURRENT_LIST_DIR}/staging_client.cpp" "${CMAKE_CURRENT_LIST_DIR}/staging_client.h")
target_include_directories(valmax_media_staging_client PUBLIC "${CMAKE_CURRENT_LIST_DIR}")
target_compile_definitions(valmax_media_staging_client PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
if(MSVC)
  target_compile_options(valmax_media_staging_client PRIVATE /W4 /WX /permissive- /utf-8)
endif()
