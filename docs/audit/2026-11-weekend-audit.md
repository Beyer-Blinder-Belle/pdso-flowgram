# Weekend code audit: 14–15 Nov 2026

The audit reviewed everything merged to `main` that weekend (17 PRs, about 26k lines, up to `6e89d07`) and the three branches pushed but not merged. Five reviewers each read one area of the code. Severe findings were checked against the code. Nothing was run in Revit.

This file tracks the fixes. Each issue has an ID; use it in commit messages and PRs (for example `Fix B3: ...`). When an issue is fixed, tick its box and add the commit or PR.

Status: `[ ]` open · `[x]` fixed · `[-]` won't fix (give the reason) · **⚠ Revit** = needs checking in a live Revit after the fix.

**Progress:** B1–B8 and R1–R6 are fixed in PR #22 (merged; API 1.6). S1–S9, A1–A12, U1–U6 and C1–C6 are fixed on branch `fix/audit-lower-priority` (API 1.7). S4 is partly fixed, and U7 is won't-fix. Only R1 has been checked in Revit so far. The Revit test cases, and the test model they need, are listed in [docs/testing/test-cases.md](../testing/test-cases.md).

Paths are relative to the repo root. Line numbers are as of `6e89d07`.

---

## B. Bugs that can damage a model

- [x] **B1. Clearing a Room deletes its Data Silo and Program Volumes.** `lib/pdso/code/catalogue/catalogue_api_service.py:232-268` (`_room_instances`). `HARVEST_CATEGORIES` includes Generic Models, and nothing filters out PDSO objects. This affects `clear_room_items` (API and MCP) and Pull from Catalogue's "Delete the furniture" option. Clear and place also run as two transactions, so a failed place leaves the Room empty.
  *Fix:* exclude Data Silos and Program Volumes from room items; clear and place in one TransactionGroup.
  **Done:**
  - `_room_instances` and `rooms_overview` skip PDSO objects (`pdso_kinds`).
  - `place_layout(..., clear_first)` deletes and places in one "PDSO - Place Unit Layout" transaction, after the fit check, so a layout that doesn't fit deletes nothing.
  - The Pull button and the `catalogue/place` route (`clear_first`) use it, and the MCP `place_layout` has a `clear_first` argument.

  *Test in Revit:* Pull with "Delete the furniture" into a Room that has a Data Silo and Program Volumes. They must stay, and one Undo must restore the furniture.
- [x] **B2. Every Generic Model counts as a Data Silo.** The catalogue tests for the `PDSO - Program UID` parameter, which is bound to all Generic Models (`parameter_registry_service.py:823`). Places: `revit_layout_service.py:147,318,582-594`, `harvest_features.py:687`, `harvest_image.py:208`. Effects:
  - Harvests drop all Generic Model furniture.
  - A Program Volume can be taken as a unit's silo.
  - `tag_data_silos` writes the layout id into Program Volumes and into items it just placed.

  *Fix:* identify Data Silos by family name from the config, and Program Volumes by the PV prefix. Unmerged `fix/place-on-rac` (cef47f2) does this everywhere except `harvest_image.py`.
  **Done:** ported Ethan's `harvest_features.pdso_kinds` from `fix/place-on-rac` unchanged, so that branch merges with fewer conflicts. Its family-file loading was not ported; that stays with F1. It is now used in harvest, `areas_with_data_silo`, `_unit_data`, `tag_data_silos` and also `harvest_image.py`. `tag_data_silos` writes only to Data Silos, never to Program Volumes or placed items, and only when an item was placed (C2's tagging part).
  *Test in Revit:* harvest a unit that has a Generic Model plant: the plant must be in the layout. Pull into a Room: no Program Volume gets a Unit Layout Type value.
- [x] **B3. A stacking typo deletes the program's Program Volumes.** `lib/pdso/code/business/correlation_service.py:520-536`. Instances on levels outside `required_levels` are marked for deletion even when the stacking failed validation. Preflight only warns, and the sync log says these programs "process normally".
  *Fix:* don't mark instances for deletion when the Data Silo's stacking didn't fully resolve.
  **Done:**
  - New `ExtendedDataSilo.has_stacking_errors` property.
  - A Data Silo with stacking errors, or whose own level didn't resolve, keeps every instance; the sync logs a warning with the count kept.
  - Levels that did resolve still get instances.
  - The misleading sync message is corrected.

  *Test in Revit:* stacking `01-03` synced, then change it to `01-0X` and sync. Instances on 02 and 03 must stay.
- [x] **B4. Editing an Area loses its holes and turns arcs into straight lines.** `lib/pdso/code/business/area_drafting_service.py:341-535` (`set_area_outline`, `move_area`, `scale_area`, `move_area_vertex`). The lines of every loop are deleted, but only the outer loop is redrawn, as tessellated points. `move_area` isn't atomic either: the outline and each program update commit separately.
  *Fix:* move and scale the existing curves (holes and arcs included) with a transform. Wrap `move_area` in one TransactionGroup. **⚠ Revit**
  **Done:**
  - `move_area` and `scale_area` transform the Area's real boundary curves, holes included, with `Curve.CreateTransformed`, so arcs stay arcs. The location point moves with them.
  - `move_area` wraps the outline and the program moves in one "PDSO - Move Area" group.
  - `scale_area` scales about the true polygon centroid, as its docstring says (it used the vertex average).
  - `set_area_outline` and `move_area_vertex` replace only the outer loop, so holes keep their lines. An arc in the outer loop still becomes straight segments when a vertex is edited, as documented.
  - `_draw_outline` now goes through the new `_draw_curves`.
  - Bad `dx`/`dy`/`factor`/`x`/`y` give a 400 (part of A8).

  *Test in Revit (**⚠ Revit**):* move and scale an Area with a hole and an arc edge, and check `ScaleBasisAndOrigin` + `CreateTransformed` on arcs. Move an Area that has programs, then press Undo once.
- [x] **B5. The pinning options "Maintain", "Unpin" and "Warn (if configured)" do nothing, and "Maintain" unpins every Program Volume.** `lib/pdso/code/business/sync_service.py:49-63, 552-555, 621, 831, 980-1104`. The code only checks for "Pin".
  *Fix:* "Pin" pins; "Unpin" unpins; "Maintain" restores each element's own pin state; "Warn (if configured)" logs a warning only.
  **Done:**
  - Phase 1 (Data Silos and Areas) and phase 2 (families) handle Pin, Unpin and Maintain; Maintain leaves elements as they are.
  - Phase 3 records which instances were pinned before its temporary unpin, and Maintain pins those again.
  - All pinning goes through `_set_pinned`, which skips an element that can't be changed instead of rolling back the whole transaction. Before, one failing Data Silo or deleted instance did that.
  - "Warn (if configured)" is a *workset* option, not a pinning one, so it is not part of B5. It still does nothing; it moves to K14.

  *Test in Revit:* sync with Maintain after you unpin one Program Volume by hand; it must stay unpinned and the rest pinned.
- [x] **B6. Geometry update "No" also turns off the data update (phase 3).** `correlation_service.py:343-351` with `sync_service.py:696-699`. `related_program_volume_family` is only set in phase 2, which returns early for "No".
  *Fix:* correlate Data Silos to their existing Program Volume families even when phase 2 is skipped.
  **Done:** when phase 2 is skipped, `_link_existing_families` links each Data Silo to the family that has its exact expected name. It never renames or builds a family, and it skips Data Silos with no UID. Data Silos whose family would need a rename or a build are skipped in phase 3 with the existing warning.
  *Test in Revit:* change a program's data, then sync with geometry "No" and data "Yes". The Program Volumes must get the new value.
- [x] **B7. One Data Silo with bad stacking stops Program Volume location updates for all of them.** `lib/pdso/code/business/data_service.py:76-84`.
  *Fix:* skip only the Data Silos that failed validation.
  **Done:** the global early return is gone. Instances are moved only if they have a `target_level`, meaning a level that resolved. An instance kept by B3 has none and stays where it is: moving it would put it on the Data Silo's level. Duplicates on one level (F4) are also left in place.
- [x] **B8. Pull from Catalogue overwrites the user's `PDSO - Unit Layout Type`** with a catalogue id (`revit_layout_service.py:593`). Harvest All Units then reads it back as the program name (`1.HarvestUnits.pushbutton/script.py:52`).
  *Fix:* store the layout id somewhere other than a user-entered parameter, or write it only when the field is empty. Decide with the team.
  **Done:**
  - Pull writes the layout id only when the field is empty or already holds a layout id; a value a user typed is kept, with a warning in the result.
  - New `layout_entry.is_entry_id`.
  - Harvest All Units ignores a layout id when it suggests the program name.
  - *Team decision still open:* should the layout id get its own parameter instead?

### Review of the B fixes (PR #22)

An adversarial review of `df04ac4`, by a reviewer with no context from this work, found 8 issues. R1–R6 are fixed in the follow-up commit.

- [x] **R1 (B6 regression).** With geometry "No", Program Volumes were moved to the Data Silo's new location, although their geometry was not rebuilt around it.
  *Fixed:* `_link_existing_families` reads the build point stored in the family (`area_basepoint`) into `ExtendedProgramVolumeFamily.built_basepoint`, and instances go there. If no point is stored, existing instances aren't moved. **Checked in Revit on 2026-11-16 (test T-S3).**
- [x] **R2 (B7 regression).** Duplicate instances on one level had no `target_level`, so they no longer moved with their Data Silo.
  *Fixed:* each duplicate gets its level, location and primary flag, and the sync warns about it. Removing duplicates is still F4.
- [x] **R3 (B1 incomplete).** With `clear_first`, the furniture was deleted even when nothing could be placed (families not loaded).
  *Fixed:* `place_layout` raises a 409 in that case, which rolls the whole transaction back.
- [x] **R4 (B8 incomplete).** `is_entry_id` missed the sample's `program--name` ids and Harvest All Units' `-2` suffix.
  *Fixed:* the pattern accepts both.
- [x] **R5 (B3/B7 claim not met).** `commit_pending_changes` still skipped every instance of a Data Silo with stacking errors, so the levels that resolved were not updated either.
  *Fixed:* the skip is removed. B3 already keeps instances on unresolved levels from being deleted, and they aren't moved.
- [x] **R6 (B4 new behaviour).** `set_area_outline` kept a hole's lines even when the new outline no longer contained the hole.
  *Fixed:* a hole is kept only if it lies wholly inside the new outline; otherwise its own lines are removed, with a warning.
- [ ] **R7 → K15.** See K15.
- [ ] **R8 → K16.** See K16.

### Review of the S, A, U and C fixes

A second review, again by a reviewer with no context from the fixing, ran on the four commits of `fix/audit-lower-priority` and found 13 issues. All are fixed in the follow-up commit.

- [x] **V1 (C4).** `catalogue_lock` looped forever, freezing Revit, when the folder couldn't be written. A run's own lock could also be left behind.
  *Fixed:* a folder that can't be written is reported, and the handle is always closed.
- [x] **V2 (A9).** Single-parameter errors came back only as warnings, and `setup_model` dropped them.
  *Fixed:* any error is a 409 again, and the message says whether the tracked list was saved.
- [x] **V3 (C4).** In IronPython the "atomic" write deleted the old file before the rename, and a failed rename lost the entry.
  *Fixed:* `System.IO.File.Replace` / `File.Move`.
- [x] **V4 (C3).** `model_id` alone could match projects made from the same template.
  *Fixed:* a match needs the same saved file name too.
- [x] **V5 (C4).** A long run's lock could be taken as stale. A run could also remove another run's lock, and clock skew between computers counted.
  *Fixed:* `touch()` per unit, an owner token, and age measured against a probe file in the same folder.
- [x] **V6 (U2).** On an older Revit, Quick Setup still tried to load the 2026 family.
  *Fixed:* files for a newer Revit (`RvtNN` > the running version) are skipped, a failed load is caught, and both failures carry the version hint.
- [x] **V7 (S9).** Workset enforcement could fail the whole worksets-and-pinning transaction on an element another user owns.
  *Fixed:* such elements are skipped (`WorksharingUtils.GetCheckoutStatus`). **⚠ Revit**
- [x] **V8 (A8).** NaN and infinity passed `to_number`.
  *Fixed:* they are refused.
- [x] **V9 (A12).** The numeric match also applied to text fields ("01" matched "1").
  *Fixed:* only number fields are compared as numbers.
- [x] **V10 (A6).** Nested captures that closed out of order left the logger patched.
  *Fixed:* one shared patch, removed when the last capture closes. Tested with stubs of both pyRevit logger builds.
- [x] **V11 (A11).** A bad hole loop could roll back the whole `color_areas`.
  *Fixed:* building the loops is inside the fallback.
- [x] **V12 (A3).** The acceptance runner sent a write again after a timeout during send. The MCP message didn't mention that the API might not be running behind the tunnel.
  *Fixed:* both.
- [x] **V13 (S3).** A failed phase 1 marked phases 2 and 3 "skipped", like a phase the user turned off.
  *Fixed:* they are marked "not run".

Still open from this review:
- The workset transaction name inside `WorksetCoordinationService` (K8).
- `assign_elements_to_workset` for Program Volume families (S9 ⚠ Revit).

## S. Sync, preflight and audit bugs

- [x] **S1.** Family documents are never closed after a successful `LoadFamily` (`family_manager_service.py:230-247`; `Close` is only called at 357 and 427). Each rebuilt Program Volume leaks an open family document.
  *Done: the family document is closed after it is loaded.*
- [x] **S2.** Data Silos with no UID make correlation expect a family named `"<prefix>None"`. Phase 2's progress callback then raises AttributeError (`family_manager_service.py:95,119-126`), which aborts phase 2: no renames and no hashes are stored.
  *Done: correlation and phase 2 skip a Data Silo with no UID and warn; the progress callback no longer fails on a missing wrapper.*
- [x] **S3.** If phase 1 raises, phases 2 and 3 fail with an unbound-local error, and the window still shows "✓ completed" (`sync_service.py:255-338`).
  *Done: phases 2 and 3 are skipped when phase 1 fails, and the window shows the errors instead of "completed".*
- [ ] **S4.** Reads write to the model. The vendored `BaseSchema.__exit__` always runs `SetEntity` in an "Update Entity" transaction. Preflight and the audit also call `CheckoutElements`. Refresh runs preflight from a WPF click with no ExternalEvent (`body_2_harvest_areas.py:337`). **⚠ Revit**
  *Partly done: config and family entities are read without the vendored `with` block, so reading no longer writes (preflight, audit, Project Config, level mapping). Still open: preflight and audit call `WorksharingUtils.CheckoutElements`, and Refresh runs preflight from a WPF click with no ExternalEvent. **⚠ Revit***
- [x] **S5.** The arc bounding-box pre-check uses curve endpoints only, so points inside an arc's bulge are rejected (`correlation_service.py:677-720`).
  *Done: the bounding box is built from the sampled arc points.*
- [x] **S6.** One failed instance rolls back the whole "Update Program Volume Instances" transaction, deletions included (`extended_program_volume_instance_collection.py:322-331`).
  *Done: a failed instance is logged and counted; the rest of the transaction commits.*
- [x] **S7.** A level key that contains a hyphen (`B1-03-A2`, the format Project Config suggests) can't be used in stacking (`level_mapping_service.py:210-282`).
  *Done, by refusing such keys instead of parsing them (the first fix let the parser guess, which was ambiguous: with keys `B1`, `03` and `B1-03`, `B1-03` silently meant one level). Project Config and `update_config` refuse a key with `-` or `,` (`level_key_problem`), and the Project Config hint now suggests `B1.03.A2`. Preflight warns about keys saved before the rule. Stacking that names such a key gets a clear error for that Data Silo. Before S7 these keys never worked in stacking, so no model relies on them.*
- [x] **S8.** The audit counts placeholder wrappers as "Families without geometry hashes", so the number is too high (`audit_service.py:1342-1346`).

  *Done: placeholder wrappers (no family) are not counted.*
- [x] **S9. (new) Workset enforcement never ran.** The sync read the worksets under "Data Silo", "Area" and "Program Volume", but Project Config and the API save "data-silos", "areas" and "program-volumes" (`sync_service.py`).
  *Done: `_workset_name` reads the saved keys (the old ones still work), and worksets are skipped in a model that is not workshared. **⚠ Revit:** families may refuse a workset change; check the log.*

## A. HTTP API and MCP bugs

- [x] **A1.** After `set_target_document`, `list_programs` and `select_programs` fail with "Unknown filter field: 'document'", and `render_plan` draws no programs. `GET programs` passes the whole query as filters (`handlers.py:358-360`, `program_service.py:207-209`).
  *Done: `document` is removed from the filters.*
- [x] **A2.** `show_view`, `show_program_volumes` and `select_elements` look up ids in the target document, then act on the active one, so the wrong elements can be selected (`mcp/pdso_mcp_server.py:202-205`, `handlers.py:179-183, 430-435`).
  *Done: the window routes call `ctx.require_active_doc()` and return 409 for another document; `show_program_volumes` still returns its image (with `not_shown_reason`).*
- [x] **A3.** On an SSH tunnel reconnect, the request is sent again even if it's a write (`mcp/pdso_mcp_server.py:276-286`, `tools/snowdon_acceptance.py:136-146`).
  *Done: a write is sent again only if urllib wrapped a connect error (not a reset); same in the acceptance runner.*
- [x] **A4.** A write that times out is reported as "Cannot reach Revit" but stays queued and runs later (`mcp/pdso_mcp_server.py:281`, `startup.py:88-98`).
  *Done: a timeout gets its own message saying Revit may still make the change.*
- [x] **A5.** `PUT config` rejects a `document` field in the body (`handlers.py:216-219`, `config_service.py:131`).
  *Done.*
- [x] **A6.** The `log` in the `run_sync` reply is probably always empty: `LogCapture` hooks the root logger, and pyRevit loggers don't propagate to it (`log_capture.py:36`).
  *Done: `LogCapture` wraps the pyRevit logger class (`_emit` in the newer runtime, `handle` in the older) and restores it. Tested with stubs of both in CPython.*
- [x] **A7.** A bad `uid_pattern` is saved without a compile check, and every program create or update then returns 500 (`config_service.py:139-146`, `program_service.py:449`). `re.match` also doesn't anchor at the end of the string.
  *Done: `config_service.check_uid_pattern` on save, `uid_matches` (whole-UID match) in the API and preflight. A UID that only began with a match (`CL-02-ABX`) now fails.*
- [x] **A8.** Bad numeric input returns 500 with a traceback, not 400: `config_service.py:149`, `model_service.py:39`, `program_service.py:497, 677-678`, `area_drafting_service.py:468, 488, 510, 524`.
  *Done: `revit_helpers.to_number` in config, model, program and area drafting.*
- [x] **A9.** `parameter_registry.apply()` commits partial bindings, then the API reports 409. A tracked parameter that fails to resolve is silently untracked (`parameter_registry_service.py:509-536`, `parameter_service.py:225-241`).
  *Done: a previously tracked parameter that fails stays tracked with a warning. Any error is still a 409, which now says whether the tracked list was saved (review V2).*
- [x] **A10.** `move_area` and `create_area` can leave a new area plan behind after a failure (`area_drafting_service.py:373-378`).
  *Done: the area plan is made inside the transaction group (create, set outline, move/scale).*
- [x] **A11.** `color_areas` clears and colors in two undo steps, changes the crop box permanently, and fills over holes (`visualization_service.py:328-361`).
  *Done: clear and draw are one undo step; holes are holes of the filled region. The crop change is kept: the MCP `color_areas` docstring promises it.*
- [x] **A12.** `list_programs` compares filters as strings, so a filter of `1` doesn't match `1.0` (`program_service.py:210`).

  *Done: numbers compare as numbers.*
## U. Setup and ribbon bugs

- [x] **U1.** Project Config and Sync Data share the window Uid `bbbtools-pdso-window`. With one open, the other button just brings it to the front (`0.Config` and `3.RunScripts` `script.py:30`, `xaml/0_window.xaml:7`).
  *Done: `bbbtools-pdso-config-window` and `bbbtools-pdso-sync-window`; window titles now match the ribbon.*
- [x] **U2.** `families.json` builds the file name as `Rvt{revit_yy}`, and only the Rvt26 family ships, so Quick Setup fails on Revit 2027. README.md:208 says the code was tested on 2027.
  *Done: families.json falls back to the Rvt26 file and name; on an older Revit the failure says the family needs Revit 2026. **⚠ Revit 2027***
- [x] **U3.** When Project Config names another Data Silo family, the families step still loads the shipped family and never adds the tracked parameters to the user's family (`setup/steps/families.py:117-162`).
  *Done: a loaded Project Config family is kept, the shipped one is not loaded, and the tracked parameters go into the chosen family.*
- [x] **U4.** The shipped shared parameter file has `USERMODIFIABLE=1` and empty descriptions for all 20 parameters, including the harvested SF parameters and Last Synced (`standards/parameters.json`, `PDSO Shared Parameters.txt`).
  *Done: parameters.json and the regenerated `.txt` lock the four parameters; GUIDs unchanged.*
- [x] **U5.** Opening Project Config writes the default config before the user clicks Save (`body_1_setup.py:136`).
  *Done: Project Config shows `default_config_values()` for a model without a configuration and Save creates it; Parameter Manager no longer writes on open.*
- [x] **U6.** The 3D template's `on_existing: "update"` overwrites the user's category visibility on every Quick Setup run (`view_templates.json:3`).
  *Done: `on_existing` defaults to `skip`.*
- [-] **U7.** `ElementId.Value` in `setup/revit_utils.py:320` needs Revit 2024 or later.

  *Won't fix: the whole codebase uses the Revit 2024+ `ElementId.Value`, and the Data Silo family needs Revit 2026.*
## C. Catalogue bugs

- [x] **C1.** `harvest_image.py:189-257`: an exception between `Start()` and `Commit()` leaves the transaction open, and the swallowed group `RollBack` leaves the group open. Every later export then fails. GDI objects also leak for each unit (`:85-133`).
  *Done: `_roll_back` on failure in the constructor and in `export`; GDI objects disposed.*
- [x] **C2.** An entry with no items always "fits" (`all([])`), so Pull offers unfurnished units (`layout_recommender.best_placement`). *Partly done with B2: when nothing is placed, no Data Silo is tagged. The recommender still offers empty entries.*
  *Done: the recommender rejects entries with no items.*
- [x] **C3.** `remove_previous` matches only the model's basename plus element id, or the title "Project1" for unsaved models, so it can delete another project's harvest. It also deletes before the new save succeeds (`catalogue_store.py:115-121`).
  *Done: entries record `source.model_id`. A match needs the same saved file name and, when both entries have one, the same `model_id` (review V4: projects from one template can share it). Unsaved models never match. The new entry is saved before the old one is removed.*
- [x] **C4.** Catalogue numbers are counted per folder, but Pull shows several folders together. On a shared drive the numbering races, and the JSON writes aren't atomic (`catalogue_store.py:132-173`).
  *Done: `_write_text` writes a temporary file, then replaces the file in one step (`os.replace`, or `File.Replace` in IronPython). Harvest All Units holds `catalogue_lock` while it numbers: the lock carries an owner token, is touched for each unit, and stale locks are judged by the shared folder's clock. The same number can still mean different shapes in different folders, since Pull shows several; that stays open as K17.*
- [x] **C5.** The MCP `harvest_layout` docstring says a re-harvest replaces the earlier one. It doesn't. Two harvests in the same second overwrite each other (`mcp/pdso_mcp_server.py:1359`, `layout_entry.py:226-229`).
  *Done: `-2`, `-3`... suffix; MCP docstring corrected.*
- [x] **C6.** `POST catalogue/harvest` takes an undocumented `folder` that can be any path (`catalogue_handlers.py:120-131`).

  *Done: the route ignores `folder`.*
## K. Consistency

- [x] **K1. The ribbon and the API make different models.** `bootstrap_service.py:165-213, 444-614` still does three things itself: it builds a placeholder family, adds NSF and ZFA plans (which `views.json` disables), and falls back to a hardcoded "plain" plan that hides views.json errors. Also, `show_program_volumes` creates "PDSO - Program Volumes" while Quick Setup makes "PDSO - Program Volumes - 3D".
  *Done:*
  - Bootstrap adds nothing of its own.
  - The placeholder Data Silo family moved into the families step (`placeholder_if_missing` in families.json), so Quick Setup builds it too in an older Revit.
  - The extra NSF and ZFA plans are gone: those plans are made when Areas are drawn in them.
  - `ensure_area_plan` raises when views.json can't be read or its plan can't be made. A plain plan is only made for a scheme views.json has no entry for.
  - `setup_status` expects the plans views.json turns on.
  - The API's 3D view is now "PDSO - Program Volumes - Colored": it ghosts the building, so it can't share the working view's template. An old view with the previous name is renamed. API 1.8.
- [x] **K2. Business logic in the wrong layer:**
  - `1.HarvestUnits.pushbutton/script.py` holds unit finding, naming, numbering, replacement and images; the API `harvest_layout` has none of it.
  - The MCP holds the Room→Area outline algorithm, `render_plan`'s point-in-polygon test, the setup default levels and the crossing-items check.
  - The `documents/*` handlers call the Revit API directly; `documents/open` has no dialog suppression.
  - `run_sync` gating is in the handler.
  *Partly done:*
  - `documents/*` now go through `model_service`, and `documents/open` answers dialogs (K7).
  - The sync gate and option checks are in `sync_service.run_with_preflight` and `check_options`; the handler only adds the Program Volume summary.
  - Harvest All Units: done with F1. `catalogue_api_service.harvest_units` does naming, ids, catalogue numbers under the lock, replacement, family files and plan images. The button, Harvest Layout (`harvest_layout`, which never replaces) and the new `POST catalogue/harvest-units` / MCP `harvest_units` all use it. API 1.9.
  - The MCP's Room logic, reviewed from first principles (PDSO uses Areas only, never Revit Rooms; decided with Matthew 2026-11-17):
    - Room→Area drafting (`draft_area(s)_from_room(s)`, `list_rooms`, `GET rooms`) is removed. Its useful part is now `area_drafting_service.derive_area(s)` (`POST areas/derive`, MCP `derive_areas`): an Area in another scheme from an Area, moved in by an offset with Revit's `CurveLoop.CreateViaOffset`.
    - `setup_model` uses Quick Setup's default levels; `show_view` fit has no Room fallback.
    - The crossing-items check uses the API's `across` flag instead of its own geometry.
  - The catalogue works on units (an Area with a data silo, in the GSF scheme), named by Program UID or Area/data silo id; Room targets are refused. Edge kinds come from the neighbouring Areas, unit counts from the data silo. `room`/`similar-rooms`/`room-items` became `unit`/`similar-units`/`unit-items` (old HTTP names kept until the next API version).
- [x] **K3.** Project Config bypasses `config_service`, so its saves skip the service's validation. It also repeats `run_model_checks` (`body_1_setup.py:643-766, 1306-1365`).
  *Done: Save calls `config_service.update_config`, so the window gets the API's checks: UID pattern, extrusion height, level keys and levels, and area scheme mapping parameters. A refused save names the problem and saves nothing. The window's copy of the model checks had no button or bindings in its XAML, so it is removed.*
- [x] **K4.** There are four point-in-polygon implementations; only `correlation_service` handles holes. The others are `program_service._outline`, `visualization_service.program_by_area` and `area_drafting_service`, plus the MCP's `_inside`.
  *Done:*
  - `layout_geometry.point_in_polygon` is the one ray-casting test in lib; `revit_helpers.point_in_polygon` calls it.
  - New `point_in_area` (outline minus holes) and `outline_and_holes` helpers. `program_service.containing_areas`, its Area placement, `visualization_service.program_by_area`, `move_area` and the MCP's `render_plan` now leave out holes.
  - `correlation_service` keeps its winding-number test on Revit curves, which handles holes and arcs.
  - The MCP keeps its own copy, since it can't import lib.
- [x] **K5. Docs that are stale or contradict each other:**
  - concurrency: `startup.py` and README:142 vs README:242 and AGENTS.md
  - "127.0.0.1 only" isn't enforced: it depends on pyRevit's `routes.Host`
  - `architecture.md:31`: `related_area_elements` is keyed by parameter, not by scheme
  - `architecture.md:160`: `catalogue_ui.py` doesn't exist
  - the standards README says setup doesn't create area schemes; it does
  - the user guide's Area Workset setting doesn't exist
  - "16 preflight checks" should be 17
  - the README tree omits the catalogue
  - `pyrevit-mcp.md`'s destructive-calls list predates the catalogue
  *Done: all nine fixed. startup.py and the README no longer say overlapping requests are safe. The `127.0.0.1` claims say it's pyRevit's default. The code comment in `extended_data_silo.py` that repeated the wrong key was fixed too.*
- [x] **K6.** `API_VERSION` wasn't bumped when 6e89d07 added `image` and `catalogue` to the layout replies. `image` is a local path, so remote agents can't use it. *Done: the bump to 1.6 (with B1) covers those reply changes. `GET catalogue/layouts/<id>` now also returns the image as `image_base64`, and the MCP's `get_layout` returns it as an image. The summary's `image` stays a path on the Revit computer, for local use.*
- [x] **K7.** Duplicate routes: `document/save|close` vs `documents/save|close`, with different shapes. Read-only catalogue routes use POST. Parameter names differ between `handlers.py` and `catalogue_handlers.py`, and their `_flag` helpers are duplicated.
  *Done:*
  - `document/save` and `documents/save` are one handler, as are `document/close` and `documents/close` (which takes `document` or `title`). The `documents/*` names are the main ones, and the MCP uses them.
  - The document logic moved to `model_service`, and `documents/open` answers dialogs as `document/open` does (part of K2).
  - `catalogue_handlers` uses `handlers._flag`.
  - `catalogue/recommend` and `catalogue/similar-rooms` stay POST: a body reaches the handler on every pyRevit, query parameters don't (K12). Their docstrings say why.
  - The other argument names already agree.
- [x] **K8. Transaction names without the "PDSO - " prefix:**
  - Quick Setup: "PDSO Quick Setup" and "Create view {}" etc.
  - "Assign Elements to Workset"
  - "Set Data Silo Pin Status"
  - "Set Program Volume Family Pin Status"
  *Done: Quick Setup's group is "PDSO - Quick Setup" and its step transactions get the prefix; the workset transaction is "PDSO - Assign Elements to Workset". The three `set_element_pin_status` methods had no callers since B5 and are removed. Transactions inside family documents keep their names.*
- [x] **K9. Parameters handled by name instead of GUID or registry:**
  - The sync reads and writes by name (`data_service.py:145`, `extended_data_silo.py:106`, `extended_program_volume_instance.py:235`).
  - `"PDSO - Program UID"` and `"PDSO - Program Stacking"` are hardcoded about 12 times.
  - `summarize_program_types` hardcodes gsf, nsf and zfa.
  *Done:*
  - New `data/parameter_access.py` reads and writes PDSO parameters by GUID: from the registry, then the built-in catalog; only a name with no known GUID is looked up by name.
  - The sync's push and commits use it, and so do the duplicate check and `pdso_kinds`.
  - `ExtendedDataSilo.uid` and `.stacking` replace the 12 hardcoded reads.
  - `summarize_program_types` totals the area parameters the model tracks.
- [x] **K10. Repeated defaults:**
  - The area scheme mappings default is written out three times, and the Data Silo family name and the PV prefix are also copied.
  - There are three `IFamilyLoadOptions` classes.
  - `bootstrap_service` copies `find_family` and `find_area_scheme`.
  *Done:*
  - New `lib/pdso/code/defaults.py` holds the configuration defaults. Project Config's defaults, Quick Setup, preflight, the catalogue and `setup_status` read it.
  - One `FamilyLoadOptions` in `setup/revit_utils.py`.
  - `bootstrap_service` uses the finders of `setup/revit_utils.py`.
- [x] **K11.** `ruff check .` fails: 478 E501 plus 8 real errors (`family_manager_service` F811 and 3× F841, `hashing_service` F841, E711 in the `0.Config` and `3.RunScripts` scripts, 1 W291). 30 legacy files have no `coding` header.
  *Done: the 6 errors are fixed (the E711 ones went with U1). E501 is ignored in `pyproject.toml`: the long lines are strings, comments and docstrings, and reformatting 47 files would conflict with the unmerged branches (F1–F3). The 30 files have the header. `ruff check .` passes.*
- [x] **K12.** No minimum pyRevit version is stated or checked. On 6.1 the API ignores query parameters (filters, `document`, `_rid`).
  *Done:*
  - The README states the requirement.
  - `GET status` reports `pyrevit.version` and `pyrevit.query_parameters`.
  - The MCP's `get_status` adds `pyrevit_warning` when that is false, or when a reply came back without the request id the server sent as a query parameter.
  - No exact version number is given: the first pyRevit that passes query parameters isn't known here.
- [x] **K13.** `remove_previous` and every Pull row reload the whole catalogue folder, and Pull runs full-model collectors for each row.
  *Done: entry files are read again only when their modified time or size changed (`catalogue_store._read_entry_text`). `preview_layout_in_room` takes a `lookups` dict that Pull shares across its rows, so family types, plan boxes and the Room's current items are collected once.*
- [x] **K14.** The workset option "Warn (if configured)" does nothing in phases 1–3 (`sync_service.py`); only "Fix" is acted on. Also, `pdso_kinds` (from B2) swallows a config read error and falls back to the default names.
  *Done:*
  - "Warn" reports how many Data Silos, Areas and Program Volumes are on another workset than Project Config sets, and moves nothing. Families, whose workset can't change, are left out.
  - Found while fixing this: the Sync Data window sends "Fix (if configured)" and the sync only acted on "Fix", so workset enforcement never ran from the ribbon (besides S9). Option values are normalized now; the API lists "Fix", "Warn" and "Do Nothing" and still accepts the old labels. API 1.8.
  - `pdso_kinds` logs a warning when it can't read the configuration.
- [x] **K15.** `move_area`, `scale_area` and `set_area_outline` keep the lines an Area shares with a neighbour and also draw the moved copy. The copy can split the neighbour, and the moved Area stays bounded by the kept line. Example: Areas A and B share x=10; moving A by +2 draws x=12 inside B, and A becomes 80 SF. This was already on main. **⚠ Revit**
  *Done, by refusing: `move_area` and `scale_area` refuse an Area that shares a boundary line with another Area (409, naming the neighbour). `set_area_outline` refuses only if a shared line would be left off the new outline; a shared edge that stays in place is fine. Moving both Areas' shared edge is left to Revit.*
- [x] **K16.** `move_area` picks the programs to move with a point-in-polygon test on the outer outline only, so it also moves the Data Silo of an island Area inside a hole. This was already on main.
  *Done: it uses `point_in_area` with the Area's holes (K4).*

- [x] **K17.** Catalogue numbers are per folder, but Pull shows layouts from the sample, local and chosen folders together, so one number can mean two shapes (left over from C4).
  *Done: a layout's `catalogue` now says which folder numbered it ("sample", "local" or the folder's name), and Pull shows "catalogue 3, local".*

## F. Unfinished work

- [x] **F1.** `fix/place-on-rac` is unmerged and conflicts with main in `1.HarvestUnits.pushbutton/script.py`. It overlaps with B1 and B2.
  *Done: ported onto main by hand on `fix/catalogue-harvest-place`. The parts B1/B2 had already done differently (`pdso_kinds`, PDSO objects not furniture, tagging only when placed) were left out. Kept: family files saved at harvest and loaded at place (`save_family_files`, `load_missing_families`, `loadable_families`, with `FamilyLoadOptions(keep_existing=True)`), `refine_placement`, and the mirror fix. The branch can be deleted.*
- [x] **F2.** `feature/mcp-pull-from-catalogue` is finished but unmerged, and conflicts with F1 in the `place_layout` docstring. Its delete and place aren't atomic, and `limit: 200` hides layouts ranked lower.
  *Done: ported onto main for units (`unit` argument, Program UID). `pull_from_catalogue` uses `place_layout(clear_first)`, so the delete and the place are one undo step, and ranks every layout. The branch can be deleted.*
- [ ] **F3.** `feature/lodging-unit-harvest` is a prototype that has never been run in Revit. It repeats the catalogue's heuristics, reports m², and calls `os.startfile`.
  *Open, needs a decision: it turns Revit Rooms into unit types, and PDSO now uses Areas only with no Room import (decided 2026-11-17). Either rebuild it on Areas and the catalogue's code, or drop it.*
- [x] **F4.** Duplicate Program Volume instances on one level are skipped (`correlation_service.py:492`, "To do - figure out deletion").
  *Done: the sync keeps the oldest instance on the level (lowest element id; tags most likely point to it) and deletes the others, with a warning.*
- [x] **F5.** Orphaned Program Volume families and instances are never cleaned up (architecture known gap 3).
  *Done: `program_service.orphaned_program_volumes` lists families that belong to no Data Silo (not named for a current UID, and no instance pointing to an existing Data Silo); `purge_orphaned_program_volumes` deletes chosen or all of them. API `GET program-volumes/orphans`, `POST program-volumes/purge-orphans`; MCP tools of the same names; ribbon: Shift-click Audit Report (choose, confirm). The sync still never deletes them on its own.*
- [x] **F6.** Unused schema fields: `config.last_run_timestamp` is never written after "Never"; `hashed_parameters`, `parameter_names_hash` and `parameter_values_hash` are never used.
  *Done: the sync writes "<time> <user>" to `last_run_timestamp` for each phase that completed. The two unused hash methods (and their signatures) are removed. `hashed_parameters` stays in the schema, marked unused: a schema can't change in models that store it.*
- [ ] **F7.** `extended_data_silo.py:126` TODO: the multi-area fallback.
  *Open, needs a decision: which Area should shape a Program Volume when there is no Area mapped to PDSO - GSF (today: the smallest Area that holds the Data Silo).*
- [x] **F8.** Quick Setup doesn't check that Project Config and Parameter Manager ran first.
  *Done: the button says what's missing (`runner.missing_prerequisites`) and asks before it runs. The API's bootstrap runs both first, so it needs no check.*
- [x] **F9.** `parameters.json` keeps keys (`categories`, `binding`, `group`, `required`) that nothing reads.
- [x] **F10.** The acceptance runner doesn't cover `catalogue/*`, `documents/*`, `selection`, `views/activate` or `color-areas`.
  *Done: new steps for empty2 after V1: X1 (`areas/derive`), V2 (`views/activate`, `selection`), O1 (orphans and purge), C1 (catalogue: unit items, harvest, similar units, recommend, preview, place with clear_first, older names, Room number refused), D1 (`documents/*`, background document, 409s). `color-areas` was already covered by A8. Not run yet.*
- [ ] **F11.** The Exterior Space and Exterior Space Type parameters aren't built (`docs/parameter-reference.md`).
  *Open, needs a decision: build them as built-in parameters, or take them out of the reference.*

## D. Dead code

- [ ] **D1.** `body_1_setup.run_model_checks` and the six `check_*_status` properties: there is no button for them in the XAML.
- [x] **D2.** `PickerWindow`, `RoomIndex.picker_rows` and `layout_sketches`, left over from the Pull merge.
  *Done: all three removed (with `RoomIndex`, replaced by `UnitIndex`).*
- [ ] **D3.** `process_controller.py`, an empty shell built for every window.
- [ ] **D4.** `data_bound_example.py` and its `.xaml`.
- [ ] **D5.** In the sync code:
  - `family_manager_service.convert_area_and_insert_as_directshape`
  - `_enumerate_expected_program_volume_instances`
  - the broken `filter_by_level_int` and `find_id_int`
  - `reset_stale_data`
  - `config_service.WORKSET_KEYS`

## N. Naming

- [ ] **N1.** The Sync Data window is titled "Harvest Areas", and its files are `body_2_harvest_areas` and `2_harvest_areas.xaml`. The Project Config window is titled "Setup".
- [ ] **N2.** Tooltips: "Program Mass" and the typo "configuation". `4.Audit` has an empty `help_url`, and `extension.json` has an empty `image`.
- [x] **N3.** The shipped shared parameter file still says "Model Setup"; regenerating it changes the file.
- [ ] **N4.** Two catalogue buttons share the prefix `1.`. Their `config.py` files are identical copies.

## Checked and clean

- All 20 shared parameter GUIDs match across the constants, `parameters.json` and the `.txt`, and none changed since the initial commit. The ES schema GUIDs are unchanged.
- No Py3-only syntax in IronPython code. No Revit or `pdso` imports in `mcp/` or `tools/`.
- All 61 MCP tools call real routes with the right method, path and parameter names. `API_VERSION` and `EXPECTED_API_VERSION` match.
- `area_scheme_mappings` is {parameter: scheme} at every use.
- Modeless windows write through ExternalEvents.
- `setup.json` step ids match `STEP_RUNNERS`.
