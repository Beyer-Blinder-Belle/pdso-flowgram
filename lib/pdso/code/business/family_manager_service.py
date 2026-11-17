# -*- coding: utf-8 -*-
# Python standard libraries
import os
import traceback

# 3rd Party Modules
from rpw import revit, db
from pyrevit import script, DB, HOST_APP
from rpw.utils.dotnet import List

# Custom Modules
from pdso.code.business.hashing_service import PDSOHashingService
from pdso.code.business.area_boundary_service import (
    PROGRAM_VOLUME_EXTRUSION_HEIGHT,
    REPAIR_BRIDGE,
    REPAIR_SNAP,
    build_closed_curve_loop,
    get_boundary_options,
)

# Keys of PdsoProgramVolumeFamilySchema fields. The Area hash and the Data Silo
# basepoint are stored under the name of the shape Area's PDSO scheme.
GEOMETRY_KEY = "PDSO - GSF"
# Extra key in hashed_area_curve_geometry: the height the family was built with
EXTRUSION_HEIGHT_KEY = "Extrusion Height"
# Families built before the height was stored were all 8 ft tall
LEGACY_EXTRUSION_HEIGHT = PROGRAM_VOLUME_EXTRUSION_HEIGHT
# A Data Silo that moves less than this (ft, about 1/256") doesn't rebuild its family
BASEPOINT_TOLERANCE = 1.0 / (256 * 12.0)

# Module-level constant for Revit version template paths
REVIT_VERSION_TEMPLATES = {
    "2026": r"C:/ProgramData/Autodesk/RVT 2026/Family Templates/English-Imperial/Generic Model.rft",
    "2025": r"C:/ProgramData/Autodesk/RVT 2025/Family Templates/English-Imperial/Generic Model.rft",
    "2024": r"C:/ProgramData/Autodesk/RVT 2024/Family Templates/English-Imperial/Generic Model.rft",
    "2023": r"C:/ProgramData/Autodesk/RVT 2023/Family Templates/English-Imperial/Generic Model.rft",
    "2022": r"C:/ProgramData/Autodesk/RVT 2022/Family Templates/English-Imperial/Generic Model.rft",
    "2021": r"C:/ProgramData/Autodesk/RVT 2021/Family Templates/English-Imperial/Generic Model.rft",
}


class PDSOFamilyManagerService(object):
    """
    Service class for handling PDSO synchronization operations, pretty much anything that needs a transaction.
    """

    def __init__(self, doc):
        self.logger = script.get_logger()
        self.output = script.get_output()

        self.doc = doc

    def create_or_update_program_volume_families(
        self,
        ext_data_silo_collection,
        ext_program_volume_family_collection,
        progress_callback=None,
        extrusion_height=None,
        force_recompute=False,
    ):
        """Create or update Program Volume families based on the provided ExtendedDataSilo objects.

        A family is rebuilt when its Area outline, its Data Silo's location or
        the extrusion height changed since it was last built, or when
        force_recompute is set.

        Args:
            ext_data_silo_collection (ExtendedDataSiloCollection): The Data
                Silos to process.
            ext_program_volume_family_collection
                (ExtendedProgramVolumeFamilyCollection): The existing Program
                Volume families (gives the name prefix).
            progress_callback (callable, optional): Callback function for progress updates.
                Called with (current_index, total_count, family_name) after each family is processed.
            extrusion_height (float, optional): Program Volume height in feet. Defaults
                to PROGRAM_VOLUME_EXTRUSION_HEIGHT.
            force_recompute (bool): Rebuild every family, even if nothing changed.
        Returns:
                None (mutates input objects)
        """
        if not extrusion_height or extrusion_height <= 0:
            extrusion_height = PROGRAM_VOLUME_EXTRUSION_HEIGHT
        self.extrusion_height = float(extrusion_height)

        self.logger.info("Creating or updating Program Volume families...")

        hashing_service = PDSOHashingService().get_instance()
        total_families = len(ext_data_silo_collection.extended_data_silos)

        for index, ext_data_silo in enumerate(
            ext_data_silo_collection.extended_data_silos, start=1
        ):
            self.logger.debug("Processing Data Silo '{}'...".format(ext_data_silo.Id))

            # A correlated Data Silo has a family wrapper, but the Family
            # (rpw.Element) inside may be None. A Data Silo with no UID, or one
            # that correlation could not pair, has no wrapper and is skipped.
            ext_program_volume_family = ext_data_silo.related_program_volume_family
            uid = ext_data_silo.parameters["PDSO - Program UID"].value

            desired_family_name = "{}{}".format(
                ext_program_volume_family_collection.program_volume_family_name_prefix,
                str(uid),
            )

            if ext_program_volume_family is None or not uid:
                self.logger.warning(
                    ".  Skipping Data Silo '{}': {}".format(
                        ext_data_silo.Id,
                        "it has no Program UID"
                        if not uid
                        else "it has no Program Volume family (see the errors above)",
                    )
                )
            else:
                # One bad Area boundary must not abort the remaining Data Silos
                try:
                    self._create_or_update_family_for_data_silo(
                        ext_data_silo,
                        ext_program_volume_family,
                        desired_family_name,
                        hashing_service,
                        force_recompute,
                    )
                except Exception as error:
                    self.logger.error(
                        ".  Failed to create or update Program Volume family '{}' for Data Silo '{}': {}".format(
                            desired_family_name, ext_data_silo.Id, error
                        )
                    )
                    self.logger.debug(traceback.format_exc())

            # Call progress callback if provided
            if progress_callback:
                family_name = desired_family_name
                if ext_program_volume_family is not None and ext_program_volume_family.rpw_element:
                    family_name = ext_program_volume_family.rpw_element.Name
                progress_callback(index, total_families, family_name)

        self.logger.info("Finished creating or updating Program Volume families...")

    def _create_or_update_family_for_data_silo(
        self,
        ext_data_silo,
        ext_program_volume_family,
        desired_family_name,
        hashing_service,
        force_recompute=False,
    ):
        """Create, update or skip the Program Volume family for a single Data Silo.

        Args:
            ext_data_silo (ExtendedDataSilo): The Data Silo to process.
            ext_program_volume_family (ExtendedProgramVolumeFamily): The Data Silo's family wrapper.
            desired_family_name (str): The name the family should end up with.
            hashing_service (PDSOHashingService): Service used to hash the area geometry.
            force_recompute (bool): Rebuild the geometry even if nothing changed.
        Returns:
            None (mutates input objects)
        """
        # TO DO - SHOULD BE PLURAL
        ext_area_for_program_volume_family_geometry = (
            ext_data_silo.get_area_for_program_volume_family_geometry()
        )

        if ext_area_for_program_volume_family_geometry is None:
            self.logger.warning(
                ".  Skipping Data Silo '{}' because no Area contains it.".format(
                    ext_data_silo.Id
                )
            )
            return

        new_hash = hashing_service.area_tessellation_hash(
            ext_area_for_program_volume_family_geometry.rpw_element
        )

        basepoint = ext_data_silo.Location.Point
        extrusion_height = self._extrusion_height()

        # The fdoc starts as None
        fdoc = None

        # There's no existing family, so we need to create one
        if ext_program_volume_family.rpw_element is None:
            self.logger.debug(
                ".  Creating new Program Volume family for Data Silo '{}'".format(
                    ext_data_silo.Id
                )
            )

            # Create new family
            fdoc = self.create_new_program_volume_family(
                desired_family_name,
                ext_area_for_program_volume_family_geometry.rpw_area,
                basepoint,
            )

            if fdoc is not None:
                self.logger.debug(".  Successfully created a Program Volume family.")
            else:
                self.logger.error(
                    ".  Failed to create new Program Volume family '{}' for Data Silo '{}'".format(
                        desired_family_name, ext_data_silo.Id
                    )
                )

        # Existing family - check if we need to update geometry
        elif force_recompute or self._geometry_changed(
            ext_program_volume_family, new_hash, basepoint, extrusion_height
        ):
            self.logger.debug(
                ".  Updating Program Volume family for Data Silo '{}'{}".format(
                    ext_data_silo.Id, " (recompute all)" if force_recompute else ""
                )
            )

            # Try to update the family geometry
            fdoc = self.update_program_volume_family_geometry(
                ext_program_volume_family.rpw_element,
                ext_area_for_program_volume_family_geometry.rpw_area,
                basepoint,
            )

            if fdoc is None:
                self.logger.error(
                    ".  Failed to update Program Volume family '{}' for Data Silo '{}'; existing geometry was kept".format(
                        desired_family_name, ext_data_silo.Id
                    )
                )

        # Nothing changed - no update needed
        else:
            self.logger.debug(
                ".  Geometry unchanged, skipping Family updates for existing Program Volume family for Data Silo '{}'".format(
                    ext_data_silo.Id
                )
            )
            fdoc = None

        # If new or changed, load/ reload into project
        if fdoc is not None:
            self.logger.debug(
                ".  Loading updated Program Volume family into project for Data Silo '{}'".format(
                    ext_data_silo.Id
                )
            )

            # Load the updated family back into the project, then close the
            # family document: an open one stays in memory for the session
            try:
                wrapped_family = self.load_family_into_document(fdoc)
            finally:
                try:
                    fdoc.Close(False)
                except Exception as error:
                    self.logger.debug(
                        ".  Could not close the family document: {}".format(error)
                    )

            if wrapped_family is not None:
                self.logger.debug("Successfully updated Program Volume family geometry")

                # Save the updated wrapped rpw.family to the ext_program_volume_family
                ext_program_volume_family.rpw_element = wrapped_family

                # Set desired family name for renaming later
                ext_program_volume_family.desired_family_name = desired_family_name

                # Store what the geometry was built from, so the next sync
                # can tell whether it needs rebuilding
                ext_program_volume_family.entity_fields_to_update[
                    "hashed_area_curve_geometry"
                ] = {
                    GEOMETRY_KEY: new_hash,
                    EXTRUSION_HEIGHT_KEY: repr(float(extrusion_height)),
                }
                ext_program_volume_family.entity_fields_to_update[
                    "area_basepoint"
                ] = {GEOMETRY_KEY: basepoint}

            else:
                self.logger.error("Failed to load new/ updated family back into project")

        # No changes, no need to load/ reload into project
        else:
            self.logger.debug(
                ".  No changes made to Program Volume family for Data Silo '{}'".format(
                    ext_data_silo.Id
                )
            )

    def _extrusion_height(self):
        """Program Volume height in feet for this run."""
        return getattr(self, "extrusion_height", PROGRAM_VOLUME_EXTRUSION_HEIGHT)

    def _geometry_changed(
        self, ext_program_volume_family, new_hash, basepoint, extrusion_height
    ):
        """Whether a family's geometry is out of date.

        The geometry is the shape Area's outline, placed relative to the Data
        Silo's location and extruded to the configured height. A family with no
        stored basepoint (built before basepoints were stored) is rebuilt once.

        Returns:
            bool: True if the Area outline, the Data Silo location or the
                extrusion height differs from what the family was built with.
        """
        # Read the entity once
        entity = ext_program_volume_family.entity
        hashes = _as_dict(getattr(entity, "hashed_area_curve_geometry", None))
        basepoints = _as_dict(getattr(entity, "area_basepoint", None))

        previous_hash = hashes.get(GEOMETRY_KEY)
        if previous_hash is None or previous_hash != new_hash:
            self.logger.debug(
                ".  Area outline changed (previous hash {}, new hash {})".format(
                    previous_hash, new_hash
                )
            )
            return True

        try:
            previous_height = float(
                hashes.get(EXTRUSION_HEIGHT_KEY, LEGACY_EXTRUSION_HEIGHT)
            )
        except (TypeError, ValueError):
            previous_height = None
        if previous_height is None or abs(previous_height - extrusion_height) > 1e-6:
            self.logger.debug(
                ".  Extrusion height changed ({} -> {})".format(
                    previous_height, extrusion_height
                )
            )
            return True

        previous_basepoint = basepoints.get(GEOMETRY_KEY)
        if previous_basepoint is None or not previous_basepoint.IsAlmostEqualTo(
            basepoint, BASEPOINT_TOLERANCE
        ):
            self.logger.debug(
                ".  Data Silo location changed ({} -> {})".format(
                    previous_basepoint, basepoint
                )
            )
            return True

        return False

    def create_new_program_volume_family(
        self, desired_family_name, wrapped_area_element, reference_point
    ):
        """
        Create a new Program Volume family in the document from a template. Must be called within a transaction.
        Args:
            desired_family_name (str): The name for the new family.
            wrapped_area_element (rpw.db.spatial_element.Area): The area element to use for geometry creation.
        Returns:
            DB.Document: The newly created Program Volume family document, or None if
                the geometry could not be created (an empty family is never returned).
        """
        if wrapped_area_element is None:
            self.logger.error(
                "Cannot create Program Volume family because the provided ExtendedArea element is None."
            )
            return

        fdoc = self.new_family_document()

        if fdoc is None:
            return None

        if not self.convert_area_and_insert_as_freeform(
            fdoc, wrapped_area_element, reference_point
        ):
            # Don't load a family with no geometry into the project
            fdoc.Close(False)
            return None

        return fdoc

    def new_family_document(self):
        """Create a new Program Volume family document from template.

        Returns:
            DB.Document: The new family document, or None if creation fails

        Raises:
            ValueError: If Revit version is not supported
            IOError: If template file path does not exist
        """
        app = revit.app
        host_app_version = HOST_APP.version

        # Use the known template path. For a Revit version that is not in the
        # table, use the family template folder that Revit reports.
        if host_app_version in REVIT_VERSION_TEMPLATES:
            template_path = REVIT_VERSION_TEMPLATES[host_app_version]
        else:
            template_path = os.path.join(
                app.FamilyTemplatePath, "English-Imperial", "Generic Model.rft"
            )

        # Validate template file exists
        if not os.path.exists(template_path):
            error_msg = "Family template file does not exist: {}".format(template_path)
            self.logger.error(error_msg)
            raise IOError(error_msg)

        # Create new family document
        fdoc = app.NewFamilyDocument(template_path)

        if fdoc is None:
            self.logger.error("Failed to create new family document from template")
            return None
        else:
            self.logger.debug("Successfully created new family document from template")

        return fdoc

    def update_program_volume_family_geometry(
        self, volume_family, ext_area_element, reference_point
    ):
        """
        Update the geometry of an existing Program Volume family document based on the provided area element.
        Args:
            volume_family (db.Family): The existing Program Volume family to update.
            ext_area_element (rpw.db.spatial_element.Area): The area element to use for geometry creation.
        Returns:
            DB.Document: The updated family document, or None if the new geometry could
                not be created (the family in the project is left unchanged).
        """

        fdoc = self.doc.EditFamily(volume_family.unwrap())

        if fdoc is None:
            self.logger.debug("Error: Existing Family document could not be opened")
            return
        else:
            self.logger.debug("Success: Existing Family document was opened")

        self.clear_geometry_from_family_document(fdoc)
        if not self.convert_area_and_insert_as_freeform(
            fdoc, ext_area_element, reference_point
        ):
            # Discard the edit so the project keeps the previous geometry
            fdoc.Close(False)
            return None

        return fdoc

    def clear_geometry_from_family_document(self, fdoc):
        """
        Clear existing geometry from the family document.
        Args:
            fdoc (DB.Document): The family document to clear geometry from.
        Returns:
            None (mutates input objects)
        """
        self.logger.debug(
            "Collecting existing geometry from family document for deletion"
        )

        extrusions = db.Collector(doc=fdoc, of_class=DB.Extrusion).get_elements()

        directshapes = db.Collector(doc=fdoc, of_class=DB.DirectShape).get_elements()

        freeforms = db.Collector(doc=fdoc, of_class=DB.FreeFormElement).get_elements()

        self.logger.debug(
            "Found {} extrusions, {} direct shapes, and {} freeforms to delete".format(
                len(extrusions), len(directshapes), len(freeforms)
            )
        )

        self.logger.debug(
            "Starting transaction to delete old geometry from family document"
        )

        with db.Transaction("Delete old Geometry", fdoc):
            deleted_count = 0
            for element in extrusions:
                element.delete()
                deleted_count += 1
            for element in directshapes:
                element.delete()
                deleted_count += 1
            for element in freeforms:
                element.delete()
                deleted_count += 1

            self.logger.debug(
                "Deleted {} geometry elements from family document".format(
                    deleted_count
                )
            )

    def convert_area_and_insert_as_directshape(
        self, fdoc, spatial_element, reference_point
    ):
        """Convert the area geometry from the spatial element and insert it as a DirectShape in the family document.

        Args:
            fdoc (DB.Document): The family document to insert the geometry into.
            spatial_element (ExtendedArea): The spatial element containing the area geometry.
            reference_point (DB.XYZ): The reference point to translate the geometry to origin.
        Returns:
            None (mutates input objects)
        """
        self.logger.debug(
            "Starting transaction to create geometry in family document from spatial element"
        )

        with db.Transaction("Create Geometry", fdoc):
            vector_to_origin = DB.Transform.CreateTranslation(reference_point.Negate())

            self.logger.debug(
                "Translating spatial element from reference point ({:.2f}, {:.2f}, {:.2f}) to origin".format(
                    reference_point.X, reference_point.Y, reference_point.Z
                )
            )

            opts = DB.SpatialElementBoundaryOptions()
            opts.StoreFreeBoundaryFaces = False
            opts.SpatialElementBoundaryLocation = (
                DB.SpatialElementBoundaryLocation.Center
            )

            self.logger.debug("Extracting boundary segments from spatial element")
            boundary_segments = spatial_element.GetBoundarySegments(opts)
            self.logger.debug(
                "Found {} boundary polygons".format(len(boundary_segments))
            )

            # Hold a list of CurveLoops
            curve_loops = List[DB.CurveLoop]()

            # Coverts polygon to list[list[boundary segments]] and loop through it
            for polygon in boundary_segments:
                temp_loop = List[DB.Curve]()

                # Loops through all the sides of the polygon
                for polygon_component in polygon:
                    curve_from_spatial_element = polygon_component.GetCurve()
                    curve_moved_to_origin = (
                        curve_from_spatial_element.CreateTransformed(vector_to_origin)
                    )

                    # Append the curve to the CurveLoop
                    # self.logger.debug("Adding curve to loop")
                    temp_loop.Add(curve_moved_to_origin)

                # Append the CurveLoop to the list
                # self.logger.debug("Number of curves in temp loop: " + str(len(temp_loop)))
                curve_loop = DB.CurveLoop.Create(temp_loop)
                # self.logger.debug("Number of curves in curve loop:" + str(curve_loop.NumberOfCurves()))
                curve_loops.Add(curve_loop)
            # self.logger.debug("Number of curve loops: " + str(len(curve_loops)))

            # material_id = db.Collector(of_class='Material').get_first(wrapped=True).Id
            # self.logger.debug("Material id: " + str(material_id))

            cat_id = DB.ElementId(DB.BuiltInCategory.OST_GenericModel)

            try:
                self.logger.debug("Creating DirectShape element in family document")
                directshape = DB.DirectShape.CreateElement(fdoc, cat_id)

                self.logger.debug(
                    "Creating extrusion geometry from {} curve loops (height: 8)".format(
                        curve_loops.Count
                    )
                )
                sol = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
                    curve_loops, DB.XYZ.BasisZ, 8
                )

                geom = List[DB.GeometryObject]()
                geom.Add(sol)
                directshape.SetShape(geom)

                self.logger.debug(
                    "Successfully created DirectShape geometry in family document"
                )
            except Exception as error:
                self.logger.error(
                    "Failed to create DirectShape geometry: {}".format(error)
                )

    def load_family_into_document(self, family_document):
        """
        Load the family document into the project document.
        Args:
            family_document (DB.Document): The family document to load.
        Returns:
            rpw.db.Family: A wrapped version of the loaded family in the project document.
        """
        # Note: All open transactions must be closed before loading a family

        self.logger.debug(
            "Loading program volume family into project document (overwrite=True)"
        )

        # Load the family into the project document
        fam_load_opts = ProgramVolumeFamilyLoadOptions(True, False)

        family = family_document.LoadFamily(self.doc, fam_load_opts)

        if family is None:
            self.logger.error("Failed to load program volume family into project")
            return

        self.logger.debug("Successfully loaded program volume family into project")
        wrapped_family = db.Family(family)

        return wrapped_family

    def get_or_create_subcategory(self, fdoc, parent_bic, subcat_name):
        """Get (or create) a subcategory under the given BuiltInCategory in a family doc."""
        cats = fdoc.Settings.Categories
        parent_cat = cats.get_Item(parent_bic)

        # Try to find existing subcategory
        for sc in parent_cat.SubCategories:
            if sc.Name == subcat_name:
                return sc

        # Otherwise create one
        subcat = cats.NewSubcategory(parent_cat, subcat_name)
        # Optional: tweak graphics / material here:
        # subcat.LineWeight = 1
        # subcat.LineColor  = DB.Color(0, 0, 0)
        # subcat.Material   = some_material
        return subcat

    def convert_area_and_insert_as_freeform(
        self, fdoc, spatial_element, reference_point
    ):
        """
        Convert area geometry from spatial element and insert as FreeFormElement in a subcategory.

        Args:
            fdoc (DB.Document): The family document to insert the geometry into.
            spatial_element (rpw.db.spatial_element.Area): The spatial element containing the area geometry.
            reference_point (DB.XYZ): The reference point to translate the geometry to origin.
        Returns:
            bool: True if the FreeFormElement was created, False otherwise.
        """

        self.logger.debug(
            "Starting transaction to create geometry in family document from spatial element"
        )

        subcategory_name = spatial_element.scheme.name
        created = False

        with db.Transaction("Create Geometry", fdoc):
            vector_to_origin = DB.Transform.CreateTranslation(reference_point.Negate())

            self.logger.debug(
                "Translating spatial element from reference point ({:.2f}, {:.2f}, {:.2f}) to origin".format(
                    reference_point.X, reference_point.Y, reference_point.Z
                )
            )

            self.logger.debug("Extracting boundary segments from spatial element")
            boundary_segments = spatial_element.GetBoundarySegments(
                get_boundary_options()
            )
            self.logger.debug(
                "Found {} boundary polygons".format(len(boundary_segments))
            )

            # Build CurveLoops
            curve_loops = List[DB.CurveLoop]()
            try:
                for polygon in boundary_segments:
                    curves_moved_to_origin = [
                        polygon_component.GetCurve().CreateTransformed(vector_to_origin)
                        for polygon_component in polygon
                    ]
                    curve_loops.Add(self.build_closed_curve_loop(curves_moved_to_origin))
            except Exception as error:
                self.logger.error(
                    "Failed to build a closed profile from boundary of Area {}: {}".format(
                        spatial_element.Id, error
                    )
                )
                return False

            # --- NEW: get/create the subcategory on Generic Models ---
            subcat = self.get_or_create_subcategory(
                fdoc,
                DB.BuiltInCategory.OST_GenericModel,
                subcategory_name + " Program Volume",
            )

            try:
                self.logger.debug(
                    "Creating FreeFormElement in family document (instead of DirectShape)"
                )
                # Create a solid from the loops
                self.logger.debug(
                    "Creating extrusion geometry from {} curve loops "
                    "(height: {})".format(curve_loops.Count, self._extrusion_height())
                )
                sol = DB.GeometryCreationUtilities.CreateExtrusionGeometry(
                    curve_loops, DB.XYZ.BasisZ, self._extrusion_height()
                )

                # Create a FreeFormElement in the family document
                freeform = DB.FreeFormElement.Create(fdoc, sol)

                # Assign subcategory
                # freeform.Subcategory = gs
                freeform.Subcategory = subcat

                self.logger.debug(
                    "Successfully created FreeFormElement geometry in family document on subcategory '{}'".format(
                        subcategory_name
                    )
                )
                created = True
            except Exception as error:
                self.logger.error(
                    "Failed to create FreeFormElement geometry from Area {}: {}".format(
                        spatial_element.Id, error
                    )
                )

        return created

    def build_closed_curve_loop(self, curves):
        """Chain boundary curves into a closed CurveLoop, healing small defects.

        See area_boundary_service.build_closed_curve_loop; this logs each repair made.

        Args:
            curves (list[DB.Curve]): Boundary curves of a single polygon.
        Returns:
            DB.CurveLoop: A closed curve loop.
        Raises:
            Exception: If the curves can't be made into a closed loop.
        """
        repairs = []
        curve_loop = build_closed_curve_loop(curves, repairs)

        for kind, gap in repairs:
            if kind == REPAIR_BRIDGE:
                self.logger.warning(
                    "Bridging a {:.4f} ft gap in an Area boundary with a new line".format(
                        gap
                    )
                )
            elif kind == REPAIR_SNAP:
                self.logger.debug(
                    "Snapping a {:.5f} ft gap in an Area boundary".format(gap)
                )

        return curve_loop


class ProgramVolumeFamilyLoadOptions(DB.IFamilyLoadOptions):
    def __init__(self, overwrite_family, overwrite_parameters):
        self.overwrite_family = overwrite_family
        self.overwrite_parameters = overwrite_parameters

    def OnFamilyFound(self, family_in_use, overwrite_parameter_values):
        overwrite_parameter_values.Value = (
            not family_in_use or self.overwrite_parameters
        )
        return not family_in_use or self.overwrite_family

    def OnSharedFamilyFound(
        self, shared_family, family_in_use, source, overwrite_parameter_values
    ):
        source.Value = DB.FamilySource.Family
        overwrite_parameter_values.Value = (
            not family_in_use or self.overwrite_parameters
        )
        return not family_in_use or self.overwrite_family


def _as_dict(field_value):
    """A plain dict from an extensible storage map field value (None gives {})."""
    if field_value is None:
        return {}
    try:
        return dict((key, field_value[key]) for key in field_value.Keys)
    except AttributeError:
        return dict(field_value)
