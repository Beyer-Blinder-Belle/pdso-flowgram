# Level Mapping Process

## Overview

The **Level Mapping** system transforms a simple string parameter (`PDSO - Program Stacking`) on a Data Silo Object into a validated list of Revit Level objects where program volume instances should be created. This document explains the conceptual process independent of specific implementation details.

## Purpose

In architectural programming workflows, units often span multiple building levels. Rather than creating separate PDSO entries for each floor, architects specify a level range (e.g., "03-05, 10") indicating that a unit appears on levels 3, 4, 5, and 10. The Level Mapping system:

1. **Parses** the stacking notation into discrete level numbers
2. **Maps** those numbers to actual Revit Level elements
3. **Validates** that all referenced levels exist and are properly configured
4. **Generates** the final list of Level objects for downstream operations

## The Three-Stage Process

### Stage 1: Parse Program Stacking Parameter

**Input:** Text string from `PDSO - Program Stacking` parameter  
**Output:** List of integer level numbers

The system parses compact stacking notation that supports both individual levels and ranges:

**Examples:**

- `"03-05"` → `[3, 4, 5]`
- `"02, 05, 07, 10"` → `[2, 5, 7, 10]`
- `"03-06, 10"` → `[3, 4, 5, 6, 10]`
- `""` → `[]` (no stacking)

**Process:**

1. Split the string by commas to get individual segments
2. For each segment:
   - If it contains a hyphen (`-`), treat it as a range (e.g., `03-05`)
     - Parse start and end numbers
     - Generate all integers in the range (inclusive)
   - If it's a plain number, add it directly
3. Remove duplicates and sort the results

**Error Handling:**

- Invalid formats (non-numeric, malformed ranges) are logged as warnings
- Parsing continues with remaining valid segments
- Returns an empty list if no valid numbers are found

### Stage 2: Map Numbers to Levels

**Input:** List of integer level numbers  
**Output:** List of Revit Level elements

Once we have a list of numbers, we need to resolve each to an actual Level object in the Revit model.

**Resolution Strategy (Dual-Path):**

The system attempts lookup via an Explicit User Mapping:

- Check the Level Mapping Configuration for a user-defined mapping
- The configuration stores mappings:
  - `user_key(alphanumeric) → level_name`
- If a mapping exists, retrieve the Level element by name

### Stage 3: Validate and Aggregate

**Input:** Individual Level lists from multiple PDSOs  
**Output:** Validated, deduplicated Level collections per PDSO

The final stage ensures data integrity before creating or updating program volumes:

**Validation Checks:**

1. **Missing Mappings Detection**

   - Identify level numbers that couldn't be resolved to Level objects
   - Track which numbers are missing across all PDSOs
   - Report to user with actionable warnings

2. **Duplicate Prevention**

   - Compare Level objects by ID (not object reference)
   - Ensure each level appears only once per PDSO
   - Critical for preventing duplicate program volume instances

3. **Original Level Inclusion**
   - Each PDSO has a placement level (where the point object sits)
   - This level is automatically included even if not in the stacking parameter
   - Ensures at least one volume exists for every PDSO

**Output Structure:**

For each Data Silo, the system produces:

- `required_levels`: List of Level objects where volumes should exist
- `missing_mappings`: List of numbers that couldn't be resolved

## Data Flow Example

**Scenario:** A residential unit spans floors 2, 3, 4, 5, and 8

```
PDSO Parameters:
├─ PDSO - Program UID: "UNIT-201"
├─ PDSO - Program Stacking: "03-05, 05, 08"
└─ Level: "Level 2" (placement level)

Stage 1: Parse Ranges and Single Values
"03-05" → [3, 4, 5]
"05" → [5]
"08" → [8]

Stage 2: Map to Levels and Validate
3 → Level Mapping Config → "Level 3" → <Level: Level 3>
4 → Level Mapping Config → "Level 4" → <Level: Level 4>
5 → Level Mapping Config → "Level 5" → <Level: Level 5>
5 → Level Mapping Config → "Level 5" → <Level: Level 5>
8 → Level Mapping Config → "Level 8" → <Level: Level 8>

Stage 3: Aggregate
Ensure the "Canonical" level is present as well. Data Silo is hosted on "Level 2", so there should be a copy of the program volume on Level 2 as well
Original level: <Level: Level 2>
Stacking levels: [<Level: Level 3>, <Level: Level 4>, <Level: Level 5>]
Deduplicated: [<Level: Level 3>, <Level: Level 4>, <Level: Level 5>, <Level: Level 8>]
Missing mappings: []

Result: Create/sync program volumes on 3 levels
```

## Level Mapping Configuration

The Level Mapping Configuration is a persistent, bidirectional lookup table stored in the Revit model via the Extensible Data Storage.

### Storage Format

**Physical Storage:** Extensible Storage (DataStorage element with PDSO_Config schema). Keys are alphanumeric, so they can be sorted and deterministic ranges can be extracted.

**Stored As:**

```
{
  "3": <ElementId: Level 3>,
  "4": <ElementId: Level 4>,
  "5": <ElementId: MKT Level 5>,
  "10": <ElementId: Mezzanine>
}
```

**In-Memory Format:**

```python
level_mappings = {
  3: "Level 3",
  4: "Level 4",
  5: "Level 5",
  10: "Mezzanine"
}
```

### Why Store Mappings?

1. **Explicit Control:** Users define exactly which number maps to which level
2. **Non-Standard Names:** Handles "Ground Floor", "Penthouse", "Typical 02-18"
3. **Project Consistency:** Same mapping used across all PDSOs and sessions
4. **Validation:** Can detect when stacking refers to unmapped numbers
5. **Performance:** Avoids repeated pattern matching searches

### Configuration Lifecycle

**Initialization:**

1. User opens the PDSO tool
2. System loads mappings from DataStorage

**User Configuration:**

1. User opens the Config tab
2. Sees all levels in the model
3. Assigns numbers to relevant levels (e.g., 3 → "Level 3")
4. Saves mappings back to DataStorage

**Runtime Usage:**

1. During validation or synchronization
2. System requests level by number
3. Configuration looks up in reverse_mappings
4. Returns Level element or None

**Refresh:**

1. Mappings loaded at beginning of script, cached in memory for performance

## Integration Points

The following information refers to the implementation in the previous codebase. Some of these details may change to match the new refactoring patterns.

The Level Mapping process integrates with several other PDSO workflows:

### 1. Correlation Service

**When:** Before running sync operations  
**What:** Validates that all stacking parameters reference mappable levels & determines which levels need volume instances
**Output:** Error/warning messages identifying problematic PDSOs

```
Correlation Flow:
├─ For each PDSO with stacking parameter
│  ├─ Parse stacking string
│  ├─ Attempt to map each number
│  └─ Get all required levels (original + stacking)
├─ Report aggregate missing numbers
└─ Pass/fail validation check
```

### 2. Queue Updates

**When:** Creating initial program volume instances  
**What:** Determines which levels need volume instances  
**Output:** Creates one volume per required level per PDSO

```
Queue Updates Flow:
├─ For each PDSO
│  ├─ Check existing volumes
│  ├─ Identify either 0, 1, or multiple existing program volume instances
│  ├─ If there are multiple, identify the closest one, mark the others as orphaned
│  └─ Queue list of required changes (use ExtendedProgramVolumeInstance)
│     ├─ Creation needed
│     └─ Location update needed
├─ Mark orphaned instances for later deletion
```

Find or create volume on each level
Sync parameters from PDSO to volume
Update sync timestamp

### 3. Execute Changes (within transaction)

**When:** Updating existing program volumes from PDSOs  
**What:** Pushes data to volumes, manages multi-level stacking  
**Output:** Updated parameter values on all volume instances

```
Sync Flow:
├─ For each ExtendedProgramVolumeInstance
│  ├─ Place program volume instances where missing
│  ├─ Update locations as needed
│  └─ Delete orphans
```

## Error Handling

The system handles various error conditions gracefully:

### Missing Mappings

**Problem:** Stacking parameter references a number with no mapping  
**Detection:** During level resolution in Stage 2  
**Response:**

- Log warning with specific level numbers
- Continue processing other levels
- Display error in validation UI
- Prompt user to configure mappings

### Invalid Stacking Format

**Problem:** Malformed parameter value (e.g., "3-5-7", "Level 3")  
**Detection:** During parsing in Stage 1  
**Response:**

- Log warning about invalid segment
- Skip the invalid portion
- Continue with valid segments
- Does not fail the entire operation

### Deleted Levels

**Problem:** Level mapping references a level that was deleted  
**Detection:** During mapping load from DataStorage  
**Response:**

- Log warning with level ID and number
- Skip the invalid mapping
- Continue with remaining mappings
- User can reconfigure in Config tab

### Duplicate Mappings

**Problem:** Two levels assigned the same number  
**Detection:** During mapping save operations  
**Response:**

- Reject the duplicate assignment
- Display error to user
- Keep existing mapping intact
- Prompt user to choose different number

## Best Practices

### For Users

1. **Configure Mappings Early:** Set up level mappings before creating stacking parameters
2. **Use Consistent Numbering:** Match your project's level numbering scheme
3. **Validate Before Sync:** Run validation checks to catch missing mappings
4. **Document Special Levels:** For non-standard levels (Mezzanine, Penthouse), use clear number assignments

### For Developers

1. **Always Pass Level Mapping Config:** Don't rely on fallback pattern matching
2. **Check for Missing Mappings:** Validate before creating volumes
3. **Compare Levels by ID:** Avoid duplicate Level objects with different references
4. **Handle Null Gracefully:** Level resolution can return None
5. **Log at Appropriate Levels:** Debug for normal flow, warnings for missing mappings, errors for critical failures

## Future Considerations

### Potential Enhancements

1. **Auto-Mapping:** Automatically generate mappings from level names on first run
2. **Mapping Templates:** Save/load mapping configurations across projects
3. **Visual Feedback:** Highlight PDSOs with unmapped levels in the UI
4. **Level Groups:** Support for "typical floor groups" (e.g., 3-10 all map to "Typical Floor")
5. **Validation Preview:** Show which volumes will be created/deleted before running sync

### Limitations

1. **Single Number Per Level:** Each level can only have one number
2. **Manual Configuration:** Users must set up mappings explicitly
3. **No Level Hierarchies:** Doesn't support sub-levels or level groups
4. **String-Based Parsing:** Relies on consistent stacking parameter format

---

## Quick Reference

### Key Concepts

- **Stacking Parameter:** Text field with level ranges (e.g., "03-05")
- **Level Mapping:** User-defined number → level name association
- **Required Levels:** Original level + stacking levels for a PDSO
- **Program Volume:** 3D mass instance representing program area on a level

### Process Summary

```
Parse → Map → Validate → Use
"03-05" → [3,4,5] → [L3,L4,L5] → Create/sync volumes
```

### Integration Points

- Correlate Existing: Compare existing to what is needed via relationships
- Queue Updates: Prep all the updates that need to be made
- Sync Changes: Execute everything within a minimal number of transactions
