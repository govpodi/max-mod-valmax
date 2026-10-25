cmake_minimum_required(VERSION 3.28)
project(ValMAXQtWebViewGuard LANGUAGES CXX)
if(NOT WIN32 OR NOT MSVC OR NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
    message(FATAL_ERROR "Windows x64 MSVC required")
endif()
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
find_package(Qt6 6.11.2 EXACT REQUIRED COMPONENTS Core Gui Widgets WebView WebViewPrivate)
set(VALMAX_QT_WEBVIEW_SOURCE "C:/ValMAXBuild/build/qt-webview-guard-source" CACHE PATH "Verified generated Qt source")
set(WEBVIEW2_SDK_ROOT "C:/ValMAXBuild/deps/webview2-build/microsoft-sdk" CACHE PATH "Pinned Microsoft SDK")
add_library(qtwebview_webview2 MODULE
    "${VALMAX_QT_WEBVIEW_SOURCE}/qwebview2webview.cpp"
    "${VALMAX_QT_WEBVIEW_SOURCE}/qwebview2webview_p.h"
    "${VALMAX_QT_WEBVIEW_SOURCE}/qwebview2webviewplugin.cpp")
target_include_directories(qtwebview_webview2 PRIVATE "${WEBVIEW2_SDK_ROOT}/build/native/include")
target_link_libraries(qtwebview_webview2 PRIVATE Qt6::CorePrivate Qt6::Gui Qt6::Widgets Qt6::WebViewPrivate
    "${WEBVIEW2_SDK_ROOT}/build/native/x64/WebView2LoaderStatic.lib" urlmon advapi32 ole32)
target_compile_definitions(qtwebview_webview2 PRIVATE QT_PLUGIN)
target_compile_options(qtwebview_webview2 PRIVATE /W3 /permissive- /utf-8)
set_target_properties(qtwebview_webview2 PROPERTIES PREFIX "" MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")

# Separate fault DLL: intentionally no Microsoft loader library is linked.
option(VALMAX_WEBVIEW_BUILD_TESTS "Build separate synthetic fault-test targets" ON)
if(VALMAX_WEBVIEW_BUILD_TESTS)
add_library(valmax_webview_timeout_test MODULE
    "${VALMAX_QT_WEBVIEW_SOURCE}/qwebview2webview.cpp"
    "${VALMAX_QT_WEBVIEW_SOURCE}/qwebview2webview_p.h"
    "${VALMAX_QT_WEBVIEW_SOURCE}/qwebview2webviewplugin.cpp"
    ../../tests/qt/webview_timeout_loader.cpp)
target_include_directories(valmax_webview_timeout_test PRIVATE "${WEBVIEW2_SDK_ROOT}/build/native/include")
target_link_libraries(valmax_webview_timeout_test PRIVATE Qt6::CorePrivate Qt6::Gui Qt6::Widgets Qt6::WebViewPrivate urlmon advapi32 ole32)
target_compile_definitions(valmax_webview_timeout_test PRIVATE QT_PLUGIN)
target_compile_options(valmax_webview_timeout_test PRIVATE /W3 /permissive- /utf-8)
set_target_properties(valmax_webview_timeout_test PROPERTIES PREFIX "" OUTPUT_NAME "qtwebview_webview2"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/timeout-test" LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/timeout-test"
    ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/timeout-test" PDB_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/timeout-test"
    MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")

add_executable(valmax_webview_delete_test ../../tests/qt/webview_delete_test.cpp)
target_link_libraries(valmax_webview_delete_test PRIVATE Qt6::Core Qt6::Gui Qt6::WebView)
target_compile_options(valmax_webview_delete_test PRIVATE /W4 /WX /permissive- /utf-8)
set_target_properties(valmax_webview_delete_test PROPERTIES MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
endif()
