"""PDSO Extensible Storage Schemas - Document and element-level data persistence.

This module defines extensible storage schemas for persisting PDSO configuration
and state data directly within the Revit document. Schemas are versioned and provide
structured storage for configuration, relationships, and geometry hashes.

Schemas Defined:
    - PdsoConfigDocumentSchema: Global configuration stored on ProjectInfo
    - PdsoProgramVolumeInstanceSchema: Per-instance data for program volumes
    - PdsoProgramVolumeFamilySchema: Per-family data for program volumes

Example Usage:
    # Reading configuration
    with PdsoConfigDocumentSchema(project_info) as entity:
        uid_pattern = entity.uid_pattern
        extrusion_height = entity.program_volume_extrusion_height

    # Writing configuration
    with PdsoConfigDocumentSchema(project_info, update=True) as entity:
        entity.uid_pattern = "Building-Level-UID"
        entity.program_volume_extrusion_height = 10.0
"""

import System
from extensible_storage import ES, BaseSchema, simple_field, map_field


class PdsoConfigDocumentSchema(BaseSchema):
    """Global config for PDSO per document."""

    guid = System.Guid("7b533d48-f078-48ad-a393-48d73b71e96b")

    read_access_level = ES.AccessLevel.Public
    write_access_level = ES.AccessLevel.Public

    # Metadata
    @simple_field(value_type="string")
    def version():
        """Schema / plugin data version"""

    # HISTORY
    @map_field(key_type=str, value_type=str)
    def last_run_timestamp():
        """
        Dictionary logging last run timestamps per functional domain.
        Key: Name of Functional Domain
        Value: ISO timestamps of last run + username
        """

    # UID pattern requirements
    @simple_field(value_type="string")
    def data_silo_family_name():
        """
        Data Silo Family name (None for default naming)
        """

    # UID pattern requirements
    @simple_field(value_type="string")
    def uid_pattern():
        """
        UID pattern type (picked from list of options)
        """

    # Area Scheme to Parameter mappings
    @map_field(key_type=str, value_type=str)
    def area_scheme_mappings():
        """
        Dictionary mapping Area Schemes to Data Silo parameters.
        Key: Name of Area Scheme (e.g., "PDSO - GSF", "PDSO - NSF", "PDSO - ZFA")
        Value: Name of parameter to bind to (e.g., "PDSO - GSF", "PDSO - NSF", "PDSO - ZFA")
        """

    # Program Volume Family prefix
    @simple_field(value_type="string")
    def program_volume_family_name_prefix():
        """
        Program Volume Family prefix (used for naming)
        """

    # Desired extrusion heights for program volumes
    @simple_field(value_type="double")
    def program_volume_extrusion_height():
        """
        Default extrusion height for program volumes (in decimal feet)
        """

    # Tethered parameters
    @map_field(key_type=str, value_type="guid")
    def tethered_parameters():
        """
        Parameters that should be pushed from Data Silos to Program Volumes
        Key: Parameter name
        Value: GUID of the parameter
        """

    # Mapping of "Level" elements into simple ordered list
    @map_field(key_type=str, value_type="element_id")
    def levels_mapping():
        """
        Disambiguation of Revit Levels for stacking ranges
        Key: "<building_id>|<unambiguous_level_number>"
        Value: Level "ElementId"
        """

    # Worksets {areas, data silos, program volumes}
    @map_field(key_type=str, value_type=str)
    def worksets():
        """
        Dictionary mapping of pdso element components to worksets.
        Key: Name of element type (e.g., "Area", "Data Silo", "Program Volume")
        Value: Name of workset to use (or None, if not enforced)
        """


class PdsoProgramVolumeInstanceSchema(BaseSchema):
    """Per-element data for PDSO Program Volumes."""

    guid = System.Guid("2cd38c37-88ed-42c7-9fc2-a03b1cbb5cb7")

    read_access_level = ES.AccessLevel.Public
    write_access_level = ES.AccessLevel.Public

    @simple_field(value_type="boolean")
    def is_primary_instance():
        """
        True if this element is co-located with its parent data silo,
        or False if it is a stacked clone
        """

    @simple_field(value_type="element_id")
    def parent_data_silo_id():
        """
        ElementId of the parent Data Silo element
        """

    @map_field(key_type=str, value_type=str)
    def hashed_parameters():
        """
        Single Key-Value pair representing:
        Key: Hash representing parameter names
        Value: Hash represeting parameter values
        """


class PdsoProgramVolumeFamilySchema(BaseSchema):
    """Per-family data for PDSO Program Volumes."""

    guid = System.Guid("581b2f54-bec4-46ab-9afc-07656e795f65")

    read_access_level = ES.AccessLevel.Public
    write_access_level = ES.AccessLevel.Public

    @map_field(key_type=str, value_type="xyz")
    def area_basepoint():
        """
        Dictionary representing the basepoints of associated areas (as of last update).
        Key: Area Scheme name
        Value: Basepoint coordinates of related data
        """

    @map_field(key_type=str, value_type=str)
    def hashed_area_curve_geometry():
        """
        Dictionary representing the hashed curve geometry of associated areas (as of last update).
        Key: Area Scheme name
        Value: Hash representing curve geometry
        """
