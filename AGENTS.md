# AGENTS.md: PDSO Flowgram

Orientation for coding agents. Read this first, then open only the doc you need.

| Need | Read |
| --- | --- |
| How the sync works, data model, storage, invariants | [docs/agent/architecture.md](docs/agent/architecture.md) |
| Operating PDSO in a live Revit session (PDSO MCP server, pyRevit agent runtime) | [docs/agent/pyrevit-mcp.md](docs/agent/pyrevit-mcp.md) |
| HTTP API routes, MCP tools and setup | [README.md → HTTP API and MCP server](README.md#http-api-and-mcp-server) |
| What users see and do, step by step | [docs/user-guide.md](docs/user-guide.md) |
| Every Data Silo parameter, who writes it | [docs/parameter-reference.md](docs/parameter-reference.md) |
| Model Setup JSON standards format | [PDSO.extension/lib/pdso/standards/README.md](PDSO.extension/lib/pdso/standards/README.md) |

## What this is

A pyRevit extension (the **PDSO** ribbon tab) that keeps architectural program data in one place:

- **Data Silo**: a point-based Generic Model family instance, one per unit ("program"). It is the **only** place program data is entered.
- **Areas** in the mapped area schemes (default `PDSO - GSF`, `PDSO - NSF`, `PDSO - ZFA`) are where SF comes from.
- **Program Volume**: a generated Generic Model family per Data Silo, placed on every stacked level. It is a taggable copy of the Data Silo's tracked data.

Data flow: **Area SF → Data Silo → Program Volumes.** Nothing flows back to Areas.

The same UI-free services in `lib/pdso/code/business/` drive the ribbon windows, an HTTP API (pyRevit Routes), and an MCP server for agents.

## Hard constraints

- **IronPython 2.7 for everything under `PDSO.extension/`.** No f-strings, type hints, `pathlib` or other Py3-only syntax and stdlib. Use `"{}".format()`. Files start with `# -*- coding: utf-8 -*-`.
  - **Exception:** `mcp/pdso_mcp_server.py` and `tools/*.py` are **CPython 3.10+**, run outside Revit (`uv run --script`). They talk to Revit only over HTTP. Never import Revit or `pdso` code there.
- **Never change a shared parameter GUID** or an extensible storage schema GUID. The code looks parameters up by GUID and existing models store data under these schema GUIDs.
- **Never hardcode the PDSO parameter list.** Each model tracks its own set. Read it through `parameter_registry_service.get_registry(doc)`.
- **Revit API writes need a transaction**, and from a modeless window they need an `ExternalEvent` (`lib/common/code/view_model_external_events.py`).
- **Never write to Program Volumes or to the harvested SF parameters** as a data-entry path. The sync overwrites them.
- **Keep business logic in `business/` services**, not in view models or API handlers. Windows and API handlers are thin callers.
- Lint: `ruff check .` (config in `pyproject.toml`). No unit tests. `tools/snowdon_acceptance.py` runs acceptance steps against the HTTP API in a real Revit.
- Runtime deps: `pyrevit` and `rpw` (both ship with pyRevit). `extensible_storage` is vendored, unmodified, in `PDSO.extension/lib/extensible_storage/` (third-party, see its `NOTICE.md`; excluded from Ruff). Don't edit it.

## Map

```
PDSO.extension/
  startup.py                 registers the HTTP API (pyRevit Routes, API name "pdso")
  PDSO.tab/Program Data Silo Tools.panel/
    0.Setup.pushbutton/      → setup.runner.run_setup (config.py = Shift+Click menu)
    1.Config.pushbutton/     → view_models/body_1_setup.py (Project Config window)
    1b.Parameters.pushbutton/→ view_models/body_3_parameters.py (Parameter Manager window)
    2.RunScripts.pushbutton/ → view_models/body_2_harvest_areas.py (ribbon title "Sync Data")
    3.Audit.pushbutton/      → business/audit_service.py (prints to the output window)
  lib/extensible_storage/    vendored third-party ES library (don't edit)
  lib/pdso/code/
    api/        dispatcher.py (routing, {"ok","data"|"error"} envelope), handlers.py (route table)
    business/   UI-free services:
                  sync_service, preflight_service, audit_service        ← the 3 workflows
                  config_service, bootstrap_service, parameter_service,
                  program_service (Data Silo CRUD), area_drafting_service,
                  visualization_service, model_service                  ← mostly used by the API
                  correlation, data, family_manager, hashing, level_mapping,
                  workset_coordination, area_boundary, extensible_storage_service,
                  parameter_registry_service, parameter_preset_service  ← building blocks
                  revit_helpers, log_capture, errors
    data/       Extended* wrappers around rpw elements + collections; schemas; parameter catalog
    setup/      Model Setup: standards loader, runner, steps/*.py
    view_models/ WPF MVVM (thin over the services)
  lib/pdso/standards/   default JSON standards + generated shared parameter file
  lib/pdso/xaml/        WPF windows
  lib/common/           external-event helper, background.call_with_timeout, utils, icons
mcp/pdso_mcp_server.py  MCP server (CPython, stdio) → HTTP API, optional SSH port forward
tools/                  snowdon_acceptance.py (API acceptance runner), deploy_vm.sh (copy code + reload)
families/               Data Silo .rfa (Revit 2026)
```

## Where to find things

| Question | Look at |
| --- | --- |
| Run the sync from code | `business/sync_service.PDSOSyncService(doc, options).run()`; options and their values are in `SYNC_OPTIONS` |
| Preflight checks | `business/preflight_service.PDSOPreflightService(doc).run_preflight_checks()`, then `.can_run_sync`, `.results()` |
| Audit report | `business/audit_service.py` (the API passes a `RecordingOutput` to get it as data) |
| Read/write Project Config as plain dicts | `business/config_service.get_config` / `update_config` |
| Make a fresh model PDSO-ready | `business/bootstrap_service.bootstrap(doc, ...)` |
| Create/read/update Data Silos ("programs") | `business/program_service.py` (API field names such as `uid`) |
| Draw or edit Area outlines | `business/area_drafting_service.py` |
| HTTP route → service | `api/handlers.py` (`@route(METHOD, "pattern")`) |
| MCP tool → route | `mcp/pdso_mcp_server.py` (`@mcp.tool()` functions calling `api(...)`) |
| Built-in parameter catalog (names, types, GUIDs, roles) | `data/shared_parameter_constants.py`; roles in `data/extended_shared_parameter.py` |
| Which parameters a model tracks and syncs | `business/parameter_registry_service.py` (`get_registry`, `check_parameter_bindings`, `PDSOParameterSetupService.apply`), stored in `config.tethered_parameters`; API side in `parameter_service.py` |
| What is stored in the model | `data/extensible_storage_schemas.py` |
| Which Area a Data Silo belongs to | `business/correlation_service.py` (same level + winding-number point-in-polygon) |
| Stacking notation (`03-05, 10`) | `business/level_mapping_service.py`; background in `lib/pdso/code/legacy/LEVEL_MAPPING_PROCESS.md` |
| Program Volume geometry and naming | `business/family_manager_service.py` (`<prefix><Program UID>`); shape Area chosen in `ExtendedDataSilo.get_area_for_program_volume_family_geometry` |
| Skip-if-unchanged logic | `business/hashing_service.py` + hashes stored in the PV family schema |
| Adding a Model Setup step | `setup/steps/__init__.py` (`STEP_RUNNERS`) + `standards/setup.json` |

## Known gaps and traps

- **The HTTP API has no authentication.** It listens on `127.0.0.1` only; reach it from another machine with an SSH port forward, never by opening the port. The dev flag file `%APPDATA%\pyRevit\pdso_api_dev.flag` enables `POST /pdso/dev/exec` (arbitrary Python in Revit); keep it off non-dev machines.
- **`area_scheme_mappings` is `{DSO parameter: Revit area scheme}`.** The code is consistent on that, but the docstring in `extensible_storage_schemas.py` still says the opposite.
- **"Yes - Recompute All Geometry" isn't acted on.** It's accepted as an option, but phase 2 only checks for `"No"`.
- **Program Volumes are found by family name `{prefix}{UID}`.** Changing a UID or the prefix leaves the old family behind (suspected; see architecture.md).
- **Model Setup's `parameters` step only reports.** The Parameter Manager (or `POST bootstrap` / `parameter_service`) creates and binds parameters.
- **Default `data_silo_family_name`** in a fresh config is `Program Data Silo Object - Rvt25`, which doesn't match the shipped family. Model Setup and bootstrap set the real one.
- **Large files:** `audit_service.py`, `sync_service.py`, `preflight_service.py`, `body_3_parameters.py`, `mcp/pdso_mcp_server.py` (1–1.5k lines each). Navigate with `grep -n "def "`.
- **The intranet docs predate this code.** Where they disagree, the code wins. Differences are listed at the end of [docs/parameter-reference.md](docs/parameter-reference.md).

## Conventions

- Services take `doc` and use `script.get_logger()`. Collections wrap `rpw` elements as `Extended*` objects that *queue* changes (`params_to_update`, `entity_fields_to_update`) and apply them in `commit_pending_changes()` inside a transaction the caller opens.
- Caller-fixable errors raise types from `business/errors.py`; the API turns them into `{"ok": false, "error": ...}`.
- API and MCP units: lengths in decimal feet, areas in square feet.
- Transactions in the project document are named `PDSO - ...` so they're recognizable in Undo.
- Ribbon titles differ from folder names: `2.RunScripts.pushbutton` shows as **Sync Data**.
- User-facing text: short, plain sentences. Name buttons as the ribbon shows them.
