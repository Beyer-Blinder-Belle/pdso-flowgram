# -*- coding: utf-8 -*-
"""Plan image of a harvested unit: a PNG of the unit as the model shows it.

The catalogue's SVG sketch is a diagram (outline, dots for families). The
image is the real plan: walls, doors, furniture as drawn. It is saved beside
the entry as <id>.png and shown where a layout is chosen for placing.

How it is made: the active plan view is duplicated without its detailing
(tags and dimensions are not copied), its view template is taken off, every
annotation category, the Area lines (and any Room lines) and the PDSO data silos are
hidden, and the view is cropped to each unit in turn and exported. All of
this happens inside a transaction group that is rolled back at the end, so
the model is left exactly as it was.

The exported picture is then made black on white (Revit exports the colours
of its drawing area, which is dark in the dark theme) and everything outside
the unit is painted out, so only the unit and its own walls are left.
"""

import os
import shutil
import tempfile

import clr

clr.AddReference("System.Drawing")

from System import Array, Convert, Single  # noqa: E402
from System.IO import MemoryStream  # noqa: E402
from System.Drawing import (  # noqa: E402
    Bitmap,
    Color,
    Graphics,
    GraphicsUnit,
    Pen,
    PointF,
    Rectangle,
    Region,
    SolidBrush,
)
from System.Drawing.Drawing2D import GraphicsPath  # noqa: E402
from System.Drawing.Imaging import ColorMatrix, ImageAttributes, ImageFormat  # noqa: E402

from rpw import DB  # noqa: E402
from rpw.utils.dotnet import List  # noqa: E402

from pdso.code.catalogue.harvest_features import pdso_kinds  # noqa: E402

IMAGE_PIXELS = 1000  # width of the PNG
MARGIN = 1.0  # ft of plan kept around the unit: room for its own walls

# Model categories hidden in the image, besides every annotation category
HIDDEN_CATEGORIES = (
    "OST_AreaSchemeLines",
    "OST_Areas",
    "OST_Rooms",
    "OST_RoomSeparationLines",
    "OST_VolumeOfInterest",
)


def _luminance(color):
    return (0.299 * color.R + 0.587 * color.G + 0.114 * color.B) / 255.0


def clean_image(path, corners, outline):
    """Make an exported plan black on white and paint out all but the unit.

    Args:
        path (str): The PNG; it is rewritten in place.
        corners (tuple): (min x, min y, max x, max y) of the plan the picture
            shows, in the same coordinates as outline.
        outline (list): The unit's boundary, [(x, y), ...].
    """
    source = Bitmap(path)
    result = None
    # GDI objects hold Windows handles: each one is disposed (see finally)
    disposables = []
    try:
        width, height = source.Width, source.Height
        result = Bitmap(width, height)
        graphics = Graphics.FromImage(result)
        disposables.append(graphics)
        try:
            graphics.Clear(Color.White)

            # Dark drawing area: turn it to white paper with dark lines. The
            # corner pixel is the background; it goes to white, white to black.
            background = _luminance(source.GetPixel(2, 2))
            attributes = ImageAttributes()
            disposables.append(attributes)
            if background < 0.5:
                gain = 1.0 / max(1.0 - background, 0.05)
                shift = 1.0 + gain * background
                rows = [
                    Array[Single]([-0.299 * gain] * 3 + [0.0, 0.0]),
                    Array[Single]([-0.587 * gain] * 3 + [0.0, 0.0]),
                    Array[Single]([-0.114 * gain] * 3 + [0.0, 0.0]),
                    Array[Single]([0.0, 0.0, 0.0, 1.0, 0.0]),
                    Array[Single]([shift, shift, shift, 0.0, 1.0]),
                ]
                attributes.SetColorMatrix(ColorMatrix(Array[Array[Single]](rows)))
            graphics.DrawImage(
                source,
                Rectangle(0, 0, width, height),
                0,
                0,
                width,
                height,
                GraphicsUnit.Pixel,
                attributes,
            )

            # Paint out what is outside the unit. The unit is grown by the
            # margin so that its own boundary walls stay in the picture.
            min_x, min_y, max_x, max_y = corners
            scale_x = width / max(max_x - min_x, 1e-9)
            scale_y = height / max(max_y - min_y, 1e-9)
            points = Array[PointF](
                [
                    PointF((x - min_x) * scale_x, (max_y - y) * scale_y)
                    for x, y in outline
                ]
            )
            unit = GraphicsPath()
            grown = GraphicsPath()
            pen = Pen(Color.Black, 2.0 * MARGIN * scale_x)
            outside = Region(Rectangle(0, 0, width, height))
            brush = SolidBrush(Color.White)
            disposables.extend([unit, grown, pen, outside, brush])
            unit.AddPolygon(points)
            grown.AddPolygon(points)
            grown.Widen(pen)
            outside.Exclude(unit)
            outside.Exclude(grown)
            graphics.FillRegion(brush, outside)
        finally:
            for item in reversed(disposables):
                item.Dispose()
    except Exception:
        if result is not None:
            result.Dispose()
        raise
    finally:
        source.Dispose()
    # The source file is closed now, so the result can be saved over it
    try:
        result.Save(path, ImageFormat.Png)
    finally:
        result.Dispose()


def thumbnail_data_uri(path, height):
    """A small copy of a PNG as a data URI, to put straight into HTML.

    The picture travels inside the page, so it shows wherever the page does;
    a link to a local file can be blocked by the window that shows the page.

    Returns:
        str: "data:image/png;base64,...", or "" if the file can't be read.
    """
    try:
        source = Bitmap(path)
        try:
            width = max(1, int(round(source.Width * float(height) / source.Height)))
            small = Bitmap(source, width, int(height))
        finally:
            source.Dispose()
        stream = MemoryStream()
        try:
            small.Save(stream, ImageFormat.Png)
            return "data:image/png;base64," + Convert.ToBase64String(stream.ToArray())
        finally:
            small.Dispose()
            stream.Dispose()
    except Exception:
        return ""


def _roll_back(transaction):
    """Roll back a transaction that was started and not ended."""
    try:
        if (
            transaction is not None
            and transaction.HasStarted()
            and not transaction.HasEnded()
        ):
            transaction.RollBack()
    except Exception:
        pass


class UnitImager(object):
    """Exports one PNG per unit from a temporary copy of a plan view.

    Use:
        imager = UnitImager(doc, view)
        try:
            imager.export(outline, path)   # once per unit
        finally:
            imager.close()

    A view that is not a plan gives an imager that exports nothing; why is in
    imager.error. Harvest must never fail because an image could not be made.
    """

    def __init__(self, doc, view):
        self.doc = doc
        self.view = None
        self.group = None
        self.error = ""

        if not isinstance(view, DB.ViewPlan) or view.IsTemplate:
            self.error = "the active view is not a plan view"
            return
        transaction = None
        try:
            self.group = DB.TransactionGroup(doc, "PDSO - Layout images")
            self.group.Start()
            transaction = DB.Transaction(doc, "PDSO - Image view")
            transaction.Start()
            copy = doc.GetElement(view.Duplicate(DB.ViewDuplicateOption.Duplicate))
            copy.ViewTemplateId = DB.ElementId.InvalidElementId

            # Every annotation: tags, grids, section marks, matchlines... Some
            # reach far outside the crop and would stretch the picture
            for category in doc.Settings.Categories:
                if category.CategoryType == DB.CategoryType.Annotation:
                    self._hide(copy, category.Id)
            for name in HIDDEN_CATEGORIES:
                try:
                    self._hide(copy, DB.ElementId(getattr(DB.BuiltInCategory, name)))
                except Exception:
                    pass

            # A data silo is the unit's label and a Program Volume is its
            # generated copy. They are not part of the layout.
            kind = pdso_kinds(doc)
            silos = List[DB.ElementId]()
            for instance in DB.FilteredElementCollector(doc, copy.Id).OfClass(DB.FamilyInstance):
                if kind(instance) is not None:
                    if instance.CanBeHidden(copy):
                        silos.Add(instance.Id)
            if silos.Count:
                copy.HideElements(silos)

            copy.CropBoxActive = True
            copy.CropBoxVisible = False
            transaction.Commit()
            self.view = copy
        except Exception as error:
            self.error = "{}: {}".format(type(error).__name__, error)
            _roll_back(transaction)
            self.close()

    def _hide(self, view, category_id):
        try:
            if view.CanCategoryBeHidden(category_id):
                view.SetCategoryHidden(category_id, True)
        except Exception:
            pass

    def export(self, outline, path):
        """Save a PNG of the plan of one unit.

        Args:
            outline (list): The unit's boundary, [(x, y), ...] in project feet.
            path (str): Where the PNG goes.
        Returns:
            bool: True if the file was written.
        """
        if self.view is None or len(outline) < 3:
            return False
        folder = None
        transaction = None
        try:
            transaction = DB.Transaction(self.doc, "PDSO - Image crop")
            transaction.Start()
            box = self.view.CropBox
            inverse = box.Transform.Inverse
            points = [inverse.OfPoint(DB.XYZ(x, y, 0.0)) for x, y in outline]
            local = [(point.X, point.Y) for point in points]
            corners = (
                min(x for x, _ in local) - MARGIN,
                min(y for _, y in local) - MARGIN,
                max(x for x, _ in local) + MARGIN,
                max(y for _, y in local) + MARGIN,
            )
            box.Min = DB.XYZ(corners[0], corners[1], box.Min.Z)
            box.Max = DB.XYZ(corners[2], corners[3], box.Max.Z)
            self.view.CropBox = box
            transaction.Commit()

            folder = tempfile.mkdtemp(prefix="pdso_image_")
            views = List[DB.ElementId]()
            views.Add(self.view.Id)
            options = DB.ImageExportOptions()
            options.ExportRange = DB.ExportRange.SetOfViews
            options.SetViewsAndSheets(views)
            options.FilePath = os.path.join(folder, "unit")
            options.HLRandWFViewsFileType = DB.ImageFileType.PNG
            options.ShadowViewsFileType = DB.ImageFileType.PNG
            options.ZoomType = DB.ZoomFitType.FitToPage
            options.FitDirection = DB.FitDirectionType.Horizontal
            options.PixelSize = IMAGE_PIXELS
            options.ImageResolution = DB.ImageResolution.DPI_150
            self.doc.ExportImage(options)

            # Revit adds the view's type and name to the file name
            made = [name for name in os.listdir(folder) if name.lower().endswith(".png")]
            if not made:
                self.error = "Revit wrote no image"
                return False
            if os.path.isfile(path):
                os.remove(path)
            shutil.move(os.path.join(folder, made[0]), path)

            # Black on white, and only the unit. If this fails the plain
            # export is kept: a rough image is better than none.
            try:
                clean_image(path, corners, local)
            except Exception as error:
                self.error = "image kept as exported ({}: {})".format(
                    type(error).__name__, error
                )
            return True
        except Exception as error:
            self.error = "{}: {}".format(type(error).__name__, error)
            # An open crop transaction would make every later export fail
            _roll_back(transaction)
            return False
        finally:
            if folder and os.path.isdir(folder):
                shutil.rmtree(folder, True)

    def close(self):
        """Undo the temporary view. Safe to call more than once."""
        group, self.group, self.view = self.group, None, None
        if group is None:
            return
        try:
            if group.HasStarted() and not group.HasEnded():
                group.RollBack()
        except Exception:
            pass
