# Unit Layout Catalogue

Reusable unit layouts, harvested from finished projects and placed into new ones. **The unit is an apartment**: its partitions, doors, rooms, furniture, kitchen and bathroom fittings. The approach follows Oliver Green, *An Introspective Approach to Apartment Design* (AHMM, 2020).

```
Harvest  ->  Store  ->  Recommend  ->  Create
```

| Step | Where | What it does |
| --- | --- | --- |
| **Harvest** | `Harvest Layout` button | Open an area plan and run: **every Area in it with a PDSO data silo inside is harvested, with nothing to pick.** To harvest something else (a single Room, an Area with no silo), select it first. The unit type comes from the data silo, else from the bedroom count; the name is made from the type, size and Area number. Running again replaces the earlier harvest of the same unit. Each unit's free-standing families are saved **in the room's own frame**: a bounding rectangle lined up with the direction most of the room's walls run in, origin at its corner, X along the longer side. **What surrounds the layout is saved too** (see below). |
| **Store** | this folder | One `<id>.json` per layout, with an `<id>.svg` plan sketch beside it |
| **Recommend** | `Place Layout` button | Pick a target Room or Area. Every layout is tried in all 8 orientations (4 turns, with and without a mirror) and ranked: 60% area match, 40% how snugly it fits. Same-program layouts come first. A layout is offered only if every item lands inside the target. |
| **Create** | `Place Layout` button | Places the chosen layout, then writes its id to `PDSO - Unit Layout Type` on any PDSO data silo in the room |

## Folders

| Folder | In git? | Holds |
| --- | --- | --- |
| `sample/` | yes | **Made-up** layouts, so the tool can be tried without harvesting anything |
| `local/` | **no** (`.gitignore`) | Where harvests go by default. **Layouts from real projects are client and firm data; keep them out of this public repo.** |

Shift-click **Harvest Layout** to save to another folder instead, such as a shared office drive. **Place Layout** reads `sample/`, `local/` and the chosen folder.

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

**Source model.** `source.model` is the Revit file's title and `source.model_path` its path.

Files written before this (`schema_version` 1) still load; they have empty `features`.

## Limits

- Only free-standing (level-based) families are placed. Wall- and face-hosted families (wall cabinets, vanities) are recorded in `hosted_items` with the wall they sit on, but not placed yet. Ceiling-hosted lights are not read.
- A wall's Function is often left at Revit's default (Exterior), so a wall facing a space with no Room in it can read as exterior.
- An Area's boundary is a line, not a wall. The wall is found by looking for a straight wall on the same level running along the line; where none is found, an edge is called exterior only if a window sits in it.
- Only straight walls based on the unit's level are read. Curved partitions are missed.
- **Place Layout does not rebuild partitions, doors or rooms yet.** It places free-standing families only.
- Walls, doors and windows in a linked model are not read.
- A family not loaded in the target model is reported as missing, not loaded automatically.
- The layout is centred in the target room. It does not yet align to a door or a teaching wall.
- Only the room's outer boundary is used. Columns and holes are ignored.

## Code

`lib/pdso/code/catalogue/`. The geometry, entry format, storage and recommender have no Revit imports, so they can be read and tested outside Revit.
