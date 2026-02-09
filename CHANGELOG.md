# Changelog

All notable changes to bmad-assist are documented in this file.

## [0.4.27] - 2026-02-08

### Added
- **QA Remediate** - New `qa_remediate` epic_teardown phase: 6-source issue aggregation, master LLM fix→retest loop with regression detection, escalation reports. Externalized XML prompt (`qa/prompts/remediate.xml`) with proactive INVESTIGATE → FIX → ESCALATE workflow. Configurable via `QaConfig` (max iterations, age, safety cap)
- **Scorecard: Multi-Stack** - Python and Node/TS stack detection alongside Go; modular `stacks/` registry
- **A/B Testing** - QA artifacts snapshot support; loop config phase gating (phases filtered through variant's `loop:` config)

### Changed
- **Experiments** - Relocated evaluation/testing/scorecard from `experiments/` into `src/bmad_assist/experiments/`; monolithic `scorecard.py` split into `scorecard/` package

### Fixed
- **Sprint Sync** - Corrupted sprint-status log level downgraded from ERROR to WARNING

## [0.4.26] - 2026-02-08

### Added
- **A/B Analysis CLI** - Standalone `ab-analysis` command to re-run LLM analysis on existing A/B test results

### Changed
- **A/B Testing: Per-Story Refs** - Stories use `{id, ref}` objects; each pins to its own git commit. `analysis: true` enables LLM-powered variant comparison
- **Docs** - README rewrite with plain-language intro and new feature sections; experiments.md and ab-testing.md updated to match current code

### Fixed
- **Security: CodeQL Alerts** - Resolve 10 of 11 alerts: workflow permissions, socket binds hardened to localhost, URL assertion refactors
- **A/B: ConfigError on Analysis** - Fixed singleton reset crash in `generate_ab_analysis()`

## [0.4.25] - 2026-02-08

### Added
- **A/B Workflow Tester** - Comparative workflow testing with git worktree isolation, per-story ref checkouts, artifact snapshots, LLM analysis, workflow/template sets, and full config pass-through per variant
- **Deep Verify Synthesis: Grouped Findings** - Findings grouped by `file_path` with prioritization

### Performance
- **Providers: CPU/Memory Hotspots** - Replace busy-wait poll loop in `claude.py` with blocking `process.wait()` (10→2 wakeups/sec); reduce pause polling from 100ms to 2s; add `timeout=10` to `thread.join()` in all 8 subprocess providers

### Changed
- **Claude Provider: `claude` is now primary** - Use `provider: claude` (SDK-based) instead of `provider: claude-subprocess` (legacy). SDK provider now supports streaming input, cancel, display_model, and prefers system CLI. `claude-subprocess` is retained for benchmarking only. Upgrade `claude-agent-sdk` 0.1.20 → 0.1.33

### Fixed
- **A/B: Worktree Artifact Bleed** - Gitignored artifact dirs leaked between stories; now purged before each ref checkout
- **A/B: Hardcoded Provider** - Analysis used hardcoded `claude-sdk`/`opus` instead of master provider from config
- **Scorecard: Float Rounding** - Round scores to prevent IEEE 754 artifacts in YAML

## [0.4.24] - 2026-02-07

### Changed
- **Config: .example Convention** - Renamed `bmad-assist.yaml` to `bmad-assist.yaml.example` in published repo; user config (`bmad-assist.yaml`) is now gitignored. Standard copy-and-customize pattern

### Performance
- **Deep Verify: Batch Boundary Analysis** - Consolidate per-finding boundary analysis into single LLM call, reducing token usage and latency in code review phase

### Fixed
- **Security Review: 0% Detection Rate** - Security agent received empty diffs after artifact exclusions; fixed source code extraction and pattern matching
- **Deep Verify: File List Extraction** - Regex was extracting file descriptions instead of paths from DV output
- **Deep Verify: DV Report Aggregation** - Aggregate per-file DV results into single archival report during code review phase
- **Scorecard: Broken Metric** - Replaced non-functional `stories_completed` with `test_to_code_ratio` metric
- **Dashboard: Mixed ID Sorting** - Resolve `TypeError` when sorting stories with mixed numeric/string IDs (PR #11, thanks [@DevRGT](https://github.com/DevRGT))

## [0.4.23] - 2026-02-06

### Added
- **Scorecard v2.0** - Restructured `code_quality` (20 pts) into five sub-metrics: linting, complexity, security, test_pass_rate, code_maturity. KLOC-normalized gradient for gosec with FP filtering. Correctness proxy advisory checks for Go

### Fixed
- **Security Review: Zero Findings** - Diffs were dominated by `.bmad-assist/` and `_bmad-output/` metadata YAML; all source code was truncated away at `max_lines=500`. Added BMAD artifact exclusions to `DEFAULT_EXCLUDE_PATTERNS`, raised `max_lines` to 2000, and added diff section prioritization (source > test > config > other)
- **Security Review: Missing Metadata** - `detected_languages` and `patterns_loaded` were not propagated from `SecurityReviewCompiler` to `run_security_review()` via `CompiledWorkflow.variables`
- **Deep Verify: Domains Not Propagated** - Detected domains were never passed through the engine call chain (`_run_methods_with_errors` → `_run_single_method_with_result` → `_run_single_method`). Methods received `domains=None`, causing `_should_run_for_domains()` to return False and 6/8 methods to skip instantly with 0 findings
- **Loop: Epic Transition on Resume** - `advance_to_next_epic` failed when current epic was missing from filtered list after resume. Now jumps to first available epic (PR #2, thanks [@DevRGT](https://github.com/DevRGT))
- **Parser: Bracketed Statuses** - Support `[DONE]`/`[IN PROGRESS]` status format in story headers and mixed standard/fallback story formats in epic files (PR #1, thanks [@DevRGT](https://github.com/DevRGT))
- **Deep Verify: Helper Config** - Propagate helper provider config to `DomainDetector`; add pricing for `zai-coding-plan` models
- **Sharding: Empty Directory Precedence** - Single file now takes precedence over empty sharded directory
- **Anonymization: Provider Leak** - Removed provider/model fields from report YAML frontmatter that defeated reviewer anonymization; deanonymization mapping in `.bmad-assist/cache/` already stores this info
- **CI: Lint/Type Errors** - Fix 4 mypy errors in `epic_transitions.py`, 16 ruff issues in parser/sharding modules
- **Dependencies** - Remove `typer[all]` extra, bump typer to 0.21.1
- **Compiler** - Remove `__init__.py` from hyphenated `security-review` workflow directory (mypy fix)

## [0.4.22] - 2026-02-06

### Added
- **Security Review Agent** - Parallel security-focused reviewer in code review phase with CWE classification, confidence scoring, and severity-based handling in synthesis
- **Deep Verify: Phase Type** - `phase_type` field in DV reports distinguishes validation vs code review findings
- **Logging: Restructured Levels** - Verbose/debug/full-stream logging levels for granular output control

### Changed
- **Synthesis: Remove METRICS_JSON** - Removed LLM-generated quality/consensus metrics from both synthesis workflows. Fields like `follows_template` (always true), `missed_findings` (always 0), `internal_consistency` (self-assessment) provided no signal. Saves LLM context and time per synthesis run. Data recoverable from synthesis reports post-hoc if needed

### Fixed
- **Deep Verify: Line Number Coercion** - Coerce LLM `line_number` strings to int in all deep-verify methods
- **Security Reports: Zero Findings** - Always save archival security report even with zero findings
- **Loop: Resume After Hard Kill** - Preserve resume position after hard kill via fsync + phase restore
- **Git: Empty Repo Branch Creation** - Use atomic `checkout -b` in `ensure_epic_branch` for empty repo support (no HEAD)
- **Providers: Reasoning Effort Param** - Add `reasoning_effort` parameter to all provider `invoke()` signatures

## [0.4.21] - 2026-02-06

### Added
- **Bundled Compiled Templates** - Ship pre-compiled `.tpl.xml` + `.meta.yaml` in the package for deterministic zero-LLM workflow compilation. Default-patch users get instant templates without LLM calls; custom-patch users keep existing recompile flow via hash-based detection
- **TEA Default Patches** - Synced 8 TEA workflow patches + `defaults-testarch.yaml` to `default_patches/`
- **Codex: Reasoning Effort Support** - New `reasoning_effort` config field for Codex provider (minimal/low/medium/high/xhigh), passed via `-c model_reasoning_effort` flag. Supported in master, multi, and phase_models configs

### Fixed
- **Benchmarking: Extraction Model Mismatch** - Extraction was using `model_name` (display-only, e.g., "glm-4.5") instead of `model` (CLI identifier, e.g., "haiku") for provider invocation, causing 100% extraction failure when `model_name` was set
- **Codex: ARG_MAX Overflow** - Codex provider passed compiled prompt as CLI argument, hitting OS `ARG_MAX` limit on large prompts (>=100KB). Now uses `platform_command` temp file approach matching copilot/cursor pattern
- **Deep Verify: Go Module Filtering** - Filter Go module dependencies from DV File List extraction
- **Deep Verify: Null Evidence Fields** - Accept null `evidence_quote`/`explanation` in DV checklist response
- **Deep Verify: Markdown Artifacts** - Filter markdown artifacts from DV File List extraction
- **Compiler: XML Corruption** - Remove literal `<o>` from patch instructions to prevent XML corruption
- **Config: Path Fallbacks** - `project_knowledge` existence check with `epics_dir` and sprint `docs/` fallback; defaults to `planning_artifacts` with `docs/` as fallback
- **Compiler: Epic Headers** - Support `#`/`##`/`###` epic headers in `EPIC_TITLE_PATTERN`
- **Compiler: DV Settings Passthrough** - Pass `timeout`, `settings`, and `thinking` from config to DV methods; pass `display_model` and `settings_file` through patch compiler
- **Compiler: Cache Invalidation** - Template `.md` changes now properly invalidate compiled cache; added `defaults_hash` tracking to `CacheMeta` and `TemplateMetadata`; package fallback for `_load_defaults_file`
- **Epic Discovery: Empty Planning Artifacts** - When `planning-artifacts/` exists but is empty, epic loader now falls back to `paths.epics_dir` (which searches `docs/epics/`), fixing "No epics found in project" error
- **Lint: Ruff N812** - Rename `BMAD_ASSIST_VERSION` import to satisfy ruff N812 rule

## [0.4.20] - 2026-02-05

> Massive thanks to [@LKrysik](https://github.com/LKrysik) for the Deep Verify system - a game-changing addition to bmad-assist's quality pipeline.

### Added
- **Deep Verify: Synthesis Instructions** - Both synthesis workflows now include step 1.5 guiding LLM on DV finding severity handling, cross-referencing with reviewer/validator findings, and structured output sections
- **Deep Verify: Output Templates** - Synthesis reports now include "Deep Verify Integration" section with DV Findings Fixed/Addressed, Dismissed, and DV-Reviewer/Validator Overlap subsections
- **Story File Rescue** - Automatic retry with fresh story file when story content is missing or corrupt, with configurable previous stories limit (default: 1)

### Fixed
- **Deep Verify: Schema Mismatch** - DV findings formatter now handles both handler dict schemas (`domains`/`methods`/`method` from code_review handler and `domains_detected`/`methods_executed`/`method_id` from serialize_validation_result)
- **Deep Verify: Raw Dict in Prompt** - validate_story_synthesis was passing raw Python dict instead of formatted markdown to LLM; now uses shared `format_dv_findings_for_prompt()` formatter
- **Deep Verify: Code Review Synthesis Missing DV** - code_review_synthesis compiler was not adding DV findings to prompt at all despite handler passing them in context
- **Deep Verify: Story vs Compiled Prompt** - DV now analyzes the actual story file instead of the compiled prompt, producing relevant findings
- **Deep Verify: Cache Aggregation** - Fixed DV cache aggregation for synthesis phase

## [0.4.19] - 2026-02-04

### Added
- **Deep Verify: Synthesis Integration** - DV findings now flow to synthesis phase with 1.5x weight multiplier
  - `[Deep Verify Findings]` section embedded in synthesis context
  - Instructions prioritize DV findings over validator opinions (objective code analysis vs subjective reviews)
  - CRITICAL severity from DV = MUST FIX in synthesis
- **Deep Verify: Dedicated Reports Directory** - Reports saved to `implementation_artifacts/deep-verify/` instead of `story-validations/`

### Changed
- **Deep Verify: Non-blocking Validation** - CRITICAL findings no longer abort validation phase; they flow to synthesis for resolution
- **Synthesis Report Format** - New "Deep Verify Technical Findings (1.5x weight)" section in output template

### Fixed
- **Validation: Duplicate Logging** - Removed duplicate "Anonymizing N outputs" log messages from orchestrators
- **Rate Limiter: Event Loop Binding** - Fixed `asyncio.Lock bound to different event loop` error when using `run_async_in_thread()` via lazy lock initialization per event loop

## [0.4.18] - 2026-02-04

### Added
- **Epic 26: Deep Verify Integration** - Complete verification module with 23 stories:
  - `bmad-assist verify <file>` - Standalone CLI command for code verification
  - 8 verification methods: Pattern Match (#153), Boundary Analysis (#154), Assumption Surfacing (#155), Temporal Consistency (#157), Adversarial Review (#201), Domain Expert (#203), Integration Analysis (#204), Worst-Case Construction (#205)
  - Domain detection with LLM-based classification (Security, API, Messaging, Storage, Concurrency, Transform)
  - Pattern library with 50+ regex-based detection patterns
  - Evidence-based scoring with configurable severity weights
  - Integration hooks for validation and code review synthesis phases
  - LLM infrastructure: retry handler, rate limiter, cost tracker
  - Benchmarking corpus with golden test cases
- **IPC Foundation** - Tech-spec and Epics 29-32 for JSON-RPC protocol over Unix sockets
- **Sprint Management Guide** - Documentation for sprint-status.yaml workflow

### Changed
- **Deep Verify: Configurable Provider** - Use `helper` provider from config instead of hardcoded `haiku` model
- **CI: Streamlined Pipeline** - Remove LLM benchmark steps from GitHub Actions (too slow/expensive for CI)

### Fixed
- **Test Performance** - Suite optimized from 12 minutes to 1:53 (under 2 minute target)
  - Fixed fixture dependency bugs causing real LLM API calls in mocked tests
  - Added `@pytest.mark.slow` decorator for tests requiring real LLM calls
  - Default pytest config now skips slow tests (`-m "not slow"`)
- **Type Safety** - Resolved all mypy errors (25 → 0) with proper generic types and annotations
- **Linting** - Fixed ruff errors (F821 undefined names, type annotations)
- **Corpus Loader** - Made `source` and `artifact_type` optional in label loading

## [0.4.17] - 2026-02-02

### Added
- **Dashboard: Direct Orchestrator** - In-process LoopController with xterm.js terminal and SSE streaming
- **Dashboard: Dynamic Log Level** - Change verbosity mid-run, takes effect within 1 second
- **Dashboard: Elapsed Time** - Real elapsed time from run log instead of fake progress percentage
- **Sprint** - Support `optional` as valid retrospective status

### Fixed
- **Dashboard: Current Task Header** - Shows Epic/Story/Phase during run (thanks [@rafaelpini](https://github.com/rafaelpini))
- **Dashboard: Config Inheritance** - CWD config properly inherited in subprocess via `BMAD_ORIGINAL_CWD`
- **Dashboard: Pause States** - "PAUSING" badge with spinner; proper button state reset on stop/error
- **Dashboard: Misc** - Phase banners always visible; epic sorting with mixed IDs; filter `/api/loop/status` from logs
- **Providers** - Workflow logs at INFO level; safe process termination; dynamic log level from control file
- **Core** - Rename `CancelledException` → `CancelledError`; UTC timezone for elapsed time calculation

### Changed
- **Dashboard UI** - Log level control moved to terminal header
- **Tests** - Mocked timeouts and patch compilation for faster CI

## [0.4.16] - 2026-02-01

### Fixed
- **Run Tracking Improvements**: Phase events timeline for CSV export with explicit STARTED/COMPLETED events (thanks [@mattbrun](https://github.com/mattbrun)); phase start recording for crash diagnostics (current_phase field); per-phase atomic saves for crash resilience
- **Config Flexibility**: Support `loop: "default"` marker to explicitly use default loop config; support `phase_models: null` marker to clear phase-specific overrides
- **Config Wizard**: Helper provider and minimum one multi-validator now required; project/user name prompts; sensible defaults for new projects
- **Benchmarking**: Apply `parallel_delay` to extraction providers (was starting all at once)
- **Config Wizard**: Generate modern `timeouts.default` instead of legacy `timeout` field
- **Project Setup**: Show letter meanings inline in workflow conflict prompts (was cryptic `[a/s/i/d/?]`)
- **Init Command**: Show options inline instead of cryptic shortcuts
- **TEA Standalone**: Disable CWD config loading to prevent workspace config override
- **Handler Error Messages**: Remove deprecated YAML fallback, show clear "Run bmad-assist init" instructions instead of confusing "Handler config not found: ~/.bmad-assist/handlers/*.yaml" errors
- **Code Quality**: Resolve all ruff lint errors and mypy strict type checking errors


## [0.4.15] - 2026-02-01

### Added
- **Bundled TEA Knowledge Base** - All 34 knowledge fragments now ship with bmad-assist package, enabling TEA workflows to run without `_bmad/tea/testarch/` in target projects
- **TEA Master Switch** - `testarch.enabled` config option to completely disable all TEA functionality
- **Interactive Config Wizard** - `bmad-assist init --wizard` for guided configuration setup
- **Config Verify Command** - `bmad-assist config verify` to validate configuration files
- **TEA Standalone Banners** - Visual phase banners and notifications for TEA standalone workflows
- **TEA Prompt Saving** - Unit tests for context enhancement and prompt saving

### Documentation
- **TEA Configuration Guide** - New `docs-public/tea-configuration.md` explaining switch hierarchy, `auto` mode behavior, and common configurations

### Fixed
- **Provider Signatures** - Add `display_model` and `thinking` params to BaseProvider.invoke() - fixes validation orchestrator errors with multi-LLM configs
- **Kimi Thinking Config** - Pass thinking configuration correctly to kimi provider
- **Phase Banners** - Add banners to epic setup/teardown phases for better visibility
- **TEA Patch Resolution** - Resolve `{installed_path}` placeholder in TEA workflow compilation
- **Knowledge Fragment IDs** - Fix incorrect IDs in defaults.py (`test-levels`, `test-priorities`)

### Changed
- **Bundled Workflows** - All 8 TEA workflows now bundled for fallback when BMAD not installed
- **Knowledge Fallback** - Loader now falls back to bundled knowledge base when project has none

## [0.4.14] - 2026-01-31

### Added
- **Epic 25: TEA Enterprise Full Integration** - Complete Test Architect module with 8 workflows:
  - `testarch-atdd` - ATDD checklist generation
  - `testarch-trace` - Traceability matrix
  - `testarch-test-review` - Test code review
  - `testarch-framework` - Test framework setup
  - `testarch-ci` - CI/CD test integration
  - `testarch-test-design` - Test design documents
  - `testarch-automate` - Test automation discovery
  - `testarch-nfr-assess` - NFR assessment
- **KimiProvider** - New provider for kimi-cli (MoonshotAI) integration
- **TEA Context Loader** - Artifact injection from previous TEA runs (ATDD checklists, test-design docs) into workflow prompts
- **Context services for TEA workflows** - StrategicContextService, TEAContextService, SourceContextService integration with per-workflow configuration
- **Prompt saving for TEA workflows** - All TEA handlers now save compiled prompts to `.bmad-assist/prompts/` for debugging
- **CLI observability** - Run tracking with timestamps and staggered parallel execution
- **`--tea` flag** - Enable full TEA loop configuration (all 8 TEA phases)
- **TEA workflow configs** - Per-workflow strategic context settings (project-context, prd, architecture)
- **TEA source budgets** - 10000 tokens for automate/nfr-assess, disabled for other TEA workflows
- **Notifications:** Credential masking and phase completion events
- **Sprint repair logging** - Loud logging when new entries added during repair

### Fixed
- **Notifications:** Eliminate duplicate `phase_completed` events, add TEA phase support with proper labels
- **Workflow paths:** Two bugs in workflow path resolution
- **Placeholders:** Standardize format to `{project-root}` across codebase (thanks @Snowreg)
- **Patch compiler:** Add testarch workflow name mapping (testarch-atdd → tea-atdd)
- **Loop messages:** Remove misleading "guardian halt" message on normal phase failure
- **Parser:** Support non-standard story formats with Priority anchor
- **Kimi:** Handle array content format in kimi-cli responses
- **Kimi:** Use full model name `kimi-code/kimi-for-coding`
- **Benchmarking:** Use actual provider names in evaluation records
- **Patches:** Update code-review patch for 6-step workflow

### Changed
- **Performance:** Lazy loading for heavy imports (providers, core, handlers) - faster CLI startup
- **Loop config:** `DEFAULT_LOOP_CONFIG` now minimal (standard phases only), use `--tea` for full TEA integration
- **README:** Improved feature descriptions and added community section

## [0.4.13] - 2026-01-29

### Added
- **Interactive element detection** - CRITICAL warning when workflow contains `<ask>` elements without patch (prevents hangs in non-interactive/subprocess mode)
- **Auto-discover epics directory** in `project_knowledge` path

### Fixed
- **Compiler:** Escaped XML comment placeholders in CDATA - fixes METRICS_JSON markers appearing as `<__xml_comment__>` in compiled prompts
- **Compiler:** Downgrade missing patch log from CRITICAL to DEBUG (not an error for custom workflows without bundled patches)
- **Config:** Missing workflow fields in `StrategicContextConfig`
- **CLI:** Use config paths for epic loading instead of hardcoded `docs/`
- **CLI:** Pass `bmad_paths.epics` to `init_paths` in all commands

### Changed
- Example `bmad-assist.yaml` marked as optimized reference configuration

## [0.4.12] - 2026-01-28

### Added
- **Flexible Epic Story Parser** with fallback for non-standard formats (thanks @Richard)
  - Parses `PRSP-5-1`, `REFACTOR-2-1` style story headers when standard `## Story X.Y:` not found
  - Status-anchored detection: finds stories by `**Status:**` field presence
  - Sequential numbering (1.1, 1.2, 1.3...) regardless of original IDs
  - New `code` field on `EpicStory` preserves original story codes
  - Mixed heading levels supported (###, ####, #####)
  - Non-standard dependency extraction (`**Dependencies:** PRSP-5-1, PRSP-5-2`)
- **Default patches fallback** for pip-installed users without local BMAD installation (thanks [@mattbrun](https://github.com/mattbrun))
- **GitHub Actions CI** workflow for tests, mypy, and ruff
- **Test health initiative** completed - mutation testing analysis, 73% mutation score achieved

### Fixed
- **Scorecard:** gosec reliability and error handling, eliminate false positives when tools not installed
- **Config validation:** identify specific config file causing errors, include actual validation error in messages
- **Strategic context:** truncate docs instead of skipping when over token budget (thanks [@mattbrun](https://github.com/mattbrun))
- **Mypy:** resolve all type errors, enable strict CI checks
- **CI tests:** Rich/Typer help tests now skip properly in Docker/root environments

### Changed
- `_extract_status()` now cleans trailing asterisks (handles typos like `done**`)
- `_parse_story_sections()` accepts optional `epic_num` and `path` parameters

## [0.4.11] - 2026-01-27

### Added
- **Per-phase model configuration** (`phase_models`) - specify different LLM providers/models for each workflow phase
  - Single-LLM phases: object format with `provider`, `model`, `model_name`, `settings`
  - Multi-LLM phases: array format with full control over validator/reviewer list
  - Phases not in `phase_models` fall back to global `providers` config
  - Settings path validation with tilde expansion
- **23 unit tests** for phase_models validation, resolution, and fallback behavior
- **Documentation** for per-phase configuration in `docs/configuration.md`

### Changed
- **Breaking:** When `phase_models` defines a multi-LLM phase (`validate_story`, `code_review`), master is NOT auto-added - user has full control over the list
- When falling back to global `providers.multi` (no phase_models override), master IS still auto-added (existing behavior preserved)

## [0.4.10] - 2026-01-27

### Added
- **`bmad-assist test scorecard <fixture>`** command for automated quality scoring of experiment fixtures
  - Completeness: TODOs, placeholders, empty files detection
  - Functionality: build verification, unit tests, behavior tests
  - Code Quality: linting (go vet), complexity (gocyclo), security (gosec)
  - Documentation: README, API docs, inline comments ratio
- **Experiment prerequisites documentation** (`docs/experiments/prerequisites.md`)
- **Benchmark fixtures release** with webhook-relay-001, 002, 003 (available in GitHub Releases)

### Fixed
- **Scorecard false positives** when Go tools not installed - now correctly reports `skipped: true` with reason instead of giving max scores
- Graceful degradation for missing `gocyclo` and `gosec` tools

### Changed
- Scorecard defaults changed from max to 0 for code_quality when tools unavailable

### Benchmark Results
First empirical validation of Antipatterns module effectiveness:

| Fixture | Config | Score | Build | Tests |
|---------|--------|-------|-------|-------|
| webhook-relay-001 | baseline | 40.0% | FAIL | 252 |
| webhook-relay-002 | strategic context | 28.2% | FAIL | 0 |
| webhook-relay-003 | strategic + antipatterns | **55.3%** | **PASS** | **1254** |

Key findings:
- Strategic context alone caused regression (-11.8pp)
- Antipatterns module improved quality significantly (+15.3pp vs baseline, +27.1pp vs strategic-only)
- 5x more unit tests generated with antipatterns guidance

## [0.4.9] - 2026-01-25

### Changed
- **refactor(config):** Split `config.py` into modular `core/config/` package
- **refactor(providers):** Extract shared helpers, add cross-platform `platform_command.py` (thanks [@mattbrun](https://github.com/mattbrun))
- **refactor(loop):** Split `runner.py` into 5 helper modules
- **refactor(antipatterns):** Replace LLM extraction with deterministic regex

### Fixed
- Full mypy/ruff compliance across 102 files

## [0.4.8] - 2026-01-25

### Added
- **Strategic Context Optimization** for workflow compilers - configurable loading of strategic docs (PRD, Architecture, UX, project-context)
  - New `strategic_context` section in bmad-assist.yaml with per-workflow overrides
  - `StrategicContextService` replaces hardcoded document loading
  - Token budget enforcement and `main_only` flag for sharded docs
  - Benchmark analysis showed 0% PRD usage in code-review → excluded by default
  - `create_story`: all docs (prd, architecture, ux, project-context), indexes only
  - `validate_story`: project-context + architecture only
  - `dev_story`, `code_review`, synthesis workflows: project-context only

## [0.4.7] - 2026-01-23

### Added
- **Cursor Agent provider** for Multi-LLM orchestration (subprocess-based)
  (thanks [@mattbrun](https://github.com/mattbrun))
- **GitHub Copilot provider** for Multi-LLM orchestration (subprocess-based)
  (thanks [@mattbrun](https://github.com/mattbrun))

## [0.4.6] - 2026-01-23

### Added
- **Evidence Score System** for validation and code review workflows - replaces 1-10 scoring with mathematical model
  - CRITICAL (+3), IMPORTANT (+1), MINOR (+0.3), CLEAN PASS (-0.5)
  - Deterministic verdict thresholds: ≥6 REJECT, 4-6 REWORK, ≤3 READY/APPROVE, ≤-3 EXCELLENT/EXEMPLARY
  - Mandatory evidence enforcement ("no quote, no finding" for stories; "no code snippet, no finding" for code review)
  - Anti-Bias Battery with 5 self-checks (Devil's Advocate, Ego Check, Context Check, Best Intent, Pattern Recognition)
  - Based on Deep Verify methodology by [@LKrysik](https://github.com/LKrysik/BMAD-METHOD) 🎯
- Evidence Score extraction in `validation_metrics.py` with backward compatibility
- New regex patterns for Evidence Score report parsing
- `bmad-assist patch compile-all` command for batch compilation of all patches without valid cache
- Per-phase timeout configuration via `timeouts:` section in bmad-assist.yaml
- Antipatterns extraction from synthesis reports for organizational learning
- Source context support for validation workflows (story file, epic context)
- Auto-generate sprint-status.yaml from epic files when missing

### Fixed
- Python 3.14+ compatibility: `Traversable` import moved to `importlib.resources.abc` (thanks [@mattbrun](https://github.com/mattbrun))
- Clearer error messages for outdated/incompatible cache files
- Evidence Score calculation deduplication in code review reports
- `webhook-relay` experiment fixture repaired (other fixtures still broken)

### Changed
- `validate-story` workflow now uses Evidence Score instead of 1-10 severity
- `validate-story-synthesis` workflow updated for CRITICAL/IMPORTANT/MINOR terminology
- `code-review` workflow now uses Evidence Score with mandatory code snippet enforcement
- `code-review-synthesis` workflow updated for CRITICAL/IMPORTANT/MINOR terminology
- `ValidatorMetrics` dataclass extended with Evidence Score fields
- `AggregateMetrics` with auto-detection of report format (legacy vs Evidence Score)
- `format_deterministic_metrics_header()` dynamically switches output format
- Providers now accept any model format without hardcoded restrictions

## [0.4.5] - 2026-01-21

### Added
- Project setup consolidation - `init` and `run` now copy bundled workflows
- Batch overwrite prompt `[a/s/i/d/?]` when local workflows differ from bundled
- `--reset-workflows` flag for `init` command to restore bundled versions
- `warnings.suppress_gitignore` config option to silence gitignore warnings
- Exit code 2 for "success with warnings" (workflows skipped in CI)

### Changed
- `init` command refactored to use shared `ensure_project_setup()` logic
- `run` command now performs implicit project setup (without gitignore changes)
- File copy uses atomic write with rollback on partial failure

### Security
- Path traversal protection in workflow copying
- Symlinks not followed during file operations
- Temp files created with 0o644 permissions

## [0.4.4] - 2026-01-21

### Added
- OpenCode provider for Multi-LLM validation (subprocess-based, JSON streaming)
- Amp provider for Sourcegraph's Claude wrapper (smart mode only)
- Three-tier config hierarchy: Global → CWD → Project

### Fixed
- Config loading now respects CWD when using `--project` flag
- OpenCode provider accepts any `provider/model` format (no hardcoded list)

## [0.4.3] - 2026-01-21

### Added
- CLI `--phase` flag to override starting phase in development loop

### Changed
- Sprint reconciler with forward-only protection and improved entry ordering
- Retrospective entries now tracked in sprint-status.yaml

## [0.4.2] - 2026-01-20

### Added
- Bundled workflow templates for standalone package distribution
- External docs support - planning artifacts can live outside project root
- Configurable loop phases via `loop:` key in bmad-assist.yaml

### Changed
- Paths singleton pattern for consistent path resolution
- Project knowledge path added to CompilerContext

### Fixed
- Patch/cache discovery in subprocess and experiment contexts
- Sprint-status sync before project completion
- ruamel.yaml now a required dependency

## [0.4.1] - 2026-01-15

### Changed
- Modularized CLI commands (patch, benchmark, sprint, experiment, qa)
- Modularized dashboard routes into package structure

### Fixed
- Test suite async isolation and E2E skip handling
- Story counting in sync and QA result parsing

## [0.4.0] - 2026-01-10

### Added
- Sprint-status auto-repair and reconciliation
- Human-readable notification formatting with story titles
- Descriptive prompt filenames (epic/story/phase identifiers)
- Auto-archive artifacts after code review synthesis

### Changed
- Unified phase naming convention across all components
- Report extraction with flexible fallbacks for Multi-LLM outputs

### Fixed
- Validator tools restricted to read-only operations
- Code review anonymization and report extraction

## [0.3.0] - 2025-12-28

### Added
- Workflow compiler with variable resolution and patch system
- Multi-LLM validation with output anonymization
- Benchmarking module for LLM performance metrics
- Code review orchestration with Multi-LLM synthesis
- Telegram and Discord notification providers
- Context menu system for story/phase actions

### Changed
- Provider pattern with BaseProvider ABC for CLI adapters
- Atomic state persistence with temp file + rename

## [0.1.1] - 2025-12-15

### Added
- Typer CLI with Pydantic configuration models
- BMAD file parsing (markdown frontmatter, epic files)
- YAML state persistence with atomic writes
- LLM providers: Claude, Codex, Gemini CLI
- Development loop with phase execution and transitions
- Multi-LLM parallel orchestration with Master synthesis

### Notes
- Initial release with core development loop functionality
