# PDSO Flowgram

**Program Data Silo Objects (PDSO)**: a [pyRevit](https://github.com/pyrevitlabs/pyRevit) extension that keeps architectural program data (target areas, program names, unit types) in sync between Revit **Areas** and 3D **program volume** families.

Built by the DCT team at [Beyer Blinder Belle](https://www.beyerblinderbelle.com). This is BBB's first open-source project.

## What it does

The **Program Data Silo Tools** panel (on the **PDSO** ribbon tab) has three commands:

| Button | What it does |
| --- | --- |
| **Project Config** | Sets up the model-level PDSO configuration (area schemes, level mapping, program volume family). The configuration is stored in the model with Revit Extensible Storage. |
| **Parameter Manager** | Chooses which parameters PDSO tracks and creates them as shared parameters, bound to Generic Models (and optionally embedded in the Data Silo family). Must be run at least once per model before syncing. |
| **Run Scripts** | Syncs parameter data between Area objects, PDSO objects, and program volume (mass) objects, after a preflight check. |
| **Audit Report** | Checks PDSO objects for data consistency, sync problems, and Area boundary issues. |

All commands need an open **project** document; they are disabled in the family editor.

## Tracked parameters

The PDSO tools only sync the parameters a model *tracks*. The tracked list is set in the **Parameter Manager** and saved in the model's PDSO configuration (Extensible Storage).

- **Built-in parameters** (Program UID, Program Stacking, ZFA/NSF/GSF, Last Synced, and the program data fields) ship with the tools as options. Nothing is created until you click **Apply Parameters**. On a model's first run, every built-in is ticked.
- **Locked parameters** can't be untracked. They are *Program UID*, *Program Stacking*, *Last Synced*, and any parameter an Area Scheme mapping writes into.
- **Custom parameters** can be added, with types Text, Multiline Text, Number, Integer, Area, Length and Yes/No. Once created, their name and type can't change.
- **Shared parameter file**: the manager uses the file set in Revit (*Manage > Shared Parameters*). If that file is missing, it asks you to pick or create one, remembers your choice for the PDSO tools, and leaves Revit's setting unchanged. New definitions go in a `PDSO` group. Existing definitions are reused (first by GUID, then by name) instead of being duplicated.
- **Presets** tick a set of parameters for a type of project. *Default* (every built-in parameter) and *Blank* (only the required ones) are built in. **Save as Preset** stores the current ticks as a JSON file in the presets folder. That folder is `%APPDATA%\PDSO\ParameterPresets` unless you choose another; point it at a shared network folder so the team uses the same presets. Presets keep the GUIDs of custom parameters, so they match across projects. Choosing a preset changes nothing in the model until you click **Apply Parameters**. To delete a preset, delete its file from the presets folder.
- **Run Scripts** is blocked until the manager has run, and blocked again whenever a tracked parameter is missing from Generic Models.

The built-in GUIDs are fixed in `lib/pdso/code/data/shared_parameter_constants.py` and must never change.

## Requirements

- Autodesk Revit
- [pyRevit](https://github.com/pyrevitlabs/pyRevit)
- The [`extensible-storage-pyrevit`](https://github.com/khorn06/extensible-storage-pyrevit) library extension by Kris Horn. It is a separate project and is **not** bundled here.

## Installation

Using the pyRevit CLI:

```bash
pyrevit extend lib extensible_storage https://github.com/khorn06/extensible-storage-pyrevit.git
```

```bash
pyrevit extend ui PDSO https://github.com/Beyer-Blinder-Belle/pdso-flowgram.git
```

Then reload pyRevit.

For development, clone this repo and add its folder to pyRevit's custom extension directories (pyRevit → Settings → Custom Extension Directories), or run `pyrevit extensions paths add <path-to-repo>`.

## Repository layout

The repository uses the standard pyRevit extension layout:

```
PDSO.extension/
├── PDSO.tab/
│   └── Program Data Silo Tools.panel/
│       ├── 1.Config.pushbutton/      # Project configuration window
│       ├── 1b.Parameters.pushbutton/ # Parameter Manager (tracked shared parameters)
│       ├── 2.RunScripts.pushbutton/  # Harvest Areas and sync program volumes
│       └── 3.Audit.pushbutton/       # Audit report
└── lib/
    ├── common/                       # Shared helpers and UI assets
    └── pdso/
        ├── code/
        │   ├── business/             # Services: sync, hashing, level mapping, storage, ...
        │   ├── data/                 # Wrappers around Revit elements, schemas, parameter constants
        │   └── view_models/          # WPF view models (MVVM)
        ├── xaml/                     # WPF windows and styles
        └── documentation/            # Developer notes
```

## Development notes

- pyRevit runs this code on **IronPython 2.7**, so use Python 2.7-compatible syntax (no f-strings or type hints).
- Linting uses [Ruff](https://docs.astral.sh/ruff/), configured in `pyproject.toml`:

  ```bash
  ruff check .
  ```

## Contributing

Issues and pull requests are welcome. Please open an issue to discuss larger changes first.

## License

[GPL-3.0](LICENSE) © Beyer Blinder Belle
