if(NOT TARGET valmax_media_service)
  set(_valmax_service_dir "${CMAKE_CURRENT_LIST_DIR}")
  add_library(valmax_media_service STATIC
    "${_valmax_service_dir}/service_host.cpp"
    "${_valmax_service_dir}/service_host.h"
    "${_valmax_service_dir}/service_generation.h"
    "${_valmax_service_dir}/staging_client.cpp")
  set_target_properties(valmax_media_service PROPERTIES AUTOMOC ON)
  target_link_libraries(valmax_media_service PUBLIC valmax_media_controller valmax_media_decode_queue valmax_media_engine)
  target_include_directories(valmax_media_service PUBLIC "${_valmax_service_dir}")
  target_compile_definitions(valmax_media_service PRIVATE QT_NO_CAST_FROM_ASCII QT_NO_CAST_TO_ASCII)
  if(MSVC)
    target_compile_options(valmax_media_service PRIVATE /W4 /WX /permissive- /utf-8)
  endif()
  unset(_valmax_service_dir)
endif()
