# Makefile — OmniFlow C plugin (plugins/c)
# Production-ready build rules for sample_plugin.
#
# Features:
#  - Respectable defaults (CC, CFLAGS, LDFLAGS) with overrides via environment
#  - BUILD_TYPE: Release | Debug
#  - ENABLE_ASAN=1 to enable AddressSanitizer (for QA/CI)
#  - OUTDIR defaults to build/
#  - Targets: all, build, release, debug, asan, test, install, uninstall, clean, dist, lint, static-check
#  - Packaging: tar.gz with metadata (git commit, date)
#  - Uses vendor/cJSON by default; supports adding extra sources in SRC_EXTRA
#
# Usage examples:
#   make                # Release build (default)
#   make BUILD_TYPE=Debug
#   make ENABLE_ASAN=1  # debug/asan build
#   make install PREFIX=/usr/local DESTDIR=/tmp/stage
#   make dist VERSION=v1.2.3
#
# Notes:
#  - You can override CC, CFLAGS, LDFLAGS from environment.
#  - For reproducible builds pass BUILD_DATE and VCS_REF as env build args in CI.
#  - Intended binary name: sample_plugin (change BIN if needed)
# -----------------------------------------------------------------------------

# --- Configurable variables (can be overridden through environment) ---
CC        ?= gcc
# Example: CC=clang
BUILD_TYPE?= Release        # Release or Debug
ENABLE_ASAN?=0              # 1 to enable ASAN
BIN       ?= sample_plugin
OUTDIR    ?= build
PREFIX    ?= /usr/local
DESTDIR   ?=
SRCDIR    ?= .
VENDOR    ?= vendor/cJSON

# Additional source files (if you have other .c in this folder)
SRC_CORE  := $(wildcard $(SRCDIR)/*.c)
SRC_VENDOR:= $(wildcard $(VENDOR)/*.c)
SRC_EXTRA ?=
SRCS      := $(filter-out $(VENDOR)/cJSON.c, $(SRC_CORE)) $(SRC_VENDOR) $(SRC_EXTRA)
# If sample_plugin.c is in the plugin root, it will be included via SRC_CORE

# Derived
OBJS      := $(patsubst %.c,$(OUTDIR)/%.o,$(notdir $(SRCS)))
BIN_PATH  := $(OUTDIR)/$(BIN)
NPROC     ?= $(shell getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)

# Reproducible-ish metadata (set in CI with --env)
BUILD_DATE ?= $(shell date -u +"%Y-%m-%dT%H:%M:%SZ")
VCS_REF    ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)

# Default warning and optimization flags
ifeq ($(BUILD_TYPE),Debug)
  CFLAGS_DEFAULT := -g -O0 -DDEBUG -Wall -Wextra -fno-omit-frame-pointer
else
  CFLAGS_DEFAULT := -O2 -march=native -pipe -DNDEBUG -Wall -Wextra -fstack-protector-strong -fno-strict-aliasing
endif

# Sanitizer flags
ifeq ($(ENABLE_ASAN),1)
  SANITIZER_FLAGS := -fsanitize=address,undefined -fno-omit-frame-pointer -g
  # AddressSanitizer requires linking with sanitizer runtime; system compiler usually handles it
else
  SANITIZER_FLAGS :=
endif

# Allow overriding
CFLAGS ?= $(CFLAGS_DEFAULT) $(SANITIZER_FLAGS) -DBUILD_DATE="\"$(BUILD_DATE)\"" -DVCS_REF="\"$(VCS_REF)\""
LDFLAGS ?= $(SANITIZER_FLAGS)

# Install paths
BINDIR ?= $(DESTDIR)$(PREFIX)/bin

# Tools
RM := rm -rf
MKDIR_P := mkdir -p
TAR := tar
GIT := git

# PHONY targets
.PHONY: all build release debug asan clean install uninstall test lint static-check dist help

# Default target -> build Release
all: build

# Primary build rule (respects BUILD_TYPE & ENABLE_ASAN)
build: $(BIN_PATH)
	@echo "Built: $(BIN_PATH)"

# Alias: release build (explicit)
release:
	@$(MAKE) BUILD_TYPE=Release ENABLE_ASAN=0

# Debug build
debug:
	@$(MAKE) BUILD_TYPE=Debug ENABLE_ASAN=0

# ASAN build (useful for CI)
asan:
	@$(MAKE) BUILD_TYPE=Debug ENABLE_ASAN=1

# Object compilation rule
# Place object files into OUTDIR with same basename
$(OUTDIR)/%.o: $(SRCDIR)/%.c | $(OUTDIR)
	@echo "[CC] $< -> $@"
	$(CC) $(CFLAGS) -I$(VENDOR) -c $< -o $@

# If vendor sources are in vendor dir (not top-level SRCDIR)
$(OUTDIR)/%.o: $(VENDOR)/%.c | $(OUTDIR)
	@echo "[CC] $< -> $@"
	$(CC) $(CFLAGS) -I$(VENDOR) -c $< -o $@

# Ensure OUTDIR exists
$(OUTDIR):
	$(MKDIR_P) $(OUTDIR)

# Link step
$(BIN_PATH): $(OBJS) | $(OUTDIR)
	@echo "[LD] Linking $@"
	$(CC) $(LDFLAGS) -o $@ $(OBJS)
	@echo " -> $@"
	@ls -lh $@

# Simple rule: if there are .c files under SRCDIR root (like sample_plugin.c), compile them into objects
# Generate object rules dynamically if needed (safe fallback)
$(foreach s,$(SRCS),$(eval $(call obj_rule_template,$(s))))

# Helper template to map .c path to object; supports both SRCDIR/*.c and vendor/*.c
define obj_rule_template
$(OUTDIR)/$(notdir $(1:.c=.o)): $(1)
	@echo "[CC] $$< -> $$@"
	$(CC) $(CFLAGS) -I$(VENDOR) -c $$< -o $$@
endef

# Install to system prefix
install: $(BIN_PATH)
	@echo "Installing $(BIN) -> $(BINDIR)"
	$(MKDIR_P) $(BINDIR)
	install -m 0755 $(BIN_PATH) $(BINDIR)/$(BIN)

# Uninstall / remove installed file
uninstall:
	@echo "Removing $(BINDIR)/$(BIN)"
	-$(RM) $(BINDIR)/$(BIN) || true

# Run integration tests (calls the test harness script if present)
test: $(BIN_PATH)
	@echo "Running integration tests..."
	@if [ -x tests/test_sample_plugin.sh ]; then \
	  ./tests/test_sample_plugin.sh; \
	else \
	  echo "No test harness found at tests/test_sample_plugin.sh"; \
	fi

# Linting: clang-format (if installed); this is non-fatal by default
lint:
	@echo "Running clang-format check (non-fatal)"
	@if command -v clang-format >/dev/null 2>&1; then \
	  find . -name '*.c' -o -name '*.h' | xargs -r clang-format -n --Werror || true; \
	else \
	  echo "clang-format not installed, skipping"; \
	fi

# Static analysis: clang-tidy (best-effort); non-fatal
static-check:
	@echo "Running clang-tidy (best-effort, non-fatal)"
	@if command -v clang-tidy >/dev/null 2>&1; then \
	  for f in $(SRCS); do \
	    echo "=> $${f}"; clang-tidy $$f -- -I$(VENDOR) $(CFLAGS) || true; \
	  done; \
	else \
	  echo "clang-tidy not installed, skipping"; \
	fi

# Create a distribution tarball with metadata (VERSION can be provided, else uses VCS_REF)
dist: clean build
	@VER=$(or $(VERSION),$(VCS_REF)) ; \
	if [ -z "$$VER" ] || [ "$$VER" = "unknown" ]; then VER=unreleased; fi ; \
	DST=omniflow-plugin-c-$${VER}.tar.gz ; \
	$(MKDIR_P) dist ; \
	cp -v $(BIN_PATH) dist/ ; \
	echo "version: $${VER}" > dist/release_metadata.txt ; \
	echo "vcs_ref: $(VCS_REF)" >> dist/release_metadata.txt ; \
	echo "build_date: $(BUILD_DATE)" >> dist/release_metadata.txt ; \
	cd dist && $(TAR) czf ../$$DST . ; \
	echo "Created: $$DST"

# Clean build artifacts
clean:
	@echo "Cleaning $(OUTDIR) and dist/"
	$(RM) $(OUTDIR) dist

# Very aggressive cleanup (remove installed binary too)
distclean: clean
	-$(RM) $(PREFIX)/bin/$(BIN) || true

# Print help
help:
	@printf "\nUsage:\n"
	@printf "  make            # release build (default)\n"
	@printf "  make release    # same as default\n"
	@printf "  make debug      # debug build (no optimizations)\n"
	@printf "  make asan       # debug + AddressSanitizer (ENABLE_ASAN=1)\n"
	@printf "  make test       # run integration tests (if present)\n"
	@printf "  make install    # install to PREFIX (use DESTDIR= for packaging)\n"
	@printf "  make dist VERSION=v1.2.3  # create .tar.gz with metadata\n"
	@printf "\nEnvironment variables you can override:\n"
	@printf "  CC, CFLAGS, LDFLAGS, BUILD_TYPE, ENABLE_ASAN, BIN, OUTDIR, PREFIX, DESTDIR, VCS_REF, BUILD_DATE\n\n"

# Default phony target list (for completeness)
.PHONY: all build release debug asan install uninstall test lint static-check dist clean distclean help
