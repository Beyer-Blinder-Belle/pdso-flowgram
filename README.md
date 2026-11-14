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
| Sync | `GET preflight`, `GET sync/options`, `POST sync`, `GET audit` |
| Programs (Data Silos) | `GET programs/schema`, `GET programs/summary`, `GET/POST/PUT programs`, `GET/PUT/DELETE programs/<uid or id>` |
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
| Sync | `run_preflight`, `run_sync`, `get_audit_report` |
| Programs (room types and unit types) | `get_program_fields`, `list_programs`, `describe_program`, `summarize_programs`, `create_program`, `update_program`, `update_programs`, `delete_program` |
| Selection | `select_elements`, `select_programs`, `get_selection` |
| Area outlines | `list_area_schemes`, `list_areas`, `draft_area`, `draft_area_from_room`, `adjust_area`, `delete_area` |
| Visualization | `render_plan`, `color_areas`, `clear_area_colors`, `export_view_image`, `show_program_volumes`, `list_views`, `show_view` |

`render_plan` draws a plan diagram from the model data and can show draft outlines before you create them. `export_view_image` gives the view as Revit draws it.

### Development

The API code is imported on each request. With the flag file `%APPDATA%\pyRevit\pdso_api_dev.flag` on the Revit computer, `POST /pdso/dev/reload` drops the cached `pdso` modules, so that new code is used without a pyRevit reload. The flag also enables `POST /pdso/dev/exec`, which runs Python code in Revit. Do not keep the flag on a computer that is not for development.

The windows and the API use the same UI-free services in `lib/pdso/code/business/`.

## Repository layout

The repository uses the standard pyRevit extension layout:

```
mcp/
└── pdso_mcp_server.py                # MCP server for the HTTP API (runs on the agent's computer)
PDSO.extension/
├── startup.py                        # Registers the HTTP API with pyRevit Routes
├── PDSO.tab/
│   └── Program Data Silo Tools.panel/
│       ├── 1.Config.pushbutton/      # Project configuration window
│       ├── 2.RunScripts.pushbutton/  # Harvest Areas and sync program volumes
│       └── 3.Audit.pushbutton/       # Audit report
└── lib/
    ├── common/                       # Shared helpers and UI assets
    └── pdso/
        ├── code/
        │   ├── api/                  # HTTP API: request dispatcher and route handlers
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
