cmake_minimum_required(VERSION 3.28)
project(ValMAXSyntheticSuspendAck LANGUAGES CXX)

if(NOT WIN32 OR NOT MSVC OR NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
  message(FATAL_ERROR "Owned suspend/ACK fixture requires Windows x64 MSVC")
endif()

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY MultiThreaded)

add_library(valmax_synthetic_target SHARED target.cpp)
add_executable(valmax_synthetic_ack_child child.cpp)
target_link_libraries(valmax_synthetic_ack_child PRIVATE valmax_synthetic_target)

foreach(target valmax_synthetic_target valmax_synthetic_ack_child)
  target_compile_definitions(${target} PRIVATE WIN32_LEAN_AND_MEAN NOMINMAX)
  target_compile_options(${target} PRIVATE /W4 /WX /permissive- /utf-8)
endforeach()
