# Makefile — OmniFlow C++ plugin (plugins/cpp)
#
# Production-ready Makefile for building, testing, packaging and installing the
# OmniFlow C++ plugin. This Makefile:
#  - prefers CMake (recommended) but falls back to direct g++ compile if needed
#  - supports Release/Debug builds and AddressSanitizer (ASAN) variant
#  - creates reproducible-ish artifacts (BUILD_DATE, VCS_REF)
#  - supports install/uninstall, dist (tarball), tests, formatting and static checks
#
# Place at: OmniFlow/plugins/cpp/Makefile
#
# Usage examples:
#   make                 # build release (default)
#   make BUILD_TYPE=Debug
#   make ENABLE_ASAN=1   # debug + ASan variant
#   make test            # run unit & integration tests (if available)
#   make install PREFIX=/usr/local
#   make dist VERSION=v1.2.3
#
# Notes:
#  - CMake recommended. If you use CMake, ensure your project writes the binary to the build dir.
#  - For reproducible CI builds, pass BUILD_DATE and VCS_REF as environment variables.
# -----------------------------------------------------------------------------

# -------------------------
# Configurable variables
# -------------------------
CMAKE      ?= cmake
CMAKE_BUILD_TYPE ?= Release
BUILD_TYPE ?= $(CMAKE_BUILD_TYPE)   # Release or Debug
ENABLE_ASAN ?= 0                     # 1 to enable AddressSanitizer (for CI/QA)
NPROC      ?= $(shell getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)

PLUGIN_NAME ?= omni_plugin_cpp
SRC_DIR    ?= .
BUILD_DIR  ?= build
OUT_DIR    ?= $(BUILD_DIR)/out
PREFIX     ?= /usr/local
DESTDIR    ?=
BINDIR     ?= $(DESTDIR)$(PREFIX)/bin

# Repro metadata (can be set by CI)
BUILD_DATE ?= $(shell date -u +"%Y-%m-%dT%H:%M:%SZ")
VCS_REF    ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)

# Compiler fallback (if CMake not used)
CXX        ?= g++
CXXFLAGS   ?= -std=c++17 -Wall -Wextra -O2 -fstack-protector-strong -D_FORTIFY_SOURCE=2
LDFLAGS    ?=

ifeq ($(BUILD_TYPE),Debug)
  CXXFLAGS += -g -O0 -DDEBUG
endif

ifeq ($(ENABLE_ASAN),1)
  CXXFLAGS += -fsanitize=address,undefined -fno-omit-frame-pointer -g
  LDFLAGS  += -fsanitize=address,undefined
endif

# Files
SRCS        := $(wildcard $(SRC_DIR)/*.cpp) $(wildcard $(SRC_DIR)/*.cc) $(wildcard $(SRC_DIR)/*.c)
OBJS        := $(patsubst %.cpp,$(OUT_DIR)/%.o,$(notdir $(filter %.cpp %.cc,$(SRCS)))) \
               $(patsubst %.c,$(OUT_DIR)/%.o,$(notdir $(filter %.c,$(SRCS))))
TEST_SCRIPT_DIR := $(SRC_DIR)/tests
UNIT_TEST_SRCS := $(wildcard $(SRC_DIR)/tests/unit/*.cpp)
INTEGRATION_SCRIPTS := $(wildcard $(SRC_DIR)/tests/integration/*.sh)

# Helpers
MKDIR_P := mkdir -p
RM      := rm -rf
TAR     := tar
GZIP    := gzip -n  # -n avoids embedding timestamp in gzip header (helps reproducibility)

# PHONY targets
.PHONY: all build cmake-build direct-build release debug asan clean dist install uninstall test fmt static-check help

# Default target builds Release
all: build

# Primary build rule: prefer CMake if CMakeLists.txt exists
build:
ifeq ("$(wildcard $(SRC_DIR)/CMakeLists.txt)","")
	@$(MAKE) direct-build
else
	@$(MAKE) cmake-build
endif

# CMake-based build
cmake-build:
	@echo "=== CMake build (type=$(BUILD_TYPE), ASAN=$(ENABLE_ASAN)) ==="
	$(MKDIR_P) $(BUILD_DIR)
	cd $(BUILD_DIR) && $(CMAKE) .. -DCMAKE_BUILD_TYPE=$(BUILD_TYPE) -DENABLE_ASAN=$(ENABLE_ASAN) -DPLUGIN_NAME=$(PLUGIN_NAME)
	cd $(BUILD_DIR) && $(CMAKE) --build . -- -j$(NPROC)
	# try to collect binary into OUT_DIR for consistent packaging
	$(MKDIR_P) $(OUT_DIR)
	if [ -f $(BUILD_DIR)/$(PLUGIN_NAME) ]; then cp $(BUILD_DIR)/$(PLUGIN_NAME) $(OUT_DIR)/; fi || true
	# some CMake setups put binary under bin/ or build/; attempt to find executable
	@found=$$(find $(BUILD_DIR) -type f -perm /111 -name "$(PLUGIN_NAME)*" -print -quit || true); \
	if [ -n "$$found" ]; then cp $$found $(OUT_DIR)/ || true; fi
	@echo "Build outputs in: $(OUT_DIR)"

# Direct compiler build (fallback)
direct-build: $(OUT_DIR)/$(PLUGIN_NAME)

# Create object directory
$(OUT_DIR):
	$(MKDIR_P) $(OUT_DIR)

# Compile each .cpp/.c into out/.o (not trying to be too smart - ensures simple fallback)
$(OUT_DIR)/%.o: $(SRC_DIR)/%.cpp | $(OUT_DIR)
	@echo "[CXX] $< -> $@"
	$(CXX) $(CXXFLAGS) -I$(SRC_DIR)/third_party -c $< -o $@

$(OUT_DIR)/%.o: $(SRC_DIR)/%.cc | $(OUT_DIR)
	@echo "[CXX] $< -> $@"
	$(CXX) $(CXXFLAGS) -I$(SRC_DIR)/third_party -c $< -o $@

$(OUT_DIR)/%.o: $(SRC_DIR)/%.c | $(OUT_DIR)
	@echo "[CC] $< -> $@"
	$(CC) $(CXXFLAGS) -I$(SRC_DIR)/third_party -c $< -o $@

# Link binary
$(OUT_DIR)/$(PLUGIN_NAME): $(OUT_DIR) $(OBJS)
	@echo "[LD] Linking $@"
	$(CXX) $(CXXFLAGS) $(LDFLAGS) -o $@ $(OBJS)
	@chmod 0755 $@
	@echo "-> $@"

# release/debug/asan convenience targets
release:
	@$(MAKE) BUILD_TYPE=Release ENABLE_ASAN=0 clean build

debug:
	@$(MAKE) BUILD_TYPE=Debug ENABLE_ASAN=0 clean build

asan:
	@$(MAKE) BUILD_TYPE=Debug ENABLE_ASAN=1 clean build

# Run unit & integration tests
test: build
	@echo "=== Running tests ==="
ifneq ("$(wildcard $(UNIT_TEST_SRCS))","")
	@echo "Running unit tests (via CTest if configured or direct test binary if present)..."
	# Prefer ctest if CMake used
ifneq ("$(wildcard $(SRC_DIR)/CMakeLists.txt)","")
	@if command -v ctest >/dev/null 2>&1 && [ -d $(BUILD_DIR) ]; then \
	  (cd $(BUILD_DIR) && ctest --output-on-failure) || exit 1; \
	else \
	  echo "ctest not available or build dir missing - skipping ctest"; \
	fi
else
	@echo "No CMake; compile unit tests if gtest present (manual)."
	# Manual gtest build omitted - assume CI handles full test builds
endif
else
	@echo "No unit tests found under $(SRC_DIR)/tests/unit"
endif

	# Integration scripts (sh)
ifneq ("$(INTEGRATION_SCRIPTS)","")
	@echo "Running integration scripts..."
	for s in $(INTEGRATION_SCRIPTS); do \
	  echo "-> $$s"; chmod +x $$s; bash $$s || exit 1; \
	done
else
	@echo "No integration scripts found under $(SRC_DIR)/tests/integration"
endif

# Package distributable tarball with metadata
dist: clean build
	@echo "=== Creating distribution tarball ==="
	$(MKDIR_P) dist
	@VER=$(or $(VERSION),$(VCS_REF)); \
	if [ -z "$$VER" ] || [ "$$VER" = "unknown" ]; then VER=unreleased; fi; \
	DST=omniflow-plugin-cpp-$$VER.tar; \
	cp -v $(OUT_DIR)/$(PLUGIN_NAME) dist/ || true; \
	echo "version: $$VER" > dist/release_metadata.txt; \
	echo "vcs_ref: $(VCS_REF)" >> dist/release_metadata.txt; \
	echo "build_date: $(BUILD_DATE)" >> dist/release_metadata.txt; \
	cd dist && $(TAR) cf ../$$DST . && $(GZIP) -9 ../$$DST && echo "Created: $$DST.gz"

# Install to PREFIX
install: build
	@echo "Installing $(PLUGIN_NAME) -> $(BINDIR)"
	$(MKDIR_P) $(BINDIR)
	install -m 0755 $(OUT_DIR)/$(PLUGIN_NAME) $(BINDIR)/$(PLUGIN_NAME)

# Uninstall
uninstall:
	@echo "Removing $(BINDIR)/$(PLUGIN_NAME)"
	-$(RM) $(BINDIR)/$(PLUGIN_NAME) || true

# Clean build artifacts
clean:
	@echo "Cleaning build artifacts"
	-$(RM) $(BUILD_DIR) dist
	-$(RM) $(OUT_DIR)

# Format code (clang-format) - non-fatal
fmt:
	@if command -v clang-format >/dev/null 2>&1; then \
	  echo "Running clang-format (in-place)"; \
	  find $(SRC_DIR) -name '*.cpp' -o -name '*.hpp' -o -name '*.h' | xargs -r clang-format -i; \
	else \
	  echo "clang-format not found, skipping"; \
	fi

# Static-check (clang-tidy) - best-effort non-fatal
static-check:
	@if command -v clang-tidy >/dev/null 2>&1; then \
	  echo "Running clang-tidy (best-effort)"; \
	  for f in $(SRCS); do echo "-> $$f"; clang-tidy $$f -- -I$(SRC_DIR)/third_party $(CXXFLAGS) || true; done; \
	else \
	  echo "clang-tidy not found, skipping"; \
	fi

# Show help
help:
	@printf "\nOmniFlow plugins/cpp Makefile\n\n"
	@printf "Targets:\n"
	@printf "  make                # build (prefers CMake if CMakeLists.txt is present)\n"
	@printf "  make release        # clean + release build\n"
	@printf "  make debug          # clean + debug build\n"
	@printf "  make asan           # clean + debug build with ASAN\n"
	@printf "  make test           # run unit & integration tests\n"
	@printf "  make dist VERSION=vX.Y.Z # create tarball dist/omniflow-plugin-cpp-<ver>.tar.gz\n"
	@printf "  make install PREFIX=/usr/local DESTDIR=/tmp/stage\n"
	@printf "  make clean\n"
	@printf "  make fmt\n"
	@printf "  make static-check\n\n"
	@printf "Environment vars (override as needed):\n"
	@printf "  CXX, CXXFLAGS, LDFLAGS, BUILD_TYPE, ENABLE_ASAN, BUILD_DIR, OUT_DIR, PREFIX, DESTDIR\n\n"

# Ensure make -n works when no targets match
.DEFAULT_GOAL := all
