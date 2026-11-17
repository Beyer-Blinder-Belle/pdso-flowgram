# -*- coding: utf-8 -*-
"""Windows of the Unit Layout Catalogue buttons.

The suggestion window shows a ranked list: one row for each suggestion, with a
plan thumbnail, the data of the unit or the layout, and the match in percent.
The best row is highlighted. A double-click on a row, or the action button,
accepts the row.

The windows are made in code (no XAML file). They take plain data from
``catalogue_api_service``, so the buttons, the HTTP API and the MCP server
show the same numbers.
"""

import math

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.Windows import (  # noqa: E402
    FontWeights,
    GridLength,
    GridUnitType,
    HorizontalAlignment,
    Point,
    ResizeMode,
    SystemParameters,
    TextTrimming,
    TextWrapping,
    Thickness,
    VerticalAlignment,
    Window,
    WindowStartupLocation,
)
from System.Windows.Controls import (  # noqa: E402
    Border,
    Button,
    Canvas,
    CheckBox,
    ColumnDefinition,
    ComboBox,
    Dock,
    DockPanel,
    Grid,
    Image,
    Orientation,
    RowDefinition,
    ScrollBarVisibility,
    ScrollViewer,
    StackPanel,
    TextBlock,
    TextBox,
)
from System.Windows.Input import Cursors  # noqa: E402
from System.Windows.Interop import WindowInteropHelper  # noqa: E402
from System.Windows.Media import (  # noqa: E402
    Color,
    DoubleCollection,
    FontFamily,
    PointCollection,
    SolidColorBrush,
)
from System.Windows.Media.Imaging import BitmapCacheOption, BitmapImage  # noqa: E402
from System.Windows.Shapes import Ellipse, Line, Polygon  # noqa: E402
from System import Uri  # noqa: E402

WINDOW_TITLE = "PDSO Unit Layout Catalogue"
PROJECT_TYPES = ["K-12", "Higher Education", "Residential", "Office", "Healthcare", "Other"]


def brush(red, green, blue, alpha=255):
    return SolidColorBrush(Color.FromArgb(alpha, red, green, blue))


WHITE = brush(255, 255, 255)
ACCENT = brush(63, 169, 216)
ROW = brush(233, 233, 233)
TEXT = brush(40, 40, 40)
MUTED = brush(105, 105, 105)
CHIP = brush(238, 238, 238)
GOOD = brush(22, 130, 60)
WARN = brush(176, 96, 0)
BAD = brush(196, 40, 40)
STATE = {"good": GOOD, "warn": WARN, "bad": BAD}

UNIT_FILL = brush(250, 250, 247)
UNIT_LINE = brush(35, 35, 35)
CURRENT_FILL = brush(214, 214, 214)
CURRENT_LINE = brush(165, 165, 165)
ITEM_FILL = brush(33, 111, 219, 95)
ITEM_LINE = brush(20, 84, 180)
CROSS_FILL = brush(245, 150, 20, 115)
CROSS_LINE = brush(200, 105, 0)
OUTSIDE_FILL = brush(220, 50, 40, 90)
OUTSIDE_LINE = brush(200, 30, 30)
LAYOUT_OUTLINE = brush(150, 60, 190)


# =============================================================================
# Small parts
# =============================================================================


def text(content, size=14, color=TEXT, bold=False, wrap=False):
    block = TextBlock()
    block.Text = content
    block.FontSize = size
    block.Foreground = color
    if bold:
        block.FontWeight = FontWeights.SemiBold
    if wrap:
        block.TextWrapping = TextWrapping.Wrap
    else:
        block.TextTrimming = TextTrimming.CharacterEllipsis
    return block


def button(label, width=150, default=False, cancel=False):
    control = Button()
    control.Content = label
    control.MinWidth = width
    control.Height = 34
    control.FontSize = 14
    control.Margin = Thickness(10, 0, 0, 0)
    control.Padding = Thickness(14, 0, 14, 0)
    control.IsDefault = default
    control.IsCancel = cancel
    return control


def percent(value):
    label = "{:.1f}".format(value * 100.0)
    if label.endswith(".0"):
        label = label[:-2]
    return label + "%"


def base_window(width, height, owner_handle=None):
    window = Window()
    window.Title = WINDOW_TITLE
    # Keep the window on the screen (a display scale of 200 % gives a small work area)
    area = SystemParameters.WorkArea
    window.Width = min(width, area.Width - 20)
    window.Height = min(height, area.Height - 16)
    window.Background = WHITE
    window.FontFamily = FontFamily("Segoe UI")
    window.ResizeMode = ResizeMode.CanResize
    window.ShowInTaskbar = False
    if owner_handle is not None:
        WindowInteropHelper(window).Owner = owner_handle
        window.WindowStartupLocation = WindowStartupLocation.CenterOwner
    else:
        window.WindowStartupLocation = WindowStartupLocation.CenterScreen
    return window


def chips(fields):
    """A row of label and value pairs, in the look of a filter row."""
    panel = StackPanel()
    panel.Orientation = Orientation.Horizontal
    for label, value in fields:
        column = StackPanel()
        column.Margin = Thickness(0, 0, 14, 0)
        column.Children.Add(text(label, 11.5, MUTED))
        box = Border()
        box.Background = CHIP
        box.Padding = Thickness(9, 4, 12, 4)
        box.Margin = Thickness(0, 2, 0, 0)
        box.Child = text(value, 13)
        column.Children.Add(box)
        panel.Children.Add(column)
    return panel


# =============================================================================
# Plan thumbnail
# =============================================================================


def _turned(point, turn):
    return (point[1], -point[0]) if turn else (point[0], point[1])


def plan_extent(preview, turn=False, unit=True):
    points = list(preview["layout_outline"])
    if unit:
        points = points + list(preview["unit"]["outline"])
    points = [_turned(point, turn) for point in points]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _polygon(canvas, points, fill, stroke, thickness, dash=None):
    shape = Polygon()
    collection = PointCollection()
    for x, y in points:
        collection.Add(Point(x, y))
    shape.Points = collection
    if fill is not None:
        shape.Fill = fill
    shape.Stroke = stroke
    shape.StrokeThickness = thickness
    if dash:
        dashes = DoubleCollection()
        for value in dash:
            dashes.Add(float(value))
        shape.StrokeDashArray = dashes
    canvas.Children.Add(shape)


def plan_canvas(preview, width, height, scale, turn=False, unit=True):
    """Draw a unit with the items of a layout in it.

    Args:
        preview (dict): Reply of catalogue_api_service.preview_layout_in_unit
        width, height (float): Size of the canvas
        scale (float): Pixels for each foot
        turn (bool): Turn the plan 90 degrees clockwise
        unit (bool): Draw the unit and its current items. False: only the layout
    """
    canvas = Canvas()
    canvas.Width = width
    canvas.Height = height
    canvas.Background = WHITE
    canvas.ClipToBounds = True
    min_x, min_y, max_x, max_y = plan_extent(preview, turn, unit)
    left = width / 2.0 - (max_x - min_x) * scale / 2.0
    top = height / 2.0 - (max_y - min_y) * scale / 2.0

    def px(point):
        x, y = _turned(point, turn)
        return (left + (x - min_x) * scale, top + (max_y - y) * scale)

    if unit:
        _polygon(canvas, [px(p) for p in preview["unit"]["outline"]], UNIT_FILL, UNIT_LINE, 1.6)
        for item in preview["current_items"]:
            if item["footprint"]:
                _polygon(
                    canvas, [px(p) for p in item["footprint"]], CURRENT_FILL, CURRENT_LINE, 0.6
                )
        _polygon(
            canvas, [px(p) for p in preview["layout_outline"]], None, LAYOUT_OUTLINE, 1.0, (4, 3)
        )
    else:
        _polygon(canvas, [px(p) for p in preview["layout_outline"]], UNIT_FILL, UNIT_LINE, 1.6)

    for item in preview["items"]:
        fill, line = ITEM_FILL, ITEM_LINE
        if unit and item.get("across"):
            fill, line = CROSS_FILL, CROSS_LINE
        if unit and (not item["inside"] or not item["loaded"]):
            fill, line = OUTSIDE_FILL, OUTSIDE_LINE
        x, y = px((item["x"], item["y"]))
        if item["footprint"]:
            _polygon(canvas, [px(p) for p in item["footprint"]], fill, line, 1.0)
        else:
            dot = Ellipse()
            dot.Width = dot.Height = max(5.0, scale)
            dot.Fill = fill
            dot.Stroke = line
            Canvas.SetLeft(dot, x - dot.Width / 2.0)
            Canvas.SetTop(dot, y - dot.Height / 2.0)
            canvas.Children.Add(dot)
        dx, dy = _turned((math.cos(item["rotation"]), math.sin(item["rotation"])), turn)
        tick = Line()
        tick.X1, tick.Y1 = x, y
        tick.X2, tick.Y2 = x + dx * max(scale * 1.2, 5.0), y - dy * max(scale * 1.2, 5.0)
        tick.Stroke = line
        tick.StrokeThickness = 1.0
        canvas.Children.Add(tick)
    return canvas


def sketch_canvas(outline, width, height, boxes=None, points=None):
    """A small plan: an outline, grey boxes (furniture) and blue points (layout items).

    The plan is scaled to fill the canvas. Y is up.

    Args:
        outline (list): [[x, y], ...]
        width, height (float): Size of the canvas
        boxes (list): Plan boxes, each [[x, y] x 4]
        points (list): [[x, y, rotation], ...]
    """
    canvas = Canvas()
    canvas.Width = width
    canvas.Height = height
    canvas.Background = WHITE
    canvas.ClipToBounds = True
    if not outline or len(outline) < 3:
        return canvas
    xs = [point[0] for point in outline]
    ys = [point[1] for point in outline]
    min_x, max_y = min(xs), max(ys)
    span_x = max(max(xs) - min_x, 1.0)
    span_y = max(max_y - min(ys), 1.0)
    scale = min((width - 8.0) / span_x, (height - 8.0) / span_y)
    left = width / 2.0 - span_x * scale / 2.0
    top = height / 2.0 - span_y * scale / 2.0

    def px(point):
        return (left + (point[0] - min_x) * scale, top + (max_y - point[1]) * scale)

    _polygon(canvas, [px(p) for p in outline], UNIT_FILL, UNIT_LINE, 1.2)
    for box in boxes or []:
        _polygon(canvas, [px(p) for p in box], CURRENT_FILL, CURRENT_LINE, 0.5)
    for point in points or []:
        x, y = px(point)
        dot = Ellipse()
        dot.Width = dot.Height = max(3.5, min(7.0, 0.8 * scale))
        dot.Fill = ITEM_LINE
        Canvas.SetLeft(dot, x - dot.Width / 2.0)
        Canvas.SetTop(dot, y - dot.Height / 2.0)
        canvas.Children.Add(dot)
    _polygon(canvas, [px(p) for p in outline], None, UNIT_LINE, 1.2)
    return canvas


def plan_image(path, width, height):
    """The plan image that Harvest saved, fitted into width x height, or None."""
    try:
        bitmap = BitmapImage()
        bitmap.BeginInit()
        # Read the file now and let go of it, so that a new harvest can replace it
        bitmap.CacheOption = BitmapCacheOption.OnLoad
        bitmap.UriSource = Uri(path)
        bitmap.EndInit()
        image = Image()
        image.Source = bitmap
        image.Width = width
        image.Height = height
        image.HorizontalAlignment = HorizontalAlignment.Left
        image.VerticalAlignment = VerticalAlignment.Top
        return image
    except Exception:
        return None


def fit_line(preview):
    """One line that says if the layout fits the unit, and "good", "warn" or "bad"."""
    match = preview["match"]
    count = len(preview["items"])
    if match["fits"]:
        line = "Fits: all {} insertion points are in the unit ({})".format(
            count, match["placement"]["description"]
        )
        state = "good"
        across = match.get("items_across_outline", 0)
        if across:
            line += ", but {} item{} across the unit outline".format(
                across, " goes" if across == 1 else "s go"
            )
            state = "warn"
        if match.get("families_to_load"):
            line += "; {} famil{} will be loaded".format(
                len(match["families_to_load"]),
                "y" if len(match["families_to_load"]) == 1 else "ies",
            )
        if match["items_not_loaded"]:
            line += "; {} items: family not loaded".format(match["items_not_loaded"])
            state = "warn"
        return line, state
    if match["items_outside"]:
        return (
            "Does not fit: {} of {} items are outside the unit".format(
                match["items_outside"], count
            ),
            "bad",
        )
    return (
        "Does not fit: the layout ({:.1f} x {:.1f} ft) is larger than the unit"
        " ({:.1f} x {:.1f} ft)".format(
            preview["layout"]["width"],
            preview["layout"]["depth"],
            preview["unit"]["width"],
            preview["unit"]["depth"],
        ),
        "bad",
    )


# =============================================================================
# Suggestion window
# =============================================================================


class SuggestionWindow(object):
    """Ranked list of suggestions with thumbnails.

    Args:
        heading (str): Text above the list
        fields (list): (label, value) pairs for the top row
        rows (list): dicts with "preview", "title", "lines" [(text, state or
            None)], "right" [text], "match" (text), "enabled" (bool), "data"
        action_label (str): Text of the action button
        option_label (str): Text of a check box, or None
        reference (dict): The original, shown as a fixed first row for a
            comparison by eye: "preview", "unit" (draw the unit, or only the
            layout), "tag", "title", "lines", "right"
        owner_handle: Window handle of Revit
    """

    THUMB_WIDTH = 200.0
    ROW_HEIGHT = 78.0
    IMAGE_WIDTH = 100.0

    def __init__(
        self, heading, fields, rows, action_label, option_label=None, reference=None,
        owner_handle=None,
    ):
        self.rows = rows
        self.has_images = any(row.get("image") for row in rows)
        self.result = None
        self.option = False
        self._selected = None
        self._parts = []
        self.window = base_window(1220, 212 + (len(rows) + (1 if reference else 0)) * 82, owner_handle)

        extents = [plan_extent(row["preview"]) for row in rows]
        if reference is not None:
            extents.append(plan_extent(reference["preview"], unit=reference["unit"]))
        span_x = max([e[2] - e[0] for e in extents] + [1.0])
        span_y = max([e[3] - e[1] for e in extents] + [1.0])
        self.turn = span_y > span_x
        if self.turn:
            span_x, span_y = span_y, span_x
        self.scale = min((self.THUMB_WIDTH - 20) / span_x, (self.ROW_HEIGHT - 16) / span_y)

        root = DockPanel()
        root.Margin = Thickness(16, 8, 16, 10)
        self.window.Content = root

        top = DockPanel()
        DockPanel.SetDock(top, Dock.Top)
        root.Children.Add(top)
        info = StackPanel()
        info.Children.Add(chips(fields))
        top.Children.Add(info)
        if reference is not None:
            original = self._reference_row(reference)
            original.Margin = Thickness(0, 8, 0, 0)
            DockPanel.SetDock(original, Dock.Top)
            root.Children.Add(original)
        note = text(heading, 14)
        note.Margin = Thickness(0, 8, 0, 5)
        DockPanel.SetDock(note, Dock.Top)
        root.Children.Add(note)

        bottom = DockPanel()
        bottom.Margin = Thickness(0, 8, 0, 0)
        DockPanel.SetDock(bottom, Dock.Bottom)
        root.Children.Add(bottom)
        buttons = StackPanel()
        buttons.Orientation = Orientation.Horizontal
        DockPanel.SetDock(buttons, Dock.Right)
        self.action = button(action_label, 220, default=True)
        self.action.Click += self._accept
        close = button("Close", 110, cancel=True)
        buttons.Children.Add(self.action)
        buttons.Children.Add(close)
        bottom.Children.Add(buttons)
        left = StackPanel()
        left.VerticalAlignment = VerticalAlignment.Center
        self.check = None
        if option_label:
            self.check = CheckBox()
            self.check.Content = option_label
            self.check.FontSize = 14
            self.check.VerticalContentAlignment = VerticalAlignment.Center
            left.Children.Add(self.check)
        legend = text(
            "Blue: items of the layout.  Grey: furniture in the unit now.  Dashed: outline of"
            " the source unit.  Amber: item across a wall.  Red: item outside."
            + ("  Plans turned 90 deg." if self.turn else ""),
            11,
            MUTED,
        )
        legend.Margin = Thickness(0, 4, 0, 0)
        left.Children.Add(legend)
        bottom.Children.Add(left)

        scroll = ScrollViewer()
        scroll.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
        scroll.BorderBrush = brush(200, 200, 200)
        scroll.BorderThickness = Thickness(1)
        stack = StackPanel()
        scroll.Content = stack
        root.Children.Add(scroll)
        for index, row in enumerate(rows):
            stack.Children.Add(self._row(index, row))
        if rows:
            self._select(0)

    def _with_image(self, middle, path):
        """Put the harvested plan image of a layout to the left of its text.

        Every row keeps the same slot when any row has an image, so that the
        text lines up. Hover over the image for a large view.
        """
        if not self.has_images:
            return middle
        host = DockPanel()
        Grid.SetColumn(host, 1)
        slot = Border()
        slot.Width = self.IMAGE_WIDTH
        slot.Margin = Thickness(10, 2, 0, 2)
        if path:
            picture = plan_image(path, self.IMAGE_WIDTH, self.ROW_HEIGHT - 8)
            if picture is not None:
                slot.Child = picture
                large = plan_image(path, 560, 560)
                if large is not None:
                    slot.ToolTip = large
        DockPanel.SetDock(slot, Dock.Left)
        host.Children.Add(slot)
        host.Children.Add(middle)
        return host

    def _reference_row(self, reference):
        """The original as a row with the same columns and the same plan scale."""
        border = Border()
        border.Background = WHITE
        border.BorderBrush = brush(120, 120, 120)
        border.BorderThickness = Thickness(1.5)
        grid = Grid()
        for width in (self.THUMB_WIDTH, None, 200.0):
            column = ColumnDefinition()
            column.Width = (
                GridLength(1, GridUnitType.Star) if width is None else GridLength(width)
            )
            grid.ColumnDefinitions.Add(column)
        # The plan image from Harvest if there is one, else the diagram
        picture = None
        if reference.get("image"):
            picture = plan_image(
                reference["image"], self.THUMB_WIDTH - 4, self.ROW_HEIGHT - 4
            )
        if picture is None:
            picture = plan_canvas(
                reference["preview"],
                self.THUMB_WIDTH - 4,
                self.ROW_HEIGHT - 4,
                self.scale,
                self.turn,
                unit=reference["unit"],
            )
        grid.Children.Add(picture)
        middle = StackPanel()
        middle.Margin = Thickness(16, 2, 8, 2)
        Grid.SetColumn(middle, 1)
        head = StackPanel()
        head.Orientation = Orientation.Horizontal
        tag = Border()
        tag.Background = brush(70, 70, 70)
        tag.Padding = Thickness(7, 1, 7, 2)
        tag.Margin = Thickness(0, 3, 10, 0)
        tag.VerticalAlignment = VerticalAlignment.Top
        tag.Child = text(reference["tag"], 11, WHITE, bold=True)
        head.Children.Add(tag)
        head.Children.Add(text(reference["title"], 16))
        middle.Children.Add(head)
        for content in reference["lines"]:
            block = text(content, 12, MUTED)
            block.Margin = Thickness(0, 1, 0, 0)
            middle.Children.Add(block)
        grid.Children.Add(self._with_image(middle, None))
        right = StackPanel()
        right.Margin = Thickness(0, 5, 16, 2)
        right.HorizontalAlignment = HorizontalAlignment.Right
        Grid.SetColumn(right, 2)
        for content in reference["right"]:
            block = text(content, 12, MUTED)
            block.HorizontalAlignment = HorizontalAlignment.Right
            right.Children.Add(block)
        grid.Children.Add(right)
        border.Child = grid
        return border

    def _row(self, index, row):
        border = Border()
        border.Margin = Thickness(0, 0, 0, 4)
        border.Cursor = Cursors.Hand
        grid = Grid()
        for width in (self.THUMB_WIDTH, None, 200.0):
            column = ColumnDefinition()
            column.Width = (
                GridLength(1, GridUnitType.Star) if width is None else GridLength(width)
            )
            grid.ColumnDefinitions.Add(column)
        thumb = Border()
        thumb.Background = WHITE
        thumb.BorderThickness = Thickness(2)
        thumb.Child = plan_canvas(
            row["preview"], self.THUMB_WIDTH - 4, self.ROW_HEIGHT - 4, self.scale, self.turn
        )
        grid.Children.Add(thumb)

        middle = StackPanel()
        middle.Margin = Thickness(16, 2, 8, 2)
        Grid.SetColumn(middle, 1)
        blocks = [(text(row["title"], 16), None)]
        for content, state in row["lines"]:
            block = text(content, 12)
            block.Margin = Thickness(0, 1, 0, 0)
            blocks.append((block, state))
        for block, _ in blocks:
            middle.Children.Add(block)
        grid.Children.Add(self._with_image(middle, row.get("image")))

        right = StackPanel()
        right.Margin = Thickness(0, 3, 16, 2)
        right.HorizontalAlignment = HorizontalAlignment.Right
        Grid.SetColumn(right, 2)
        for content in row["right"]:
            block = text(content, 11.5)
            block.HorizontalAlignment = HorizontalAlignment.Right
            blocks.append((block, "soft"))
            right.Children.Add(block)
        match = text(row["match"], 16)
        match.HorizontalAlignment = HorizontalAlignment.Right
        blocks.append((match, None))
        right.Children.Add(match)
        grid.Children.Add(right)

        border.Child = grid
        border.MouseLeftButtonDown += lambda sender, args, i=index: self._clicked(i, args)
        self._parts.append((border, thumb, blocks))
        return border

    def _clicked(self, index, args):
        self._select(index)
        if args.ClickCount >= 2 and self.rows[index].get("enabled", True):
            self._accept(None, None)

    def _select(self, index):
        self._selected = index
        for number, (border, thumb, blocks) in enumerate(self._parts):
            selected = number == index
            border.Background = ACCENT if selected else ROW
            thumb.BorderBrush = ACCENT if selected else ROW
            for block, state in blocks:
                if selected:
                    block.Foreground = WHITE
                elif state == "soft":
                    block.Foreground = MUTED
                else:
                    block.Foreground = STATE.get(state, TEXT)
        self.action.IsEnabled = bool(self.rows[index].get("enabled", True))

    def _accept(self, sender, args):
        if self._selected is None:
            return
        self.result = self.rows[self._selected]["data"]
        self.option = bool(self.check is not None and self.check.IsChecked)
        self.window.DialogResult = True

    def show(self):
        """Show the window. Returns the data of the accepted row, or None."""
        self.window.ShowDialog()
        return self.result


# =============================================================================
# Harvest window
# =============================================================================


class HarvestWindow(object):
    """Ask for the unit and the names of a new layout.

    Args:
        describe_unit: Function (Program UID) -> dict {"uid", "number", "name",
            "level", "area", "items", "outline", "boxes"} or None if no unit
            has that UID
        uid (str): Program UID of the selected unit, or ""
        owner_handle: Window handle of Revit
    """

    def __init__(self, describe_unit, uid="", owner_handle=None):
        self.describe_unit = describe_unit
        self.result = None
        self._auto_name = ""
        self._auto_program = ""
        self.window = base_window(900, 440, owner_handle)
        outer = DockPanel()
        outer.Margin = Thickness(22, 14, 22, 16)
        self.window.Content = outer
        side = StackPanel()
        side.Width = 270
        side.Margin = Thickness(22, 44, 0, 0)
        DockPanel.SetDock(side, Dock.Right)
        side.Children.Add(text("Unit preview", 12, MUTED))
        self.preview = Border()
        self.preview.BorderBrush = brush(200, 200, 200)
        self.preview.BorderThickness = Thickness(1)
        self.preview.Margin = Thickness(0, 3, 0, 0)
        self.preview.Width = 270
        self.preview.Height = 210
        side.Children.Add(self.preview)
        side.Children.Add(text("Grey: the furniture that the harvest takes.", 11.5, MUTED))
        outer.Children.Add(side)
        root = StackPanel()
        outer.Children.Add(root)
        root.Children.Add(text("Harvest Layout", 22))
        hint = text(
            "Save the free-standing furniture of a unit (an Area with a PDSO data silo)"
            " as one layout in the catalogue.",
            13,
            MUTED,
        )
        hint.Margin = Thickness(0, 2, 0, 12)
        root.Children.Add(hint)

        grid = Grid()
        for width in (150.0, None):
            column = ColumnDefinition()
            column.Width = (
                GridLength(1, GridUnitType.Star) if width is None else GridLength(width)
            )
            grid.ColumnDefinitions.Add(column)
        root.Children.Add(grid)

        def add(label, control):
            row = grid.RowDefinitions.Count
            grid.RowDefinitions.Add(RowDefinition())
            caption = text(label, 14, MUTED)
            caption.VerticalAlignment = VerticalAlignment.Center
            caption.Margin = Thickness(0, 6, 0, 6)
            Grid.SetRow(caption, row)
            grid.Children.Add(caption)
            control.Margin = Thickness(0, 6, 0, 6)
            control.FontSize = 14
            Grid.SetRow(control, row)
            Grid.SetColumn(control, 1)
            grid.Children.Add(control)
            return control

        def box():
            control = TextBox()
            control.Height = 30
            control.VerticalContentAlignment = VerticalAlignment.Center
            control.Padding = Thickness(6, 0, 6, 0)
            return control

        self.unit_box = add("Program UID", box())
        self.unit_info = add(
            "", text("Type the Program UID of a unit, or select it first.", 13, MUTED, wrap=True)
        )
        self.name_box = add("Layout name", box())
        self.program_box = add("Program", box())
        self.type_box = add("Project type", ComboBox())
        self.type_box.Height = 30
        for name in PROJECT_TYPES:
            self.type_box.Items.Add(name)
        self.type_box.SelectedIndex = PROJECT_TYPES.index("Other")

        buttons = StackPanel()
        buttons.Orientation = Orientation.Horizontal
        buttons.HorizontalAlignment = HorizontalAlignment.Right
        buttons.Margin = Thickness(0, 18, 0, 0)
        self.ok = button("Harvest", 150, default=True)
        self.ok.IsEnabled = False
        self.ok.Click += self._accept
        buttons.Children.Add(self.ok)
        buttons.Children.Add(button("Cancel", 110, cancel=True))
        root.Children.Add(buttons)

        self.unit_box.TextChanged += self._unit_changed
        self.unit_box.Text = uid or ""
        if uid:
            self.unit_info.Text = "From the selection: " + self.unit_info.Text
        self.window.Loaded += lambda sender, args: self.unit_box.Focus()

    def _unit_changed(self, sender, args):
        unit = self.describe_unit(self.unit_box.Text.strip())
        if unit is None:
            self.unit_info.Text = "No unit has this Program UID."
            self.unit_info.Foreground = MUTED
            self.ok.IsEnabled = False
            self.preview.Child = None
            return
        self.preview.Child = sketch_canvas(unit["outline"], 268, 208, unit["boxes"])
        area_name = unit["name"] or ""
        self.unit_info.Text = "Area {} {} on {}, {:,.0f} SF, {} free-standing items".format(
            unit["number"] or "", area_name, unit["level"], unit["area"], unit["items"]
        )
        self.unit_info.Foreground = GOOD
        self.ok.IsEnabled = True
        name = "{} {} ({})".format(area_name, unit["uid"], unit["level"]).strip()
        if self.name_box.Text in ("", self._auto_name):
            self.name_box.Text = name
        if self.program_box.Text in ("", self._auto_program):
            self.program_box.Text = area_name
        self._auto_name, self._auto_program = name, area_name
        lowered = area_name.lower()
        guess = "Other"
        if "office" in lowered:
            guess = "Office"
        elif "unit" in lowered or "studio" in lowered:
            guess = "Residential"
        self.type_box.SelectedIndex = PROJECT_TYPES.index(guess)

    def _accept(self, sender, args):
        if not self.name_box.Text.strip() or not self.program_box.Text.strip():
            return
        self.result = {
            "unit": self.unit_box.Text.strip(),
            "name": self.name_box.Text.strip(),
            "program": self.program_box.Text.strip(),
            "project_type": str(self.type_box.SelectedItem),
        }
        self.window.DialogResult = True

    def show(self):
        self.window.ShowDialog()
        return self.result


# =============================================================================
# Result and question windows
# =============================================================================


def show_layout_result(harvest, owner_handle=None):
    """Show what a harvest stored: the sketch, the items and the file."""
    entry = harvest["entry"]
    summary = harvest["layout"]
    window = base_window(980, 560, owner_handle)
    root = DockPanel()
    root.Margin = Thickness(22, 14, 22, 16)
    window.Content = root

    bottom = StackPanel()
    bottom.Orientation = Orientation.Horizontal
    bottom.HorizontalAlignment = HorizontalAlignment.Right
    DockPanel.SetDock(bottom, Dock.Bottom)
    bottom.Children.Add(button("Close", 110, default=True, cancel=True))
    root.Children.Add(bottom)

    head = StackPanel()
    DockPanel.SetDock(head, Dock.Top)
    head.Children.Add(text("Harvested: {}".format(entry["name"]), 22))
    line = text(
        "{} items saved. {} wall-hosted or face-based families skipped.".format(
            len(entry["items"]), len(harvest["skipped"])
        ),
        14,
        GOOD,
    )
    line.Margin = Thickness(0, 4, 0, 10)
    head.Children.Add(line)
    head.Children.Add(
        chips(
            [
                ("Program", entry["program"]),
                ("Project type", entry["project_type"]),
                ("Size", "{:,.0f} SF, {:.1f} x {:.1f} ft".format(entry["area"], entry["width"], entry["depth"])),
                ("Items", str(len(entry["items"]))),
            ]
        )
    )
    root.Children.Add(head)

    body = Grid()
    body.Margin = Thickness(0, 14, 0, 10)
    for width in (430.0, None):
        column = ColumnDefinition()
        column.Width = GridLength(1, GridUnitType.Star) if width is None else GridLength(width)
        body.ColumnDefinitions.Add(column)
    root.Children.Add(body)

    # The layout in its own frame: outline and items
    scale = min(410.0 / max(entry["width"], 1.0), 300.0 / max(entry["depth"], 1.0))
    canvas = Canvas()
    canvas.Width, canvas.Height = 430.0, 320.0
    canvas.Background = WHITE
    left = 215.0 - entry["width"] * scale / 2.0
    top = 160.0 - entry["depth"] * scale / 2.0

    def px(point):
        return (left + point[0] * scale, top + (entry["depth"] - point[1]) * scale)

    _polygon(canvas, [px(p) for p in entry["boundary"]], UNIT_FILL, UNIT_LINE, 1.6)
    for item in entry["items"]:
        x, y = px((item["x"], item["y"]))
        dot = Ellipse()
        dot.Width = dot.Height = max(6.0, 0.7 * scale)
        dot.Fill = ITEM_LINE
        Canvas.SetLeft(dot, x - dot.Width / 2.0)
        Canvas.SetTop(dot, y - dot.Height / 2.0)
        canvas.Children.Add(dot)
        tick = Line()
        tick.X1, tick.Y1 = x, y
        tick.X2 = x + math.cos(item["rotation"]) * max(scale * 1.3, 8.0)
        tick.Y2 = y - math.sin(item["rotation"]) * max(scale * 1.3, 8.0)
        tick.Stroke = ITEM_LINE
        tick.StrokeThickness = 1.5
        canvas.Children.Add(tick)
    frame = Border()
    frame.BorderBrush = brush(200, 200, 200)
    frame.BorderThickness = Thickness(1)
    frame.VerticalAlignment = VerticalAlignment.Top
    frame.Child = canvas
    body.Children.Add(frame)

    right = StackPanel()
    right.Margin = Thickness(20, 0, 0, 0)
    Grid.SetColumn(right, 1)
    right.Children.Add(text("Items (family : type)", 15, TEXT, bold=True))
    families = sorted(summary["families"].items(), key=lambda pair: (-pair[1], pair[0]))
    for name, count in families[:11]:
        block = text("{} x  {}".format(count, name), 13.5)
        block.Margin = Thickness(0, 3, 0, 0)
        right.Children.Add(block)
    if len(families) > 11:
        right.Children.Add(text("... and {} more types".format(len(families) - 11), 13, MUTED))
    stored = text(
        "Each item has its position, rotation, flips, height and instance parameters,"
        " in the frame of the unit.",
        13,
        MUTED,
        wrap=True,
    )
    stored.Margin = Thickness(0, 12, 0, 0)
    right.Children.Add(stored)
    path = text("File: {}".format(harvest["file"]), 12.5, MUTED, wrap=True)
    path.Margin = Thickness(0, 8, 0, 0)
    right.Children.Add(path)
    body.Children.Add(right)
    window.ShowDialog()


def show_message(title, lines, owner_handle=None):
    """Show a result: a title and lines of (text, state or None)."""
    window = base_window(620, 150 + 30 * len(lines), owner_handle)
    root = StackPanel()
    root.Margin = Thickness(22, 14, 22, 16)
    window.Content = root
    root.Children.Add(text(title, 21))
    for content, state in lines:
        block = text(content, 14, STATE.get(state, TEXT), wrap=True)
        block.Margin = Thickness(0, 8, 0, 0)
        root.Children.Add(block)
    buttons = StackPanel()
    buttons.Orientation = Orientation.Horizontal
    buttons.HorizontalAlignment = HorizontalAlignment.Right
    buttons.Margin = Thickness(0, 18, 0, 0)
    buttons.Children.Add(button("OK", 110, default=True, cancel=True))
    root.Children.Add(buttons)
    window.ShowDialog()
