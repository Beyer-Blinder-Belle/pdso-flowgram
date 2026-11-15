# Unit Layout Catalogue

Reusable room layouts (furniture, casework, equipment), harvested from finished projects and placed into new ones.

```
Harvest  ->  Store  ->  Recommend  ->  Create
```

| Step | Where | What it does |
| --- | --- | --- |
| **Harvest** | `Harvest Layout` button | Select a Room, or objects that are in one Room; the form is then filled in (Room, layout name, program, project type) and shows a preview of the Room. You can also type a Room number. The free-standing families of the Room are saved **in the room's own frame**: origin at the corner of its tightest bounding rectangle, X along the longer side. A second window shows the sketch, the items and the file. |
| **Store** | this folder | One `<id>.json` per layout, with an `<id>.svg` plan sketch beside it |
| **Find** | `Find Rooms` button | Choose a layout in a table with a plan for each row and a search filter (the newest is first). A window shows the Room that the layout came from as the first row, and below it the best 5 Rooms of the model, ranked, at the same plan scale: a plan of the layout in each Room, the Room data, the area match, the shape match, the match in percent, and if the layout fits. You can place the layout in a Room that fits. |
| **Recommend** | `Place Layout` button | Choose the target Room in a table with a plan for each Room and a search filter (a selection in the model selects its Room). A window shows the Room as it is now as the first row, and below it the best 5 layouts that fit, ranked, with a plan of each layout in the Room. Every layout is tried in all 8 orientations (4 turns, with and without a mirror) and ranked: 60% area match, 40% how snugly it fits. Same-program layouts come first. A layout is offered only if every item lands inside the target, or no more than 6 in. outside it (a family against a wall has its insertion point on the boundary). If more than one orientation fits equally, the one whose room outline is nearest to the target outline is used. |
| **Create** | `Find Rooms` or `Place Layout` button | Places the chosen layout and selects the placed items. An option deletes the furniture that is in the Room first. Then the layout id is written to `PDSO - Unit Layout Type` on any PDSO data silo in the room |

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

Shift-click **Harvest Layout** to save to another folder instead, such as a shared office drive. **Place Layout** reads `sample/`, `local/` and the chosen folder.

## Version 1 limits

- Only free-standing (level-based) families are harvested. Wall-, face- and ceiling-hosted families (doors, wall cabinets, whiteboards, lights) are listed as skipped.
- A family not loaded in the target model is reported as missing, not loaded automatically.
- The layout is centred in the target room. It does not yet align to a door or a teaching wall.
- Only the room's outer boundary is used. Columns and holes are ignored.
- A fit is checked with the insertion point of each item only. The body of an item can go across a wall of a Room with a different plan (`preview_similar_rooms` shows this in amber).
- An item whose insertion point is on the Room boundary is placed, but `list_room_items` and a later harvest of that Room do not include it.

## Code

`lib/pdso/code/catalogue/`. The geometry, entry format, storage and recommender have no Revit imports, so they can be read and tested outside Revit.
