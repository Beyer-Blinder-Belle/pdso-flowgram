# PDSO Flowgram

**Program Data Silo Objects (PDSO)**: a [pyRevit](https://github.com/pyrevitlabs/pyRevit) extension that keeps architectural program data (target areas, program names, unit types) in sync between Revit **Areas** and 3D **program volume** families.

Built by the DCT team at [Beyer Blinder Belle](https://www.beyerblinderbelle.com). This is BBB's first open-source project.

## What it does

The **Program Data Silo Tools** panel (on the **PDSO** ribbon tab) has four commands:

| Button | What it does |
| --- | --- |
| **Model Setup** | Sets up a model for PDSO: adds the PDSO parameters, loads the Data Silo family, and creates view templates, working views and schedules. What it creates comes from JSON [standards files](PDSO.extension/lib/pdso/standards/README.md) that a company or project can customize. Shift+Click to choose or export a standards folder. |
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

## HTTP API and MCP server

The extension also has an HTTP API, so that a program or an AI agent can do the work of the three windows, and can create and change programs and Area outlines. The API runs in Revit through [pyRevit Routes](https://docs.pyrevitlabs.io/reference/pyrevit/routes/). An [MCP](https://modelcontextprotocol.io) server gives the API to agents such as Claude Code. The agent can be on the same computer as Revit, or on a different computer.

```
agent  --MCP (stdio)-->  mcp/pdso_mcp_server.py  --HTTP (direct, or SSH port forward)-->  Revit + pyRevit + PDSO.extension
```

All lengths are decimal feet. All areas are square feet.

### Enable the API (on the Revit computer)

```bash
pyrevit configs routes enable
```

Then reload pyRevit. The API is at `http://127.0.0.1:48884/pdso/` (48884 is the default pyRevit Routes port). `GET /pdso/routes` lists all routes. pyRevit Routes has no authentication and listens only on `127.0.0.1`. To use it from a different computer, use an SSH port forward. Do not open the port to a network.

### Routes

| Group | Routes |
| --- | --- |
| Status and model | `GET status`, `GET document`, `POST document/new`, `POST document/open`, `POST document/close`, `POST document/save`, `GET/POST levels`, `GET rooms`, `GET views`, `POST views/activate` |
| Setup | `POST bootstrap`, `GET/PUT config`, `GET config/checks` |
| Parameters | `GET/POST parameters`, `DELETE parameters/<field or name>` |
| Sync | `GET preflight`, `GET sync/options`, `POST sync`, `GET audit` |
| Programs (Data Silos) | `GET programs/schema`, `GET programs/summary`, `GET/POST/PUT programs`, `POST programs/batch`, `GET/PUT/DELETE programs/<uid or id>` |
| Selection | `GET/POST selection` |
| Areas | `GET area-schemes`, `GET/POST area-plans`, `GET/POST areas`, `GET/PUT/DELETE areas/<id>` |
| Visualization | `POST views/export`, `POST views/color-areas`, `POST views/clear-colors`, `POST views/program-volumes` |

A reply is `{"ok": true, "data": ...}` or `{"ok": false, "error": {"type", "message", "traceback"}}`.

`POST bootstrap` makes a model ready for PDSO. It creates only the items that are missing: the configuration, the 20 shared parameters (bound to Generic Models), a Data Silo family, the configured area schemes, and the area plans.

### MCP server (on the agent's computer)

`mcp/pdso_mcp_server.py` is one file. [uv](https://docs.astral.sh/uv/) installs its dependencies. Add it to your MCP client. For Claude Code, put this in `.mcp.json`:

```json
{
  "mcpServers": {
    "pdso": {
      "command": "uv",
      "args": ["run", "--script", "/path/to/pdso-flowgram/mcp/pdso_mcp_server.py"],
      "env": { "PDSO_SSH_HOST": "user@revit-computer" }
    }
  }
}
```

Set `PDSO_SSH_HOST` only if Revit is on a different computer. The server then opens the SSH port forward when it is necessary (key-based SSH login is necessary). Other settings: `PDSO_API_URL`, `PDSO_REMOTE_PORT`, `PDSO_OUTPUT_DIR`.

#### Windows, same computer as Revit

Use this setup when the agent and Revit are on the same Windows computer. The server sends the API calls directly to `127.0.0.1`. It does not use SSH.

1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/). In PowerShell, run `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`.
2. Open a new terminal. Run `where uv`. Make sure that the command shows the path of `uv.exe`.
3. Put this in `.mcp.json`. Change the path to the location of `pdso_mcp_server.py` on your computer. In JSON, write each backslash two times.

```json
{
  "mcpServers": {
    "pdso": {
      "command": "uv",
      "args": ["run", "--script", "C:\\path\\to\\pdso-flowgram\\mcp\\pdso_mcp_server.py"]
    }
  }
}
```

Do not set `PDSO_SSH_HOST`. At the first start, uv downloads Python (if it is necessary) and the dependencies (`mcp`, `pillow`). Thus the first start can be slow. The next starts use the uv cache.

Proxy:

- The server does not use a proxy for the API calls. It ignores `HTTP_PROXY`, `HTTPS_PROXY` and the Windows system proxy for these calls. You do not have to set `NO_PROXY`.
- uv uses the Windows system proxy to download the dependencies (tested with uv 0.12.1). If your computer gets to the internet only through a different proxy, add `"env": { "HTTPS_PROXY": "http://<proxy host>:<port>" }` to the `pdso` entry.

The server writes the images to `%TEMP%\pdso-mcp`. To use a different folder, set `PDSO_OUTPUT_DIR`. This setup was tested on Windows 11 ARM64 with Revit 2027 and uv 0.12.1.

| Task | Tools |
| --- | --- |
| Model | `get_status`, `open_project`, `close_project`, `new_project`, `save_project`, `list_levels`, `create_level`, `list_rooms` |
| Setup | `setup_model`, `get_config`, `update_config`, `check_config` |
| Parameters | `list_parameters`, `create_parameter`, `delete_parameter` |
| Sync | `run_preflight`, `run_sync`, `get_audit_report` |
| Programs (room types and unit types) | `get_program_fields`, `list_programs`, `describe_program`, `summarize_programs`, `create_program`, `create_programs`, `update_program`, `update_programs`, `delete_program` |
| Selection | `select_elements`, `select_programs`, `get_selection` |
| Area outlines | `list_area_schemes`, `list_areas`, `draft_area`, `draft_area_from_room`, `adjust_area`, `delete_area` |
| Visualization | `render_plan`, `color_areas`, `clear_area_colors`, `export_view_image`, `show_program_volumes`, `list_views`, `show_view` |

`render_plan` draws a plan diagram from the model data and can show draft outlines before you create them. `export_view_image` gives the view as Revit draws it.

### Tracked parameters in the API

The API uses the tracked parameters of the model (see [Tracked parameters](#tracked-parameters)). It does the work of the Parameter Manager without the window.

- `POST bootstrap` sets up the parameters of a model that tracks no parameter: all built-in parameters (the *Default* preset). It does not change a model that tracks parameters.
- `GET parameters` lists the tracked parameters, and the built-in parameters that the model does not track.
- `POST parameters` (body: `name`, `type`) tracks a new parameter. The types are `string`, `multilineText`, `number`, `integer`, `area`, `length` and `yesno`. If the model has one shared parameter with the name, that parameter is used and its values stay.
- Each tracked parameter has a field name for the program routes, for filters and for `color_by`. A built-in parameter has a short name (`PDSO - Program UID` is `uid`). A custom parameter gets a name from its Revit name (`Rent per SF` is `rent_per_sf`).
- `DELETE parameters/<field or name>` stops the tracking. The Revit parameter and its values stay. With `purge=true`, the parameter is also removed from Generic Models, and its values are deleted. The required parameters cannot be deleted.
- New shared parameter definitions go into the shared parameter file that Revit is set to use, then the fallback file of the Parameter Manager. If there is none, the API uses a file in the temporary folder.

### A model that has its own Areas

A model that was not made for PDSO can have its own area schemes and Areas (for example `Gross Building` and `Rentable`).

1. `POST bootstrap` with `area_scheme_mappings`, for example `{"PDSO - GSF": "Gross Building", "PDSO - NSF": "Rentable"}`. The key is the parameter that gets the Area value. The value is the area scheme. Then the bootstrap makes no `PDSO - ...` area scheme.
2. `POST programs/batch` with `items`: `[{"area_id": <id>, "fields": {"uid": ...}}]`. Each Data Silo goes to a point inside its Area. If one item is not valid, no Data Silo is placed.
3. `POST sync`.

The Program Volume of a program gets the shape of one Area that contains the Data Silo: the Area in a scheme with the name `PDSO - GSF` if there is one, and the smallest Area if not.

### Development

The API code is imported on each request. With the flag file `%APPDATA%\pyRevit\pdso_api_dev.flag` on the Revit computer, `POST /pdso/dev/reload` drops the cached `pdso` modules, so that new code is used without a pyRevit reload. The flag also enables `POST /pdso/dev/exec`, which runs Python code in Revit. Do not keep the flag on a computer that is not for development.

The windows and the API use the same UI-free services in `lib/pdso/code/business/`.

## Repository layout

The repository uses the standard pyRevit extension layout:

```
families/                             # Revit family files (.rfa)
mcp/
└── pdso_mcp_server.py                # MCP server for the HTTP API (runs on the agent's computer)
PDSO.extension/
├── startup.py                        # Registers the HTTP API with pyRevit Routes
├── PDSO.tab/
│   └── Program Data Silo Tools.panel/
│       ├── 0.Setup.pushbutton/       # Model Setup from JSON standards (Shift+Click: options)
│       ├── 1.Config.pushbutton/      # Project configuration window
│       ├── 1b.Parameters.pushbutton/ # Parameter Manager (tracked shared parameters)
│       ├── 2.RunScripts.pushbutton/  # Harvest Areas and sync program volumes
│       └── 3.Audit.pushbutton/       # Audit report
└── lib/
    ├── common/                       # Shared helpers and UI assets
    └── pdso/
        ├── code/
        │   ├── api/                  # HTTP API: request dispatcher and route handlers
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
