# Unit Layout Catalogue

Reusable room layouts (furniture, casework, equipment), harvested from finished projects and placed into new ones.

```
Harvest  ->  Store  ->  Recommend  ->  Create
```

| Step | Where | What it does |
| --- | --- | --- |
| **Harvest** | `Harvest Layout` button | Pick a Room or Area. Its free-standing families are saved **in the room's own frame**: origin at the corner of its tightest bounding rectangle, X along the longer side. |
| **Store** | this folder | One `<id>.json` per layout, with an `<id>.svg` plan sketch beside it |
| **Recommend** | `Place Layout` button | Pick a target Room or Area. Every layout is tried in all 8 orientations (4 turns, with and without a mirror) and ranked: 60% area match, 40% how snugly it fits. Same-program layouts come first. A layout is offered only if every item lands inside the target. |
| **Create** | `Place Layout` button | Places the chosen layout, then writes its id to `PDSO - Unit Layout Type` on any PDSO data silo in the room |

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

## Code

`PDSO.extension/lib/pdso/code/catalogue/`. The geometry, entry format, storage and recommender have no Revit imports, so they can be read and tested outside Revit.
