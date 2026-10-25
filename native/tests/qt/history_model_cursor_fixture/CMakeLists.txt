cmake_minimum_required(VERSION 3.28)
project(ValMAXHistoryModelCursorFixture LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
find_package(Qt6 6.11.2 EXACT REQUIRED COMPONENTS Core Qml)
add_executable(valmax_history_model_cursor_test
    ../history_model_cursor_test.cpp
    ../../../qt/history_model_cursor.cpp
    ../../../qt/history_model_cursor.h)
target_include_directories(valmax_history_model_cursor_test PRIVATE ../../../qt)
target_link_libraries(valmax_history_model_cursor_test PRIVATE Qt6::Core Qt6::Qml)
target_compile_definitions(valmax_history_model_cursor_test PRIVATE QT_NO_CAST_FROM_ASCII QT_NO_CAST_TO_ASCII)
target_compile_options(valmax_history_model_cursor_test PRIVATE /W4 /WX /permissive-)
set_target_properties(valmax_history_model_cursor_test PROPERTIES MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
