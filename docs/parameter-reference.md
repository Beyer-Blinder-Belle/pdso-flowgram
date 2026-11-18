# Data Silo Parameter Reference

These are the **built-in** PDSO parameters. They're options, not requirements: each project picks the ones it **tracks** in the **Parameter Manager**, and only tracked parameters are created in the model and synced. Projects can also add their own custom parameters there.

**Locked** parameters are always tracked: `PDSO - Program UID`, `PDSO - Program Stacking`, `PDSO - Last Synced`, and any parameter an area scheme mapping writes into (by default GSF, NSF and ZFA).

All parameters are **instance** parameters on **Generic Models**, in the `PDSO` shared parameter group. The PDSO tools find built-ins by **GUID**, so don't recreate them under a new GUID. The definitions live in [`shared_parameter_constants.py`](../lib/pdso/code/data/shared_parameter_constants.py).

**Who writes each value**

- **You**: type it on the Data Silo.
- **Sync (Area)**: Sync Data reads it from the Area the Data Silo sits in. Anything typed here is overwritten.
- **Sync (system)**: Sync Data sets it.

**Every tracked** parameter is copied from the Data Silo to its Program Volumes on each sync, so the same names are available to tag and schedule on Program Volumes. Values typed directly on a Program Volume are overwritten.

## Identity

| Parameter | Type | Who writes it | Purpose | Example |
| --- | --- | --- | --- | --- |
| `PDSO - Program UID` (locked) | Text | You | Unique ID for the unit. It names the unit's Program Volume family (`<prefix><UID>`) and ties the Data Silo to its Program Volumes. Must be unique. Convention is `BLDG-FLR-UNIT`, 2 characters each, which the default Project Config pattern enforces. | `00-07-01`, `NB-02-UD` |
| `PDSO - Program Display Name` | Text | You | Name shown on drawings. Often the same as the unit number; use it if the client has a naming standard. | `701` |
| `PDSO - Unit Layout Type` | Text | You | Layout type that matches the enlarged unit plans. Add `Mirr.` or `Sim.` for mirrored or similar layouts. | `2BR - Type A`, `0BR - Type C Mirr.` |

## Area

You can't type these. They're harvested from the Area that contains the Data Silo, in the scheme mapped in **Project Config → Area Scheme to Parameter Mappings**. Make them read-only in your schedules if you can.

| Parameter | Type | Default scheme | Purpose | Example |
| --- | --- | --- | --- | --- |
| `PDSO - GSF` | Area | `PDSO - GSF` | Gross square footage. **Its Area also gives the Program Volume its shape.** | `1040 SF` |
| `PDSO - NSF` | Area | `PDSO - NSF` | Net rentable or sellable square footage. | `760 SF` |
| `PDSO - ZFA` | Area | `PDSO - ZFA` | Zoning floor area. Useful for non-residential program too. | `980 SF` |

## Program

| Parameter | Type | Who writes it | Purpose | Standard values | Example |
| --- | --- | --- | --- | --- | --- |
| `PDSO - Program Type` | Text | You | Kind of program. | Dwelling Unit, Condo, Rental, Commercial, Community Facility | `Rental` |
| `PDSO - Program Market Segment` | Text | You | Approximate market bracket. | LUX, MR, AFF | `MR` |
| `PDSO - Use Group` | Text | You | Occupancy classification under the applicable building code. | R-2, S-1 | `R-2` |
| `PDSO - Load Factor` | Number | You | Assumed square feet per occupant. | | `60` |
| `PDSO - Accessibility` | Text | You | Accessibility labels. | IH, IV, Adaptable (Type A/B/C), UFAS | `Adaptable (Type A)` |
| `PDSO - Planning Dept. Misc` | Text | You | Other labels required by agencies or planning departments, such as HPD. | | |
| `PDSO - Program Stacking` (locked) | Multiline text | You | Other floors this unit repeats on, as level identifiers from **Project Config → Level Mapping**. Ranges and lists are allowed. The Data Silo's own level is always included. | | `10-22, 24, 26` |

## Unit details

| Parameter | Type | Who writes it | Purpose | Example |
| --- | --- | --- | --- | --- |
| `PDSO - DU Bedroom Count` | Number | You | Number of bedrooms. Use Misc Modifier for special cases. | `2` |
| `PDSO - Bathroom Count` | Number | You | Number of bathrooms. | `2` |
| `PDSO - Bathroom Types` | Multiline text | You | Bathroom types, comma separated. | `Type B1, Type B1` |
| `PDSO - Kitchen Count` | Number | You | Number of kitchens. | `1` |
| `PDSO - Kitchen Types` | Multiline text | You | Kitchen types, comma separated. | `Type K1` |
| `PDSO - Misc Modifier` | Text | You | Open-ended features. Combine with `+`. Common (not standard): JR (junior bedroom), O (home office), D (den). | `JR + D/O` |

## System

| Parameter | Type | Who writes it | Purpose | Example |
| --- | --- | --- | --- | --- |
| `PDSO - Last Synced` (locked) | Text | Sync (system) | On a Data Silo: when Sync Data last ran over it. If it's older than your last edit, sync again. On a Program Volume: when Sync Data last changed it (a sync with nothing to change leaves it alone). | `2026-11-14 09:32:05` |

## Not implemented yet

These are in the original design, but there are no built-in parameters for them yet. A project can add them as custom parameters in the Parameter Manager.

| Planned field | Type | Purpose |
| --- | --- | --- |
| Exterior Space | Area | Square footage of private outdoor space. |
| Exterior Space Type | Text | N/A, Balcony, Terrace, Lawn. |

## Name changes from the original intranet reference

The original reference used short names. If you're working from older notes, the Revit names are:

| Original name | Revit parameter |
| --- | --- |
| Program UID | `PDSO - Program UID` |
| Unit Display Name | `PDSO - Program Display Name` |
| Unit Layout Type | `PDSO - Unit Layout Type` |
| ZFA / GSF / NSF | `PDSO - ZFA` / `PDSO - GSF` / `PDSO - NSF` |
| Stacking | `PDSO - Program Stacking` |
| Program | `PDSO - Program Type` |
| Market Segment | `PDSO - Program Market Segment` |
| Use Group | `PDSO - Use Group` |
| Load Factor | `PDSO - Load Factor` |
| Accessibility | `PDSO - Accessibility` |
| Planning Dept Misc. | `PDSO - Planning Dept. Misc` |
| BR Count | `PDSO - DU Bedroom Count` |
| Kitchen Count / Types | `PDSO - Kitchen Count` / `PDSO - Kitchen Types` |
| Bathroom Count / Types | `PDSO - Bathroom Count` / `PDSO - Bathroom Types` |
| Modifier | `PDSO - Misc Modifier` |
| Last_Synced | `PDSO - Last Synced` |

The original reference also said the sync copies Data Silo values onto **Area** objects. It doesn't: values go from Areas to Data Silos (SF only), and from Data Silos to Program Volumes (everything).
