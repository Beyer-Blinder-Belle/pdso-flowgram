# PDSO Flowgram

**Program Data Silo Objects (PDSO)**: a [pyRevit](https://github.com/pyrevitlabs/pyRevit) extension that keeps architectural program data (target areas, program names, unit types) in sync between Revit **Areas** and 3D **program volume** families.

Built by the DCT team at [Beyer Blinder Belle](https://www.beyerblinderbelle.com). This is BBB's first open-source project.

## What it does

The **Program Data Silo Tools** panel (on the **PDSO** ribbon tab) has three commands:

| Button | What it does |
| --- | --- |
| **Project Config** | Sets up the model-level PDSO configuration (area schemes, level mapping, program volume family). The configuration is stored in the model with Revit Extensible Storage. |
| **Run Scripts** | Syncs parameter data between Area objects, PDSO objects, and program volume (mass) objects, after a preflight check. |
| **Audit Report** | Checks PDSO objects for data consistency, sync problems, and Area boundary issues. |

All commands need an open **project** document; they are disabled in the family editor.

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
