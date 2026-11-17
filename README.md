# PDSO Flowgram

**Program Data Silo Objects (PDSO)**: a [pyRevit](https://github.com/pyrevitlabs/pyRevit) extension that keeps architectural program data (target areas, program names, unit types) in sync between Revit **Areas** and 3D **program volume** families.

Built by the DCT team at [Beyer Blinder Belle](https://www.beyerblinderbelle.com). This is BBB's first open-source project.

**New to PDSO?** Start with the [User Guide](docs/user-guide.md) and the [Parameter Reference](docs/parameter-reference.md). **Coding agents** start at [AGENTS.md](AGENTS.md). All docs: [docs/](docs/README.md).

## What it does

The **Program Data Silo Tools** panel (on the **PDSO** ribbon tab) has five commands. The first three set up a model, in this order:

| Button | What it does |
| --- | --- |
| **Project Config** | Sets up the model-level PDSO configuration (area scheme mappings, level mapping, Data Silo family, program volume family). The configuration is stored in the model with Revit Extensible Storage. |
| **Parameter Manager** | Chooses which parameters PDSO tracks and creates them as shared parameters, bound to Generic Models (and optionally embedded in the Data Silo family). Must be run at least once per model before syncing. |
| **Quick Setup** | Creates everything else PDSO needs in the model: the area schemes from Project Config, the Data Silo family, view templates, working views and schedules. What it creates comes from JSON [standards files](lib/pdso/standards/README.md) that a company or project can customize. Shift+Click to choose or export a standards folder. |
| **Sync Data** | Syncs parameter data between Area objects, PDSO objects, and program volume (mass) objects, after a preflight check. |
| **Audit Report** | Checks PDSO objects for data consistency, sync problems, and Area boundary issues. |

All commands need an open **project** document; they are disabled in the family editor.

## Tracked parameters

The PDSO tools only sync the parameters a model *tracks*. The tracked list is set in the **Parameter Manager** and saved in the model's PDSO configuration (Extensible Storage).

- **Built-in parameters** (Program UID, Program Stacking, ZFA/NSF/GSF, Last Synced, and the program data fields) ship with the tools as options. Nothing is created until you click **Apply Parameters**. On a model's first run, every built-in is ticked.
- **Locked parameters** can't be untracked. They are *Program UID*, *Program Stacking*, *Last Synced*, and any parameter an Area Scheme mapping writes into.
- **Custom parameters** can be added, with types Text, Multiline Text, Number, Integer, Area, Length and Yes/No. Once created, their name and type can't change.
- **Shared parameter file**: the manager uses the file set in Revit (*Manage > Shared Parameters*). If that file is missing, it asks you to pick or create one, remembers your choice for the PDSO tools, and leaves Revit's setting unchanged. New definitions go in a `PDSO` group. Existing definitions are reused (first by GUID, then by name) instead of being duplicated.
- **Presets** tick a set of parameters for a type of project. *Default* (every built-in parameter) and *Blank* (only the required ones) are built in. **Save as Preset** stores the current ticks as a JSON file in the presets folder. That folder is `%APPDATA%\PDSO\ParameterPresets` unless you choose another; point it at a shared network folder so the team uses the same presets. Presets keep the GUIDs of custom parameters, so they match across projects. Choosing a preset changes nothing in the model until you click **Apply Parameters**. To delete a preset, delete its file from the presets folder.
- **Sync Data** is blocked until the manager has run, and blocked again whenever a tracked parameter is missing from Generic Models.

The built-in GUIDs are fixed in `lib/pdso/code/data/shared_parameter_constants.py` and must never change.

## Requirements

- Autodesk Revit 2024 or later (the shipped Data Silo family is for Revit 2026; older versions get a placeholder family)
- [pyRevit](https://github.com/pyrevitlabs/pyRevit). For the HTTP API, a build whose Routes pass query parameters to the handler: pyRevit 6.1 doesn't, and then list filters, document targeting and request ids are lost. `GET status` reports `pyrevit.query_parameters`, and the MCP server's `get_status` warns when it is false.
- The [`extensible-storage-pyrevit`](https://github.com/khorn06/extensible-storage-pyrevit) library by Kris Horn. A copy is bundled in `lib/extensible_storage`, so no separate install is needed (see its `NOTICE.md`).

## Installation

### From pyRevit (recommended)

In Revit, click **pyRevit → Extensions**. Under **Git information**, enter:

- Name: `PDSO`
- Git URL: `https://github.com/Beyer-Blinder-Belle/pdso-flowgram.git`

Leave Path and Token empty, and click **Add and install**. pyRevit clones the repository into `%APPDATA%\pyRevit\Extensions\PDSO.extension` and reloads. Update it later with pyRevit's **Update** button or from the Extensions window.

Or use the pyRevit CLI, then reload pyRevit:

```bash
pyrevit extend ui PDSO https://github.com/Beyer-Blinder-Belle/pdso-flowgram.git --branch=main
```

Pass `--branch=main`: the CLI uses `master` when no branch is given.

### For development

pyRevit loads folders whose names end in `.extension` from its search paths. Clone into such a folder, and add the folder above it as a search path:

```bash
git clone https://github.com/Beyer-Blinder-Belle/pdso-flowgram.git PDSO.extension
```

```bash
pyrevit extensions paths add <folder that contains PDSO.extension>
```

To keep a clone that has another name (such as `pdso-flowgram`), make a directory junction named `PDSO.extension` in a folder of its own, and add that folder instead. In PowerShell:

```powershell
New-Item -ItemType Junction -Path C:\pyRevit-dev\PDSO.extension -Target C:\path\to\pdso-flowgram
```

```bash
pyrevit extensions paths add C:\pyRevit-dev
```

### Upgrading from the old layout

Until this change the extension was in a `PDSO.extension` subfolder of the repository, and the search path was the repository folder. After you pull this change, the PDSO tab disappears until you re-point pyRevit:

1. Forget the old search path: `pyrevit extensions paths forget <path-to-pdso-flowgram>`.
2. Install from pyRevit, or set up a development folder as above.
3. Reload pyRevit. Delete any leftover `PDSO.extension` folder inside the clone (Windows can't remove files that Revit had open during the pull).

## Revit family

PDSO needs the **Program Data Silo Object** family loaded in your project. The family file is in [`families/`](families/):

- `PDSO - Program Data Silo Object - Rvt26.rfa` (Revit 2026)

The family only carries the three required parameters: `PDSO - Program UID`, `PDSO - Program Stacking` and `PDSO - Last Synced`. Each project adds the parameters it needs in the **Parameter Manager**, which binds them to Generic Models. So a residential and an office project can use the same family.

**Quick Setup** loads it, adds the model's tracked parameters to it, and, unless Project Config already names a different loaded family, sets it as the Data Silo family in Project Config. To do it by hand, load the family, then open **Project Config** and set the Data Silo family name to match the loaded family's name.

Before reloading this family into a project that used an older copy, track every parameter you use in the Parameter Manager: values of untracked parameters live only in the old family and are lost on reload.

## Standards

Quick Setup is driven by JSON files in [`lib/pdso/standards/`](lib/pdso/standards/). They ship with working defaults. A company can override any of them from a shared folder (set the `PDSO_STANDARDS_DIR` environment variable, or Shift+Click Quick Setup), and a project can override them from a `PDSO Standards` folder next to the model. See the [standards README](lib/pdso/standards/README.md) for every option.

## HTTP API and MCP server

The extension also has an HTTP API, so that a program or an AI agent can do the work of the three windows, and can create and change programs and Area outlines. The API runs in Revit through [pyRevit Routes](https://docs.pyrevitlabs.io/reference/pyrevit/routes/). An [MCP](https://modelcontextprotocol.io) server gives the API to agents such as Claude Code. The agent can be on the same computer as Revit, or on a different computer.

```
agent  --MCP (stdio)-->  mcp/pdso_mcp_server.py  --HTTP (direct, or SSH port forward)-->  Revit + pyRevit + PDSO extension
```

All lengths are decimal feet. All areas are square feet.

### Enable the API (on the Revit computer)

```bash
pyrevit configs routes enable
```

Then reload pyRevit (a Revit restart isn't needed). The API is at `http://127.0.0.1:48884/pdso/` (48884 is the default pyRevit Routes port). To check it, open `http://127.0.0.1:48884/pdso/status` in a browser on the Revit computer: it shows `"ok": true`, the open model and `api_version`. `GET /pdso/routes` lists all routes. pyRevit Routes has no authentication. It listens on `127.0.0.1` by default; PDSO doesn't enforce that, so make sure the Routes host setting in pyRevit hasn't been changed to a network address. To use it from a different computer, use an SSH port forward. Do not open the port to a network.

### Routes

| Group | Routes |
| --- | --- |
| Status and model | `GET status`, `GET document`, `POST document/new`, `POST document/open`, `GET/POST levels`, `GET views`, `POST views/activate` |
| Setup | `GET setup`, `POST bootstrap`, `GET/PUT config`, `GET config/checks` |
| Parameters | `GET/POST parameters`, `DELETE parameters/<field or name>` |
| Sync | `GET preflight`, `GET sync/options`, `POST sync`, `GET audit` |
| Programs (Data Silos) | `GET programs/schema`, `GET programs/summary`, `GET/POST/PUT programs`, `POST programs/batch`, `GET/PUT/DELETE programs/<uid or id>` |
| Program Volumes | `GET program-volumes/orphans`, `POST program-volumes/purge-orphans` (families that belong to no Data Silo) |
| Selection | `GET/POST selection` |
| Areas | `GET area-schemes`, `GET/POST area-plans`, `GET/POST areas`, `POST areas/derive`, `GET/PUT/DELETE areas/<id>` |
| Visualization | `POST views/export`, `POST views/color-areas`, `POST views/clear-colors`, `POST views/program-volumes` |
| Open documents | `GET documents`, `POST documents/open` (in the background), `POST documents/save` (`document`, default the active one), `POST documents/close` (`document` or `title`; not the active one). `document/save` and `document/close` are older names of the same routes. |
| Unit Layout Catalogue | `GET catalogue/layouts`, `GET/DELETE catalogue/layouts/<id>`, `POST catalogue/harvest`, `POST catalogue/harvest-units`, `POST catalogue/recommend`, `POST catalogue/similar-units`, `GET catalogue/preview`, `GET catalogue/item`, `POST catalogue/place`, `GET/DELETE catalogue/unit-items`. Older names, accepted until the next API version: `POST catalogue/similar-rooms`, `GET/DELETE catalogue/room-items`, and the argument `room` for `unit`. |

A request can name the open document that it works on with the field `document` (a title or a file path). Without it, a request works on the active document. `POST documents/open` opens a file but does not show it, so a person or a different agent can use the same Revit at the same time.

The API runs requests one at a time on Revit's main thread. Still, send them one at a time: when requests overlapped, pyRevit Routes sometimes gave a client the reply of a different request (see "Versions, reloads and more than one client").

A reply is `{"ok": true, "data": ...}` or `{"ok": false, "error": {"type", "message", "traceback"}}`.

`POST bootstrap` makes a model ready for PDSO the same way as the ribbon's three setup buttons. It writes the configuration (and `area_scheme_mappings`, if given), sets up the 20 built-in parameters if the model tracks none, then runs **Quick Setup** from the model's standards files: area schemes, the Data Silo family, view templates, working views and schedules. It adds nothing of its own, so it makes the same model as the ribbon. The views step makes the area plans `views.json` turns on (by default `PDSO - GSF - <level>`); plans of other schemes are made, with the same naming, when Areas are drawn in them. `levels` (names or ids) chooses the levels; without it, the levels `views.json` gives plans to (all but Roof and Parapet). If no Data Silo family file can be loaded (a Revit older than the shipped family), Quick Setup builds a placeholder family. The reply lists what it made and, under `setup`, the Quick Setup report rows. Items that are there are left alone.

The body can narrow the scope. `parts` lists the Quick Setup steps to run (`area_schemes`, `families`, `view_templates`, `views`, `schedules`; default: the ones `setup.json` turns on). `parameters` is `"all"` (the 20 built-in parameters, the default) or `"required"` (only Program UID, Program Stacking, Last Synced and the area parameters); it only matters for a model that tracks no parameters yet. `GET setup` (read-only) describes each part and says what the model has already.

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

Or register the server with the `claude` command instead of editing `.mcp.json`. Run it in **Command Prompt** or **Git Bash**, in the repository folder. PowerShell drops the `--`, and `claude` then rejects `--script`. `-s local` keeps the entry private to you and this project:

```bash
claude mcp add pdso -s local -- "C:\Users\<you>\.local\bin\uv.exe" run --script "C:\path\to\pdso-flowgram\mcp\pdso_mcp_server.py"
```

```bash
claude mcp get pdso
```

`claude mcp get pdso` starts the server once and should show `Status: ✔ Connected`.

The uv installer adds `%USERPROFILE%\.local\bin` to your PATH, but programs that were already running don't see it until they restart. That includes the Claude Code app. The full path to `uv.exe` (as above, or as `"command"` in `.mcp.json`) works either way. Restart the Claude Code session afterwards: it loads MCP servers only when a session starts.

Proxy:

- The server does not use a proxy for the API calls. It ignores `HTTP_PROXY`, `HTTPS_PROXY` and the Windows system proxy for these calls. You do not have to set `NO_PROXY`.
- uv uses the Windows system proxy to download the dependencies (tested with uv 0.12.1). If your computer gets to the internet only through a different proxy, add `"env": { "HTTPS_PROXY": "http://<proxy host>:<port>" }` to the `pdso` entry.

The server writes the images to `%TEMP%\pdso-mcp`. To use a different folder, set `PDSO_OUTPUT_DIR`. This setup was tested on Windows 11 ARM64 with Revit 2027 and uv 0.12.1, and on Windows 11 x64 with Revit 2026 and uv 0.13.8 (registered with `claude mcp add`, checked over MCP with `get_status`, `get_setup_status` and `setup_model`).

| Task | Tools |
| --- | --- |
| Model | `get_status`, `open_project`, `close_project`, `new_project`, `save_project`, `list_levels`, `create_level` |
| Setup | `get_setup_status`, `setup_model`, `get_config`, `update_config`, `check_config` |
| Parameters | `list_parameters`, `create_parameter`, `delete_parameter` |
| Sync | `run_preflight`, `run_sync`, `get_audit_report` |
| Programs (space types and unit types) | `get_program_fields`, `list_programs`, `describe_program`, `summarize_programs`, `create_program`, `create_programs`, `update_program`, `update_programs`, `delete_program`, `list_orphaned_program_volumes`, `purge_orphaned_program_volumes` |
| Selection | `select_elements`, `select_programs`, `get_selection` |
| Area outlines | `list_area_schemes`, `list_areas`, `draft_area`, `derive_areas`, `adjust_area`, `delete_area` |
| Visualization | `render_plan`, `color_areas`, `clear_area_colors`, `export_view_image`, `show_program_volumes`, `list_views`, `show_view` |
| Open documents | `list_open_documents`, `open_document_in_background`, `set_target_document`, `save_document`, `close_background_document` |
| Unit Layout Catalogue | `list_layouts`, `get_layout`, `harvest_layout`, `harvest_units`, `preview_pull_from_catalogue`, `pull_from_catalogue`, `recommend_layouts`, `find_similar_units`, `preview_similar_units`, `place_layout`, `list_unit_items`, `describe_item`, `clear_unit_items`, `delete_layout` |

`render_plan` draws a plan diagram from the model data and can show draft outlines before you create them. `export_view_image` gives the view as Revit draws it.

### Work from intent

The server instructions give an agent the PDSO workflow, so that a user can ask in plain language ("map out the program of the second floor of this school") and does not need to know PDSO. The tools that make this short:

- Setup keeps the user in the loop. The agent calls `get_setup_status` (what each part makes and what the model has), agrees the scope with the user, then calls `setup_model`. `parts` and `parameters` choose between the full package (the default: what the ribbon's setup buttons make) and the minimum the sync needs (`parts=["area_schemes", "families"]`, `parameters="required"`). `setup_model` can add parts later.
- `setup_model()` with no levels makes area plans for Quick Setup's default levels (`views.json`: all levels but Roof and Parapet).
- PDSO works with Areas and area schemes only. It does not read Revit Rooms.
- `draft_area` draws one Area from points, in the scheme that feeds `PDSO - GSF`. `render_plan` with `draft_outlines` previews the outlines first.
- `derive_areas(scheme, offset, area_ids, from_scheme, level)` makes Areas in another scheme from those Areas: the same outline moved in by `offset` feet, with the source Area's name and number. For example `derive_areas("PDSO - NSF", 0.25, from_scheme="PDSO - GSF", level="L2")`. Arcs stay arcs and holes grow by the offset. An Area that fails does not stop the others; `errors` lists it.
- `create_programs` places one program in each Area by `area_id`.
- `run_sync` returns `program_volumes`: the families and the id and level of each instance.
- `show_view(view, fit=true)` opens a plan and zooms an area plan to its Areas in one call. `view_type` selects one view when a floor plan and an area plan have the same name.
- `show_program_volumes` shows the 3D view in the Revit window.

### Versions, reloads and more than one client

- `GET status` returns `api_version`. `get_status` of the MCP server adds `version_warning` when the extension in Revit is not the version that the server expects.
- While pyRevit reloads, the PDSO routes are gone for some seconds. The MCP server waits (up to 60 s) and sends the request again.
- The MCP server sends a request id (`_rid`) with each request, and the API returns it (`rid`). In tests, pyRevit Routes gave a client the reply of a different request when more than one request ran at the same time. With the request id, the server finds a wrong reply: it sends a read-only request again, and it stops with a clear error for a request that changes the model. Use one client for each Revit when you can.
- `POST document/open` answers the dialog "Unresolved References" ("Ignore and continue opening the project"), so that the request does not wait for a click. The reply lists `dialogs_answered` and `dialogs_seen`.

### Tracked parameters in the API

The API uses the tracked parameters of the model (see [Tracked parameters](#tracked-parameters)). It does the work of the Parameter Manager without the window.

- `POST bootstrap` sets up the parameters of a model that tracks no parameter: all built-in parameters (the *Default* preset). It does not change a model that tracks parameters.
- `GET parameters` lists the tracked parameters, and the built-in parameters that the model does not track.
- `POST parameters` (body: `name`, `type`) tracks a new parameter. The types are `string`, `multilineText`, `number`, `integer`, `area`, `length` and `yesno`. A custom name gets the `PDSO - ` prefix, as in the Parameter Manager (`Tenant Type` becomes `PDSO - Tenant Type`). If the model has one shared parameter with the name as given, that parameter is used and its values stay.
- Each tracked parameter has a field name for the program routes, for filters and for `color_by`. A built-in parameter has a short name (`PDSO - Program UID` is `uid`). A custom parameter gets a name from its Revit name without the prefix (`PDSO - Rent per SF` is `rent_per_sf`).
- `DELETE parameters/<field or name>` stops the tracking. The Revit parameter and its values stay. With `purge=true`, the parameter is also removed from Generic Models, and its values are deleted. The required parameters cannot be deleted.
- New shared parameter definitions go into the shared parameter file that Revit is set to use, then the fallback file of the Parameter Manager. If there is none, the API uses a file in the temporary folder.

### A model that has its own Areas

A model that was not made for PDSO can have its own area schemes and Areas (for example `Gross Building` and `Rentable`).

1. `POST bootstrap` with `area_scheme_mappings`, for example `{"PDSO - GSF": "Gross Building", "PDSO - NSF": "Rentable"}`. The key is the parameter that gets the Area value. The value is the area scheme. Then the bootstrap makes no `PDSO - ...` area scheme.
2. `POST programs/batch` with `items`: `[{"area_id": <id>, "fields": {"uid": ...}}]`. Each Data Silo goes to a point inside its Area. If one item is not valid, no Data Silo is placed.
3. `POST sync`.

The Program Volume of a program gets the shape of one Area that contains the Data Silo: the Area in a scheme with the name `PDSO - GSF` if there is one, and the smallest Area if not.

### Development

`tools/deploy_vm.sh` copies `lib/` and `startup.py` to the Revit computer, checks the copy, and loads the new code: with the dev route if the flag file is there, or with a pyRevit reload if it is not (`PDSO_RELOAD=auto|dev|pyrevit|none`).

The API code is imported on each request. With the flag file `%APPDATA%\pyRevit\pdso_api_dev.flag` on the Revit computer, `POST /pdso/dev/reload` drops the cached `pdso` modules, so that new code is used without a pyRevit reload. The flag also enables `POST /pdso/dev/exec`, which runs Python code in Revit. Do not keep the flag on a computer that is not for development.

The windows and the API use the same UI-free services in `lib/pdso/code/business/`.

## Repository layout

The repository root is the pyRevit extension, so pyRevit's Extensions window can install it. pyRevit ignores the folders that aren't bundles (`families/`, `mcp/`, `tools/`, `docs/`, `catalogue/`).

```
extension.json                        # Extension details shown in pyRevit's Extensions window
startup.py                            # Registers the HTTP API with pyRevit Routes
PDSO.tab/
├── Program Data Silo Tools.panel/
│   ├── 0.Config.pushbutton/          # Project configuration window
│   ├── 1.Parameters.pushbutton/      # Parameter Manager (tracked shared parameters)
│   ├── 2.Setup.pushbutton/           # Quick Setup from JSON standards (Shift+Click: options)
│   ├── 3.RunScripts.pushbutton/      # Harvest Areas and sync program volumes
│   └── 4.Audit.pushbutton/           # Audit report
└── Unit Layout Catalogue.panel/      # Harvest All Units, Harvest Layout, Pull from Catalogue
lib/
├── common/                           # Shared helpers and UI assets
├── extensible_storage/               # Vendored third-party library (don't edit)
└── pdso/
    ├── code/
    │   ├── api/                      # HTTP API: request dispatcher and route handlers
    │   ├── business/                 # Services: sync, hashing, level mapping, storage, ...
    │   ├── catalogue/                # Unit Layout Catalogue: harvest, store, recommend, place, windows
    │   ├── data/                     # Wrappers around Revit elements, schemas, parameter constants
    │   ├── defaults.py               # Defaults of a new PDSO configuration
    │   ├── setup/                    # Quick Setup: standards loader, runner and steps
    │   └── view_models/              # WPF view models (MVVM)
    ├── standards/                    # Default JSON standards for Quick Setup
    ├── xaml/                         # WPF windows and styles
    └── documentation/                # Developer notes
families/                             # Revit family files (.rfa)
mcp/
└── pdso_mcp_server.py                # MCP server for the HTTP API (runs on the agent's computer)
tools/                                # Acceptance runner and remote deploy script
catalogue/                            # Unit Layout Catalogue samples
docs/                                 # User guide, parameter reference, agent docs
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
