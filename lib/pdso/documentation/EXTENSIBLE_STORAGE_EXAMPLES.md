# PDSO Extensible Storage Service - Usage Examples

This document provides examples of using the `PDSOExtensibleStorageService` to manage PDSO extensible storage schemas in Revit.

## Overview

The PDSO extension uses two extensible storage schemas:

1. **Document-level schema** (`PdsoConfigDocumentSchema`) - Stores global configuration on ProjectInfo
2. **Element-level schema** (`PdsoProgramVolumeInstanceSchema`) - Stores per-element data on program volumes

## Basic Usage

### 1. Check and Initialize Schemas (Recommended)

```python
from pdso.code.business.extensible_storage_service import PDSOExtensibleStorageService
from pyrevit import revit

doc = revit.doc

# Create service instance
es_service = PDSOExtensibleStorageService(doc)

# Check both schemas and initialize document schema if needed
status = es_service.ensure_schemas_exist()

if status["success"]:
    self.logger.info("All schemas ready!")
else:
    self.logger.warning("Schema issues detected:")
    self.logger.warning("  Document schema: {}".format(status["document_schema_exists"]))
    self.logger.warning("  Element schema: {}".format(status["element_schema_exists"]))
```

### 2. Check Individual Schemas

```python
# Check document-level schema only
if es_service.check_document_schema_exists():
    self.logger.info("Document schema exists")
else:
    self.logger.warning("Document schema missing")

# Check element-level schema only
if es_service.check_element_schema_exists():
    self.logger.info("Element schema registered")
else:
    self.logger.warning("Element schema not registered")
```

### 3. Manual Initialization

```python
# Initialize document schema with defaults if it doesn't exist
if not es_service.check_document_schema_exists():
    success = es_service.initialize_document_schema_with_defaults()
    if success:
        self.logger.info("Document schema initialized successfully")
```

## Working with Document Configuration

### Reading Configuration

```python
# Get document configuration
config = es_service.get_document_config()

if config:
    self.logger.info("Version: {}".format(config.version))
    self.logger.info("UID Pattern: {}".format(config.uid_pattern))
    self.logger.info("Default Height: {} ft".format(config.default_volume_extrusion_height))

    # Access map fields
    self.logger.info("Worksets: {}".format(config.worksets))
    self.logger.info("Last Run Timestamps: {}".format(config.last_run_timestamp))
    self.logger.info("Levels Mapping: {}".format(config.levels_mapping))
```

### Updating Configuration

```python
# Update single field
es_service.update_document_config(version="1.1.0")

# Update multiple fields
es_service.update_document_config(
    version="1.2.0",
    default_volume_extrusion_height=12.0,
    uid_pattern="PV-"
)
```

### Working with Map Fields

```python
from pyrevit import revit

# Get config for editing
config = es_service.get_document_config()

with revit.Transaction("Update Worksets", doc):
    with config:
        # Update workset mappings
        config.worksets["Area"] = "PDSO - Areas"
        config.worksets["Data Silo"] = "PDSO - Data Silos"
        config.worksets["Program Volume"] = "PDSO - Volumes"

        # Update timestamp for a workflow step
        import datetime
        timestamp = datetime.datetime.now().isoformat()
        config.last_run_timestamp["Harvest Areas"] = "{} - {}".format(
            timestamp,
            doc.Application.Username
        )
```

## Working with Element Schema

### Checking Element Schema on Program Volumes

```python
from pdso.code.data.extensible_storage_schemas import PdsoProgramVolumeInstanceSchema

# Get a program volume element
program_volume = # ... get element ...

# Check if element has schema entity
schema = PdsoProgramVolumeInstanceSchema.schema
entity = program_volume.GetEntity(schema)

if entity.IsValid():
    self.logger.info("Element has PDSO schema data")
else:
    self.logger.warning("Element needs schema initialization")
```

### Creating/Updating Element Schema Data

```python
from pdso.code.data.extensible_storage_schemas import PdsoProgramVolumeInstanceSchema
from pyrevit import revit

program_volume = # ... get element ...

with revit.Transaction("Update Program Volume Schema", doc):
    with PdsoProgramVolumeInstanceSchema(program_volume) as pv_schema:
        # Set simple fields
        pv_schema.is_primary_instance = True

        # Initialize map fields
        pv_schema.hashed_parameters = {}
        pv_schema.hashed_curve_geometry = {}

        # Add hashed data
        pv_schema.hashed_parameters["params_v1"] = "abc123def456"
        pv_schema.hashed_curve_geometry["PDSO - GSF"] = "xyz789uvw012"
        pv_schema.hashed_curve_geometry["PDSO - NSF"] = "mno345pqr678"
```

## Common Workflow Patterns

### Setup Script Pattern

```python
def validate_pdso_setup(doc):
    """Validate PDSO is properly configured in document.

    Returns:
        bool: True if setup is valid
    """
    es_service = PDSOExtensibleStorageService(doc)
    status = es_service.ensure_schemas_exist()

    if not status["success"]:
        self.logger.error("ERROR: PDSO schemas not properly configured!")
        return False

    # Check configuration values
    config = es_service.get_document_config()
    if not config.uid_pattern:
        self.logger.warning("WARNING: UID pattern not configured")

    return True
```

### Workflow Timestamp Pattern

```python
def record_workflow_execution(doc, workflow_name):
    """Record execution timestamp for a workflow step.

    Args:
        doc: Revit document
        workflow_name (str): Name of workflow step
    """
    import datetime
    from pyrevit import revit

    es_service = PDSOExtensibleStorageService(doc)
    config = es_service.get_document_config()

    timestamp = datetime.datetime.now().isoformat()
    user = doc.Application.Username

    with revit.Transaction("Record Workflow Timestamp", doc):
        with config:
            config.last_run_timestamp[workflow_name] = "{} - {}".format(timestamp, user)
```

### Geometry Hash Comparison Pattern

```python
def check_geometry_changed(program_volume, area_scheme_name, new_hash):
    """Check if program volume geometry has changed.

    Args:
        program_volume: Program volume element
        area_scheme_name (str): Name of area scheme
        new_hash (str): New geometry hash

    Returns:
        bool: True if geometry changed or no previous hash
    """
    from pdso.code.data.extensible_storage_schemas import PdsoProgramVolumeInstanceSchema

    schema = PdsoProgramVolumeInstanceSchema.schema
    entity = program_volume.GetEntity(schema)

    if not entity.IsValid():
        return True  # No previous data, consider changed

    pv_schema = PdsoProgramVolumeInstanceSchema(program_volume, update=False)
    previous_hash = pv_schema.hashed_curve_geometry.get(area_scheme_name)

    return previous_hash != new_hash
```

## Error Handling

```python
try:
    es_service = PDSOExtensibleStorageService(doc)
    status = es_service.ensure_schemas_exist()

    if not status["success"]:
        # Handle missing schemas
        if not status["document_schema_exists"]:
            self.logger.error("ERROR: Could not initialize document schema")
            # Abort or retry

        if not status["element_schema_exists"]:
            self.logger.warning("WARNING: Element schema will be registered on first use")
            # Continue, schema will register automatically

except ValueError as ve:
    self.logger.error("Configuration error: {}".format(ve))
except Exception as e:
    self.logger.error("Unexpected error: {}".format(e))
```

## Default Configuration Values

When `initialize_document_schema_with_defaults()` is called, the following defaults are set:

- **version**: "1.0.0"
- **uid_pattern**: "ProgramVolume-UID-"
- **default_volume_extrusion_height**: 10.0 (feet)
- **last_run_timestamp**: {} (empty dictionary)
- **worksets**: {} (empty dictionary)
- **levels_mapping**: {} (empty dictionary)

These defaults can be updated using `update_document_config()` after initialization.
