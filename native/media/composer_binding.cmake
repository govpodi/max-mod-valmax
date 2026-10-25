find_package(Qt6 6.11.2 EXACT REQUIRED COMPONENTS Core Qml)
add_library(valmax_media_composer_binding STATIC
    "${CMAKE_CURRENT_LIST_DIR}/composer_binding.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/composer_binding.h")
target_link_libraries(valmax_media_composer_binding PUBLIC Qt6::Core Qt6::Qml)
target_compile_features(valmax_media_composer_binding PUBLIC cxx_std_20)
set_target_properties(valmax_media_composer_binding PROPERTIES MSVC_RUNTIME_LIBRARY MultiThreadedDLL)
if(MSVC)
    target_compile_options(valmax_media_composer_binding PRIVATE /W4 /WX /permissive- /utf-8)
endif()
