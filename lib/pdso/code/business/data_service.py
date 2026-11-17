# -*- coding: utf-8 -*-
# Python standard libraries
import datetime

# 3rd Party Modules
from pyrevit import script

# Custom Modules
from pdso.code.data.shared_parameter_constants import LAST_SYNCED
from pdso.code.business.parameter_registry_service import get_registry


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

            ext_data_silo.params_to_update[LAST_SYNCED] = current_time

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

        # Instances on levels that the stacking does not resolve to have no
        # target_level and are not moved (see below).
        self.logger.info("Queueing Program Volume location updates from Data Silos...")

        for ext_data_silo in ext_data_silos:
            self.logger.debug(".  Processing Data Silo ID: {}".format(ext_data_silo.Id))

            # The family geometry is built around the Data Silo's location, so
            # its instances go to that point. If the geometry was not rebuilt
            # this sync (geometry update "No"), they go to the point it was
            # built around, which is stored in the family.
            data_silo_location = ext_data_silo.rpw_element.Location.Point
            family = ext_data_silo.related_program_volume_family
            if family is not None and family.geometry_not_rebuilt:
                data_silo_location = family.built_basepoint
            if data_silo_location is None:
                # No stored build point: existing instances stay where they
                # are, and new ones go to the Data Silo (set in correlation)
                self.logger.debug(
                    ".  Family of Data Silo {} has no stored build point; "
                    "existing instances are not moved".format(ext_data_silo.Id)
                )
                continue

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

                # An instance with no target level is on a level that the
                # stacking does not resolve to. Moving it would put it on the
                # Data Silo's level (see commit_pending_changes), so it stays.
                if ext_program_volume_instance.target_level is None:
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

        # Every tracked parameter is pushed (set up in the Parameter Manager)
        pdso_params_to_push = get_registry(self.doc).parameters

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
