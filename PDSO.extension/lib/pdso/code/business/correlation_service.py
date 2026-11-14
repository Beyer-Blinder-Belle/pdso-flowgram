# Python standard libraries
import math

# 3rd Party Modules
from rpw import DB
from pyrevit import script, DB

# Custom Modules
# from ..data import model_document_collections as pdso_models
from pdso.code.data.extended_program_volume_family import ExtendedProgramVolumeFamily
from pdso.code.data.extended_program_volume_instance import (
    ExtendedProgramVolumeInstance,
)


class PDSOCorrelationService(object):
    """Service class for handling PDSO correlation operations"""

    def __init__(self):
        self.logger = script.get_logger()
        self.output = script.get_output()

    def correlate_data_silos_to_areas(
        self, ext_level_collection, ext_data_silo_collection, ext_area_collection
    ):
        """Correlate Data Silos to Areas based on Level and spatial containment.

        Args:
            data_silos (ExtendedDataSiloCollection): Collection of ExtendedDataSilo objects.
            area_collection (ExtendedAreaCollection): Collection of ExtendedArea objects.
        """
        self.logger.debug(
            "Data silo ids: {}".format(
                [
                    ds.rpw_element.LevelId.Value
                    for ds in ext_data_silo_collection.extended_data_silos
                ]
            )
        )
        self.logger.debug(
            "Area ids: {}".format(
                [
                    area.rpw_area.LevelId.Value
                    for area in ext_area_collection.extended_areas
                ]
            )
        )

        for ext_level in ext_level_collection.extended_levels:
            self.logger.debug(
                "Processing Level: {} with id of {}".format(
                    ext_level.rpw_level.Name, ext_level.rpw_level.Id.Value
                )
            )
            # Get Data Silos on this level
            ext_data_silos_on_level = [
                ds
                for ds in ext_data_silo_collection.extended_data_silos
                if ds.rpw_element.LevelId.Value == ext_level.rpw_level.Id.Value
            ]

            if not ext_data_silos_on_level or len(ext_data_silos_on_level) == 0:
                self.logger.debug("  No Data Silos found on this level.")
                continue

            # Get Areas on this level
            ext_areas_on_level = [
                area
                for area in ext_area_collection.extended_areas
                if area.rpw_area.LevelId.Value == ext_level.rpw_level.Id.Value
            ]

            if not ext_areas_on_level or len(ext_areas_on_level) == 0:
                self.logger.debug("  No Areas found on this level.")
                continue

            self.logger.debug(
                "  Found {} Data Silos and {} Areas on this level.".format(
                    len(ext_data_silos_on_level), len(ext_areas_on_level)
                )
            )
            # Correlate each Data Silo to Areas on the same level
            for ext_data_silo in ext_data_silos_on_level:
                for ext_area in ext_areas_on_level:
                    if self._is_instance_in_2d_spatial_element_winding_number(
                        ext_data_silo.rpw_element, ext_area.rpw_area
                    ):
                        # Correlation found - store it and move to next data silo
                        self.logger.debug(
                            "  Correlated Data Silo ID {} to Area ID {}".format(
                                str(ext_data_silo.Id.Value), str(ext_area.Id.Value)
                            )
                        )
                        # Keyed by PDSO parameter ("PDSO - GSF"), which is what
                        # every reader looks it up by, not by the Revit scheme
                        # name, which can be anything ("Gross Building")
                        ext_data_silo.related_area_elements[
                            ext_area.bound_silo_parameter_name
                        ] = ext_area
                        ext_area.related_data_silos.append(ext_data_silo)
                        break

    def correlate_data_silos_to_program_volume_families(
        self, ext_data_silo_collection, ext_program_volume_family_collection
    ):
        """Correlate Data Silos to Program Volume Families based on Level and spatial containment.

        Args:
            data_silos (ExtendedDataSiloCollection): Collection of ExtendedDataSilo objects.
            program_volume_family_collection (ExtendedProgramVolumeFamilyCollection): Collection of ExtendedProgramVolumeFamily objects.
        """
        self.logger.info("Correlating Data Silos to Program Volume Families...")

        new_extended_program_volume_families = []
        program_volume_family_name_prefix = (
            ext_program_volume_family_collection.program_volume_family_name_prefix
        )

        # Correlate each Data Silo to Program Volume Families on the same level
        for ext_data_silo in ext_data_silo_collection.extended_data_silos:
            # Find Program Volume Families by matching family name. Family name is prefix + data silo UID
            expected_family_name = "{}{}".format(
                program_volume_family_name_prefix,
                str(ext_data_silo.parameters["PDSO - Program UID"].value),
            )

            # Use next() to find program volume family with matching family name (there can only be one)
            matching_ext_program_volume_family = next(
                (
                    pvf
                    for pvf in ext_program_volume_family_collection.extended_program_volume_families
                    if pvf.rpw_element.Name == expected_family_name
                ),
                None,
            )

            # If there is no matching program volume family, create an extended object, which will faciliate Family creation later
            if matching_ext_program_volume_family is None:
                self.logger.info(
                    "No matching Program Volume Family found for family name: {}".format(
                        expected_family_name
                    )
                )

                matching_ext_program_volume_family = ExtendedProgramVolumeFamily(None)
                new_extended_program_volume_families.append(
                    matching_ext_program_volume_family
                )

            # Add matching program volume families to data silo's related_program_volume_families list
            error_flag = False
            if ext_data_silo.related_program_volume_family is not None:
                self.logger.error(
                    "Data silo {} already is already correlated with Program Volume Family {} (Family name: {}).".format(
                        ext_data_silo.Id,
                        ext_data_silo.related_program_volume_family.rpw_element.Name,
                        expected_family_name,
                    )
                )
                error_flag = True
            if matching_ext_program_volume_family.parent_data_silo is not None:
                self.logger.error(
                    "Program Volume Family {} (Family name: {}) is already correlated with Data Silo {}.".format(
                        matching_ext_program_volume_family.rpw_element.Name,
                        expected_family_name,
                        matching_ext_program_volume_family.parent_data_silo.Id,
                    )
                )
                error_flag = True

            if not error_flag:
                ext_data_silo.related_program_volume_family = (
                    matching_ext_program_volume_family
                )
                matching_ext_program_volume_family.parent_data_silo = ext_data_silo

        # Add all the new ExtendedProgramVolumeFamily objects to the collection
        ext_program_volume_family_collection.extended_program_volume_families.extend(
            new_extended_program_volume_families
        )

    # TO DO - Look deeper in the level mapping service, make sure this is the right place for this logic
    def correlate_data_silos_to_program_volume_instances(
        self,
        ext_level_collection,
        ext_data_silo_collection,
        ext_program_volume_instance_collection,
        level_mapping_service,
    ):
        """Correlate Data Silos to Program Volumes based on Level and spatial containment.

        Args:
            ext_level_collection (ExtendedLevelCollection): Collection of ExtendedLevel objects.
            ext_data_silo_collection (ExtendedDataSiloCollection): Collection of ExtendedDataSilo objects.
            ext_program_volume_instance_collection (ExtendedProgramVolumeInstanceCollection): Collection of ExtendedProgramVolumeInstance objects.
            level_mapping_service (PDSOLevelMappingService): Service for level mapping and validation.
        """

        # Validate level mappings before proceeding
        validation_result = level_mapping_service.validate_all_stacking_parameters(
            ext_data_silo_collection, ext_level_collection
        )

        # Store validation result on collection
        ext_program_volume_instance_collection.validation_result = validation_result

        ext_program_volume_instance_collection.validation_passed = (
            validation_result.passed
        )

        if not validation_result.passed:
            self.logger.warning(
                "Level mapping validation failed - continuing with available mappings"
            )
            self.logger.warning(
                "PDSOs with missing level mappings may not create instances correctly"
            )
            # Don't abort - continue processing PDSOs that have valid mappings

        program_volume_family_name_prefix = (
            ext_program_volume_instance_collection.program_volume_family_name_prefix
        )

        # Note: Program Volume istances have a "desired_instance_location" property that starts as "None"
        # To do: transitition to a flag-based system for better clarity, eg. "instance_needs_creation", "instance_needs_move", etc.

        # Correlate each Data Silo to Program Volumes on the same level
        for ext_data_silo in ext_data_silo_collection.extended_data_silos:
            related_family = ext_data_silo.related_program_volume_family
            if related_family is None or related_family.rpw_element is None:
                self.logger.warning(
                    "Skipping Program Volume instance correlation for Data Silo {} because no Program Volume family is available".format(
                        str(ext_data_silo.Id.Value)
                    )
                )
                continue

            # Find Program Volumes by matching family name. Family name is prefix + data silo UID
            data_silo_uid = str(ext_data_silo.parameters["PDSO - Program UID"].value)
            expected_family_name = "{}{}".format(
                program_volume_family_name_prefix, data_silo_uid
            )

            self.logger.debug(
                "DEBUG: Correlating Data Silo UID='{}' to Program Volumes with family name='{}'".format(
                    data_silo_uid, expected_family_name
                )
            )

            # Use list comprhension to filter program volumes instances to those with matching family name
            # Only match existing instances (rpw_element is not None)
            matching_ext_program_volumes = []
            for pv in (
                ext_program_volume_instance_collection.extended_program_volume_instances
            ):
                if pv.rpw_element is None:
                    continue

                try:
                    instance_family_name = pv.rpw_element.Symbol.Family.Name
                except Exception:
                    continue

                if instance_family_name == expected_family_name:
                    matching_ext_program_volumes.append(pv)

            if len(matching_ext_program_volumes) == 0:
                self.logger.debug(
                    "  No existing program volume instances found for family name: {}".format(
                        expected_family_name
                    )
                )
            else:
                self.logger.debug(
                    "  Found {} existing program volume instances for family name: {}".format(
                        len(matching_ext_program_volumes), expected_family_name
                    )
                )

            # Add matching program volumes to data silo's related_program_volume_instances list
            ext_data_silo.related_program_volume_instances.extend(
                matching_ext_program_volumes
            )

            # Enumerate expected program volume instances based on stacking parameters
            # TO DO - Move this out of validation, and into correlation?
            # This is populated by level_mapping_service during validation
            expected_levels = ext_data_silo.required_levels

            self.logger.debug(
                "DEBUG: Data Silo '{}' expected_levels (count={}): {}".format(
                    data_silo_uid,
                    len(expected_levels),
                    [
                        (el.rpw_level.Name, el.rpw_level.Id.Value)
                        for el in expected_levels
                    ],
                )
            )

            # Get canonical level ID for marking primary instance
            canonical_level_id = ext_data_silo.rpw_element.LevelId.Value

            for expected_level in expected_levels:
                # Skip if expected_level is None or invalid
                if expected_level is None or expected_level.rpw_level is None:
                    self.logger.warning(
                        "  Skipping invalid expected level for Data Silo {}".format(
                            str(ext_data_silo.Id.Value)
                        )
                    )
                    continue

                self.logger.debug(
                    "DEBUG: Processing expected level '{}' (ElementId: {}) for Data Silo '{}'".format(
                        expected_level.rpw_level.Name,
                        expected_level.rpw_level.Id.Value,
                        data_silo_uid,
                    )
                )

                # Check if a matching program volume instance already exists on this level
                matching_instances_on_level = [
                    pv
                    for pv in matching_ext_program_volumes
                    if pv.LevelId.Value == expected_level.rpw_level.Id.Value
                ]

                self.logger.debug(
                    "DEBUG: Found {} matching instances on level '{}'".format(
                        len(matching_instances_on_level), expected_level.rpw_level.Name
                    )
                )

                self.logger.debug(
                    "DEBUG: Found {} matching instances on level '{}'".format(
                        len(matching_instances_on_level), expected_level.rpw_level.Name
                    )
                )

                # There are no matching instances on this level - create one
                if len(matching_instances_on_level) == 0:
                    # Create new ExtendedProgramVolumeInstance (Instance will be created within a transaction later)
                    new_ext_program_volume_instance = ExtendedProgramVolumeInstance(
                        None
                    )

                    # Set initial location to Data Silo location (family geometry reference point)
                    # This may be updated later by queue_program_volume_instance_location_updates
                    new_ext_program_volume_instance.desired_instance_location = (
                        ext_data_silo.Location.Point
                    )
                    new_ext_program_volume_instance.target_level = expected_level
                    new_ext_program_volume_instance.is_primary_instance = (
                        expected_level.rpw_level.Id.Value == canonical_level_id
                    )
                    new_ext_program_volume_instance.parent_data_silo = (
                        ext_data_silo  # Link to parent for parameter updates
                    )

                    ext_program_volume_instance_collection.extended_program_volume_instances.append(
                        new_ext_program_volume_instance
                    )

                    ext_data_silo.related_program_volume_instances.append(
                        new_ext_program_volume_instance
                    )

                    self.logger.debug(
                        "  Created new ExtendedProgramVolumeInstance on expected level {} for Data Silo {} (primary: {}).".format(
                            expected_level.rpw_level.Name,
                            str(ext_data_silo.Id.Value),
                            new_ext_program_volume_instance.is_primary_instance,
                        )
                    )

                # There are too many matching instances on this level - keep the best, delete others
                elif len(matching_instances_on_level) > 1:
                    # To do - figure out deletion
                    continue

                # Exactly one matching instance on this level - set its desired location
                else:
                    # Link existing instance to this Data Silo and set location
                    matching_instance_on_level = matching_instances_on_level[0]

                    # Keep existing instance at its current location initially
                    # queue_program_volume_instance_location_updates will update to Data Silo location if needed
                    matching_instance_on_level.desired_instance_location = (
                        matching_instance_on_level.rpw_element.Location.Point
                    )
                    matching_instance_on_level.target_level = expected_level
                    matching_instance_on_level.is_primary_instance = (
                        expected_level.rpw_level.Id.Value == canonical_level_id
                    )
                    self.logger.debug(
                        "  Found existing Program Volume Instance {} on expected level {} for Data Silo {} (primary: {}).".format(
                            str(matching_instance_on_level.Id.Value),
                            expected_level.rpw_level.Name,
                            str(ext_data_silo.Id.Value),
                            matching_instance_on_level.is_primary_instance,
                        )
                    )

            # Identify orphaned instances (on levels not in required_levels)
            # Filter out None values when building the set
            required_level_ids = set(
                el.rpw_level.Id.Value
                for el in expected_levels
                if el is not None and el.rpw_level is not None
            )
            for ext_program_volume in matching_ext_program_volumes:
                # Skip if this is a newly created instance (rpw_element is None)
                if ext_program_volume.rpw_element is None:
                    continue

                if ext_program_volume.LevelId.Value not in required_level_ids:
                    ext_program_volume.should_be_deleted = True
                    ext_program_volume_instance_collection.instances_to_delete.append(
                        ext_program_volume
                    )

                    # Safely get level name
                    level_name = "UNKNOWN"
                    try:
                        if (
                            ext_program_volume.rpw_element
                            and ext_program_volume.rpw_element.Level
                        ):
                            level_name = ext_program_volume.rpw_element.Level.Name
                    except:
                        pass

                    self.logger.debug(
                        "  Marked orphaned instance {} on level {} for deletion".format(
                            ext_program_volume.Id.Value, level_name
                        )
                    )

            # Add data silo to each matching program volume's "parent_data_silo" property
            for ext_program_volume in matching_ext_program_volumes:
                ext_program_volume.parent_data_silo = ext_data_silo

    def _enumerate_expected_program_volume_instances(self, ext_data_silo):
        """
        DEPRECATED: This method is no longer used. Level enumeration is now handled by
        PDSOLevelMappingService.enumerate_required_levels_for_data_silo() during validation.

        The required_levels property on ExtendedDataSilo is populated during validation
        and used directly in correlate_data_silos_to_program_volume_instances().

        Args:
            ext_data_silo (ExtendedDataSilo): The data silo to evaluate.
        Returns:
            List of ExtendedLevel objects where program volume instances are expected.
        """
        # Return required_levels that were populated during validation
        return ext_data_silo.required_levels

    def _is_instance_in_2d_spatial_element_winding_number(
        self, point_based_rpw_instance, rpw_spatial_element
    ):
        """
        Alternative point-in-polygon test using the winding number (sum of angles) method.

        This method calculates the sum of angles between rays from the test point to
        consecutive vertices of the polygon. If the sum is close to 360, the point is inside.

        Args:
            point_based_instance (): Any point-based "Family Instance" that has a Location property
            spatial_element (): Any spatial class (eg Area class) that has the GetBoundarySegments() method

        Returns:
            True if point_based_instance is inside of spatial_element, False otherwise

        Notes:
            This method is often more robust than ray casting for complex polygons because:
            - It doesn't suffer from ray-vertex intersection issues
            - It works well with polygons that have holes
            - It's less sensitive to numerical precision errors
            - It handles self-intersecting polygons better
        """
        try:
            # Get the point to test
            test_point = point_based_rpw_instance.Location.Point

            # Get spatial element boundaries
            boundary_options = DB.SpatialElementBoundaryOptions()
            boundary_options.StoreFreeBoundaryFaces = False
            boundary_options.SpatialElementBoundaryLocation = (
                DB.SpatialElementBoundaryLocation.Center
            )

            if self.logger:
                self.logger.debug(
                    "Getting boundary segments for spatial element (checking point containment)"
                )

            boundary_segments = rpw_spatial_element.GetBoundarySegments(
                boundary_options
            )

            # If no boundaries, return False
            if not boundary_segments or len(boundary_segments) == 0:
                if self.logger:
                    self.logger.debug("No boundary segments found for spatial element")
                return False

            if self.logger:
                self.logger.debug(
                    "Found {} boundary polygons for spatial element".format(
                        len(boundary_segments)
                    )
                )

            # Quick bounding box check for early elimination
            min_x = float("inf")
            max_x = float("-inf")
            min_y = float("inf")
            max_y = float("-inf")

            # Collect polygon vertices from all boundary curves
            all_polygons = []

            for poly_index, poly in enumerate(boundary_segments):
                polygon_vertices = []

                for segment in poly:
                    curve = segment.GetCurve()

                    # Sample points along the curve to handle arcs and splines
                    try:
                        # For lines, just use endpoints
                        if curve.GetType().Name == "Line":
                            start_pt = curve.GetEndPoint(0)
                            # Only add start point to avoid duplicates (end will be start of next)
                            if not polygon_vertices or (
                                abs(polygon_vertices[-1].X - start_pt.X) > 1e-6
                                or abs(polygon_vertices[-1].Y - start_pt.Y) > 1e-6
                            ):
                                polygon_vertices.append(start_pt)

                        else:
                            # For curves, sample multiple points
                            start_param = curve.GetEndParameter(0)
                            end_param = curve.GetEndParameter(1)
                            num_samples = 10  # More samples for curved segments

                            for i in range(num_samples):
                                param = start_param + (end_param - start_param) * i / (
                                    num_samples - 1
                                )
                                pt = curve.Evaluate(param, False)

                                # Avoid duplicate points
                                if not polygon_vertices or (
                                    abs(polygon_vertices[-1].X - pt.X) > 1e-6
                                    or abs(polygon_vertices[-1].Y - pt.Y) > 1e-6
                                ):
                                    polygon_vertices.append(pt)

                        # Update bounding box
                        start_pt = curve.GetEndPoint(0)
                        end_pt = curve.GetEndPoint(1)
                        for pt in [start_pt, end_pt]:
                            min_x = min(min_x, pt.X)
                            max_x = max(max_x, pt.X)
                            min_y = min(min_y, pt.Y)
                            max_y = max(max_y, pt.Y)

                    except Exception:
                        # If curve evaluation fails, try to get at least the endpoints
                        try:
                            start_pt = curve.GetEndPoint(0)
                            end_pt = curve.GetEndPoint(1)
                            for pt in [start_pt, end_pt]:
                                if not polygon_vertices or (
                                    abs(polygon_vertices[-1].X - pt.X) > 1e-6
                                    or abs(polygon_vertices[-1].Y - pt.Y) > 1e-6
                                ):
                                    polygon_vertices.append(pt)
                        except:
                            continue

                # Close the polygon if needed
                if polygon_vertices and len(polygon_vertices) > 2:
                    first_pt = polygon_vertices[0]
                    last_pt = polygon_vertices[-1]
                    if (
                        abs(first_pt.X - last_pt.X) > 1e-6
                        or abs(first_pt.Y - last_pt.Y) > 1e-6
                    ):
                        polygon_vertices.append(first_pt)

                    all_polygons.append(polygon_vertices)

            # Quick bounding box check
            buffer = max(max_x - min_x, max_y - min_y) * 0.01
            if (
                test_point.X < min_x - buffer
                or test_point.X > max_x + buffer
                or test_point.Y < min_y - buffer
                or test_point.Y > max_y + buffer
            ):
                return False

            # Calculate winding number for each polygon
            total_winding_number = 0.0

            for polygon_vertices in all_polygons:
                if len(polygon_vertices) < 3:
                    continue

                winding_number = 0.0

                # Calculate sum of angles
                for i in range(len(polygon_vertices) - 1):
                    # Get current and next vertex
                    v1 = polygon_vertices[i]
                    v2 = polygon_vertices[i + 1]

                    # Vectors from test point to vertices
                    vec1_x = v1.X - test_point.X
                    vec1_y = v1.Y - test_point.Y
                    vec2_x = v2.X - test_point.X
                    vec2_y = v2.Y - test_point.Y

                    # Skip if test point is very close to a vertex
                    dist1 = (vec1_x * vec1_x + vec1_y * vec1_y) ** 0.5
                    dist2 = (vec2_x * vec2_x + vec2_y * vec2_y) ** 0.5

                    if dist1 < 1e-6 or dist2 < 1e-6:
                        # Point is on or very close to boundary - consider it inside
                        return True

                    # Calculate angle between vectors using atan2 for better precision
                    angle1 = math.atan2(vec1_y, vec1_x)
                    angle2 = math.atan2(vec2_y, vec2_x)

                    # Calculate the angle difference, handling wraparound
                    angle_diff = angle2 - angle1

                    # Normalize angle difference to [-pi, pi]
                    while angle_diff > math.pi:
                        angle_diff -= 2 * math.pi
                    while angle_diff < -math.pi:
                        angle_diff += 2 * math.pi

                    winding_number += angle_diff

                total_winding_number += winding_number

            # Convert to number of full rotations
            total_rotations = abs(total_winding_number) / (2 * math.pi)

            # Point is inside if we have close to a full rotation (+/-360 degrees)
            # Allow some tolerance for numerical precision
            tolerance = 0.1  # 10% tolerance

            # Check if close to any integer number of rotations (should be close to 1 for simple polygons)
            is_inside = (
                abs(total_rotations - round(total_rotations)) < tolerance
                and round(total_rotations) != 0
            )

            return is_inside

        except Exception as e:
            # Log error and return False for safety
            if self.logger:
                self.logger.error("Error in spatial correlation: {}".format(str(e)))
            return False
