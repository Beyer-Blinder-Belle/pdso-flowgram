# Unit Layout Catalogue

Reusable unit layouts, harvested from finished projects and placed into new ones. **The unit is an apartment**: its partitions, doors, rooms, furniture, kitchen and bathroom fittings. The approach follows Oliver Green, *An Introspective Approach to Apartment Design* (AHMM, 2020).

```
Harvest  ->  Store  ->  Recommend  ->  Create
```

| Step | Where | What it does |
| --- | --- | --- |
| **Harvest** | `Harvest All Units` button | Open an area plan and run: **every Area in it with a PDSO data silo inside is harvested, with nothing to pick.** To harvest something else (Rooms, Areas with no silo), select them first. The unit type comes from the data silo, else from the rooms inside (bedroom count for a dwelling, the room's name for a one-room unit); the name is made from the type, size and Area number. Running again replaces the earlier harvest of the same unit. Each unit's free-standing families are saved **in the room's own frame**: a bounding rectangle lined up with the direction most of the room's walls run in, origin at its corner, X along the longer side. **What surrounds the layout is saved too** (see below). |
| **Harvest one Room** | `Harvest Layout` button | Select a Room, or objects that are in one Room; the form is then filled in (Room, layout name, program, project type) and shows a preview of the Room. You can also type a Room number. The free-standing families of the Room are saved **in the room's own frame**: a bounding rectangle lined up with the direction most of the room's walls run in, origin at its corner, X along the longer side. **What surrounds the layout is saved too** (see below). A second window shows the sketch, the items and the file. |
| **Store** | this folder | One `<id>.json` per layout, with an `<id>.svg` plan sketch beside it. `Harvest All Units` also saves `<id>.png`: the plan of the unit as the model shows it, with tags and Area lines hidden. The image is black on white and trimmed to the unit. `Pull from Catalogue` shows it beside each suggested layout (hover for a large view). **Each entry also gets a catalogue number**: units with the same shape (turned, mirrored or of other proportions) share one number, so two L-shaped classrooms are catalogue 1 and a rectangular one is catalogue 2 |
| **Recommend** | `Pull from Catalogue` button | Puts a catalogue layout into the selected Room. Select the target Room or Area, or objects that are in one Room; the button is grey with no selection, because it needs a Room to compare the layouts with. A window shows the Room as it is now as the first row, and below it the best 5 layouts that fit, ranked, with a plan of each layout in the Room. The target does not have to be harvested. Every layout is tried in all 8 orientations (4 turns, with and without a mirror) and ranked: 60% area match, 40% how snugly it fits. Same-program layouts come first. A layout is offered only if every item lands inside the target, or no more than 6 in. outside it (a family against a wall has its insertion point on the boundary). If more than one orientation fits equally, the one whose room outline is nearest to the target outline is used. |
| **Create** | `Pull from Catalogue` button | Places the chosen layout and selects the placed items. An option deletes the furniture that is in the Room first, in the same undo step; data silos and Program Volumes are never deleted. If any item was placed, the layout id is written to `PDSO - Unit Layout Type` on the PDSO data silo in the room, unless that field already has a value that a user typed |

The windows are in `lib/pdso/code/catalogue/catalogue_windows.py`. The buttons call `catalogue_api_service`, the same code as the HTTP API and the MCP server. A test with the three buttons on the Snowdon sample placed the layout of Office Unit 401 into Office Unit 301 (19 of 19 items, same arrangement).

## Without the buttons: HTTP API and MCP tools

The same four steps are in the HTTP API (`/pdso/catalogue/...`) and in the MCP server, so that an agent can use the catalogue. They take a Room number or an element id in place of a pick in the Revit window.

| MCP tool | What it does |
| --- | --- |
| `harvest_layout` | Saves the layout of a Room or Area. Gives the JSON entry and its file path |
| `list_layouts`, `get_layout` | Lists the catalogue. Gives one entry with its JSON and its SVG sketch |
| `recommend_layouts` | Ranks the layouts for one target Room |
| `find_similar_rooms` | Ranks the Rooms of a model for one layout. `similarity` is the average of the area match and the outline match (1.0 is the same shape and size) |
| `preview_similar_rooms` | Shows images of the best 5 Rooms that `find_similar_rooms` suggests, before anything is placed: a ranked list (thumbnail, Room, level, area, area match, shape match, match in percent, fits or not) and one large plan for each Room. Each plan shows the Room outline, the items of the layout where `place_layout` would put them, the furniture that is in the Room now, and the outline of the source room. An item that goes across a wall is amber. The model does not change (route `GET catalogue/preview`) |
| `place_layout` | Places a layout into a target Room |
| `list_room_items`, `clear_room_items` | Lists (with the height above the level) or deletes the free-standing families in a Room |
| `describe_item` | Describes one family instance: position, flips, plan box, level and instance parameters. Use it to compare a placed item with the original item (route `GET catalogue/item`) |
| `delete_layout` | Deletes a layout file (not a sample) |

### Test on the Snowdon Towers sample

A test on a copy of the Autodesk sample *Snowdon Towers Sample Architectural* (Revit 2027) used the MCP tools only. The sample has 7 groups of units with the same plan on different floors (19 Rooms).

- **Harvest:** 29 furnished Rooms gave 29 layouts with 352 items. 58 wall-hosted families were skipped.
- **Find similar rooms:** for the layout of one Room of each group, the other Rooms of the group were first in the list, in 7 of 7 groups (12 of 12 Rooms, similarity 0.994 to 1.000).
- **Recommend:** for each of the 12 target Rooms, the layout of its group was first.
- **Place:** each of the 12 target Rooms was emptied, and the layout was placed. All 112 items were placed. In 10 Rooms each item was at the position of the furniture that was there before (0.01 ft or less). In the 2 Rooms whose plan is a little different from the source Room, the items were 0.03 ft and 0.25 ft or less from it.

**Three defects in that test, now corrected.** The test compared the plan position (X, Y) of the insertion points only. A later check of height and plan box found these defects in `place_layout`:

1. *Height.* An item that has no height offset went to two times the level elevation (for example 32.17 ft above L4), thus it was not visible in the floor plan. `place_layout` now always sets the height above the level.
2. *Size.* Instance parameters that a family defines (the length of a counter top, the position of a sink hole) were not stored. A new instance got default values. The harvest now stores them in `parameters` of each item, and `place_layout` sets them.
3. *Mirror.* A source instance that is mirrored, of a family with no flip control, was not mirrored. `place_layout` now mirrors such an instance in place.

`list_room_items` gives `z_offset`, and `describe_item` gives the plan box, to make these checks possible. A new run gave a height difference of 0.00 ft and a plan-box difference of 0.000 ft in Rooms 404, 403, 407, 202 and 409. The saved test model `snowdon_catalogue_sim.rvt` and the 29 test layouts (no `parameters`) are from before the corrections.

**A Room that is not a twin.** The layout of Office Unit 401 (19 items) was placed into Office Unit 301 (similarity 0.960, 92 different items before): 19 of 19 items placed, with the arrangement of Room 401 (one shift of 1.07 ft for all items, because the layout is centred in the larger Room).

The preview footprints use the size of an instance of the same family type that is in the model. A family with an instance length (a counter top) can have a different size.

## Folders

| Folder | In git? | Holds |
| --- | --- | --- |
| `sample/` | yes | **Made-up** layouts, so the tool can be tried without harvesting anything |
| `local/` | **no** (`.gitignore`) | Where harvests go by default. **Layouts from real projects are client and firm data; keep them out of this public repo.** |

Shift-click **Harvest All Units** or **Harvest Layout** to save to another folder instead, such as a shared office drive. **Pull from Catalogue** reads `sample/`, `local/` and the chosen folder.

## What Harvest records about the unit

A layout only suits a space like the one it came from: entry on the same side, windows on the same side, same shape. Harvest saves these under `features` in each JSON. The paper lists them as what designers match on.

| The paper's feature | In the JSON (`features.`) | How it is read |
| --- | --- | --- |
| Entry location | `entry`: point, edge and direction into the room | The door to the corridor if there is one, else the widest door |
| External wall location | `edges[].kind == "exterior"` | Points are probed just beyond each wall: no room there and an Exterior or curtain wall |
| Amount of window wall | `window_wall_length`, `glazed_length` | Exterior edge length; window widths plus curtain wall length |
| Overall shape | `shape_matrix` | 10 x 10 grid over the room frame, 1 where the cell is inside the room |
| Shape category | `shape.category`: `rectangle`, `L`, `T`, `Z`, `U`, `other` | The outline's corners written as C (convex) or R (reflex), put in one canonical order. **The same for a unit however it is turned, mirrored or stretched**: every L-shaped unit is `CCCCCR`. Corners under 1 ft apart are merged |
| Size | `area`, `width`, `depth`, `perimeter` | |
| Wider context | `edges[].kind`: `corridor`, `party`, `unroomed`, with `beyond` (the room's name) | The room found beyond each wall |
| Internal elements | `internal_walls`, `internal_doors`, `rooms`, `items` (free-standing families), `nested_items` (the desks and chairs inside a family that holds a whole furniture layout), `hosted_items` (wall- and face-hosted) | Walls on the unit's level whose middle is inside the unit; doors hosted in them; Rooms standing inside |
| Metadata (mix, tenure) | `unit`: bedrooms, bathrooms, kitchens (counted from room names) and `pdso`, **every** `PDSO - ...` parameter on the unit's data silo as a list of `{key, value, display}` (`value` is null when empty; `display` carries the unit) | The data silo standing inside the unit |

The paper saves each layout 8 times (rotated and mirrored). Here one copy is saved and the 8 orientations are tried when matching; `layout_geometry.orient_matrix` turns a shape matrix the same way `orient_point` turns a point.

**Not used by Recommend yet.** The score is still area and fit. The paper's score is `1 - sum(w * |A - B|) / sum(w)` over these features, dropping anything under 0.8, after first discarding layouts whose area or frame size is more than 5% off.

**Units.** Every number is in Revit's internal units, and each file says so: `"units": {"length": "ft", "area": "ft2", "angle": "rad"}`. A metric model is harvested in feet too, so entries from any model compare directly.

**Source model.** `source.model` is the Revit file's title, `source.model_path` its file name (not the full path, which would carry the user's folders), and `source.model_id` the UniqueId of the model's Project Information. Harvest All Units replaces an earlier entry of the same unit only when both models were saved to files with the same name and, if both entries have one, the same `model_id`.

Files written before this (`schema_version` 1) still load; they have empty `features`.

## Limits

- Only free-standing (level-based) families are placed. Wall- and face-hosted families (wall cabinets, vanities) are recorded in `hosted_items` with the wall they sit on, but not placed yet. Ceiling-hosted lights are not read.
- A wall's Function is often left at Revit's default (Exterior), so a wall facing a space with no Room in it can read as exterior.
- An Area's boundary is a line, not a wall. The wall is found by looking for a straight wall on the same level running along the line; where none is found, an edge is called exterior only if a window sits in it.
- Only straight walls based on the unit's level are read. Curved partitions are missed.
- **Pull from Catalogue does not rebuild partitions, doors or rooms yet.** It places free-standing families only.
- Walls, doors and windows in a linked model are not read.
- A family not loaded in the target model is reported as missing, not loaded automatically.
- The layout is centred in the target room. It does not yet align to a door or a teaching wall.
- Only the room's outer boundary is used. Columns and holes are ignored.
- A fit is checked with the insertion point of each item only. The body of an item can go across a wall of a Room with a different plan (`preview_similar_rooms` shows this in amber).
- An item whose insertion point is on the Room boundary is placed, but `list_room_items` and a later harvest of that Room do not include it.

## Code

`lib/pdso/code/catalogue/`. The geometry, entry format, storage and recommender have no Revit imports, so they can be read and tested outside Revit.
