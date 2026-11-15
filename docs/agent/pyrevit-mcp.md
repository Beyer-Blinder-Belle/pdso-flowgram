# Operating PDSO from an agent (MCP)

How an agent (Claude Code, Codex, Cursor, ...) can inspect and change a PDSO model in a running Revit session. Read [AGENTS.md](../../AGENTS.md) and [architecture.md](architecture.md) first for the domain model.

There are two ways in. **Use the PDSO MCP server unless it can't do what you need.**

| | PDSO MCP server (`mcp/pdso_mcp_server.py`) | pyRevit agent runtime (pyRevit 7) |
| --- | --- | --- |
| Level | PDSO tasks: programs, Areas, parameters, preflight, sync, audit, images | Raw Revit API scripts |
| Runs the sync | Yes: `run_sync` | Only by importing `PDSOSyncService` in a `run_modify` script |
| Safety net | PDSO validation (`PDSOError`), preflight before sync | Approval prompt in Revit, dry runs, one undo entry per run |
| Transport | stdio → HTTP on `127.0.0.1:48884` (pyRevit Routes), optional SSH forward | stdio → named pipe for the current Windows user |
| Use it for | Normal PDSO work | Ad-hoc questions the API doesn't answer, debugging |

## Path A: the PDSO MCP server (recommended)

Setup, the full tool table and the route table are in the root [README → HTTP API and MCP server](../../README.md#http-api-and-mcp-server). In short:

1. On the Revit computer, run `pyrevit configs routes enable`, then reload pyRevit.
2. Add the `pdso` server to `.mcp.json`: `uv run --script <repo>/mcp/pdso_mcp_server.py`. Set `PDSO_SSH_HOST` only when Revit is on another machine.
3. Check the connection with `get_status`.

### A typical session

| Step | Tools |
| --- | --- |
| Orient | `get_status`, `get_config`, `check_config`, `list_levels` |
| Make a model ready (only if asked) | `setup_model` (bootstrap: creates only what's missing) |
| Learn the data shape | `get_program_fields` (field names such as `uid`), `list_parameters` |
| Read the program | `list_programs`, `summarize_programs`, `describe_program`, `list_areas` |
| See it | `render_plan` (diagram from model data, can preview draft outlines), `export_view_image`, `color_areas` |
| Change it (only what the user asked) | `update_program(s)`, `create_program(s)`, `draft_area`, `adjust_area` |
| Push the data through | `run_preflight` → fix ✗ items → `run_sync` → `get_audit_report` |
| Show the user | `select_programs`, `select_elements`, `show_view`, `show_program_volumes` |

### Rules for Path A

- **Never pass `force=true` to `run_sync`** unless the user explicitly says to skip failed preflight checks. Fix the ✗ items instead, or report them.
- **Confirm before destructive calls**: `delete_program`, `delete_area`, `delete_parameter` (especially with `purge=true`, which deletes values), `close_project` without saving, and `new_project`/`open_project` when the current model has unsaved changes.
- `update_config` changes per-project setup: area scheme mappings, the family prefix and level mapping. Wrong values orphan Program Volumes. Only change config when the user asks, and say what it affects.
- Use field names from `get_program_fields`, not Revit parameter names, in program calls.
- Units are decimal feet and square feet everywhere.
- The HTTP API has no authentication. Don't suggest exposing port 48884, and don't enable the dev flag (`/pdso/dev/exec`) on a non-dev machine.
- `create_parameter` adds the `PDSO - ` prefix to a custom name, as the Parameter Manager does: `Room Finish` becomes `PDSO - Room Finish`. The field name (`room_finish`) is the same either way. A name the model already has a shared parameter for is kept as given, so its values are reused.
- The program reply's `areas` (from `containing_areas`) is a dict keyed by area scheme name, `{"PDSO - GSF": {"id", "number", "name", "area"} or null, ...}`, not a list.

### Drawing Areas from rooms

There are two ways, and they give different outlines:

| Outline | How | Suits |
| --- | --- | --- |
| The room's inside faces | `draft_area_from_room` traces the Room boundary as Revit computes it (wall finish faces by default). `inset` can only move it inwards. | NSF |
| Wall centerlines | Compute the outline yourself, for example by offsetting the room outline outwards by half of each wall's thickness, or by reading the room boundary with `SpatialElementBoundaryLocation.Center` in a Path B query. Check it with `render_plan` and `draft_outlines`, then create it with `draft_area` and the points. | GSF |

So don't use `draft_area_from_room` for GSF Areas that should run to wall centerlines: the GSF comes out too small.

## Path B: the pyRevit agent runtime (low level)

> Status, checked 2026-11-14: the runtime shipped in **pyRevit 7.0** (October 2026) and is **experimental and off by default**. Facts below come from the [pyRevit agent runtime docs](https://docs.pyrevitlabs.io/agent-runtime/). Recipes 1–5 were run against a PDSO model on 2026-11-14 (Revit 2026.4, pyRevit 7.0.1, `pyrevit mcp install claude`). Importing PDSO services through `workspace` works too. The sync script hasn't been run yet. Items marked *unverified* haven't been tested; confirm them on first use and update this file.

### Setup (once per workstation)

Enable the agent host in Revit under **pyRevit → Settings**, or from a terminal:

```bash
pyrevit configs agent enable
```

```bash
pyrevit mcp install claude
```

```bash
pyrevit agent status
```

- Reload pyRevit or restart Revit after `enable`.
- **Restart the Claude Code session after `install`.** Claude Code loads MCP servers only when a session starts, so the `pyrevit` tools don't appear until then. `/mcp` shows whether it's connected.
- `install` takes `claude`, `codex`, `cursor`, `vscode` or `opencode`. Add `--project` to register for this repo only, which writes `.mcp.json`.
- Transport: the client starts `pyrevit mcp` (stdio), which talks to Revit over a named pipe restricted to the current Windows user. There's no TCP port.
- Approval policy: `pyrevit configs agent policy ask` (the default) asks for approval in Revit for every modify run. `readonly` allows queries and dry runs only. `auto` commits without asking. Use `ask` on real projects.
- With several Revit sessions open, pass `revit="2026"` (year or process id) on each tool call.

### Tools and what to use them for

| Tool | Use it in PDSO work to |
| --- | --- |
| `get_context` | Confirm the active document is a **project** (PDSO commands need one), see the engine (`scripting`), levels, selection and active view. Call it first. |
| `run_query` | Read anything: config, Data Silos, Program Volumes, health checks. Changes are always rolled back. |
| `run_modify` | Change Data Silo values. Always do a `dry_run=true` pass first. |
| `inspect_elements` | Dump the parameters of up to 50 elements (Data Silos, Areas, Program Volumes) by id. |
| `show_elements` | Select or isolate problem elements for the user (no model change). |
| `capture_view` | Screenshot an area plan or 3D view to check Program Volume shapes. |
| `lookup_revit_api` | Check Revit API signatures for the running Revit version (for example, `ElementId.Value` vs `IntegerValue`). |
| `get_run` | Fetch the record of an earlier run. |

The runtime can't press ribbon buttons yet: `list_commands`/`run_command` and the `agent:` block in `bundle.yaml` are "Phase 3, planned". To run PDSO workflows from Path B, import the services (next sections), or use Path A.

### Script contract

The runtime injects `doc`, `uidoc`, `app`, `uiapp`, `DB`, `UI` and `inputs` (a dict). Assign `result` to return JSON-serializable data; printed output is returned too.

- Engine: IronPython by default (2.7.12 or 3.4.2, whichever pyRevit is attached to). CPython 3.12 is also available with `engine="cpython"`. Read `get_context().scripting` instead of guessing. Write the recipes below in syntax both versions accept: no f-strings, use `.format()`.
- `DB` is injected. The `revit-scripting` skill also shows `from pyrevit.api import DB, UI`, which works too.
- **IronPython can't return non-ASCII text.** A `result` containing `²` (from `AsValueString()` on an area, for example `94 m²`), `é` or similar fails with `ResultSerializationError`. Return raw numbers (`AsDouble()`, Revit internal units: feet and square feet) or run with `engine="cpython"`.
- **CPython: put the logic in a function.** List and dict comprehensions at the top level of a script can't see the script's own variables (the run fails with `'NoneType' object is not subscriptable` and "assigning None to unbound local" warnings). Wrap the script in `def main(doc, DB, inputs): ...` and end with `result = main(doc, DB, inputs)`.
- `run_query`: `timeout_s` defaults to 300. `workspace=<folder>` puts that folder on `sys.path` and re-imports it fresh on every run.
- `run_modify`: **the script opens and commits its own `DB.Transaction`.** The runtime wraps the whole run in a `TransactionGroup` named `Agent: <title>`, so it shows up as one undo entry. A transaction left open fails the run.
- Changing another open document fails the run. Family documents opened with `EditFamily` are allowed, because they have no file.
- Revit dialogs are closed automatically and listed in the result.
- Return ids with `ElementId.Value` (Revit 2024+). `IntegerValue` was removed in later versions.
- **IronPython on Revit 2026: `DB.ElementId(int)` is ambiguous** (it matches both the `Int64` and the `BuiltInParameter`/`BuiltInCategory` constructors). Write `DB.ElementId(System.Int64(i))` after `import System`.
- **A dry run doesn't undo file writes.** Creating a shared parameter writes its definition to the shared parameter file, and that write stays after the rollback. It's harmless: the real run finds and reuses the definition.
- A modal Revit dialog (for example "Project Not Saved Recently") blocks every run with `revit_busy` until the user answers it.

### Using PDSO's own library (optional)

The recipes below use only the Revit API, so they work without importing PDSO code. To reuse PDSO services instead, make its `lib` importable. The services are `PDSOPreflightService`, `PDSOSyncService`, `config_service.get_config`, `program_service` and `get_registry`.

```python
import sys
PDSO_LIB = inputs.get("pdso_lib")  # e.g. r"C:\...\pdso-flowgram\lib"
if PDSO_LIB and PDSO_LIB not in sys.path:
    sys.path.append(PDSO_LIB)
```

Or pass `workspace=<repo>/lib` to `run_query`.

That one folder covers `pdso`, `common` and the vendored `extensible_storage` library. The most useful import is `from pdso.code.business.parameter_registry_service import get_registry`.

The services already run outside a pyRevit command (the HTTP API uses them), so they should work in agent runs too.

Verified 2026-11-14: PDSO services import in agent runs with `workspace=<repo>/lib`.

*Unverified:*

- Whether `PDSOSyncService` works inside `run_modify`'s transaction group. Phase 2 edits and reloads family documents, which the runtime allows. Test with `dry_run=true` on a test model first.

If any of these fail, fall back to the raw-API recipes.

### Recipes

Each block is a complete script for `run_query` unless it says otherwise.

#### 1. Read Project Config (extensible storage)

```python
from System import Guid, String, Double, Boolean
from System.Collections.Generic import IDictionary
ES = DB.ExtensibleStorage

CONFIG_SCHEMA = Guid("7b533d48-f078-48ad-a393-48d73b71e96b")
SIMPLE = {"System.String": String, "System.Boolean": Boolean}

def eid(e):
    return e.Value if hasattr(e, "Value") else e.IntegerValue

def read_field(ent, f):
    name, vt = f.FieldName, f.ValueType.FullName
    if f.ContainerType == ES.ContainerType.Map:
        if vt == "Autodesk.Revit.DB.ElementId":
            d = ent.Get[IDictionary[String, DB.ElementId]](name)
            return dict((k, eid(d[k])) for k in d.Keys)
        if vt == "System.Guid":
            d = ent.Get[IDictionary[String, Guid]](name)
            return dict((k, str(d[k])) for k in d.Keys)
        d = ent.Get[IDictionary[String, String]](name)
        return dict((k, d[k]) for k in d.Keys)
    if vt == "System.Double":
        # Use a unit the field accepts: program_volume_extrusion_height is a
        # plain Number (feet by convention), so Feet is rejected.
        spec = f.GetSpecTypeId()
        units = [] if spec.Empty() else list(DB.UnitUtils.GetValidUnits(spec))
        unit = next((u for u in units if u.TypeId == DB.UnitTypeId.General.TypeId),
                    units[0] if units else None)
        return ent.Get[Double](name, unit) if unit else ent.Get[Double](name)
    if vt in SIMPLE:
        return ent.Get[SIMPLE[vt]](name)
    return "<{} not decoded>".format(vt)

schema = ES.Schema.Lookup(CONFIG_SCHEMA)
ent = doc.ProjectInformation.GetEntity(schema) if schema else None
if ent is None or not ent.IsValid():
    result = {"configured": False, "hint": "Ask the user to open Project Config and Save."}
else:
    cfg = {"configured": True}
    for f in schema.ListFields():
        try:
            cfg[f.FieldName] = read_field(ent, f)
        except Exception as ex:
            cfg[f.FieldName] = "<error: {}>".format(ex)
    result = cfg
```

Fields to expect:

- `data_silo_family_name`, `program_volume_family_name_prefix`
- `area_scheme_mappings`, `levels_mapping`, `uid_pattern`, `program_volume_extrusion_height`, `worksets`, `last_run_timestamp`
- `tethered_parameters`: the **tracked parameter list** (`{name: guid}`). It's empty if the Parameter Manager has never been applied, and then Sync Data is blocked.

The other recipes take `family_name`, `prefix` and `params` (the keys of `tethered_parameters`) from this result, through `inputs`.

#### 2. List Data Silos and their data

`inputs`: `{"family_name": "<data_silo_family_name>", "params": [<tracked names from recipe 1>]}`. If `params` is omitted, the script falls back to the built-in names.

```python
BUILT_IN = [
    "PDSO - Program UID", "PDSO - Program Display Name", "PDSO - Unit Layout Type",
    "PDSO - Program Type", "PDSO - Use Group", "PDSO - Planning Dept. Misc",
    "PDSO - Accessibility", "PDSO - Program Stacking", "PDSO - Program Market Segment",
    "PDSO - Misc Modifier", "PDSO - Load Factor", "PDSO - DU Bedroom Count",
    "PDSO - Bathroom Count", "PDSO - Bathroom Types", "PDSO - Kitchen Count",
    "PDSO - Kitchen Types", "PDSO - ZFA", "PDSO - NSF", "PDSO - GSF", "PDSO - Last Synced",
]
PDSO_PARAMS = inputs.get("params") or BUILT_IN

def eid(e):
    return e.Value if hasattr(e, "Value") else e.IntegerValue

def value(p):
    if p is None or not p.HasValue:
        return None
    st = p.StorageType
    if st == DB.StorageType.String:
        return p.AsString()
    if st == DB.StorageType.Integer:
        return p.AsInteger()  # Integer and Yes/No custom parameters
    if st == DB.StorageType.Double:
        return round(p.AsDouble(), 2)  # internal units: sq ft for areas, ft for lengths
    return p.AsValueString()

fam = inputs["family_name"]
rows = []
col = (DB.FilteredElementCollector(doc)
       .OfCategory(DB.BuiltInCategory.OST_GenericModel)
       .OfClass(DB.FamilyInstance))
for fi in col:
    if fi.Symbol.Family.Name != fam:
        continue
    level = doc.GetElement(fi.LevelId)
    row = {"id": eid(fi.Id), "level": level.Name if level else None}
    for name in PDSO_PARAMS:
        row[name] = value(fi.LookupParameter(name))
    rows.append(row)
result = {"count": len(rows), "data_silos": rows}
```

#### 3. Health check (no sync needed)

Builds on recipe 2. Add this after building `rows` (on CPython, put both in a function first; see Script contract):

```python
import re
from collections import Counter

uids = Counter(r["PDSO - Program UID"] for r in rows)
pattern = inputs.get("uid_pattern") or ""
result = {
    "duplicate_uids": [u for u, n in uids.items() if n > 1],
    "missing_uid": [r["id"] for r in rows if not r["PDSO - Program UID"]],
    "bad_uid_pattern": [r["id"] for r in rows
                        if pattern and r["PDSO - Program UID"]
                        and not re.match(pattern, r["PDSO - Program UID"])],
    "missing_gsf": [r["id"] for r in rows if not r["PDSO - GSF"]],
    "never_synced": [r["id"] for r in rows if not r["PDSO - Last Synced"]],
}
```

This doesn't replace preflight or Audit Report, which also check Area containment, level mapping and boundaries. It's a quick triage before you ask the user to run them.

#### 4. Program Volumes per Data Silo

`inputs`: `{"prefix": "<program_volume_family_name_prefix>"}`

```python
from collections import defaultdict

def eid(e):
    return e.Value if hasattr(e, "Value") else e.IntegerValue

prefix = inputs["prefix"]
by_family = defaultdict(list)
col = (DB.FilteredElementCollector(doc)
       .OfCategory(DB.BuiltInCategory.OST_GenericModel)
       .OfClass(DB.FamilyInstance))
for fi in col:
    fname = fi.Symbol.Family.Name
    if fname.startswith(prefix):
        lvl = doc.GetElement(fi.LevelId)
        by_family[fname[len(prefix):]].append({"id": eid(fi.Id), "level": lvl.Name if lvl else None})
result = {"uids": dict(by_family), "family_count": len(by_family)}
```

Compare with recipe 2 to find orphans: a UID here with no Data Silo means a renamed or deleted unit. Compare instance counts with `1 + stacked levels` to find stacking that hasn't been synced.

#### 5. Edit Data Silo values (`run_modify`)

`inputs`: `{"family_name": "...", "tracked": [<tracked names>], "area_params": [<area_scheme_mappings keys>], "updates": {"00-07-01": {"PDSO - DU Bedroom Count": 2, "PDSO - Unit Layout Type": "2BR - Type A"}}}`

```python
fam, updates = inputs["family_name"], inputs["updates"]
tracked = set(inputs["tracked"])
# Sync-owned: harvested SF parameters (the keys of area_scheme_mappings)
# and Last Synced.
PROTECTED = set(inputs.get("area_params") or []) | {
    "PDSO - GSF", "PDSO - NSF", "PDSO - ZFA", "PDSO - Last Synced"}

bad = [n for u in updates.values() for n in u if n in PROTECTED or n not in tracked]
if bad:
    raise Exception("Refusing to write sync-owned or untracked parameters: {}".format(bad))

silos = {}
col = (DB.FilteredElementCollector(doc)
       .OfCategory(DB.BuiltInCategory.OST_GenericModel)
       .OfClass(DB.FamilyInstance))
for fi in col:
    if fi.Symbol.Family.Name == fam:
        p = fi.LookupParameter("PDSO - Program UID")
        if p and p.AsString():
            silos[p.AsString()] = fi

changed, missing = [], []
t = DB.Transaction(doc, "PDSO - Agent edit Data Silos")
t.Start()
try:
    for uid, params in updates.items():
        fi = silos.get(uid)
        if fi is None:
            missing.append(uid)
            continue
        for name, val in params.items():
            p = fi.LookupParameter(name)
            if p is None or p.IsReadOnly:
                missing.append("{}:{}".format(uid, name))
                continue
            st = p.StorageType
            if st == DB.StorageType.Double:
                ok = p.Set(float(val))
            elif st == DB.StorageType.Integer:
                ok = p.Set(int(val))
            else:
                ok = p.Set(u"{}".format(val))
            (changed if ok else missing).append("{}:{}".format(uid, name))
    t.Commit()
except Exception:
    t.RollBack()
    raise
result = {"changed": changed, "skipped": missing}
```

Run it with `dry_run=true` first and show the user the change set. The values only reach Program Volumes after the user runs **Sync Data**.

### Rules for Path B

- **Write only to Data Silos**, and only **tracked**, user-owned parameters. Never write Program Volumes, the harvested SF parameters, `PDSO - Last Synced`, or Area parameters.
- **Don't change which parameters are tracked** from a script. That's the Parameter Manager's job (`PDSOParameterSetupService.apply`), and untracking deletes values.
- **Don't change Program UIDs** without telling the user. The next sync renames the Program Volume family, but only if its instances were synced by a PDSO version that records their Data Silo (see architecture known gaps).
- **Don't write extensible storage config** (Project Config) from scripts. It's per-project setup, and wrong values orphan elements. Ask the user to change it in Project Config.
- **Don't create or delete Areas or Data Silos** unless the user asked for that exact change.
- In workshared models, elements owned by someone else fail to edit. Report them; don't retry.
- Model data is sent to the AI provider, and run records are kept in plaintext under `%APPDATA%\pyRevit\agent\runs` for 14 days. Don't run this on projects whose data can't leave the machine.

### Running the sync from Path B

Prefer `run_sync` on the PDSO MCP server. If you must use the runtime, a `run_modify` script (after a `dry_run=true` pass) can do this:

```python
import sys
sys.path.append(inputs["pdso_lib"])  # <repo>/lib
from pdso.code.business.preflight_service import PDSOPreflightService
from pdso.code.business.sync_service import PDSOSyncService

pre = PDSOPreflightService(doc)
pre.run_preflight_checks()
if not pre.can_run_sync:
    result = {"ran": False, "preflight": pre.results()}
else:
    result = {"ran": True, "summary": PDSOSyncService(doc, options=inputs.get("options")).run()}
```

The services open their own transactions, which nest inside the runtime's `Agent: <title>` group.
