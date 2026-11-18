# PDSO Architecture (for agents)

Dense reference for code changes and for scripting against a live model. Paths are relative to `lib/pdso/code/` unless they say otherwise. The repository root is the pyRevit extension (it holds `startup.py`, `PDSO.tab/` and `lib/`).

## Domain model

| Concept | Revit object | Identified by | Written by |
| --- | --- | --- | --- |
| Data Silo (DSO) | `FamilyInstance`, Generic Model, point-based | `Symbol.Family.Name == config.data_silo_family_name` | User (except SF and Last Synced) |
| Area | `Area` in a mapped area scheme | `AreaScheme.Name` in `config.area_scheme_mappings` | User |
| Program Volume family (PV family) | `Family`, Generic Model | Name `"{prefix}{Program UID}"`, `prefix = config.program_volume_family_name_prefix` (default `PDSO_ProgramVolume-`) | Sync phase 2 |
| Program Volume instance (PV) | `FamilyInstance` of a PV family | Family name prefix + `PdsoProgramVolumeInstanceSchema.parent_data_silo_id` | Sync phase 3 |
| Level mapping | Extensible storage on ProjectInfo | `config.levels_mapping`: identifier → `ElementId` | User via Project Config |

Cardinality: one DSO → at most one Area per scheme (same level, point inside) → one PV family → one PV instance per required level (the DSO's own level plus each level in `PDSO - Program Stacking`). The instance on the DSO's level has `is_primary_instance = True`.

## Persistent state (extensible storage)

Defined in `data/extensible_storage_schemas.py` with the `extensible_storage` library (`BaseSchema`, `simple_field`, `map_field`), which is vendored, unmodified, in `lib/extensible_storage/`. Accessed through `business/extensible_storage_service.py`. **Never change these GUIDs.**

| Schema | GUID | Lives on | Fields |
| --- | --- | --- | --- |
| `PdsoConfigDocumentSchema` | `7b533d48-f078-48ad-a393-48d73b71e96b` | `doc.ProjectInformation` | `version`, `last_run_timestamp{}`, `data_silo_family_name`, `uid_pattern` (regex, may be blank), `area_scheme_mappings{pdso_key: area_scheme_name}`, `program_volume_family_name_prefix`, `program_volume_extrusion_height` (feet), `tethered_parameters{name: guid}` (the **tracked parameter list**, written only by the Parameter Manager), `levels_mapping{identifier: ElementId}`, `worksets{"Area"/"Data Silo"/"Program Volume": name or "Not enforced"}` |
| `PdsoProgramVolumeInstanceSchema` | `2cd38c37-88ed-42c7-9fc2-a03b1cbb5cb7` | each PV instance | `is_primary_instance`, `parent_data_silo_id` (both written by phase 3 when they differ), `hashed_parameters{}` (not used; see below) |
| `PdsoProgramVolumeFamilySchema` | `581b2f54-bec4-46ab-9afc-07656e795f65` | each PV `Family` | `hashed_area_curve_geometry{"PDSO - GSF": hash, "Extrusion Height": "8.0"}`, `area_basepoint{"PDSO - GSF": DSO location XYZ}`: what the geometry was last built from |

Reading: `with PdsoConfigDocumentSchema(doc.ProjectInformation) as c: c.uid_pattern`. Map fields come back as .NET dictionaries; convert with `dict(...)` or iterate `.Keys`. `get_document_config()` returns the schema object (a context manager) or `None` if Project Config was never saved.

`area_scheme_mappings` is **`{DSO parameter: Revit area scheme}`**, for example `{"PDSO - GSF": "Gross Building"}`. The sync builds `ExtendedAreaScheme(doc, area_scheme_name=value, bound_silo_parameter_name=key)`, and the parameter registry locks the **keys** as tracked parameters. The correlation stores each DSO's Area in `related_area_elements` under the same key (the DSO parameter), so lookups like `related_area_elements.get("PDSO - GSF")` work whatever the Revit scheme is called. The Project Config screen shows rows as Area Scheme → Parameter and swaps on load and save; a config saved by the old screen (`{scheme: parameter}`) is recognised on load and corrected on the next save.

`DSO.related_area_elements` is keyed by the **DSO parameter** (see above). The Program Volume shape comes from `ExtendedDataSilo.get_area_for_program_volume_family_geometry`: the Area mapped to `PDSO - GSF` if there is one, otherwise the **smallest** Area that contains the DSO. That lets models with their own schemes (for example `Gross Building`, `Rentable`) work.

## Parameters

PDSO parameters are **per model**. A model tracks a subset of the built-in catalog plus any custom parameters, chosen in the **Parameter Manager** window or through `business/parameter_service.py` (API).

- **Catalog**: `data/shared_parameter_constants.py` → `standard_pdso_parameters` (an `OrderedDict` of 20 `ExtendedSharedParameter`s, with fixed GUIDs). Each has a `role` from `data/extended_shared_parameter.py`:
  - `core`: always tracked. These are `PROGRAM_UID`, `PROGRAM_STACKING` and `LAST_SYNCED`, listed in `CORE_PARAMETER_NAMES`.
  - `area`: ZFA, NSF, GSF; written by the harvest and not user-modifiable.
  - `standard`: the remaining built-ins.
  - `custom`: added by users; the types are Text, Multiline Text, Number, Integer, Area, Length and Yes/No.
- **Tracked list**: `config.tethered_parameters{name: guid}`. **Always read it through `business/parameter_registry_service.get_registry(doc, refresh=False)`**, which returns a `TrackedParameterRegistry` (`.parameters`, `.names()`, `.get(name)`, `.by_role(...)`, `.is_configured`). Never hardcode a parameter list. Parameters written into by an area mapping are locked as tracked too.
- **Applying**: `PDSOParameterSetupService.apply(...)` in the same module. It creates or reuses shared parameter definitions in the `PDSO` group, by GUID first and then by name. It binds them to Generic Models, optionally adds them to the Data Silo family, and saves the tracked list. The shared parameter file is Revit's own, or a remembered fallback (pyRevit user config `PDSO_ParameterManager`).
- **Presets**: `business/parameter_preset_service.py`. JSON files go in `%APPDATA%\PDSO\ParameterPresets` or a chosen folder. *Default* and *Blank* are built in.
- **UI**: `view_models/body_3_parameters.py` + `xaml/3_parameters.xaml`.
- **API**: `parameter_service.py` does the same one parameter at a time, and gives each tracked parameter a short **field name** (`PDSO - Program UID` → `uid`, `Rent per SF` → `rent_per_sf`). That name is used by the program routes, filters and `color_by`.
- **Network paths**: wrap file access on possibly offline drives in `common/code/background.call_with_timeout` (never call the Revit API inside it).

Flow of values:

- Harvested (Area → DSO): the parameters named in `area_scheme_mappings`. Set in `data_service.queue_area_updates`, together with `PDSO - Last Synced` (`"%Y-%m-%d %H:%M:%S"`). `ExtendedDataSilo.queue_resetting_params` first resets the `area`-role and Last Synced values, so a DSO that left its Area loses the old SF.
- Pushed (DSO → every PV instance): **every tracked parameter** (`get_registry(doc).parameters`), in `data_service.queue_program_volume_instance_data_updates`.

`standards/parameters.json` and `standards/PDSO Shared Parameters.txt` predate the Parameter Manager and still list the 20 built-ins. They're only used by Quick Setup's Shift+Click **Export PDSO shared parameter file**.

## Commands

| Ribbon button | Folder | Entry | Writes to model? |
| --- | --- | --- | --- |
| Project Config | `0.Config.pushbutton` | `view_models/body_1_setup.PDSO_View_Model_1_Setup` (modeless WPF, window Uid `bbbtools-pdso-window`) | Yes, ES config on Save |
| Parameter Manager | `1.Parameters.pushbutton` | `view_models/body_3_parameters.py` → `PDSOParameterSetupService.apply` | Yes, on **Apply Parameters**: parameter bindings, Data Silo family, `tethered_parameters` |
| Quick Setup | `2.Setup.pushbutton` | `setup.runner.run_setup(doc, standards, steps)` | Yes, one `TransactionGroup` "PDSO Quick Setup", one transaction per item |
| Sync Data | `3.RunScripts.pushbutton` | `view_models/body_2_harvest_areas.py` (modeless WPF) → `PDSOPreflightService`, `PDSOSyncService` | Yes, via `ExternalEvent` |
| Audit Report | `4.Audit.pushbutton` | `business/audit_service.generate_detailed_report(output, logger)` | No |

Button order comes from the panel's `bundle.yaml` `layout`. Bundles use `context: doc-project`; Config, Parameters, Sync and Audit use a persistent engine (`engine: persistent: true`) so the modeless windows survive.

## Sync pipeline

The sync, its preflight and the audit are **UI-free services**. The Sync Data window, the Audit Report button and the HTTP API all call them:

- `business/preflight_service.PDSOPreflightService(doc, logger=None, on_check=None)`: `.run_preflight_checks()`, then `.can_run_sync`, `.results()` (a list of `{key, level, message}`) and `.summary()`.
- `business/sync_service.PDSOSyncService(doc, options=None, logger=None, on_progress=None)`: `.run()` returns a summary dict. `SYNC_OPTIONS` lists each option with its default and permitted values. Only `Fix`, `Pin` and `No` change behavior; the other values mean "skip this step".
- The window raises `CustomizableEvent.raise_event(...)` (`lib/common/code/view_model_external_events.py`) so the services run in Revit API context. The API runs inside pyRevit Routes, which already runs handlers in API context.
- `POST /pdso/sync` runs preflight and then the sync, and returns `{ran, preflight, log, ...summary}`. With `force=true` it **skips a failed preflight**; agents should never set that without the user's explicit OK.

### Preflight (`PDSOPreflightService.run_preflight_checks`)

The checks, in order:

1. Config in ES.
2. Level mappings complete for every DSO's stacking (⚠; DSOs with errors are skipped).
3. Tracked parameters (`check_parameter_bindings`): ✗ if the Parameter Manager has never run, if a core parameter isn't tracked, or if any tracked parameter isn't bound to Generic Models.
4. Configured area schemes exist.
5. Areas exist.
6. No grouped areas.
7. DSO family loaded.
8. DSO instances exist.
9. UIDs unique.
10. UIDs match `uid_pattern` (⚠).
11. No Area with several DSOs.
12. Areas without DSOs (⚠).
13. DSOs missing some schemes (⚠).
14. DSOs not in any Area (⚠).
15. DSOs editable (worksharing, ⚠).
16. PVs editable (worksharing, ⚠).
17. Areas editable (worksharing, ⚠).

Check 2 also warns about level keys that contain `-` or `,` (S7).

`✗` blocks the sync and `⚠` doesn't. Status strings start with `✓`, `⚠️` or `✗`.

The phases below are methods of `PDSOSyncService`.

### Phase 1: Area Harvest (`_execute_phase_1_area_harvest`)

1. Build `ExtendedDataSiloCollection(doc, config.data_silo_family_name)` and `ExtendedLevelCollection(doc)`.
2. Validate stacking for every DSO (`PDSOLevelMappingService.validate_all_stacking_parameters`). DSOs with errors are **skipped** for later phases and reported in the summary.
3. For each configured scheme, `PDSOCorrelationService.correlate_data_silos_to_areas` matches a DSO to an Area by **same `LevelId`** and **winding-number point-in-polygon** on the Area's boundary segments.
4. `data_service.queue_area_updates` → commit in transaction `PDSO - Harvest Area Data`.
5. Optional workset fix and pinning in `PDSO - Worksets and Pinning`.

### Phase 2: Family Geometry (`_execute_phase_2_family_geometry`)

Skipped when `program_volume_geometry_update` is `No`. First `correlate_data_silos_to_program_volume_families` pairs each DSO with the family named `{prefix}{UID}`. If there is none, it looks for the DSO's previous family through the `parent_data_silo_id` stored on PV instances (any name, any prefix) and queues it for renaming, so a UID or prefix change renames the family instead of orphaning it. A family whose name another DSO now expects is never taken. Then, for each DSO, `PDSOFamilyManagerService._create_or_update_family_for_data_silo`:

- It needs a shape Area (`get_area_for_program_volume_family_geometry`: the `PDSO - GSF` scheme's Area, else the smallest containing Area). Without one it logs a warning and skips.
- `new_hash = hashing_service.area_tessellation_hash(area)` (quantized tessellated boundary).
- No family → create from template, extrude the boundary (translated so the **DSO location is the origin**) to `program_volume_extrusion_height` (Project Config, default 8 ft), then load.
- Family exists and the Area hash, the stored DSO location (`area_basepoint`, 1/256" tolerance) or the stored extrusion height differs, or the option is `Yes - Recompute All Geometry (slow)` → clear and rebuild geometry in the family doc, then reload. A family with no stored location (built by an older version) is rebuilt once.
- Otherwise → skip.
- Then rename to `{prefix}{UID}`, store the hash, height and location, and pin or move to workset. Transactions: `PDSO - Update Family Geometry`, `PDSO - Pin Families`.

### Phase 3: Instance Updates (`_execute_phase_3_instance_updates`)

Skipped when `program_volume_data_update` is `No`.

- `correlate_data_silos_to_program_volume_instances` lists the expected instance per required level. It reuses existing instances, matched by **family name `{prefix}{UID}`** and `LevelId`. On commit, each instance gets `parent_data_silo_id` and `is_primary_instance`, which phase 2 uses to find renamed families. It creates missing ones and marks extra ones `should_be_deleted`.
- New instances go at the DSO's XY on each target level, with Z set so the offset from the level is 0.
- `queue_program_volume_instance_data_updates` copies every tracked parameter. The sync calls `get_registry(doc, refresh=True)` at the start.
- **Only differences are written.** `ExtendedProgramVolumeInstance.commit_pending_changes` compares each value with the instance first (`parameter_access.set_if_changed`; numbers within 1e-9, a blank text equals ""), saves the schema entity only if a field differs, and moves the instance only if it is more than 1e-6 ft from its target. `PDSO - Last Synced` is written only when something else on the instance changed, so on a PV it means "last changed by a sync". An instance with nothing to change isn't edited, so a workshared model doesn't borrow it. Only instances that move or are deleted are unpinned, and pinning sets only instances not already as asked. The sync summary reports `program_volumes`: created, updated, unchanged.
- No stored parameter hash: comparing with the instance costs about as much as computing a hash, and it also catches a value someone typed over on a PV. The geometry needs its hash because comparing geometry means opening the family.
- Transactions: `PDSO - Unpin Instances` (only if a pinned instance moves or is deleted), `PDSO - Update Program Volume Instances`, then workset fixes and pinning.

Each phase catches its own exceptions and logs them; a failure in one phase doesn't stop the next. Phases 2 and 3 reuse the collections that phase 1 built.

## Quick Setup

Users run it **last** of the three setup buttons: Project Config → Parameter Manager → Quick Setup. Steps (`setup.json` order): `area_schemes` (creates the Revit schemes named in `area_scheme_mappings` by copying an existing scheme and renaming it), `families` (loads the shipped Data Silo family; keeps Project Config's family if it names a different, loaded one; then embeds the tracked parameters in the Data Silo family with `PDSOParameterSetupService.add_tracked_parameters_to_family`), `view_templates`, `views`, `schedules`. There is no parameters step; the Parameter Manager owns parameters.


`setup/standards.py` resolves each `<id>.json` from the first folder that has it: project `PDSO Standards/` next to the (central) model, then the user folder picked with Shift+Click, then `%PDSO_STANDARDS_DIR%`, then `lib/pdso/standards/`. Files aren't merged. Tokens such as `{revit_yy}` and `{repo_dir}` are expanded. `setup/runner.py` runs the steps in `STEP_RUNNERS` (`setup/steps/__init__.py`) in `setup.json` order and collects report rows with `SetupContext.add()`. The HTTP API's bootstrap calls the same `run_setup`, after writing the config and setting up parameters. Format spec: `lib/pdso/standards/README.md`.

## HTTP API and MCP server

- `startup.py` registers one catch-all pyRevit Routes handler per HTTP method under the API name `pdso`, at `http://127.0.0.1:48884/pdso/` by default. Enable it with `pyrevit configs routes enable`.
- `api/dispatcher.py` imports the route table on each request, matches `<name>`/`<int:name>` patterns, and wraps the reply as `{"ok": true, "data": ...}` or `{"ok": false, "error": {type, message, traceback}}`. `PDSOError(message, status)` from `business/errors.py` becomes a 400, 404 or 409 error.
- `api/handlers.py` is the route table: `@route("GET", "programs/<uid_or_id>")`, and so on. Handlers stay thin and call services. `GET /pdso/routes` lists them all.
- `mcp/pdso_mcp_server.py` (CPython 3.10+, run with `uv run --script`) maps about 45 MCP tools onto those routes. It can open an SSH port forward when `PDSO_SSH_HOST` is set, and saves images to `PDSO_OUTPUT_DIR`.
- Development: with `%APPDATA%\pyRevit\pdso_api_dev.flag` present, `POST /pdso/dev/reload` drops cached `pdso` modules, and `POST /pdso/dev/exec` runs arbitrary Python in Revit. `tools/deploy_vm.sh` copies code to a remote Revit computer and reloads it. `tools/snowdon_acceptance.py` runs numbered acceptance steps against the API.
- Routes has **no authentication** and binds to `127.0.0.1` by default (pyRevit's host setting; PDSO doesn't check it). Use SSH forwarding for remote use; never expose the port.

See the README section *HTTP API and MCP server* for the full route and tool tables.

## Known gaps and suspected bugs

Verify these before relying on them. They're good candidates for issues or hackathon work.

1. ~~**The "Recompute All Geometry" option is not honored.**~~ Fixed: phase 2 rebuilds every family with that option.
2. ~~**The geometry hash ignores the DSO position and the extrusion height.**~~ Fixed: `area_basepoint` and the stored height are compared too. The configured extrusion height was also never used (every PV was 8 ft); it is now.
3. **Orphaned Program Volumes from before `parent_data_silo_id` was written.** A UID or prefix change now renames the DSO's family, but only once phase 3 has stored `parent_data_silo_id` on its instances. Families orphaned earlier, and families of deleted DSOs, stay in the model until someone deletes them: Shift-click Audit Report, or `program_service.purge_orphaned_program_volumes` (API `POST program-volumes/purge-orphans`). The sync never deletes them on its own.
4. **The UID pattern is a preflight warning, not an error.** The `uid_pattern` check warns and lists failing UIDs; only the API refuses them. The Data Silo schedule doesn't check it.
5. ~~**Stale schema docstring.**~~ Fixed: the docstring, the Project Config screen (which saved scheme → parameter) and the correlation key now all use parameter → scheme.
6. ~~**Two setup paths.**~~ Fixed: `bootstrap_service.bootstrap` (API) writes the config, sets up parameters, then calls `setup.runner.run_setup` with the enabled `setup.json` steps, so the API and the ribbon make the same items. Its `levels` reach the views step through `SetupContext.options["levels"]`. Bootstrap adds nothing of its own. `ensure_area_plan` (used by area drafting) names and templates new plans from `views.json`, and raises rather than making a different plan when that fails. The families step builds the placeholder Data Silo family when no family file loads (`placeholder_if_missing`), for the ribbon and the API alike. `parts` (Quick Setup step ids) and `parameters` (`"all"` or `"required"`) narrow the scope; `setup_status` (`GET setup`) reports each part's state for agents to agree the scope with the user.
7. ~~**Leftover global `revit.doc`.**~~ Fixed in the services and data wrappers (`generate_detailed_report` now takes `doc`). The view models and `catalogue/catalogue_windows.py` still use it, which is fine for windows on the active document.
8. **Unused field.** `hashed_parameters` (PV instance) is defined but not used, and stays: a schema can't drop a field in models that store it. Phase 3 skips unchanged values by comparing with the instance instead.
10. ~~**Legacy parameters in the shipped family.**~~ Fixed: `PDSO - Area IDs` and `PDSO - Integrity Hash` were removed from `PDSO - Program Data Silo Object - Rvt26.rfa`. The family now carries only the three required parameters (`PDSO - Program UID`, `PDSO - Program Stacking`, `PDSO - Last Synced`), so it suits non-residential projects. Every other parameter comes from the project's Generic Models binding (Parameter Manager), and Quick Setup's families step embeds the tracked ones in the family after loading it.
9. **Doc drift.** The intranet reference says DSO values are copied to Areas. They aren't; data never flows to Areas.
