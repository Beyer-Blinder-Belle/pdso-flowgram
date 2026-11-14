# PDSO Architecture (for agents)

Dense reference for code changes and for scripting against a live model. Paths are relative to `PDSO.extension/lib/pdso/code/` unless they start with `PDSO.extension/`.

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

Defined in `data/extensible_storage_schemas.py` with the `extensible_storage` library (`BaseSchema`, `simple_field`, `map_field`), which is vendored, unmodified, in `PDSO.extension/lib/extensible_storage/`. Accessed through `business/extensible_storage_service.py`. **Never change these GUIDs.**

| Schema | GUID | Lives on | Fields |
| --- | --- | --- | --- |
| `PdsoConfigDocumentSchema` | `7b533d48-f078-48ad-a393-48d73b71e96b` | `doc.ProjectInformation` | `version`, `last_run_timestamp{}`, `data_silo_family_name`, `uid_pattern` (regex, may be blank), `area_scheme_mappings{pdso_key: area_scheme_name}`, `program_volume_family_name_prefix`, `program_volume_extrusion_height` (feet), `tethered_parameters{name: guid}` (the **tracked parameter list**, written only by the Parameter Manager), `levels_mapping{identifier: ElementId}`, `worksets{"Area"/"Data Silo"/"Program Volume": name or "Not enforced"}` |
| `PdsoProgramVolumeInstanceSchema` | `2cd38c37-88ed-42c7-9fc2-a03b1cbb5cb7` | each PV instance | `is_primary_instance`, `parent_data_silo_id`, `hashed_parameters{}` (defined, not used yet) |
| `PdsoProgramVolumeFamilySchema` | `581b2f54-bec4-46ab-9afc-07656e795f65` | each PV `Family` | `hashed_area_curve_geometry{"PDSO - GSF": hash}`, `area_basepoint{}` (defined, not used yet) |

Reading: `with PdsoConfigDocumentSchema(doc.ProjectInformation) as c: c.uid_pattern`. Map fields come back as .NET dictionaries; convert with `dict(...)` or iterate `.Keys`. `get_document_config()` returns the schema object (a context manager) or `None` if Project Config was never saved.

`area_scheme_mappings` direction is **inconsistent in the code**, so treat it carefully:

- The sync (`body_2_harvest_areas.py`) reads `{key: value}` as `{DSO parameter: Revit area scheme}`. It builds `ExtendedAreaScheme(doc, area_scheme_name=value, bound_silo_parameter_name=key)`. So does `setup/runner.area_scheme_name`.
- The schema docstring and `parameter_registry_service.get_area_mapped_parameter_names` read it as `{Revit area scheme: DSO parameter}`. They treat the values as parameter names, which get locked as tracked.
- With the defaults (`"PDSO - GSF": "PDSO - GSF"`, etc.), keys equal values, so both readings agree.

Separately, `DSO.related_area_elements` is keyed by the **Revit area scheme name**, and phase 2 looks up the literal `"PDSO - GSF"`. Program Volume geometry therefore needs a Revit area scheme named exactly `PDSO - GSF`.

## Parameters

PDSO parameters are **per model**. A model tracks a subset of the built-in catalog plus any custom parameters, chosen in the **Parameter Manager**.

- **Catalog**: `data/shared_parameter_constants.py` → `standard_pdso_parameters` (an `OrderedDict` of 20 `ExtendedSharedParameter`s, with fixed GUIDs). Each has a `role` from `data/extended_shared_parameter.py`:
  - `core`: always tracked. These are `PROGRAM_UID`, `PROGRAM_STACKING` and `LAST_SYNCED`, listed in `CORE_PARAMETER_NAMES`.
  - `area`: ZFA, NSF, GSF; written by the harvest and not user-modifiable.
  - `standard`: the remaining built-ins.
  - `custom`: added by users; the types are Text, Multiline Text, Number, Integer, Area, Length and Yes/No.
- **Tracked list**: `config.tethered_parameters{name: guid}`. **Always read it through `business/parameter_registry_service.get_registry(doc, refresh=False)`**, which returns a `TrackedParameterRegistry` (`.parameters`, `.names()`, `.get(name)`, `.by_role(...)`, `.is_configured`). Never hardcode a parameter list. Parameters written into by an area mapping are locked as tracked too.
- **Applying**: `PDSOParameterSetupService.apply(...)` in the same module. It creates or reuses shared parameter definitions in the `PDSO` group, by GUID first and then by name. It binds them to Generic Models, optionally adds them to the Data Silo family, and saves the tracked list. The shared parameter file is Revit's own, or a remembered fallback (pyRevit user config `PDSO_ParameterManager`).
- **Presets**: `business/parameter_preset_service.py`. JSON files go in `%APPDATA%\PDSO\ParameterPresets` or a chosen folder. *Default* and *Blank* are built in.
- **UI**: `view_models/body_3_parameters.py` + `xaml/3_parameters.xaml`.
- **Network paths**: wrap file access on possibly offline drives in `common/code/background.call_with_timeout` (never call the Revit API inside it).

Flow of values:

- Harvested (Area → DSO): the parameters named in `area_scheme_mappings`. Set in `data_service.queue_area_updates`, together with `PDSO - Last Synced` (`"%Y-%m-%d %H:%M:%S"`). `ExtendedDataSilo.queue_resetting_params` first resets the `area`-role and Last Synced values, so a DSO that left its Area loses the old SF.
- Pushed (DSO → every PV instance): **every tracked parameter** (`get_registry(doc).parameters`), in `data_service.queue_program_volume_instance_data_updates`.

`standards/parameters.json`, the Model Setup `parameters` step and `standards/PDSO Shared Parameters.txt` predate the Parameter Manager. They still list the 20 built-ins.

## Commands

| Ribbon button | Folder | Entry | Writes to model? |
| --- | --- | --- | --- |
| Model Setup | `0.Setup.pushbutton` | `setup.runner.run_setup(doc, standards, steps)` | Yes, one `TransactionGroup` "PDSO Model Setup", one transaction per item |
| Project Config | `1.Config.pushbutton` | `view_models/body_1_setup.PDSO_View_Model_1_Setup` (modeless WPF, window Uid `bbbtools-pdso-window`) | Yes, ES config on Save |
| Parameter Manager | `1b.Parameters.pushbutton` | `view_models/body_3_parameters.py` → `PDSOParameterSetupService.apply` | Yes, on **Apply Parameters**: parameter bindings, Data Silo family, `tethered_parameters` |
| Sync Data | `2.RunScripts.pushbutton` | `view_models/body_2_harvest_areas.PDSO_View_Model_2_Harvest_Areas` (modeless WPF) | Yes, via `ExternalEvent` |
| Audit Report | `3.Audit.pushbutton` | `generate_detailed_report(output, logger)` | No |

Button order comes from the panel's `bundle.yaml` `layout`. Bundles use `context: doc-project`; Config, Parameters, Sync and Audit use a persistent engine (`engine: persistent: true`) so the modeless windows survive.

## Sync pipeline

`_on_run_sync_clicked` → `CustomizableEvent.raise_event(self._execute_sync_process)` (`PDSO.extension/lib/common/code/view_model_external_events.py`). It only runs when preflight passed (`can_run_sync`).

### Preflight (`run_preflight_checks`)

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
10. No Area with several DSOs.
11. Areas without DSOs (⚠).
12. DSOs missing some schemes (⚠).
13. DSOs not in any Area (⚠).
14. Worksharing: DSOs, PVs and Areas editable (⚠).

`✗` blocks the sync and `⚠` doesn't. Status strings start with `✓`, `⚠️` or `✗`; `_update_preflight_status` aggregates them.

### Phase 1: Area Harvest (`_execute_phase_1_area_harvest`)

1. Build `ExtendedDataSiloCollection(doc, config.data_silo_family_name)` and `ExtendedLevelCollection(doc)`.
2. Validate stacking for every DSO (`PDSOLevelMappingService.validate_all_stacking_parameters`). DSOs with errors are **skipped** for later phases, and a warning is added to the completion message.
3. For each configured scheme, `PDSOCorrelationService.correlate_data_silos_to_areas` matches a DSO to an Area by **same `LevelId`** and **winding-number point-in-polygon** on the Area's boundary segments.
4. `data_service.queue_area_updates` → commit in transaction `PDSO - Harvest Area Data`.
5. Optional workset fix and pinning in `PDSO - Worksets and Pinning`.

### Phase 2: Family Geometry (`_execute_phase_2_family_geometry`)

Skipped when the UI option is `No`. For each DSO, `PDSOFamilyManagerService._create_or_update_family_for_data_silo`:

- It needs the DSO's `"PDSO - GSF"` Area; without one it logs a warning and skips.
- `new_hash = hashing_service.area_tessellation_hash(area)` (quantized tessellated boundary).
- No family → create from template, extrude the boundary (translated so the **DSO location is the origin**) to `program_volume_extrusion_height`, then load.
- Family exists and the hash differs → clear and rebuild geometry in the family doc, then reload.
- Hash matches → skip.
- Then rename to `{prefix}{UID}`, store the hash, and pin or move to workset. Transactions: `PDSO - Update Family Geometry`, `PDSO - Pin Families`.

### Phase 3: Instance Updates (`_execute_phase_3_instance_updates`)

Skipped when the UI option is `No`.

- `correlate_data_silos_to_program_volume_instances` lists the expected instance per required level. It reuses existing instances, matched by **family name `{prefix}{UID}`** and `LevelId` (not by `parent_data_silo_id`). It creates missing ones and marks extra ones `should_be_deleted`.
- New instances go at the DSO's XY on each target level, with Z set so the offset from the level is 0.
- `queue_program_volume_instance_data_updates` copies every tracked parameter. The sync calls `get_registry(doc, refresh=True)` at the start.
- Transactions: `PDSO - Unpin Instances`, `PDSO - Update Program Volume Instances`, then workset fixes and pinning.

Each phase catches its own exceptions and logs them; a failure in one phase doesn't stop the next. Phases 2 and 3 reuse the collections that phase 1 built.

## Model Setup

`setup/standards.py` resolves each `<id>.json` from the first folder that has it: project `PDSO Standards/` next to the (central) model, then the user folder picked with Shift+Click, then `%PDSO_STANDARDS_DIR%`, then `lib/pdso/standards/`. Files aren't merged. Tokens such as `{revit_yy}` and `{repo_dir}` are expanded. `setup/runner.py` runs the steps in `STEP_RUNNERS` (`setup/steps/__init__.py`) in `setup.json` order and collects report rows with `SetupContext.add()`. Format spec: `PDSO.extension/lib/pdso/standards/README.md`.

## Known gaps and suspected bugs

Verify these before relying on them. They're good candidates for issues or hackathon work.

1. **No headless sync API.** The orchestration lives in the WPF view model and depends on UI state (`self._*_option`, `self.progress_*`, `revit.doc`). Extracting `run_preflight(doc) -> report` and `run_sync(doc, options) -> report` into `business/` would let both the button and an MCP agent call it.
2. **The "Recompute All Geometry" option is not honored.** Phase 2 only checks for `"No"`; both "Yes" options use the hash cache.
3. **The geometry hash ignores the DSO position and the extrusion height (suspected).** Geometry is stored relative to the DSO location, but the hash covers only the Area boundary. Moving a DSO inside its Area, or changing the configured height, may leave a stale family. `area_basepoint` was probably meant for this. Today the workaround is to delete the PV family and sync again.
4. **Changing a Program UID orphans its Program Volumes (suspected).** PV families and instances are found by the name `{prefix}{UID}`. After a UID edit the sync builds a new family and leaves the old one in the model. Changing the prefix in Project Config orphans every PV the same way.
5. **GSF is the only geometry source, and it is matched by scheme name.** The `"PDSO - GSF"` literal is in `family_manager_service.py`, `extended_data_silo.py` and `extended_area.py`. Renaming the Revit GSF scheme breaks Program Volume geometry even if Project Config maps it.
6. **`area_scheme_mappings` direction is inconsistent.** The sync and the Parameter Manager read it in opposite directions (see Persistent state). Any mapping where a key differs from its value probably misbehaves in one of the two.
7. **Model Setup's parameters step overlaps the Parameter Manager.** `steps/parameters.py` is still a placeholder that only reports, and `standards/parameters.json` is separate from the tracked list. Decide whether Model Setup should call `PDSOParameterSetupService.apply` or drop the step.
8. **Default family name mismatch.** A fresh config has `data_silo_family_name = "Program Data Silo Object - Rvt25"`, while the shipped family is `PDSO - Program Data Silo Object - Rvt26`. Model Setup fixes it.
9. **Command-context assumptions.** Most classes call `script.get_output()` in `__init__` and some code uses the global `revit.doc` instead of an injected `doc`.
10. **Unused fields.** `hashed_parameters` (PV instance) and `area_basepoint` (PV family) are defined but not used.
11. **Doc drift.** The intranet reference says DSO values are copied to Areas. They aren't; data never flows to Areas.
