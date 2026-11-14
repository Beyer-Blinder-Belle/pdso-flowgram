# PDSO Flowgram

**Program Data Silo Objects (PDSO)**: a [pyRevit](https://github.com/pyrevitlabs/pyRevit) extension that keeps architectural program data (target areas, program names, unit types) in sync between Revit **Areas** and 3D **program volume** families.

Built by the DCT team at [Beyer Blinder Belle](https://www.beyerblinderbelle.com). This is BBB's first open-source project.

## What it does

The **Program Data Silo Tools** panel (on the **PDSO** ribbon tab) has four commands:

| Button | What it does |
| --- | --- |
| **Model Setup** | Sets up a model for PDSO: adds the PDSO parameters, loads the Data Silo family, and creates view templates, working views and schedules. What it creates comes from JSON [standards files](PDSO.extension/lib/pdso/standards/README.md) that a company or project can customize. Shift+Click to choose or export a standards folder. |
| **Project Config** | Sets up the model-level PDSO configuration (area schemes, level mapping, program volume family). The configuration is stored in the model with Revit Extensible Storage. |
| **Run Scripts** | Syncs parameter data between Area objects, PDSO objects, and program volume (mass) objects, after a preflight check. |
| **Audit Report** | Checks PDSO objects for data consistency, sync problems, and Area boundary issues. |

All commands need an open **project** document; they are disabled in the family editor.

## Requirements

- Autodesk Revit
- [pyRevit](https://github.com/pyrevitlabs/pyRevit)
- The [`extensible-storage-pyrevit`](https://github.com/khorn06/extensible-storage-pyrevit) library by Kris Horn. A copy is bundled in `PDSO.extension/lib/extensible_storage`, so no separate install is needed (see its `NOTICE.md`).

## Installation

Using the pyRevit CLI:

```bash
pyrevit extend ui PDSO https://github.com/Beyer-Blinder-Belle/pdso-flowgram.git
```

Then reload pyRevit.

For development, clone this repo and add its folder to pyRevit's custom extension directories (pyRevit → Settings → Custom Extension Directories), or run `pyrevit extensions paths add <path-to-repo>`.

## Revit family

PDSO needs the **Program Data Silo Object** family loaded in your project. The family file is in [`families/`](families/):

- `PDSO - Program Data Silo Object - Rvt26.rfa` (Revit 2026)

**Model Setup** loads it and sets it as the Data Silo family in Project Config. To do it by hand, load the family, then open **Project Config** and set the Data Silo family name to match the loaded family's name.

## Standards

Model Setup is driven by JSON files in [`PDSO.extension/lib/pdso/standards/`](PDSO.extension/lib/pdso/standards/). They ship with working defaults. A company can override any of them from a shared folder (set the `PDSO_STANDARDS_DIR` environment variable, or Shift+Click Model Setup), and a project can override them from a `PDSO Standards` folder next to the model. See the [standards README](PDSO.extension/lib/pdso/standards/README.md) for every option.

## Repository layout

The repository uses the standard pyRevit extension layout:

```
families/                             # Revit family files (.rfa)
PDSO.extension/
├── PDSO.tab/
│   └── Program Data Silo Tools.panel/
│       ├── 0.Setup.pushbutton/       # Model Setup from JSON standards (Shift+Click: options)
│       ├── 1.Config.pushbutton/      # Project configuration window
│       ├── 2.RunScripts.pushbutton/  # Harvest Areas and sync program volumes
│       └── 3.Audit.pushbutton/       # Audit report
└── lib/
    ├── common/                       # Shared helpers and UI assets
    └── pdso/
        ├── code/
        │   ├── business/             # Services: sync, hashing, level mapping, storage, ...
        │   ├── data/                 # Wrappers around Revit elements, schemas, parameter constants
        │   ├── setup/                # Model Setup: standards loader, runner and steps
        │   └── view_models/          # WPF view models (MVVM)
        ├── standards/                # Default JSON standards for Model Setup
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
