# Python standard libraries
import datetime

# 3rd Party Modules
from pyrevit import script

# Custom Modules
from pdso.code.data.shared_parameter_constants import standard_pdso_parameters


class PDSODataService(object):
    """
    Service class for handling PDSO synchronization operations, pretty much anything that needs a transaction.
    """

    def __init__(self, doc):
        # to do - pass in list of parameters from config?
        self.logger = script.get_logger()
        self.output = script.get_output()

        self.doc = doc

    # Add helper method to check transaction is open for these operations
    # Add helper method to fix current time for a full set of operations

    def current_time(self):
        """Get the current time formatted as HH:MM:SS.mmm"""
        return datetime.datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )  # Include milliseconds

    def queue_area_updates(self, ext_data_silos, ext_areas):
        """
        Harvest area data for synchronization

        Args:
            ext_data_silos (List[ExtendedDataSilo]): List of ExtendedDataSilo objects to reset and update.
            ext_areas (List[ExtendedArea]): List of ExtendedArea objects to reset and update.
        """
        self.logger.info("Starting harvest of area data to data silos.")

        current_time = self.current_time()

        for ext_data_silo in ext_data_silos:
            for (
                area_scheme_name,
                related_area,
            ) in ext_data_silo.related_area_elements.items():
                # related_area is a single ExtendedArea object
                area_value = related_area.rpw_area.area
                ext_data_silo.params_to_update[
                    related_area.bound_silo_parameter_name
                ] = area_value

            ext_data_silo.params_to_update["PDSO - Last Synced"] = current_time

    def queue_program_volume_instance_location_updates(
        self, ext_data_silos, ext_program_volume_instance_collection
    ):
        """Queue Program Volume location updates based on Data Silo locations.

        Sets desired_instance_location on instances for later commit.
        Does NOT create instances or update locations - that happens in commit_pending_changes().

        IMPORTANT: Uses Data Silo location because Program Volume family geometry is created
        with Area boundary curves translated to origin using Data Silo location as reference point.
        This ensures geometry appears correctly positioned when instances are placed at Data Silo location.

        Args:
            ext_data_silos (List[ExtendedDataSilo]): List of ExtendedDataSilo objects to read locations from.
            ext_program_volume_instance_collection (ExtendedProgramVolumeInstanceCollection): Collection for validation check.
        """

        # Check validation status before proceeding
        if not ext_program_volume_instance_collection.validation_passed:
            self.logger.error(
                "Cannot queue location updates - level mapping validation failed"
            )
            if ext_program_volume_instance_collection.validation_result:
                self.logger.error(
                    ext_program_volume_instance_collection.validation_result.get_summary_message()
                )
            return

        self.logger.info("Queueing Program Volume location updates from Data Silos...")

        for ext_data_silo in ext_data_silos:
            self.logger.debug(".  Processing Data Silo ID: {}".format(ext_data_silo.Id))

            # Get the location of the associated Data Silo (reference point for family geometry)
            data_silo_location = ext_data_silo.rpw_element.Location.Point
            self.logger.debug(
                ".  Data Silo location: ({:.2f}, {:.2f}, {:.2f})".format(
                    data_silo_location.X, data_silo_location.Y, data_silo_location.Z
                )
            )

            # Queue location updates for related Program Volume Instances
            self.logger.debug(
                ".  Found {} related Program Volume instances".format(
                    len(ext_data_silo.related_program_volume_instances)
                )
            )

            for (
                ext_program_volume_instance
            ) in ext_data_silo.related_program_volume_instances:
                # Skip instances marked for deletion
                if ext_program_volume_instance.should_be_deleted:
                    continue

                # Set desired location to Data Silo location (matching family geometry reference point)
                ext_program_volume_instance.desired_instance_location = (
                    data_silo_location
                )

                self.logger.debug(
                    ".  Queued location update for instance on level {}".format(
                        ext_program_volume_instance.target_level.rpw_level.Name
                        if ext_program_volume_instance.target_level
                        else "Unknown"
                    )
                )

    def queue_program_volume_instance_data_updates(
        self, ext_data_silos, ext_program_volume_instances
    ):
        """
        Push data from Data Silos to Program Volumes.

        Args:
            ext_data_silos (List[ExtendedDataSilo]): List of ExtendedDataSilo objects to read data from.
            ext_program_volumes (List[ExtendedProgramVolumeInstance]): List of ExtendedProgramVolumeInstance objects to update.
        """

        self.logger.info("Pushing data from Data Silos to Program Volumes...")

        current_time = self.current_time()

        pdso_params_to_push = [
            # Core identification parameters
            standard_pdso_parameters["PDSO - Program UID"],
            standard_pdso_parameters["PDSO - Program Display Name"],
            standard_pdso_parameters["PDSO - Unit Layout Type"],
            # User-managed data parameters
            standard_pdso_parameters["PDSO - Program Type"],
            standard_pdso_parameters["PDSO - Use Group"],
            standard_pdso_parameters["PDSO - Planning Dept. Misc"],
            standard_pdso_parameters["PDSO - Accessibility"],
            standard_pdso_parameters["PDSO - Program Stacking"],
            standard_pdso_parameters["PDSO - Program Market Segment"],
            standard_pdso_parameters["PDSO - Misc Modifier"],
            standard_pdso_parameters["PDSO - Load Factor"],
            standard_pdso_parameters["PDSO - DU Bedroom Count"],
            standard_pdso_parameters["PDSO - Bathroom Count"],
            standard_pdso_parameters["PDSO - Bathroom Types"],
            standard_pdso_parameters["PDSO - Kitchen Count"],
            standard_pdso_parameters["PDSO - Kitchen Types"],
            # Programmatically managed parameters - harvested from Areas
            standard_pdso_parameters["PDSO - ZFA"],
            standard_pdso_parameters["PDSO - NSF"],
            standard_pdso_parameters["PDSO - GSF"],
            # System metadata
            standard_pdso_parameters["PDSO - Last Synced"],
        ]

        for ext_data_silo in ext_data_silos:
            for parameter in pdso_params_to_push:
                try:
                    new_value = ext_data_silo.rpw_element.parameters[
                        parameter.parameter_name
                    ].value
                except Exception:
                    self.logger.debug(
                        "Parameter '{}' not found on Data Silo {}, skipping".format(
                            parameter.parameter_name, ext_data_silo.Id
                        )
                    )
                    continue
                # Push to related Program Volume Instances
                for (
                    ext_program_volume
                ) in ext_data_silo.related_program_volume_instances:
                    ext_program_volume.params_to_update[parameter.parameter_name] = (
                        new_value
                    )
