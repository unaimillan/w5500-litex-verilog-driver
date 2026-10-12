# W5500 UDP IP core - build / test / lint / resource estimate.
#
#   make                   -> help (targets, variables, implementations, presets)
#   make generate IMPL=..  -> build/gateware/$(BUILD_NAME).v (single self-contained Verilog file)
#   make build             -> generate + copy to dist/
#   make test              -> pytest: config, generation, lint, W5500 model, cocotb (Verilator)
#   make lint              -> Verilator lint of the generated file + hand-written rtl/
#   make -j synth-estimate -> Yosys LUT/FF/BRAM estimate for MAX10, Gowin, Xilinx, Lattice
#   make check             -> ruff (Python lint)
#
# generate/build/lint/synth-* need IMPL (there is no default), e.g.:
#   make build IMPL=stub_rtl PRESET=de10lite BUILD_NAME=eth0

# ---- Configuration ----------------------------------------------------------

UV         ?= uv
PRESET     ?= default
IMPL       ?=
BUILD_NAME ?= w5500_udp
OUT_DIR    ?= build/gateware
DIST_DIR   ?= dist

SYNTH_DIR      ?= build/synth
SYNTH_TIMEOUT  ?= 120
SYNTH_TARGETS  ?= intel gowin xilinx lattice
LATTICE_FAMILY ?= xo2
SYNTH_CMD_intel   := synth_intel -family max10
SYNTH_CMD_gowin   := synth_gowin
SYNTH_CMD_xilinx  := synth_xilinx
SYNTH_CMD_lattice := synth_lattice -family $(LATTICE_FAMILY)

GATEWARE := $(OUT_DIR)/$(BUILD_NAME).v
SOURCES  := $(shell find src -name '*.py') $(wildcard rtl/*.v rtl/*.sv) pyproject.toml uv.lock
RTL      := $(wildcard rtl/*.v rtl/*.sv)

# Verilator -Wall waivers for the generated file (keep in sync with tests/common.py).
VERI_WAIVE := -Wno-DECLFILENAME -Wno-UNUSEDSIGNAL -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC \
              -Wno-COMBDLY -Wno-PROCASSINIT -Wno-CASEINCOMPLETE

# Regenerate when the parameters change, not only when sources change.
PARAMS     := $(OUT_DIR)/.params-$(BUILD_NAME)
PARAMS_VAL := $(PRESET) $(IMPL) $(BUILD_NAME)

LIST_OPTIONS := $(UV) run --quiet w5500-gen --list

# Targets that need the generated file refuse to run without IMPL: print usage + options.
ifeq ($(IMPL),)
ifneq ($(filter generate build lint synth-% hw-gen hw-build,$(MAKECMDGOALS)),)
$(info usage: make $(firstword $(filter generate build lint synth-% hw-gen hw-build,$(MAKECMDGOALS))) IMPL=<impl> [PRESET=$(PRESET)] [BUILD_NAME=$(BUILD_NAME)])
$(info )
$(shell $(LIST_OPTIONS) >&2)
$(error IMPL is not set (e.g. make build IMPL=stub_migen PRESET=default))
endif
endif

# ---- Targets ----------------------------------------------------------------

.DEFAULT_GOAL := help
.PHONY: help generate build test lint lint-rtl synth-estimate check clean FORCE \
        hw-gen hw-build hw-receive

help: ## Show this help
	@echo "usage: make <target> [IMPL=<impl>] [PRESET=$(PRESET)] [BUILD_NAME=$(BUILD_NAME)]"
	@echo
	@echo "targets:"
	@grep -E '^[a-zA-Z_%-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "variables:"
	@echo "  IMPL             implementation, required by generate/build/lint/synth-*/hw-gen/hw-build"
	@echo "  PRESET           CoreConfig preset (default: $(PRESET))"
	@echo "  BUILD_NAME       top module / file name (default: $(BUILD_NAME))"
	@echo
	@$(LIST_OPTIONS)

generate: $(GATEWARE) ## Generate the single-file Verilog core into build/

build: $(GATEWARE) ## Generate and copy the core to dist/
	@mkdir -p $(DIST_DIR)
	cp $(GATEWARE) $(DIST_DIR)/
	@echo "[make] $(DIST_DIR)/$(BUILD_NAME).v  (preset=$(PRESET), impl=$(IMPL))"

$(PARAMS): FORCE
	@mkdir -p $(OUT_DIR)
	@echo '$(PARAMS_VAL)' | cmp -s - $@ || echo '$(PARAMS_VAL)' > $@

$(GATEWARE): $(SOURCES) $(PARAMS)
	$(UV) run w5500-gen --preset $(PRESET) --impl $(IMPL) --build-name $(BUILD_NAME) \
	                    --output-dir $(OUT_DIR)

test: ## Run the whole pytest suite (incl. cocotb/Verilator)
	$(UV) run pytest

lint: $(GATEWARE) lint-rtl ## Verilator lint: generated file (waivers) + rtl/ (strict -Wall)
	verilator --lint-only -Wall $(VERI_WAIVE) --top-module $(BUILD_NAME) $(GATEWARE)
	yosys -q -p "read_verilog -sv $(GATEWARE); hierarchy -check -top $(BUILD_NAME); proc; check -assert"

lint-rtl:
	@for f in $(RTL); do echo "verilator --lint-only -Wall $$f"; \
		verilator --lint-only -Wall $$f || exit 1; done

# Print the cell summary of a yosys `stat` report (a design without logic has no cell table).
stat_cells = grep -q 'cells$$' $(1) && sed -n '/cells$$/,/^$$/p' $(1) || echo "        0 cells"

synth-estimate: $(SYNTH_TARGETS:%=$(SYNTH_DIR)/%.stat) ## Yosys resource estimate (use -j)
	@for f in $^; do echo "== $$(basename $$f .stat)"; $(call stat_cells,$$f); done

synth-%: $(SYNTH_DIR)/%.stat ## Single-target estimate (synth-intel, synth-gowin, ...)
	@echo "== $*"; $(call stat_cells,$<)

$(SYNTH_DIR)/%.stat: $(GATEWARE)
	@mkdir -p $(SYNTH_DIR)
	@echo "[make] yosys $(SYNTH_CMD_$*) (log: $(SYNTH_DIR)/$*.log)"
	@timeout $(SYNTH_TIMEOUT) yosys -q -l $(SYNTH_DIR)/$*.log \
	    -p "read_verilog -sv $<; $(SYNTH_CMD_$*) -top $(BUILD_NAME); tee -q -o $@ stat" >/dev/null 2>&1 || \
	    { rc=$$?; rm -f $@; echo "[make] $* failed (rc=$$rc, 124 = timeout after $(SYNTH_TIMEOUT)s):" >&2; \
	      tail -n 5 $(SYNTH_DIR)/$*.log >&2; exit $$rc; }

# ---- Hardware end-to-end test (DE10-Lite, tests/hwtest_e2e/README.md) ----------------------
# The core preset defaults per impl (soc_v1: de10lite, soc_v2: stream); PRESET= on the command
# line overrides it. Output: build/hwtest/$(IMPL)/.

HW_RUN    := $(UV) run python -m tests.hwtest_e2e
HW_PRESET := $(if $(filter command line,$(origin PRESET)),--preset $(PRESET))

hw-gen: ## DE10-Lite test design: core + top Verilog + Quartus project (no Quartus run)
	$(HW_RUN).de10lite --impl $(IMPL) $(HW_PRESET)

hw-build: ## DE10-Lite test design + Quartus build -> dist/hwtest_de10lite_<impl>.sof
	$(HW_RUN).de10lite --impl $(IMPL) $(HW_PRESET) --build
	@mkdir -p $(DIST_DIR)
	cp build/hwtest/$(IMPL)/gateware/hwtest_de10lite.sof $(DIST_DIR)/hwtest_de10lite_$(IMPL).sof
	@echo "[make] $(DIST_DIR)/hwtest_de10lite_$(IMPL).sof"

hw-receive: ## UDP receiver for the hardware test (RECEIVE_ARGS="--expect-rate 44100")
	$(HW_RUN).receiver $(RECEIVE_ARGS)

check: ## Python lint (ruff)
	$(UV) run ruff check .

clean: ## Remove build artifacts
	rm -rf build $(DIST_DIR)
