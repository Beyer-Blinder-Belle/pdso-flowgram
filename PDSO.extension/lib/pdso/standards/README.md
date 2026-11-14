# PDSO standards

**Model Setup** (PDSO tab → Program Data Silo Tools → Model Setup) reads the JSON files in this folder to decide what to add to a model. The files here are the shipped defaults. Copy and edit them to change what setup creates for your company or project.

| File | What it defines |
| --- | --- |
| `setup.json` | Which steps run, in what order, and which are ticked by default |
| `parameters.json` | PDSO shared parameters and the categories they're bound to |
| `families.json` | Families to load (the Program Data Silo family) |
| `view_templates.json` | View templates and their visibility and graphics settings |
| `views.json` | Working views to create and the template each one uses |
| `schedules.json` | Schedules, their fields, filters and sorting |

## Customizing

Setup looks for each file in these folders, highest priority first. The first folder that has the file wins, and the whole file is used. Files aren't merged.

1. **Project**: a `PDSO Standards` folder next to the model (for a workshared model, next to the central model).
2. **User**: a folder picked with **Shift+Click → Use a standards folder** on the Model Setup button.
3. **Company**: the folder in the `PDSO_STANDARDS_DIR` environment variable. Use this to roll out shared standards on every workstation.
4. **Default**: this folder.

So a company folder only needs the files it changes. For example, a company that only wants different view templates ships just `view_templates.json`, and everything else comes from the defaults.

To start, use **Shift+Click → Export default standards to a folder** on the Model Setup button. It copies these files to a folder you pick (usually a shared network folder).

The setup report lists which file and folder each step used.

## Common options

- Every file accepts `"_comment"` keys anywhere. Setup ignores them.
- Every item accepts `"enabled": false` to skip it without deleting it.
- `on_existing` (per file) says what to do with an item that's already in the model. See each file below.
- Setup is safe to run again. It runs as one undo step, **PDSO Model Setup**.

### Tokens

Strings can contain these tokens:

| Token | Value |
| --- | --- |
| `{revit_year}` | Revit version, e.g. `2026` |
| `{revit_yy}` | Last two digits, e.g. `26` |
| `{repo_dir}` | The folder this extension was installed or cloned into |
| `{extension_dir}` | The `PDSO.extension` folder |
| `{standards_dir}` | The folder of the file being read |
| `{data_silo_family}` | Data Silo family name from Project Config |
| `{program_volume_prefix}` | Program Volume family name prefix from Project Config |
| `{level}`, `{area_scheme}` | Level and area scheme names (`views.json` names only) |

## setup.json

```json
{ "steps": [ { "id": "views", "title": "Create PDSO working views", "enabled": true } ] }
```

`id` is one of `parameters`, `families`, `view_templates`, `views`, `schedules`, and setup reads `<id>.json` for it. Steps run in the listed order: parameters before schedules (schedule fields need the parameters), and view templates before views.

## parameters.json

This is a proposed format. The step that adds parameters is still being built. For now it only reports which parameters are already in the model. Don't change the GUIDs: the PDSO tools find parameters by GUID.

The report can show a parameter as **Only in loaded families**. That happens when a family that uses the parameter (like the Data Silo family) is loaded. The parameter exists in the model then, but it isn't a project parameter, so it isn't listed in Project Parameters.

| Key | Meaning |
| --- | --- |
| `categories` | Categories to bind the parameters to |
| `shared_parameter_group` | Group name in the shared parameter file (default `PDSO`) |
| `parameters[].type` | `string`, `multilineText`, `number` or `area` |
| `parameters[].description` | Optional tooltip text |

### Shared parameter file

Revit can only add a shared parameter to a project from a shared parameter file. **Model Setup's parameters step does this for you**: it writes a temporary file from `parameters.json`, binds every parameter to the configured categories, and puts your own shared parameter file setting back. [`PDSO Shared Parameters.txt`](PDSO%20Shared%20Parameters.txt) is the same file, generated from the default `parameters.json`. To add the parameters by hand instead:

1. **Manage → Project Parameters → New → Shared parameter → Select**.
2. Browse to this file, choose the **PDSO** group, and pick a parameter.
3. Select **Instance**, tick **Generic Models**, and click **OK**. Repeat for each parameter.

If your company changed `parameters.json`, use **Shift+Click → Export PDSO shared parameter file** on the Model Setup button instead. It writes the file from whichever `parameters.json` Model Setup would use.

After changing the default `parameters.json`, regenerate the shipped file:

```bash
python PDSO.extension/lib/pdso/code/setup/shared_parameter_file.py
```

## families.json

| Key | Meaning |
| --- | --- |
| `name` | Family name in Revit |
| `files` | Paths to try in order. The first that exists is loaded. Relative paths resolve against this file's folder. |
| `on_existing` | `skip` (default) leaves a loaded family alone. `reload` loads it again over the existing one. |
| `overwrite_parameter_values` | With `reload`, also overwrite parameter values (default `false`) |
| `use_as_data_silo_family` | Set this family as the Data Silo family in Project Config |

## view_templates.json

`on_existing`: `update` (default) re-applies these settings to an existing template. `skip` leaves it alone.

| Key | Meaning |
| --- | --- |
| `name` | Template name |
| `view_type` | `AreaPlan`, `FloorPlan`, `CeilingPlan` or `ThreeD` |
| `scale` | Scale denominator, e.g. `96` for 1/8" = 1'-0" |
| `detail_level` | `Coarse`, `Medium` or `Fine` |
| `display_style` | e.g. `Wireframe`, `HLR`, `Shading`, `ShadingWithEdges`, `FlatColors`, `Realistic` |
| `visibility.model_categories` | `hidden` or `visible` sets every model category first. `unchanged` (default) leaves them alone. |
| `visibility.annotation_categories` | Same, for annotation categories |
| `visibility.import_categories` | `hidden`, `visible` or `unchanged` |
| `categories` | Per-category settings, applied after `visibility` (see below) |
| `uncontrolled_parameters` | `BuiltInParameter` names the template should not control, e.g. `["VIEW_PHASE"]` |

Each `categories` entry:

```json
{
  "category": "OST_AreaSchemeLines",
  "visible": true,
  "halftone": false,
  "projection_lines": { "color": "#B400FF", "weight": 8, "pattern": "Solid" },
  "cut_lines": { "color": [255, 0, 0], "weight": 4 },
  "surface": { "color": "#FFE0FF", "transparency": 50 },
  "cut_pattern": { "color": "#000000" }
}
```

- `category` is a `BuiltInCategory` name (`OST_Walls`, the same in every language), a category name as Revit shows it (`Walls`), or `Parent|Subcategory` (`Generic Models|PDSO - GSF Program Volume`).
- Colors are `"#RRGGBB"` or `[r, g, b]`. Line weights are 1–16. Surface and cut colors use a solid fill.

A new template copies its starting settings from an existing view of the same type, if the model has one. Anything the file doesn't set keeps those settings. To control a category, list it.

Useful categories: `OST_AreaSchemeLines` (area boundaries), `OST_Areas`, `OST_AreaReferenceVisibility` (Areas → Reference), `OST_AreaInteriorFillVisibility` (Areas → Interior Fill), `OST_AreaTags`, `OST_GenericModel` (Data Silos and Program Volumes), `OST_RvtLinks` (Revit links).

## views.json

`on_existing`: `skip` (default) leaves an existing view with the same name alone. `update` re-applies its template and parameters.

| Key | Meaning |
| --- | --- |
| `name` | View name. Can use `{level}` and `{area_scheme}`. |
| `view_type` | `AreaPlan`, `FloorPlan`, `CeilingPlan` or `ThreeD` |
| `area_scheme` | For area plans: the PDSO scheme key (`PDSO - GSF`). It's looked up in Project Config's area scheme mapping, so renamed schemes still work. |
| `levels` | `"all"` or a list of level names. Plans get one view per level. |
| `template` | View template name to apply |
| `parameters` | View parameters to set, e.g. `{ "View Subdiscipline": "PDSO" }` for browser organization. Parameters the template controls can't be set. |

Setup doesn't create area schemes. If a scheme is missing, create it in **Area and Volume Computations** and run setup again.

## schedules.json

`on_existing`: `skip` (default) leaves an existing schedule alone. `replace` deletes it and creates it again.

| Key | Meaning |
| --- | --- |
| `name` | Schedule name |
| `category` | Category to schedule, as in view templates |
| `fields` | Columns: `parameter` (the name in Revit's field list), plus optional `heading`, `hidden`, `totals` |
| `filters` | `parameter`, `operator` and `value`. Fields used only for filtering are added as hidden fields. |
| `sort` | `parameter`, plus optional `order` (`ascending` or `descending`), `header` and `footer` |
| `itemize` | Show every instance (default Revit behavior is `true`) |
| `grand_totals` | Show the grand total row |

Operators: `equals`, `not_equals`, `greater`, `greater_or_equal`, `less`, `less_or_equal`, `contains`, `not_contains`, `begins_with`, `not_begins_with`, `ends_with`, `not_ends_with`, `has_value`, `has_no_value`.

A field whose parameter isn't in the model yet is skipped with a warning. Run the parameters step first, then use `on_existing: "replace"` to rebuild the schedule.
