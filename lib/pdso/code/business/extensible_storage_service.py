"""Service for managing PDSO Extensible Storage schemas.

This service handles checking for existing schemas and initializing them
with default values when they don't exist in the document.
"""

import traceback
from pyrevit import script
from pdso.code.data.extensible_storage_schemas import (
    PdsoConfigDocumentSchema,
    PdsoProgramVolumeInstanceSchema,
    PdsoProgramVolumeFamilySchema,
)

logger = script.get_logger()


class PDSOExtensibleStorageService(object):
    """Service for checking and initializing PDSO extensible storage schemas.

    This service provides functionality to:
    - Check if schemas exist in the current document
    - Initialize document-level configuration schema with defaults
    - Validate schema access permissions

    Attributes:
        document: Active Revit document
        data_storage_element: ProjectInfo element for document-level storage
    """

    def __init__(self, document):
        """Initialize the extensible storage service.

        Args:
            document: Revit document instance

        Raises:
            ValueError: If document is None
        """
        if document is None:
            raise ValueError("Document cannot be None")

        self.document = document
        self.data_storage_element = document.ProjectInformation

    def check_document_pdso_config_schema_exists(self):
        """Check if document-level PDSO config schema exists.

        Returns:
            bool: True if schema exists and has valid entity on ProjectInfo

        Notes:
            Checks both schema registration and entity existence on ProjectInfo.
        """
        try:
            # Check if schema is registered in document
            schema = PdsoConfigDocumentSchema.schema
            if schema is None:
                logger.debug("Document schema not registered")
                return False

            # Check if entity exists on ProjectInfo
            entity = self.data_storage_element.GetEntity(schema)
            if not entity.IsValid():
                logger.debug("Document schema exists but no entity on ProjectInfo")
                return False

            return True

        except Exception as e:
            logger.error("Error checking document schema: {}".format(e))
            return False

    def check_program_volume_instance_schema_exists(self):
        """Check if element-level PDSO program volume schema is registered.

        Returns:
            bool: True if schema is registered in document

        Notes:
            Only checks schema registration, not entity existence on elements.
            Elements will have entities created individually as needed.
        """
        try:
            schema = PdsoProgramVolumeInstanceSchema.schema
            if schema is None:
                logger.debug("Element schema not registered")
                return False

            return True

        except Exception as e:
            logger.error("Error checking element schema: {}".format(e))
            return False

    def check_program_volume_family_schema_exists(self):
        """Check if family-level PDSO program volume schema is registered.

        Returns:
            bool: True if schema is registered in document

        Notes:
            Only checks schema registration, not entity existence on families.
            Families will have entities created individually as needed.
        """
        try:
            schema = PdsoProgramVolumeFamilySchema.schema
            if schema is None:
                logger.debug("Family schema not registered")
                return False

            return True

        except Exception as e:
            logger.error("Error checking family schema: {}".format(e))
            return False

    def initialize_document_schema_with_defaults(self):
        """Initialize document-level schema with default configuration values.

        Creates entity on ProjectInfo with default PDSO configuration values.
        Only call this if check_document_pdso_config_schema_exists() returns False.

        Returns:
            bool: True if initialization successful

        Raises:
            Exception: If unable to create or set entity

        Notes:
            Uses transaction to modify document.
            Default values match typical PDSO workflow requirements.
            BaseSchema context manager handles its own transaction via __exit__.
        """
        try:
            logger.info("Initializing document-level PDSO config schema with defaults")

            # Create schema instance with ProjectInfo element
            # Set update=False since we're creating a new entity, not updating an existing one

            # Note: Map fields (dictionaries) are automatically initialized as empty
            # by the extensible storage system. No need to explicitly set to {}
            with PdsoConfigDocumentSchema(self.data_storage_element) as config:
                for field_name, value in self.default_config_values().items():
                    setattr(config, field_name, value)
                # program_volume_generic_model_subcategory is map field - initialized as empty dict by default
                # tethered_parameters is map field - initialized as empty dict by default
                # levels_mapping is map field - initialized as empty dict by default
                # worksets is map field - initialized as empty dict by default

            logger.info("Document schema initialized successfully")
            return True

        except Exception as e:
            logger.error("Failed to initialize document schema: {}".format(e))
            return False

    def default_config_values(self):
        """The values a new PDSO configuration starts with, as a dict.

        Project Config shows these for a model that has no configuration yet,
        without saving them; initialize_document_config saves them.
        """
        return {
            "version": "1.0.0",
            "last_run_timestamp": {
                "AreaHarvest": "Never",
                "VolumeGeometry": "Never",
                "VolumeData": "Never",
            },
            # The shipped Data Silo family for this Revit version (families.json)
            "data_silo_family_name": "PDSO - Program Data Silo Object - Rvt{}".format(
                str(self.document.Application.VersionNumber)[-2:]
            ),
            "uid_pattern": r"^[A-Za-z0-9]{2}-\d{2}-[A-Za-z0-9]{2}$",
            "area_scheme_mappings": {
                "PDSO - GSF": "PDSO - GSF",
                "PDSO - NSF": "PDSO - NSF",
                "PDSO - ZFA": "PDSO - ZFA",
            },
            "program_volume_family_name_prefix": "PDSO_ProgramVolume-",
            "program_volume_extrusion_height": 8.0,  # feet
        }

    def ensure_schemas_exist(self):
        """Check for all schemas and initialize document schema if missing.

        This is the main entry point for schema validation. It checks document,
        element, and family schemas, initializing the document schema with
        defaults if it doesn't exist.

        Returns:
            dict: Status dictionary with keys:
                - document_schema_exists (bool): Document schema status
                - element_schema_exists (bool): Element schema status
                - family_schema_exists (bool): Family schema status
                - document_schema_initialized (bool): True if just initialized
                - success (bool): Overall success status

        Notes:
            Element and family schemas are only registered, not initialized with data.
            Document schema is initialized with default values if missing.
        """
        result = {
            "document_schema_exists": False,
            "element_schema_exists": False,
            "family_schema_exists": False,
            "document_schema_initialized": False,
            "success": False,
        }

        try:
            # Check document-level schema
            doc_schema_exists = self.check_document_pdso_config_schema_exists()
            result["document_schema_exists"] = doc_schema_exists

            if not doc_schema_exists:
                logger.warning(
                    "Document-level PDSO config schema not found, initializing..."
                )
                initialized = self.initialize_document_schema_with_defaults()
                result["document_schema_initialized"] = initialized
                result["document_schema_exists"] = initialized

            # Check element-level schema (just registration, not data)
            pv_inst_schema_exists = self.check_program_volume_instance_schema_exists()
            result["element_schema_exists"] = pv_inst_schema_exists

            if not pv_inst_schema_exists:
                logger.warning(
                    "Element-level PDSO program volume schema not registered"
                )
                logger.info(
                    "Element schema will be registered when first program volume instance is processed"
                )

            # Check family-level schema (just registration, not data)
            pv_family_schema_exists = self.check_program_volume_family_schema_exists()
            result["family_schema_exists"] = pv_family_schema_exists

            if not pv_family_schema_exists:
                logger.warning("Family-level PDSO program volume schema not registered")
                logger.info(
                    "Family schema will be registered when first program volume family is processed"
                )

            # Overall success if all schemas exist/initialized
            result["success"] = (
                result["document_schema_exists"]
                and result["element_schema_exists"]
                and result["family_schema_exists"]
            )

            return result

        except Exception as e:
            logger.error("Error ensuring schemas exist: {}".format(e))
            return result

    def get_document_config(self):
        """Retrieve document-level PDSO configuration data.

        Returns:
            PdsoConfigDocumentSchema: Schema instance with config data, or None

        Notes:
            Returns None if schema doesn't exist.
            Use check_document_schema_exists() first to validate.
        """
        try:
            if not self.check_document_pdso_config_schema_exists():
                logger.warning("Cannot get document config - schema doesn't exist")
                return None

            return PdsoConfigDocumentSchema(self.data_storage_element)

        except Exception as e:
            logger.error("Error getting document config: {}".format(e))
            return None

    def update_document_config(self, **kwargs):
        """Update specific fields in document-level configuration.

        Args:
            **kwargs: Field names and values to update (e.g., version="1.0.1")

        Returns:
            bool: True if update successful

        Example:
            service.update_document_config(
                version="1.1.0",
                default_volume_extrusion_height=12.0
            )

        Notes:
            Only updates provided fields, leaves others unchanged.
            BaseSchema context manager handles its own transaction via __exit__.
        """
        try:
            if not self.check_document_pdso_config_schema_exists():
                # The first save of a model makes the configuration
                self.ensure_schemas_exist()
            if not self.check_document_pdso_config_schema_exists():
                logger.error("Cannot update config - schema doesn't exist")
                return False

            # BaseSchema.__exit__ will create its own transaction
            with PdsoConfigDocumentSchema(self.data_storage_element) as config:
                for field_name, value in kwargs.items():
                    if hasattr(config, field_name):
                        setattr(config, field_name, value)
                        logger.debug("Updated {} to {}".format(field_name, value))
                    else:
                        logger.warning("Unknown config field: {}".format(field_name))

            return True

        except Exception as e:
            logger.error("Error updating document config: {}".format(str(e)))
            logger.error("Full traceback:\n{}".format(traceback.format_exc()))
            return False
