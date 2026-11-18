# -*- coding: utf-8 -*-
# Python standard libraries


# 3rd Party Modules
from Autodesk.Revit.DB import (
    BuiltInParameter,
    CheckoutStatus,
    FilteredWorksetCollector,
    WorksetKind,
    WorksetId,
    WorksharingUtils,
)
from pyrevit import script
from rpw import db

# Custom Modules


def _element_id_text(element):
    """An element's id as text, or "?" for one that can't be read (deleted)."""
    try:
        return str(element.Id)
    except Exception:
        return "?"


class WorksetCoordinationService(object):
    """Service for coordinating element workset assignments.

    This service provides methods to safely change element worksets
    with proper validation and transaction management. It ensures
    elements are valid and worksets exist before making changes.

    Attributes:
        document: Active Revit document

    Example:
        service = WorksetCoordinationService(doc)
        success = service.assign_elements_to_workset(
            elements=[elem1, elem2],
            workset_id=workset.Id
        )
    """

    def __init__(self, document):
        """Initialize workset coordination service.

        Args:
            document: Revit document to operate on

        Raises:
            ValueError: If document is None
        """
        if document is None:
            raise ValueError("Document cannot be None")

        self.logger = script.get_logger()
        self.output = script.get_output()

        self.document = document
        self._worksets_cache = None

    def get_worksets(self, force_refresh=False):
        """Get all user worksets in the document.

        Args:
            force_refresh (bool): Force cache invalidation

        Returns:
            list: Collection of user worksets, or empty list if not workshared

        Notes:
            Caches results to avoid repeated Revit API queries.
            Returns empty list for non-workshared documents.
        """
        if not self.document.IsWorkshared:
            return []

        if self._worksets_cache is None or force_refresh:
            collector = FilteredWorksetCollector(self.document)
            self._worksets_cache = collector.OfKind(
                WorksetKind.UserWorkset
            ).ToWorksets()

        return list(self._worksets_cache)

    def get_workset_by_id(self, workset_id):
        """Get workset by WorksetId.

        Args:
            workset_id: WorksetId instance

        Returns:
            Workset: Workset object if found, None otherwise
        """
        if workset_id is None:
            return None

        worksets = self.get_worksets()
        for workset in worksets:
            if workset.Id.IntegerValue == workset_id.IntegerValue:
                return workset

        return None

    def get_workset_by_name(self, workset_name):
        """Get workset by name.

        Args:
            workset_name (str): Name of the workset

        Returns:
            Workset: Workset object if found, None otherwise
        """
        if not workset_name:
            return None

        worksets = self.get_worksets()
        for workset in worksets:
            if workset.Name == workset_name:
                return workset

        return None

    def validate_element_for_workset_change(self, element):
        """Validate if element can have its workset changed.

        Args:
            element: Revit element to validate

        Returns:
            tuple: (bool, str) - (is_valid, error_message)

        Notes:
            Checks if element exists, has workset parameter,
            and parameter is not read-only.
        """
        if element is None:
            return False, "Element is None"

        # Check if element has workset parameter. A deleted element raises.
        try:
            if not element.IsValidObject:
                return False, "Element was deleted"
            workset_param = element.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
        except Exception as error:
            return False, "Element can't be read: {}".format(error)
        if workset_param is None:
            return False, "Element does not have a workset parameter"

        # Check if parameter is read-only
        if workset_param.IsReadOnly:
            return False, "Element workset parameter is read-only"

        # An element another user owns can't change, and would fail the
        # whole transaction
        try:
            status = WorksharingUtils.GetCheckoutStatus(self.document, element.Id)
            if status == CheckoutStatus.OwnedByOtherUser:
                return False, "Another user owns the element"
        except Exception:
            pass

        return True, ""

    def assign_elements_to_workset(
        self, elements, workset_name, transaction_name="PDSO - Assign Elements to Workset"
    ):
        """Assign elements to specified workset.

        Args:
            elements: Single element or collection of elements
            workset_name (str): Name of the workset to assign elements to
            transaction_name (str): Name for the transaction

        Returns:
            dict: Results dictionary with keys:
                - success (bool): Overall operation success
                - assigned_count (int): Number of elements successfully assigned
                - failed_count (int): Number of elements that failed
                - errors (list): List of error messages

        Raises:
            ValueError: If workset_name is None, empty, or workset not found
            Exception: For unexpected errors during assignment

        Example:
            service = WorksetCoordinationService(doc)
            results = service.assign_elements_to_workset(
                elements=[elem1, elem2, elem3],
                workset_name="Architectural"
            )
            self.logger.info("Assigned: {}".format(results['assigned_count']))
        """
        # Validate workset name
        if not workset_name:
            raise ValueError("workset_name cannot be None or empty")

        if not self.document.IsWorkshared:
            raise ValueError("Document is not workshared")

        workset = self.get_workset_by_name(workset_name)
        if workset is None:
            raise ValueError("Workset '{}' not found".format(workset_name))

        # Normalize elements to list
        if not isinstance(elements, (list, tuple)):
            elements = [elements]

        # Initialize results tracking
        results = {
            "success": False,
            "assigned_count": 0,
            "unchanged_count": 0,
            "failed_count": 0,
            "errors": [],
        }

        # Validate all elements first
        valid_elements = []
        for element in elements:
            is_valid, error_msg = self.validate_element_for_workset_change(element)
            if is_valid:
                valid_elements.append(element)
                # Debug: Log successful validation with current workset
                try:
                    current_workset = self.get_element_workset(element)
                    current_workset_name = (
                        current_workset.Name if current_workset else "N/A"
                    )
                    self.logger.debug(
                        "[DEBUG] Element {} validated OK (current workset: {})".format(
                            element.Id, current_workset_name
                        )
                    )
                except:
                    self.logger.debug(
                        "[DEBUG] Element {} validated OK".format(element.Id)
                    )
            else:
                results["failed_count"] += 1
                error = "Element {}: {}".format(_element_id_text(element), error_msg)
                results["errors"].append(error)
                # Debug: Log validation failure
                self.logger.debug("[DEBUG] {} validation FAILED".format(error))

        if not valid_elements:
            results["success"] = False
            return results

        # Perform workset assignment within transaction
        with db.Transaction(transaction_name, doc=self.document):
            try:
                for element in valid_elements:
                    try:
                        workset_param = element.get_Parameter(
                            BuiltInParameter.ELEM_PARTITION_PARAM
                        )
                        # Already there: setting it again would still edit
                        # (borrow) the element
                        if workset_param.AsInteger() == workset.Id.IntegerValue:
                            results["unchanged_count"] += 1
                            continue
                        workset_param.Set(workset.Id.IntegerValue)
                        results["assigned_count"] += 1
                    except Exception as e:
                        results["failed_count"] += 1
                        error = "Element {}: {}".format(element.Id, str(e))
                        results["errors"].append(error)

                results["success"] = (
                    results["assigned_count"] + results["unchanged_count"] > 0
                )

            except Exception as e:
                results["success"] = False
                results["errors"].append("Transaction error: {}".format(str(e)))
                raise

        return results

    def get_element_workset(self, element):
        """Get the workset assigned to an element.

        Args:
            element: Revit element

        Returns:
            Workset: Workset object if found, None otherwise
        """
        if element is None:
            return None

        workset_param = element.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)
        if workset_param is None:
            return None

        workset_id = WorksetId(workset_param.AsInteger())
        return self.get_workset_by_id(workset_id)
