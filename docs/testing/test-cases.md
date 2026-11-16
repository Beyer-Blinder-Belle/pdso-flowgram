# Test cases and edge cases

A running list of what to test in Revit, and the test model that makes each case possible. Add a case whenever a fix or a new feature needs one. IDs in brackets (B3, R1, K15) point to [docs/audit/2026-11-weekend-audit.md](../audit/2026-11-weekend-audit.md).

Status: `[ ]` not tested · `[x]` passed (give date and Revit version) · `[!]` failed (link the issue) · `[~]` can't test yet (say what's missing).

---

## 1. The test model

One Revit 2026 model built to hold every case below. Tick each item when the model has it, and note its id or name so the steps can refer to it.

### Levels and stacking
- [ ] **M-L1.** Four occupied levels with stacking keys `01`–`04`, and a Roof level with no key.
- [ ] **M-L2.** One level with a key that contains a hyphen, for example `B1-03` [S7].

### Areas (in the GSF scheme, plus NSF and ZFA where a case says so)
- [ ] **M-A1.** A plain rectangle Area, about 20 × 30 ft.
- [ ] **M-A2.** An Area with a hole: a rectangle with a smaller rectangle hole inside.
- [ ] **M-A3.** An Area with an arc edge, for example one curved side.
- [ ] **M-A4.** An Area with both a hole and an arc edge.
- [ ] **M-A5.** Two Areas side by side that share one boundary line [K15].
- [ ] **M-A6.** An "island" Area inside the hole of another Area, each with its own Data Silo [K16].
- [ ] **M-A7.** An unplaced Area and a redundant (not enclosed) Area.

### Data Silos (programs)
- [ ] **M-D1.** A Data Silo on one level, no stacking.
- [ ] **M-D2.** A Data Silo stacked over several levels: `01-03`.
- [ ] **M-D3.** A Data Silo with no Program UID [S2].
- [ ] **M-D4.** Two Data Silos with the same UID.
- [ ] **M-D5.** A Data Silo whose UID breaks the `uid_pattern`.
- [ ] **M-D6.** A Data Silo with a typed `PDSO - Unit Layout Type` ("2BR - Type A") [B8].

### Program Volumes
- [ ] **M-P1.** Two Program Volume instances of one family on the same level [R2, F4].
- [ ] **M-P2.** A Program Volume unpinned by hand among pinned ones [B5].
- [ ] **M-P3.** An orphaned Program Volume: its Data Silo was deleted [F5].

### Rooms and furniture (Unit Layout Catalogue)
- [ ] **M-R1.** A furnished Room that also holds a Data Silo and its Program Volume [B1].
- [ ] **M-R2.** A Room with Generic Model furniture, such as a plant or a planter [B2].
- [ ] **M-R3.** A catalogue layout harvested in a *different* project, using families that this model doesn't have loaded [R3].
- [ ] **M-R4.** An unfurnished unit, harvested so the catalogue has an entry with no items [C2].
- [ ] **M-R5.** Two units that would get the same layout id in one Harvest All Units run (same program, name and second) [R4].

### Model settings
- [ ] **M-S1.** A workshared copy of the model with the worksets named in Project Config [K14, S4, S9].
- [ ] **M-S2.** A new, empty model with no PDSO configuration [U5].
- [ ] **M-S3.** A second Data Silo family (a copy of the shipped one, renamed) loaded and chosen in Project Config [U3].
- [ ] **M-S4.** Revit 2027 installed, for the version cases [U2].
- [ ] **M-S5.** A catalogue folder on a shared drive that two people can reach [C4].

---

## 2. Cases

### Sync Data

- [ ] **T-S1. Stacking typo keeps Program Volumes** [B3]
  Model: M-D2.
  Steps: sync, then change the stacking from `01-03` to `01-0X`, then sync again.
  Expected: the instances on 02 and 03 stay. The sync log warns that they were kept. Fixing the stacking and syncing again leaves no duplicates.
- [ ] **T-S2. Unmapped stacking key** [B3, R5]
  Model: M-D2.
  Steps: change the stacking to `02-99` (99 has no level), move the Data Silo, then sync.
  Expected: nothing is deleted. The instances on 01 and 02 move with the new geometry. Any instance on another level stays where it is.
- [x] **T-S3. Geometry "No" keeps Program Volumes on their geometry** [R1, B6]
  Model: M-D1.
  Steps: move a Data Silo, change one of its values, then sync with geometry "No" and data "Yes".
  Expected: the Program Volumes don't move away from their (unchanged) shape, and they get the new value.
  *Passed 2026-11-16 (reported by Matthew).*
- [ ] **T-S4. One bad Data Silo doesn't stop the others** [B7]
  Model: M-D1, plus a second Data Silo with a stacking typo.
  Steps: move both Data Silos, then sync.
  Expected: M-D1's Program Volumes move with it.
- [ ] **T-S5. Duplicates on one level move** [R2]
  Model: M-P1.
  Steps: move the Data Silo, then sync.
  Expected: both duplicates move. The log warns about the duplicate.
- [ ] **T-S6. Pinning: Maintain** [B5]
  Model: M-P2.
  Steps: sync with Program Volume pinning set to "Maintain".
  Expected: the hand-unpinned instance stays unpinned; the others stay pinned.
- [ ] **T-S7. Pinning: Unpin and Pin** [B5]
  Steps: sync with "Unpin", then sync with "Pin". Do the same for Data Silos and Areas (the Area pinning option).
  Expected: everything is unpinned after the first sync and pinned after the second. Unplaced and redundant Areas (M-A7) are skipped without an error.
- [ ] **T-S8. Data Silo with no UID** [S2]
  Model: M-D3.
  Steps: run preflight, then sync with Force if it's blocked.
  Expected: phase 2 doesn't stop. Record what actually happens.
- [ ] **T-S9. Hyphenated level key** [S7]
  Model: M-L2.
  Steps: set stacking `01, B1-03`, then sync.
  Expected: `B1-03` is one level. `B1-03-B1-05` (if three B1 keys exist) is a range. [Fixed: S7]
- [ ] **T-S11. Family documents are closed** [S1]
  Model: M-D1, M-D2.
  Steps: move both Data Silos, sync, then `list_open_documents` (or the Revit window list).
  Expected: no Program Volume family documents are left open.
- [ ] **T-S12. Phase 1 fails** [S3]
  Model: a model with a PDSO configuration and no Data Silos.
  Steps: sync.
  Expected: the window says the sync finished with errors and names phase 1. Phases 2 and 3 show as skipped, with no unbound-variable error in the log.
- [ ] **T-S13. Worksets are enforced** [S9]
  Model: M-S1.
  Steps: put a Data Silo, an Area and a Program Volume on the wrong workset, then sync with the workset options set to "Fix".
  Expected: all three move to the workset named in Project Config. Record whether Program Volume *families* accept the change (they may not).
- [ ] **T-S14. The sync log comes back over the API** [A6]
  Steps: `run_sync` on a model with one warning (for example M-D5).
  Expected: the reply's `log` lists the warning.
- [ ] **T-S10. Refresh in the Sync Data window** [S4]
  Model: M-S1.
  Steps: open Sync Data and click Refresh.
  Expected: no "Script Error" on check 2.

### Area editing (API / MCP)

- [ ] **T-A1. Move an Area with a hole and an arc** [B4]
  Model: M-A4.
  Steps: `move_area` by (5, 0).
  Expected: the hole and the arc move with the Area, and the arc is still an arc (not short straight lines). The area value is unchanged.
- [ ] **T-A2. Scale an Area with a hole and an arc** [B4]
  Model: M-A4.
  Steps: `scale_area` with factor 1.5.
  Expected: the area value is ×2.25, the arc is still an arc, and the shape stays centred on the same point.
- [ ] **T-A3. Move an Area together with its program** [B4]
  Model: M-A1 with M-D1 inside.
  Steps: `move_area` by (5, 0), then one Undo in Revit.
  Expected: the Area and the Data Silo both move; one Undo puts both back.
- [ ] **T-A4. New outline still contains the hole** [R6]
  Model: M-A2.
  Steps: `set_area_outline` with a slightly larger rectangle.
  Expected: the hole stays.
- [ ] **T-A5. New outline leaves the hole outside** [R6]
  Model: M-A2.
  Steps: `set_area_outline` with a rectangle that cuts through the hole.
  Expected: the hole's lines are removed, with a warning in the reply.
- [ ] **T-A6. Move a vertex of an Area with an arc** [B4]
  Model: M-A3.
  Steps: `move_area_vertex`.
  Expected (documented): the outer arc becomes straight segments.
- [ ] **T-A7. Move an Area that shares an edge** [K15]
  Model: M-A5.
  Steps: move one Area by 2 ft towards the other.
  Expected (known failing): the neighbour is split and the moved Area shrinks. This test is for the K15 fix.
- [ ] **T-A8. Move an Area around an island** [K16]
  Model: M-A6.
  Steps: move the outer Area.
  Expected (known failing): the island's Data Silo moves too.
- [ ] **T-A10. A failed create leaves no area plan** [A10]
  Steps: on a level with no area plan for a scheme, `draft_area` with an outline over an existing Area (so Revit gives 0 SF).
  Expected: an error, and no new area plan in the Project Browser.
- [ ] **T-A11. Color an Area with a hole** [A11]
  Model: M-A2.
  Steps: `color_areas`, then one Undo in Revit.
  Expected: the hole isn't filled. One Undo removes the new colors and brings back the previous ones.
- [ ] **T-A9. Bad numbers** [A8]
  Steps: call `move_area` with `dx="abc"`, and `scale_area` with factor 0.
  Expected: a 400 error with a plain message, not a 500.

### Unit Layout Catalogue

- [ ] **T-C1. Clear and place keeps the PDSO objects** [B1]
  Model: M-R1.
  Steps: Pull from Catalogue with "Delete the furniture" on.
  Expected: the Data Silo and the Program Volume stay, and one Undo brings the old furniture back.
- [ ] **T-C2. Clear and place, families missing** [R3]
  Model: M-R3.
  Steps: Pull that layout into a furnished room with "Delete the furniture" on.
  Expected: an error says no item could be placed and names the missing families. The room's furniture is still there.
- [ ] **T-C3. Generic Model furniture is harvested** [B2]
  Model: M-R2.
  Steps: Harvest Layout, and also Harvest All Units.
  Expected: the plant is in the layout. The Data Silo and Program Volume are not, and they are hidden in the plan image.
- [ ] **T-C4. A typed Unit Layout Type is kept** [B8]
  Model: M-D6.
  Steps: Pull a layout into its room.
  Expected: "2BR - Type A" stays, and the result warns that it was kept. With the field empty, the layout id is written. Pull again: the old id is replaced.
- [ ] **T-C5. Layout ids are not program names** [B8, R4]
  Model: M-R5, then T-C4.
  Steps: Pull the sample layout (`sample--classroom-28-seats`) and a `-2` layout into units, then run Harvest All Units.
  Expected: the suggested program names are not layout ids.
- [ ] **T-C6. Empty layouts** [C2]
  Model: M-R4.
  Steps: Pull into a room of a similar size.
  Expected: the empty layout is not offered; `recommend_layouts` lists it under "rejected" with "the layout has no items to place". [Fixed: C2]
- [ ] **T-C8. Re-harvest replaces only the same unit** [C3]
  Steps: run Harvest All Units twice in one saved model. Then run it once in an unsaved model ("Project1"), and once in another unsaved model with the same unit ids.
  Expected: the second run in the saved model replaces its first entries. The two unsaved models keep their own entries.
- [ ] **T-C9. Two people harvest into one folder** [C4]
  Model: M-S5.
  Steps: two people run Harvest All Units into the shared folder at the same time.
  Expected: one waits, or gets "Someone else is saving to the catalogue folder". No two shapes share a number, no JSON is half written, and no `.catalogue.lock` is left behind.
- [ ] **T-C10. Two harvests in one second** [C5]
  Steps: call `harvest_layout` twice quickly on the same Room with the same name.
  Expected: two layouts; the second id ends in `-2`.
- [ ] **T-C7. Plan image failure** [C1]
  Steps: Harvest All Units on a view where a crop fails (if one can be found).
  Expected: the rest of the images still export, and nothing is left open.

### Setup and ribbon

- [ ] **T-U1. Two windows** [U1]
  Steps: open Project Config, then click Sync Data (and the other way round).
  Expected: each button opens its own window, titled as the ribbon.
- [ ] **T-U2. Revit 2027** [U2]
  Model: M-S4, a new model.
  Steps: Quick Setup, twice.
  Expected: the Rvt26 family loads (upgraded) and is set as the Data Silo family. The second run skips it.
- [ ] **T-U3. A chosen Data Silo family** [U3]
  Model: M-S3.
  Steps: Quick Setup.
  Expected: the shipped family isn't loaded. The report keeps the chosen family and adds the tracked parameters to it.
- [ ] **T-U4. Locked parameters from the shipped file** [U4]
  Steps: add PDSO - GSF to a project by hand from `lib/pdso/standards/PDSO Shared Parameters.txt`.
  Expected: the parameter can't be edited in the Properties palette, and its tooltip says "Do not edit".
- [ ] **T-U5. Opening a window doesn't change the model** [U5, S4]
  Model: M-S2, and also a configured model.
  Steps: open Project Config and Parameter Manager, close them without saving, then check Undo.
  Expected: nothing to undo. In M-S2, Project Config shows the defaults marked as unsaved, and Save creates the configuration.
- [ ] **T-U6. Quick Setup keeps template changes** [U6]
  Steps: show a hidden category in "PDSO - 3D - Program Volumes", then run Quick Setup again.
  Expected: the template keeps the change, and the report says Skipped.

### API and MCP

- [ ] **T-P1. Target document** [A1, A2]
  Steps: open two projects, `set_target_document` to the background one, then `list_programs`, `select_programs` and `show_view`.
  Expected: `list_programs` works on the background document. `select_programs` and `show_view` return a 409 that says to activate the document (they never select in the wrong one). `show_program_volumes` returns its image with `not_shown_reason`. [Fixed: A1, A2]
- [ ] **T-P3. UID pattern** [A7]
  Steps: `update_config` with the `uid_pattern` "(". Then, with a valid pattern, `create_program` with a UID that only *starts* with a match.
  Expected: the bad pattern is refused (400) and not saved. The UID is refused, and preflight check 9b warns about it.
- [ ] **T-P4. Config with a document in the body** [A5]
  Steps: `PUT config` with `{"document": "<title>", "program_volume_extrusion_height": 9}`.
  Expected: saved on that document.
- [ ] **T-P5. Number filters** [A12]
  Steps: `list_programs` with a filter on a number field, for example `du_bedroom_count=1`.
  Expected: programs with 1 (stored as 1.0) are listed.
- [ ] **T-P6. A parameter that fails stays tracked** [A9]
  Steps: make one tracked parameter fail to set up (for example a name clash with a project parameter), then `create_parameter` for another one.
  Expected: 200 with "Error: ..." warnings, and the failing parameter is still tracked.
- [ ] **T-P7. SSH drop during a write** [A3, A4]
  Steps: with `PDSO_SSH_HOST` set, stop the tunnel during a long `run_sync`.
  Expected: the tool says the change may have been made and does not send it again. A timed-out write says Revit may still make it.
- [ ] **T-P2. API version**
  Steps: `get_status`.
  Expected: API 1.7 with no version warning.

---

## 3. Results log

Record each test session here: date, tester, Revit version, model file, the cases run and what failed.

| Date | Tester | Revit | Cases | Notes |
|---|---|---|---|---|
| 2026-11-16 | Matthew | 2026 | T-S3 | Passed |
