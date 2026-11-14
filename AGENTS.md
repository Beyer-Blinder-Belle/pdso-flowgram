# AGENTS.md: PDSO Flowgram

Orientation for coding agents. Read this first, then open only the doc you need.

| Need | Read |
| --- | --- |
| How the sync works, data model, storage, invariants | [docs/agent/architecture.md](docs/agent/architecture.md) |
| Driving PDSO in a live Revit session through the pyRevit MCP | [docs/agent/pyrevit-mcp.md](docs/agent/pyrevit-mcp.md) |
| What users see and do, step by step | [docs/user-guide.md](docs/user-guide.md) |
| Every Data Silo parameter, who writes it | [docs/parameter-reference.md](docs/parameter-reference.md) |
| Model Setup JSON standards format | [PDSO.extension/lib/pdso/standards/README.md](PDSO.extension/lib/pdso/standards/README.md) |

## What this is

A pyRevit extension (the **PDSO** ribbon tab) that keeps architectural program data in one place:

- **Data Silo**: a point-based Generic Model family instance, one per unit. It is the **only** place users type program data.
- **Areas** in the `PDSO - GSF`, `PDSO - NSF` and `PDSO - ZFA` schemes are where SF comes from.
- **Program Volume**: a generated Generic Model family per Data Silo, placed on every stacked level. It is a taggable copy of the Data Silo's data.

Data flow: **Area SF → Data Silo → Program Volumes.** Nothing flows back to Areas.

## Hard constraints

- **IronPython 2.7.** No f-strings, type hints, `pathlib` or other Py3-only syntax and stdlib. Use `"{}".format()`. Files start with `# -*- coding: utf-8 -*-`.
- **Never change a shared parameter GUID** or an extensible storage schema GUID. The code looks parameters up by GUID and existing models store data under these schema GUIDs.
- **Revit API writes need a transaction**, and from a modeless window they need an `ExternalEvent` (see `lib/common/code/view_model_external_events.py`).
- **Never write to Program Volumes or to the harvested SF parameters** as a data-entry path. The sync overwrites them.
- Lint: `ruff check .` (config in `pyproject.toml`). There are no automated tests; Revit is needed to run anything.
- External runtime deps that are **not** in this repo: `pyrevit`, `rpw` (bundled with pyRevit), and `extensible_storage` (separate lib extension, [khorn06/extensible-storage-pyrevit](https://github.com/khorn06/extensible-storage-pyrevit)).

## Map

```
PDSO.extension/
  PDSO.tab/Program Data Silo Tools.panel/
    0.Setup.pushbutton/      script.py  → setup.runner.run_setup   (config.py = Shift+Click menu)
    1.Config.pushbutton/     script.py  → view_models/body_1_setup.py (WPF window, modeless)
    2.RunScripts.pushbutton/ script.py  → view_models/body_2_harvest_areas.py (ribbon title "Sync Data")
    3.Audit.pushbutton/      script.py  (self-contained report, ~1.5k lines, read-only)
  lib/pdso/code/
    business/   services: correlation, data (queues param writes), family_manager (PV geometry),
                hashing (change detection), level_mapping (stacking), workset_coordination,
                extensible_storage_service, area_boundary_service
    data/       Extended* wrappers around rpw elements + collections; schemas; parameter constants
    setup/      Model Setup: standards loader (standards.py), runner.py, steps/*.py
    view_models/ WPF MVVM. body_2_harvest_areas.py holds the sync orchestration (see gaps)
  lib/pdso/standards/   default JSON standards + generated shared parameter file
  lib/pdso/xaml/        WPF windows
  lib/common/           external-event helper, generic utils, icons
families/               Data Silo .rfa (Revit 2026)
```

## Where to find things

| Question | Look at |
| --- | --- |
| Parameter names, types, GUIDs | `lib/pdso/code/data/shared_parameter_constants.py` (runtime) and `lib/pdso/standards/parameters.json` (Model Setup). Keep them in sync. |
| What is stored in the model | `lib/pdso/code/data/extensible_storage_schemas.py` |
| Default Project Config values | `extensible_storage_service.initialize_document_schema_with_defaults` |
| Sync entry point and the 3 phases | `body_2_harvest_areas.py`: `_execute_sync_process`, `_execute_phase_1_area_harvest`, `_execute_phase_2_family_geometry`, `_execute_phase_3_instance_updates` |
| Preflight checks | `body_2_harvest_areas.py`: `run_preflight_checks` |
| Which Area a Data Silo belongs to | `business/correlation_service.py` (same level + winding-number point-in-polygon) |
| Stacking notation (`03-05, 10`) | `business/level_mapping_service.py`; background in `lib/pdso/code/legacy/LEVEL_MAPPING_PROCESS.md` |
| Program Volume geometry and naming | `business/family_manager_service.py` (`<prefix><Program UID>`, shape from the GSF Area) |
| Skip-if-unchanged logic | `business/hashing_service.py` + hashes stored in the PV family/instance schemas |
| Area boundary validation | `business/area_boundary_service.py` (statuses used by Audit and the sync) |
| Adding a Model Setup step | `lib/pdso/code/setup/steps/__init__.py` (`STEP_RUNNERS`) + `standards/setup.json` |

## Known gaps and traps

- **The sync only runs through the WPF window.** The orchestration lives in the view model and writes progress to UI properties. There is no headless `run_sync(doc, options)` yet, so nothing can call it from a script or the MCP without refactoring.
- **GSF is hardcoded as the geometry source.** Program Volume shape always comes from the area mapped under the key `"PDSO - GSF"`.
- **The Model Setup `parameters` step only reports.** It doesn't add parameters yet; `parameters.json` is a proposed format.
- **`"Fake"` in `standard_pdso_parameters`** is a test entry, not a real parameter.
- **Default `data_silo_family_name`** in a fresh config is `Program Data Silo Object - Rvt25`, which doesn't match the shipped family. Model Setup overwrites it with `PDSO - Program Data Silo Object - Rvt{yy}`.
- **Most classes call `script.get_output()`** in `__init__`. That assumes a pyRevit command context and may misbehave elsewhere (for example, MCP runs).
- **`body_1_setup.py` and `body_2_harvest_areas.py` are large** (1.4k and 2.7k lines). Use `grep -n "def "` to navigate instead of reading them whole.
- **The intranet docs predate this code.** Where they disagree, the code wins. Differences are listed at the end of [docs/parameter-reference.md](docs/parameter-reference.md).

## Conventions

- Services take `doc` in the constructor and use `script.get_logger()`. Collections wrap `rpw` elements as `Extended*` objects that *queue* changes (`params_to_update`, `entity_fields_to_update`) and apply them in `commit_pending_changes()` inside a transaction the caller opens.
- Transactions in the project document are named `PDSO - ...` so they're recognizable in Undo.
- Ribbon titles differ from folder names: `2.RunScripts.pushbutton` shows as **Sync Data**.
- User-facing text: short, plain sentences. Name buttons as the ribbon shows them.
