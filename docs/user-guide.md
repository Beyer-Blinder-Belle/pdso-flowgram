# PDSO User Guide

This guide is for architects and BIM users who are setting up PDSO on a project for the first time. For the full list of fields on a Data Silo, see the [Parameter Reference](parameter-reference.md).

## What PDSO is

**Program Data Silo Objects (PDSO)** keep all of a project's program data, such as unit types, bedroom counts, market segment and square footage, in one place in the Revit model. You don't have to spread that data across several area schemes and schedules.

You enter program data once, on a **Data Silo**. A sync command then:

- reads square footage from the Areas the Data Silo sits inside, and
- builds a taggable 3D **Program Volume** for each Data Silo, on every floor where that unit repeats, carrying all of the Data Silo's data.

Why use it:

- **One source of truth.** All unit data lives on Data Silos. Everything else is generated from them.
- **Tag anywhere.** Program Volumes are Generic Models, so you can tag program data in floor plans, RCPs, sections and 3D views, not just area plans.
- **Stacked units without extra modeling.** Model a typical unit once and list the floors it repeats on. PDSO places a Program Volume on each of those floors, so schedules count the whole building correctly.
- **Presentation graphics.** Program Volumes can be hatched, colored or hidden (they're still taggable when hidden).

## The three parts

```
  Areas (you draw)          Data Silo (you fill in)        Program Volumes (PDSO builds)
  ───────────────           ───────────────────────        ─────────────────────────────
  PDSO - GSF scheme  ──SF──▶                                 one family per Data Silo,
  PDSO - NSF scheme  ──SF──▶  UID, type, BR count, ...  ──▶  one instance per stacked floor,
  PDSO - ZFA scheme  ──SF──▶  + harvested GSF/NSF/ZFA        all Data Silo values copied
```

1. **Areas**: you draw Area boundaries and place Areas in each PDSO area scheme (GSF, NSF, ZFA). Revit calculates their square footage.
2. **Data Silo**: a small point-based Generic Model family. You place one inside each unit and type the unit's program data into it. Its position tells PDSO which Area it belongs to in each scheme.
3. **Program Volume**: a lightweight 3D family that PDSO creates and keeps up to date. Its shape comes from the unit's GSF Area. **Never edit Program Volumes by hand.** The next sync overwrites them.

## The golden rule

| Do | Don't |
| --- | --- |
| Enter data on **Data Silos** only. | Type data into Program Volumes. The next sync overwrites it. |
| Change square footage by editing **Area boundaries**. | Type into `PDSO - GSF`, `NSF` or `ZFA` on a Data Silo. The next sync overwrites it. |
| Re-run **Sync Data** after any change. | Move, copy or delete Program Volumes by hand. |

Check `PDSO - Last Synced` on a Data Silo or Program Volume to see when it was last updated.

## Installing

You need:

- Autodesk Revit (the shipped Data Silo family is for Revit 2026)
- [pyRevit](https://github.com/pyrevitlabs/pyRevit)
- The [`extensible-storage-pyrevit`](https://github.com/khorn06/extensible-storage-pyrevit) library extension

Install both extensions with the pyRevit CLI, then reload pyRevit:

```bash
pyrevit extend lib extensible_storage https://github.com/khorn06/extensible-storage-pyrevit.git
```

```bash
pyrevit extend ui PDSO https://github.com/Beyer-Blinder-Belle/pdso-flowgram.git
```

A **PDSO** tab appears in the Revit ribbon with a **Program Data Silo Tools** panel. The buttons only work in a project (not in the family editor).

| Button | What it does |
| --- | --- |
| **Model Setup** | Adds PDSO parameters, loads the Data Silo family, and creates view templates, working views and schedules. Shift+Click for standards-folder options. |
| **Project Config** | Saves the project's PDSO settings in the model: area scheme mapping, level mapping, family names, worksets. |
| **Sync Data** | Runs preflight checks, then harvests Area SF into Data Silos and builds or updates Program Volumes. |
| **Audit Report** | Prints a detailed report of configuration, correlation, level mapping and Area boundary problems, with clickable element IDs. |

## Setting up a project

Do these once per project, in order.

### 1. Create the area schemes

In **Area and Volume Computations → Area Schemes**, create:

- `PDSO - GSF`
- `PDSO - NSF`
- `PDSO - ZFA`

Don't reuse Revit's built-in **Gross Building** scheme. Model Setup doesn't create area schemes. If you name yours differently, map them in Project Config (step 3).

Type the names with a plain hyphen (`-`), not an en dash (`–`).

### 2. Run Model Setup

Click **Model Setup**, keep all steps ticked, and click **Run Setup**. It:

- reports which PDSO shared parameters are in the model (adding them automatically is still being built; see [Adding the parameters](#adding-the-parameters-by-hand)),
- loads `PDSO - Program Data Silo Object - Rvt26` and records it as the Data Silo family,
- creates PDSO view templates, area plan working views for each scheme and level, and Data Silo and Program Volume schedules.

It's safe to run again. Everything it does is one undo step, **PDSO Model Setup**.

Your office or project can change what Model Setup creates with JSON standards files. See the [standards README](../PDSO.extension/lib/pdso/standards/README.md).

#### Adding the parameters by hand

Until the parameters step is finished, add the shared parameters yourself:

1. **Manage → Project Parameters → New → Shared parameter → Select**.
2. Browse to [`PDSO Shared Parameters.txt`](../PDSO.extension/lib/pdso/standards/PDSO%20Shared%20Parameters.txt), choose the **PDSO** group and pick a parameter.
3. Select **Instance**, tick **Generic Models**, and click **OK**. Repeat for each parameter.

If your office customized `parameters.json`, Shift+Click **Model Setup → Export PDSO shared parameter file** to get a matching file.

### 3. Check Project Config

Click **Project Config**. The settings are saved in the model, so set them once at the start of the project. Changing them mid-project can orphan existing Program Volumes.

| Setting | What it means |
| --- | --- |
| **Data Silo Family Name** | The family PDSO treats as Data Silos. Model Setup fills this in. |
| **Program UID Regex Pattern** | Optional rule that every Program UID must match. The default, `^[A-Za-z0-9]{2}-\d{2}-[A-Za-z0-9]{2}$`, means `BLDG-FLR-UNIT`, for example `00-07-01` or `NB-02-UD`. Leave it blank to allow any UID. |
| **Area Scheme to Parameter Mappings** | Which area scheme fills which Data Silo parameter (`PDSO - GSF` → `PDSO - GSF`, and so on). |
| **Program Volume Family Name Prefix** | PDSO names each Program Volume family `<prefix><Program UID>`, and uses the prefix to find them. Default `PDSO_ProgramVolume-`. |
| **Program Volume Extrusion Height** | Height of Program Volumes, in feet. Default 8. |
| **Data Silo / Program Volume / Area Workset** | Optional. If set, Sync Data can move elements to these worksets or warn when they're on the wrong one. |
| **Level Mapping** | Short identifiers for levels, used by stacking (see below). |

Click **Save Configuration**.

### 4. Map your levels

Stacking refers to floors by short identifiers, not Revit level names. In **Project Config → Level Mapping**, add a row for each level a unit can repeat on, for example:

| Identifier | Revit level |
| --- | --- |
| `02` | Level 2 |
| `03` | Level 3 |
| `10` | Level 10 |

Identifiers should sort correctly (`01`, `02`, ... `10`). For several buildings, prefix them, for example `A07` or `B03`. Click **Sort by Identifier**, then **Save Configuration**.

### 5. Draw Areas and place Data Silos

In the PDSO area plans:

1. Draw Area boundaries and place an Area for each unit in **each** scheme (GSF, NSF, ZFA).
2. Place **one Data Silo per unit**, inside the unit's Areas, on the unit's own level.
3. Put Data Silos on the Data Silo workset if your project uses one.

A Data Silo picks up an Area's SF only if it's on the same level and inside the boundary in plan.

### 6. Fill in the Data Silos

Use the Data Silo schedule (or Properties) to enter each unit's data. At minimum, give every Data Silo a unique **PDSO - Program UID**. Settle UIDs before the first sync. A Data Silo's Program Volume family is named after its UID, so changing a UID later leaves the old family in the model. See the [Parameter Reference](parameter-reference.md) for every field.

**Stacking.** For a unit that repeats on other floors, enter the level identifiers in **PDSO - Program Stacking**:

| Value | Meaning |
| --- | --- |
| *(blank)* | Only the Data Silo's own level |
| `03-05` | Levels 03, 04 and 05 |
| `03-06, 10` | Levels 03 to 06, and 10 |
| `A07-A09` | Building A, levels 07 to 09 |

The Data Silo's own level is always included. Every identifier must exist in the Level Mapping.

### 7. Run Sync Data

Save (and sync with central) first. Then click **Sync Data**.

1. **Preflight Checks** run automatically. Errors (✗) must be fixed before you can sync. Warnings (⚠) are worth reviewing but don't block it. Click refresh after fixing things.
2. **Script Settings** control what the sync does:

   | Setting | Options |
   | --- | --- |
   | Data Silo Workset Enforcement | Fix (if configured), Warn (if configured), Do Nothing |
   | Data Silo Pinning | Pin, Unpin, Maintain |
   | Update Geometry | Yes - Use Geometry Cache (only rebuilds families whose GSF Area changed), Yes - Recompute All Geometry (currently behaves the same as the cache option), No |
   | Program Volume Workset / Pinning | as above |
   | Update Data | Yes, No |

3. Click **Run Sync Process**. The sync runs in three phases:
   1. **Area Harvest**: finds the Area each Data Silo sits in for each scheme and writes its SF to `PDSO - GSF`/`NSF`/`ZFA`, and stamps `PDSO - Last Synced`.
   2. **Family Geometry**: creates or updates one Program Volume family per Data Silo, shaped from its GSF Area and extruded to the configured height.
   3. **Instance Updates**: places a Program Volume instance on the Data Silo's level and each stacked level, removes ones no longer needed, and copies every PDSO parameter from the Data Silo onto them.

Keep the console open. It logs each step and any element it skipped.

### 8. Check the results

- **Program Volume schedule**: counts and data for the whole building, including stacked floors.
- **3D view**: Program Volume shapes and placement.
- **Audit Report**: anything that's still wrong, with links that select the problem elements.

## Everyday workflow

1. Change Area boundaries or Data Silo values.
2. Click **Sync Data**, review preflight, run.
3. Check `PDSO - Last Synced` and the schedules.

### Working views vs presentation views

| View | Use it to |
| --- | --- |
| **Area plans** (PDSO templates) | Place Data Silos, draw Area boundaries, place Areas. Data Silos are visible and Program Volumes are semi-transparent. |
| **Data Silo schedule** | Enter, edit and check program data. |
| **Program Volume schedule** | Check counts and whole-building totals; export unit data. |
| **3D view** | Check Program Volume shapes. |
| **Floor plans for sheets** | Hide Data Silos, style or hide Program Volumes, and tag Program Volumes. |

## Troubleshooting

Run **Audit Report** first. It lists every problem with a link to the elements.

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Preflight: config not found | Project Config was never saved | Open Project Config and click Save Configuration. |
| Preflight: Data Silo family not loaded | Family name in Project Config doesn't match the loaded family | Run Model Setup, or correct the name in Project Config. |
| Preflight: duplicate UIDs | Two Data Silos share a Program UID | Give each a unique UID. Copied Data Silos keep the original's UID. |
| Data Silo has no GSF/NSF/ZFA value | It isn't inside an Area in that scheme on its level | Move it inside the Area, or place the missing Area. |
| Area has more than one Data Silo | Two units share one Area | Split the Area, or delete the extra Data Silo. |
| Area reports 0 SF / "not enclosed" | Boundary lines don't close, or two Areas share one enclosure | Close the boundary or delete the duplicate Area. |
| "Boundary can't be closed" / "self-intersecting" | Boundary lines overlap, cross or have large gaps | Trim and join boundary lines so they meet end to end. |
| No Program Volume for a Data Silo | No GSF Area, or a bad GSF boundary | Fix the GSF Area. Program Volume shape always comes from GSF. |
| Stacked floors missing | Stacking identifier not in Level Mapping | Add the identifier in Project Config → Level Mapping. Preflight only warns about this, and the sync skips those Data Silos. |
| Old Program Volumes left behind after renaming a unit | Program Volumes are found by `<prefix><Program UID>`, so a new UID gets a new family | Delete the old `<prefix><old UID>` family, or set UIDs before the first sync. |
| Program Volume shape is wrong after moving a Data Silo or changing the extrusion height | The geometry cache only notices changes to the GSF Area boundary | Delete that Program Volume family and sync again. |
| Areas in groups | Areas inside model groups can't be harvested reliably | Ungroup them. |
| Elements not editable | Another user owns them in a workshared model | Ask them to sync and relinquish. |

## Getting help

Open an issue on [GitHub](https://github.com/Beyer-Blinder-Belle/pdso-flowgram/issues). Include the console log from Sync Data and the Audit Report output.
