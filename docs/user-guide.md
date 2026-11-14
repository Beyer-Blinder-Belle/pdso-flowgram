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

Install the extension with the pyRevit CLI, then reload pyRevit. The extensible storage library it needs is bundled.

```bash
pyrevit extend ui PDSO https://github.com/Beyer-Blinder-Belle/pdso-flowgram.git
```

A **PDSO** tab appears in the Revit ribbon with a **Program Data Silo Tools** panel. The buttons only work in a project (not in the family editor). The first three set up a model, in ribbon order.

| Button | What it does |
| --- | --- |
| **Project Config** | Saves the project's PDSO settings in the model: area scheme mapping, level mapping, family names, worksets. |
| **Parameter Manager** | Chooses which parameters this project tracks, creates them as shared parameters on Generic Models and the Data Silo family, and saves or applies presets. Must be run once before syncing. |
| **Quick Setup** | Creates everything else in the model: the area schemes from Project Config, the Data Silo family, view templates, working views and schedules. Shift+Click for standards-folder options. |
| **Sync Data** | Runs preflight checks, then harvests Area SF into Data Silos and builds or updates Program Volumes. |
| **Audit Report** | Prints a detailed report of configuration, correlation, level mapping and Area boundary problems, with clickable element IDs. |

The **Unit Layout Catalogue** panel next to it is a separate tool for reusing room furniture layouts. See the [catalogue README](../catalogue/README.md).

## Setting up a project

Do these once per project, in order: **Project Config → Parameter Manager → Quick Setup**.

### 1. Set up Project Config

Click **Project Config**. The settings are saved in the model, so set them once at the start of the project. Changing them mid-project can orphan existing Program Volumes.

| Setting | What it means |
| --- | --- |
| **Data Silo Family Name** | The family PDSO treats as Data Silos. The default is the family PDSO ships with (`PDSO - Program Data Silo Object - Rvt26` in Revit 2026), which Quick Setup loads in step 3. Change it only if you use your own Data Silo family; load that family first. |
| **Program UID Regex Pattern** | Optional rule that every Program UID must match. The default, `^[A-Za-z0-9]{2}-\d{2}-[A-Za-z0-9]{2}$`, means `BLDG-FLR-UNIT`, for example `00-07-01` or `NB-02-UD`. Leave it blank to allow any UID. |
| **Area Scheme to Parameter Mappings** | Which Revit area scheme fills which Data Silo parameter. The defaults are `PDSO - GSF`, `PDSO - NSF` and `PDSO - ZFA`, filling the parameters of the same names. Quick Setup creates any of these schemes that the model doesn't have. If the model already has its own schemes (for example **Gross Building** and **Rentable**), map those instead. |
| **Program Volume Family Name Prefix** | PDSO names each Program Volume family `<prefix><Program UID>`, and uses the prefix to find them. Default `PDSO_ProgramVolume-`. |
| **Program Volume Extrusion Height** | Height of Program Volumes, in feet. Default 8. |
| **Data Silo / Program Volume / Area Workset** | Optional. If set, Sync Data can move elements to these worksets or warn when they're on the wrong one. |
| **Level Mapping** | Short identifiers for levels, used by stacking (see below). |

Program Volume shapes come from the Area in the `PDSO - GSF` scheme. If there's no scheme with that name, they come from the smallest Area that contains the Data Silo.

**Map your levels.** Stacking refers to floors by short identifiers, not Revit level names. Under **Level Mapping**, add a row for each level a unit can repeat on, for example:

| Identifier | Revit level |
| --- | --- |
| `02` | Level 2 |
| `03` | Level 3 |
| `10` | Level 10 |

Identifiers should sort correctly (`01`, `02`, ... `10`). For several buildings, prefix them, for example `A07` or `B03`. Click **Sort by Identifier**. You can add levels later too.

Click **Save Configuration**.

### 2. Choose your parameters

Click **Parameter Manager**. It lists the PDSO parameters the tools ship with, plus any custom ones you add. Only the parameters you tick (*track*) are created in the model and copied to Program Volumes.

- On a model's first run, every built-in parameter is ticked. Pick a **preset** to start from: *Default* is everything, *Blank* is only the required ones, or use one your team saved.
- **Locked** parameters can't be unticked: Program UID, Program Stacking, Last Synced, and any parameter an area scheme mapping writes into.
- **Add** custom parameters if your project needs them (Text, Multiline Text, Number, Integer, Area, Length or Yes/No). Their name and type can't change once created.
- Click **Apply Parameters**. Nothing changes in the model until you do. It uses Revit's shared parameter file (or asks you for one) and binds the parameters to Generic Models.

The Data Silo family isn't loaded yet at this point. If the option to add parameters to the Data Silo family is ticked, the manager warns that the family "isn't loaded, so it wasn't updated". That's expected: the Generic Models binding still puts every tracked parameter on every Data Silo.

**Save as Preset** stores the current ticks for other projects. Point the presets folder at a shared network folder so the whole team uses the same presets.

Sync Data won't run until the Parameter Manager has been applied, or whenever a tracked parameter goes missing from Generic Models.

### 3. Run Quick Setup

Click **Quick Setup**, keep all steps ticked, and click **Run Setup**. It:

- creates the area schemes named in Project Config that the model doesn't have yet (Revit can only copy an existing scheme, so each new one starts as a copy, renamed),
- loads `PDSO - Program Data Silo Object - Rvt26`, and records it in Project Config unless you chose a different, loaded family there,
- creates PDSO view templates, a GSF area plan for each level, a 3D Program Volumes view, and Data Silo and Program Volume schedules. Your office can turn on NSF and ZFA area plans too, in `views.json`.

Run it after the Parameter Manager, so the schedules get their PDSO columns. It's safe to run again. Everything it does is one undo step, **PDSO Quick Setup**.

Your office or project can change what Quick Setup creates with JSON standards files. See the [standards README](../PDSO.extension/lib/pdso/standards/README.md).

### 4. Draw Areas and place Data Silos

In the PDSO area plans:

1. Draw Area boundaries and place an Area for each unit in **each** scheme (GSF, NSF, ZFA).
2. Place **one Data Silo per unit**, inside the unit's Areas, on the unit's own level.
3. Put Data Silos on the Data Silo workset if your project uses one.

A Data Silo picks up an Area's SF only if it's on the same level and inside the boundary in plan.

### 5. Fill in the Data Silos

Use the Data Silo schedule (or Properties) to enter each unit's data. At minimum, give every Data Silo a unique **PDSO - Program UID**. Settle UIDs before the first sync. A Data Silo's Program Volume family is named after its UID, so changing a UID later leaves the old family in the model. See the [Parameter Reference](parameter-reference.md) for every field.

**Stacking.** For a unit that repeats on other floors, enter the level identifiers in **PDSO - Program Stacking**:

| Value | Meaning |
| --- | --- |
| *(blank)* | Only the Data Silo's own level |
| `03-05` | Levels 03, 04 and 05 |
| `03-06, 10` | Levels 03 to 06, and 10 |
| `A07-A09` | Building A, levels 07 to 09 |

The Data Silo's own level is always included. Every identifier must exist in the Level Mapping.

### 6. Run Sync Data

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
   2. **Family Geometry**: creates or updates one Program Volume family per Data Silo, shaped from its GSF Area (or its smallest Area if there's no `PDSO - GSF` scheme) and extruded to the configured height.
   3. **Instance Updates**: places a Program Volume instance on the Data Silo's level and each stacked level, removes ones no longer needed, and copies every tracked parameter from the Data Silo onto them.

Keep the console open. It logs each step and any element it skipped.

### 7. Check the results

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
| Preflight: parameters not set up, or tracked parameters missing | The Parameter Manager hasn't been applied, or a parameter was deleted or unbound | Open Parameter Manager and click Apply Parameters. |
| A parameter isn't on Program Volumes | It isn't tracked | Tick it in the Parameter Manager, apply, then sync. |
| Preflight: Data Silo family not loaded | Family name in Project Config doesn't match the loaded family | Run Quick Setup, or correct the name in Project Config. |
| Preflight: duplicate UIDs | Two Data Silos share a Program UID | Give each a unique UID. Copied Data Silos keep the original's UID. |
| Data Silo has no GSF/NSF/ZFA value | It isn't inside an Area in that scheme on its level | Move it inside the Area, or place the missing Area. |
| Area has more than one Data Silo | Two units share one Area | Split the Area, or delete the extra Data Silo. |
| Area reports 0 SF / "not enclosed" | Boundary lines don't close, or two Areas share one enclosure | Close the boundary or delete the duplicate Area. |
| "Boundary can't be closed" / "self-intersecting" | Boundary lines overlap, cross or have large gaps | Trim and join boundary lines so they meet end to end. |
| No Program Volume for a Data Silo | No Area contains it, or the shape Area (GSF, or the smallest Area if there's no `PDSO - GSF` scheme) has a bad boundary | Fix that Area's boundary, or move the Data Silo inside it. |
| Stacked floors missing | Stacking identifier not in Level Mapping | Add the identifier in Project Config → Level Mapping. Preflight only warns about this, and the sync skips those Data Silos. |
| Old Program Volumes left behind after renaming a unit | Program Volumes are found by `<prefix><Program UID>`, so a new UID gets a new family | Delete the old `<prefix><old UID>` family, or set UIDs before the first sync. |
| Program Volume shape is wrong after moving a Data Silo or changing the extrusion height | The geometry cache only notices changes to the GSF Area boundary | Delete that Program Volume family and sync again. |
| Areas in groups | Areas inside model groups can't be harvested reliably | Ungroup them. |
| Elements not editable | Another user owns them in a workshared model | Ask them to sync and relinquish. |

## Automating with an AI agent

PDSO has an HTTP API and an MCP server, so an AI agent such as Claude Code can do the work of the windows. It can read and change programs, draw Area outlines, run preflight and sync, and render plans. Setup is in the [README](../README.md#http-api-and-mcp-server). Agents should start at [AGENTS.md](../AGENTS.md).

## Getting help

Open an issue on [GitHub](https://github.com/Beyer-Blinder-Belle/pdso-flowgram/issues). Include the console log from Sync Data and the Audit Report output.
