# plugins/cpp/CMakeLists.txt
#
# OmniFlow C++ plugin build (production-ready)
#
# - Modern CMake (>= 3.16)
# - Builds an executable plugin target (default name: omni_plugin_cpp)
# - Optional unit/integration tests (GoogleTest via FetchContent if not available)
# - Optional AddressSanitizer and UndefinedBehaviorSanitizer support for CI/QA
# - Packaging (CPack) support to create .tar.gz artifacts with metadata
# - Installs executable and generates a small pkg-config file
#
# Place at: OmniFlow/plugins/cpp/CMakeLists.txt

cmake_minimum_required(VERSION 3.16)
project(OmniFlowPluginCPP
    VERSION 1.0.0
    LANGUAGES C CXX
)

# -------------------------
# User-configurable options
# -------------------------
option(BUILD_TESTS "Build unit and integration tests" ON)
option(ENABLE_ASAN "Enable AddressSanitizer / UBSan for debug builds (CI use only)" OFF)
option(BUILD_PIC "Build position independent code (for shared libs or relocatable objs)" ON)
option(INSTALL_PLUGIN "Install plugin binary to CMAKE_INSTALL_PREFIX/bin" ON)
option(BUILD_SHARED_LIBS "Build shared libraries when applicable" OFF)

# target / artifact names
set(PLUGIN_NAME "omni_plugin_cpp" CACHE STRING "Plugin binary name")
set(PLUGIN_SOVERSION ${PROJECT_VERSION_MAJOR})

# Paths (relative to this CMakeLists)
set(PLUGIN_ROOT "${CMAKE_CURRENT_SOURCE_DIR}")
set(THIRD_PARTY_DIR "${PLUGIN_ROOT}/third_party")
set(NLOHMANN_DIR "${THIRD_PARTY_DIR}/nlohmann")
set(TEST_DIR "${PLUGIN_ROOT}/tests")
set(BUILD_BIN_DIR "${CMAKE_BINARY_DIR}/bin")

# Compiler defaults and sane warnings
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ${BUILD_PIC})

# Recommended compile flags for Release/Debug
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type (Release, Debug, RelWithDebInfo)" FORCE)
endif()

# Add conservative, portable hardening flags where supported
include(CheckCXXCompilerFlag)
macro(add_if_supported flag var)
  check_cxx_compiler_flag("${flag}" ${var})
  if(${${var}})
    add_compile_options("${flag}")
  endif()
endmacro()

# Warnings and hardening
add_if_supported("-Wall" HAVE_WALL)
add_if_supported("-Wextra" HAVE_WEXTRA)
add_if_supported("-Wpedantic" HAVE_WPEDANTIC)
add_if_supported("-Werror" HAVE_WERROR)            # consider enabling in CI, optional locally
add_if_supported("-fstack-protector-strong" HAVE_FSTACK_PROTECTOR)
add_if_supported("-D_FORTIFY_SOURCE=2" HAVE_FORTIFY)

# A sensible optimization / debug flag policy
if(CMAKE_BUILD_TYPE MATCHES Debug)
  add_if_supported("-O0" HAVE_O0)
  add_if_supported("-g" HAVE_G)
else()
  add_if_supported("-O2" HAVE_O2)
  add_if_supported("-march=native" HAVE_MARCH_NATIVE)
endif()

# ASAN/UBSAN (CI/QA only)
if(ENABLE_ASAN)
  message(STATUS "ENABLE_ASAN=ON — enabling sanitizers (AddressSanitizer & UBSan)")
  add_if_supported("-fsanitize=address" HAVE_ASAN_FLAG)
  add_if_supported("-fsanitize=undefined" HAVE_UBSAN_FLAG)
  add_if_supported("-fno-omit-frame-pointer" HAVE_FRAMEPTR)
  set(SANITIZER_FLAGS "")
  if(HAVE_ASAN_FLAG)
    list(APPEND SANITIZER_FLAGS "-fsanitize=address")
  endif()
  if(HAVE_UBSAN_FLAG)
    list(APPEND SANITIZER_FLAGS "-fsanitize=undefined")
  endif()
  if(HAVE_FRAMEPTR)
    list(APPEND SANITIZER_FLAGS "-fno-omit-frame-pointer")
  endif()
  add_compile_options(${SANITIZER_FLAGS})
  link_libraries(${SANITIZER_FLAGS})
endif()

# Threading
find_package(Threads REQUIRED)

# -------------------------
# Sources
# -------------------------
file(GLOB_RECURSE PLUGIN_SOURCES CONFIGURE_DEPENDS
    "${PLUGIN_ROOT}/*.cpp"
    "${PLUGIN_ROOT}/*.c"
    "${PLUGIN_ROOT}/*.cc"
    "${PLUGIN_ROOT}/*.cxx"
)

# Exclude tests from main source list (if they reside in tests/ under same tree)
list(FILTER PLUGIN_SOURCES EXCLUDE REGEX "${TEST_DIR}")

if(PLUGIN_SOURCES STREQUAL "")
  message(FATAL_ERROR "No plugin source files found under ${PLUGIN_ROOT}. Add your .cpp files.")
endif()

# Create target
add_executable(${PLUGIN_NAME} $<TARGET_OBJECTS:${PLUGIN_NAME}-objs> ${PLUGIN_SOURCES})
set_target_properties(${PLUGIN_NAME} PROPERTIES
  OUTPUT_NAME ${PLUGIN_NAME}
  RUNTIME_OUTPUT_DIRECTORY ${BUILD_BIN_DIR}
)

# Include directories: plugin root, third_party includes
target_include_directories(${PLUGIN_NAME}
  PRIVATE
    ${PLUGIN_ROOT}
    ${NLOHMANN_DIR}         # third-party header-only JSON
)

# Compiler features & flags per-target
target_compile_features(${PLUGIN_NAME} PRIVATE cxx_std_17)

# Link threads and other system libs if required
target_link_libraries(${PLUGIN_NAME}
  PRIVATE
    Threads::Threads
)

# If you link to other libraries (e.g., libcurl, libssl) you can find_package them and link here.

# Ensure binary is portable: strip on install / packaging may be done in CI or CPack step
if (WIN32)
  # Windows-specific settings (if needed)
else()
  # Set rpath behavior for relocatability (when needed)
  set_target_properties(${PLUGIN_NAME} PROPERTIES
      BUILD_WITH_INSTALL_RPATH FALSE
      INSTALL_RPATH "$ORIGIN/../lib"
      SKIP_BUILD_RPATH FALSE
  )
endif()

# -------------------------
# Tests (GoogleTest)
# -------------------------
if(BUILD_TESTS)
  enable_testing()
  include(CTest)

  # Prefer installed GTest, otherwise fetch it
  find_package(GTest QUIET)
  if(NOT GTest_FOUND)
    include(FetchContent)
    FetchContent_Declare(
      googletest
      URL https://github.com/google/googletest/archive/refs/tags/release-1.13.0.zip
    )
    # For reproducible CI, you may pin to a commit/sha instead of tag
    FetchContent_MakeAvailable(googletest)
    set(GTest_LIBRARIES gtest gtest_main)
    set(GTEST_INCLUDE_DIRS ${googletest_SOURCE_DIR}/googletest/include)
  else()
    set(GTest_LIBRARIES GTest::GTest GTest::Main)
    set(GTEST_INCLUDE_DIRS ${GTEST_INCLUDE_DIRS})
  endif()

  # Add unit test sources if present
  file(GLOB_RECURSE TEST_SOURCES CONFIGURE_DEPENDS
    "${TEST_DIR}/unit/*.cpp"
    "${TEST_DIR}/unit/*.cxx"
    "${TEST_DIR}/integration/*.sh"   # integration harness may be shell scripts - not compiled
  )

  if(NOT TEST_SOURCES STREQUAL "")
    # Build a test executable
    add_executable(${PLUGIN_NAME}_tests ${TEST_SOURCES})
    target_include_directories(${PLUGIN_NAME}_tests PRIVATE
      ${PLUGIN_ROOT}
      ${NLOHMANN_DIR}
      ${GTEST_INCLUDE_DIRS}
    )
    target_link_libraries(${PLUGIN_NAME}_tests PRIVATE ${GTest_LIBRARIES} Threads::Threads)
    add_test(NAME plugin_unit_tests COMMAND ${PLUGIN_NAME}_tests)
    # Useful: a convenience target to run tests
    add_custom_target(check
      COMMAND ${CMAKE_CTEST_COMMAND} --output-on-failure
      DEPENDS ${PLUGIN_NAME}_tests
    )
  else()
    message(STATUS "No unit test sources found under ${TEST_DIR}/unit")
  endif()

  # Integration tests: keep as scripts - make a target to run them if they exist
  file(GLOB INTEGRATION_SCRIPTS CONFIGURE_DEPENDS "${TEST_DIR}/integration/*.sh")
  if(INTEGRATION_SCRIPTS)
    foreach(script ${INTEGRATION_SCRIPTS})
      get_filename_component(script_name ${script} NAME_WE)
      add_test(NAME integration_${script_name}
               COMMAND bash ${script})
      # Optionally add test dependencies or env vars
    endforeach()
  endif()
endif()

# -------------------------
# Formatting & static checks (optional targets)
# -------------------------
find_program(CLANG_FORMAT clang-format)
if(CLANG_FORMAT)
  file(GLOB_RECURSE ALL_SOURCE_FILES CONFIGURE_DEPENDS
       "${PLUGIN_ROOT}/*.h" "${PLUGIN_ROOT}/*.hpp" "${PLUGIN_ROOT}/*.c" "${PLUGIN_ROOT}/*.cpp")
  add_custom_target(fmt
    COMMAND ${CLANG_FORMAT} -i ${ALL_SOURCE_FILES}
    WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
    COMMENT "Formatting sources with clang-format"
  )
else()
  add_custom_target(fmt COMMENT "clang-format not found")
endif()

# clang-tidy (best-effort; non-fatal)
find_program(CLANG_TIDY clang-tidy)
if(CLANG_TIDY)
  add_custom_target(static-check
    COMMAND ${CLANG_TIDY} ${PLUGIN_SOURCES} -- -I${NLOHMANN_DIR}
    COMMENT "Running clang-tidy (best-effort, may produce warnings)"
  )
else()
  add_custom_target(static-check COMMENT "clang-tidy not found")
endif()

# -------------------------
# Installation
# -------------------------
if(INSTALL_PLUGIN)
  install(TARGETS ${PLUGIN_NAME}
    RUNTIME DESTINATION bin COMPONENT runtime
  )

  # pkg-config file (small, helpful for downstream packaging)
  include(CMakePackageConfigHelpers)
  set(prefix ${CMAKE_INSTALL_PREFIX})
  set(exec_prefix ${prefix})
  set(libdir ${exec_prefix}/lib)
  set(includedir ${prefix}/include)

  configure_file(${CMAKE_CURRENT_SOURCE_DIR}/cmake/omniflow-plugin.pc.in
                 ${CMAKE_CURRENT_BINARY_DIR}/omniflow-plugin.pc @ONLY)
  install(FILES ${CMAKE_CURRENT_BINARY_DIR}/omniflow-plugin.pc
          DESTINATION lib/pkgconfig)
endif()

# -------------------------
# Packaging via CPack
# -------------------------
include(CPack)
set(CPACK_PACKAGE_NAME "omniflow-plugin-cpp")
set(CPACK_PACKAGE_VENDOR "TheSkiF4er / OmniFlow")
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "OmniFlow C++ plugin")
set(CPACK_PACKAGE_VERSION ${PROJECT_VERSION})
set(CPACK_GENERATOR "TGZ")
set(CPACK_SOURCE_GENERATOR "TGZ")
set(CPACK_PACKAGE_FILE_NAME "${CPACK_PACKAGE_NAME}-${PROJECT_VERSION}-${CMAKE_SYSTEM_NAME}-${CMAKE_SYSTEM_PROCESSOR}")

# Add build metadata to package (VCS_REF and BUILD_DATE can be provided via -D)
if(NOT DEFINED VCS_REF)
  execute_process(COMMAND git rev-parse --short HEAD
                  WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
                  RESULT_VARIABLE _gitres
                  OUTPUT_VARIABLE _gitrev
                  OUTPUT_STRIP_TRAILING_WHITESPACE)
  if(_gitres EQUAL 0)
    set(VCS_REF ${_gitrev})
  else()
    set(VCS_REF "unknown")
  endif()
endif()
if(NOT DEFINED BUILD_DATE)
  execute_process(COMMAND date -u +%Y-%m-%dT%H:%M:%SZ
                  OUTPUT_VARIABLE _builddate
                  OUTPUT_STRIP_TRAILING_WHITESPACE)
  set(BUILD_DATE ${_builddate})
endif()

# Install packaging metadata
set(CPACK_RESOURCE_FILE_LICENSE "${CMAKE_CURRENT_SOURCE_DIR}/LICENSE" CACHE STRING "License file")
set(CPACK_PACKAGE_CONTACT "maintainers@omniflow.example")

# -------------------------
# Export targets (for downstream CMake usage)
# -------------------------
export(TARGETS ${PLUGIN_NAME} FILE ${CMAKE_CURRENT_BINARY_DIR}/OmniFlowPluginCPPTargets.cmake)
# Generate a config file for find_package (optional; simple export)
configure_package_config_file(
  ${CMAKE_CURRENT_SOURCE_DIR}/cmake/Config.cmake.in
  ${CMAKE_CURRENT_BINARY_DIR}/OmniFlowPluginCPPConfig.cmake
  INSTALL_DESTINATION lib/cmake/omniflow-plugin-cpp
)
write_basic_package_version_file(
  ${CMAKE_CURRENT_BINARY_DIR}/OmniFlowPluginCPPConfigVersion.cmake
  VERSION ${PROJECT_VERSION}
  COMPATIBILITY AnyNewerVersion
)

# -------------------------
# Developer notes
# -------------------------
message(STATUS "OmniFlow C++ plugin configuration:")
message(STATUS "  Source dir: ${PLUGIN_ROOT}")
message(STATUS "  Third-party includes: ${NLOHMANN_DIR}")
message(STATUS "  Build type: ${CMAKE_BUILD_TYPE}")
message(STATUS "  Build tests: ${BUILD_TESTS}")
message(STATUS "  Enable ASAN: ${ENABLE_ASAN}")

# -------------------------
# Helper: create cmake subfiles used above if they don't exist (lightweight templates)
# -------------------------
# Create cmake/omniflow-plugin.pc.in if missing (pkg-config template)
if(NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/cmake/omniflow-plugin.pc.in")
  file(MAKE_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/cmake")
  file(WRITE "${CMAKE_CURRENT_SOURCE_DIR}/cmake/omniflow-plugin.pc.in"
"prefix=@prefix@
exec_prefix=@exec_prefix@
libdir=@libdir@
includedir=@includedir@

Name: OmniFlow Plugin (C++)
Description: OmniFlow C++ plugin
Version: @PROJECT_VERSION@
Cflags: -I@includedir@
Libs: -L@libdir@ -lomniflow-plugin-cpp
")
endif()

# Create cmake/Config.cmake.in if missing
if(NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/cmake/Config.cmake.in")
  file(WRITE "${CMAKE_CURRENT_SOURCE_DIR}/cmake/Config.cmake.in"
"# Config file for OmniFlowPluginCPP
@PACKAGE_INIT@

set(OmniFlowPluginCPP_INCLUDE_DIR \"\${CMAKE_CURRENT_LIST_DIR}/../../..\")
")
endif()
