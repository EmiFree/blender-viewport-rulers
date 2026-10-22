import bpy
import gpu
import blf
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from bpy.types import SpaceView3D


def get_unit_info(context):
    """Get the current scene unit scale and suffix."""
    unit = context.scene.unit_settings
    scale = unit.scale_length

    if unit.system == 'METRIC':
        length_unit = unit.length_unit
        if length_unit == 'ADAPTIVE':
            return scale, "m"
        elif length_unit == 'KILOMETERS':
            return scale * 0.001, "km"
        elif length_unit == 'METERS':
            return scale, "m"
        elif length_unit == 'CENTIMETERS':
            return scale * 100, "cm"
        elif length_unit == 'MILLIMETERS':
            return scale * 1000, "mm"
        elif length_unit == 'MICROMETERS':
            return scale * 1e6, "\u00b5m"
        else:
            return scale, "m"
    elif unit.system == 'IMPERIAL':
        length_unit = unit.length_unit
        if length_unit == 'ADAPTIVE':
            return scale * 3.28084, "ft"
        elif length_unit == 'MILES':
            return scale * 0.000621371, "mi"
        elif length_unit == 'FEET':
            return scale * 3.28084, "ft"
        elif length_unit == 'INCHES':
            return scale * 39.3701, "in"
        elif length_unit == 'THOU':
            return scale * 39370.1, "thou"
        else:
            return scale * 3.28084, "ft"
    else:
        return scale, "BU"


def choose_tick_spacing(pixels_per_unit):
    """Choose a tick spacing (in scene units) that keeps ticks readable."""
    nice_steps = [0.001, 0.002, 0.005,
                  0.01, 0.02, 0.05,
                  0.1, 0.2, 0.5,
                  1, 2, 5,
                  10, 20, 50,
                  100, 200, 500,
                  1000, 2000, 5000,
                  10000, 20000, 50000]
    min_px = 60
    for step in nice_steps:
        if step * pixels_per_unit >= min_px:
            return step
    return nice_steps[-1]


def snap_value(value, step):
    """Round value to the nearest tick step to kill floating point noise."""
    return round(value / step) * step


def format_label(value, unit_suffix):
    """Format a ruler label value."""
    if abs(value) < 1e-9:
        return f"0 {unit_suffix}"
    if abs(value) >= 100:
        return f"{value:.0f}"
    if abs(value) >= 1:
        return f"{value:.4g}"
    return f"{value:.4g}"


_draw_handler = None


def draw_rulers():
    context = bpy.context
    if not hasattr(context, 'scene') or context.scene is None:
        return
    if not hasattr(context.scene, 'viewport_rulers'):
        return

    prefs = context.scene.viewport_rulers
    if not prefs.enabled:
        return

    area = context.area
    if area is None or area.type != 'VIEW_3D':
        return

    region = context.region
    rv3d = context.region_data

    if region is None or rv3d is None:
        return

    width = region.width
    height = region.height

    ruler_thickness = prefs.thickness
    left_offset = prefs.left_offset if prefs.use_left_offset else 0
    top_offset = prefs.top_offset if prefs.use_top_offset else 0
    bg_color = (*prefs.bg_color, prefs.bg_alpha)
    tick_color = (*prefs.tick_color, 1.0)
    text_color = prefs.text_color

    unit_scale, unit_suffix = get_unit_info(context)

    # Figure out how many pixels correspond to one scene unit at the center of the view.
    from bpy_extras.view3d_utils import location_3d_to_region_2d

    center_3d = Vector((0, 0, 0))
    # Use view center for reference
    view_loc = rv3d.view_location.copy()
    # Get two points 1 BU apart along the view's x-axis
    view_mat = rv3d.view_matrix.inverted()
    right_dir = view_mat.col[0].to_3d().normalized()
    up_dir = view_mat.col[1].to_3d().normalized()

    p0 = view_loc
    p1_h = view_loc + right_dir
    p1_v = view_loc + up_dir

    s0 = location_3d_to_region_2d(region, rv3d, p0)
    s1_h = location_3d_to_region_2d(region, rv3d, p1_h)
    s1_v = location_3d_to_region_2d(region, rv3d, p1_v)

    if s0 is None or s1_h is None or s1_v is None:
        return

    px_per_bu_h = (s1_h - s0).length
    px_per_bu_v = (s1_v - s0).length

    if px_per_bu_h < 1 or px_per_bu_v < 1:
        return

    # unit_scale is display units per BU, so each display unit spans fewer pixels
    px_per_unit_h = px_per_bu_h / unit_scale
    px_per_unit_v = px_per_bu_v / unit_scale

    tick_step_h = choose_tick_spacing(px_per_unit_h)
    tick_step_v = choose_tick_spacing(px_per_unit_v)

    # Where is the scene origin in screen space?
    origin_screen = location_3d_to_region_2d(region, rv3d, Vector((0, 0, 0)))
    if origin_screen is None:
        return

    ox, oy = origin_screen

    show_top = prefs.show_top
    show_left = prefs.show_left
    show_right = prefs.show_right
    show_bottom = prefs.show_bottom

    if not (show_top or show_left or show_right or show_bottom):
        return

    # Edges of the rulers, after offsets
    top_y = height - top_offset
    left_x = left_offset

    # Inner bounds of the uncovered viewport, used to clip ticks
    x_min = left_x + ruler_thickness if show_left else 0
    x_max = width - ruler_thickness if show_right else width
    y_min = ruler_thickness if show_bottom else 0
    y_max = top_y - ruler_thickness if show_top else height

    # Draw background quads
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')

    quads = []
    if show_top:
        quads.append((0, top_y - ruler_thickness, width, top_y))
    if show_bottom:
        quads.append((0, 0, width, ruler_thickness))
    if show_left:
        quads.append((left_x, y_min, left_x + ruler_thickness, y_max))
    if show_right:
        quads.append((width - ruler_thickness, y_min, width, y_max))

    verts_bg = []
    indices_bg = []
    for x0, y0, x1, y1 in quads:
        i = len(verts_bg)
        verts_bg += [(x0, y1), (x1, y1), (x1, y0), (x0, y0)]
        indices_bg += [(i, i + 1, i + 2), (i, i + 2, i + 3)]
    batch_bg = batch_for_shader(shader, 'TRIS', {"pos": verts_bg}, indices=indices_bg)
    shader.bind()
    shader.uniform_float("color", bg_color)
    batch_bg.draw(shader)

    # Draw tick marks and labels
    tick_lines = []
    blf.size(0, 11)
    blf.color(0, *text_color, 1.0)

    # Horizontal rulers (top and bottom)
    if show_top or show_bottom:
        # The display-unit coordinate at screen x is: (x - ox) / px_per_unit_h
        h_start = (x_min - ox) / px_per_unit_h
        h_end = (x_max - ox) / px_per_unit_h

        first_tick_h = int(h_start / tick_step_h) * tick_step_h
        if first_tick_h < h_start:
            first_tick_h += tick_step_h
        # Start one step early so the half tick before the first full tick is drawn
        t = first_tick_h - tick_step_h

        while t <= h_end:
            t_snap = snap_value(t, tick_step_h)
            screen_x = ox + t_snap * px_per_unit_h
            if x_min < screen_x < x_max:
                # Full tick
                label = format_label(t_snap, unit_suffix)
                tw, th = blf.dimensions(0, label)
                if show_top:
                    tick_lines.append((screen_x, top_y))
                    tick_lines.append((screen_x, top_y - ruler_thickness))
                    blf.position(0, screen_x - tw / 2, top_y - ruler_thickness + 4, 0)
                    blf.draw(0, label)
                if show_bottom:
                    tick_lines.append((screen_x, 0))
                    tick_lines.append((screen_x, ruler_thickness))
                    blf.position(0, screen_x - tw / 2, ruler_thickness - th - 4, 0)
                    blf.draw(0, label)

            # Half ticks
            half_x = ox + (t + tick_step_h / 2) * px_per_unit_h
            if x_min < half_x < x_max:
                if show_top:
                    tick_lines.append((half_x, top_y))
                    tick_lines.append((half_x, top_y - ruler_thickness * 0.5))
                if show_bottom:
                    tick_lines.append((half_x, 0))
                    tick_lines.append((half_x, ruler_thickness * 0.5))

            t += tick_step_h

    # Vertical rulers (left and right)
    if show_left or show_right:
        v_start = (y_min - oy) / px_per_unit_v
        v_end = (y_max - oy) / px_per_unit_v

        first_tick_v = int(v_start / tick_step_v) * tick_step_v
        if first_tick_v < v_start:
            first_tick_v += tick_step_v
        t = first_tick_v - tick_step_v

        while t <= v_end:
            t_snap = snap_value(t, tick_step_v)
            screen_y = oy + t_snap * px_per_unit_v
            if y_min < screen_y < y_max:
                # Label (rotated is hard with blf, so draw horizontally)
                label = format_label(t_snap, unit_suffix)
                tw, th = blf.dimensions(0, label)
                if show_left:
                    tick_lines.append((left_x, screen_y))
                    tick_lines.append((left_x + ruler_thickness, screen_y))
                    blf.position(0, left_x + 3, screen_y + 3, 0)
                    blf.draw(0, label)
                if show_right:
                    tick_lines.append((width, screen_y))
                    tick_lines.append((width - ruler_thickness, screen_y))
                    blf.position(0, width - tw - 3, screen_y + 3, 0)
                    blf.draw(0, label)

            half_y = oy + (t + tick_step_v / 2) * px_per_unit_v
            if y_min < half_y < y_max:
                if show_left:
                    tick_lines.append((left_x, half_y))
                    tick_lines.append((left_x + ruler_thickness * 0.5, half_y))
                if show_right:
                    tick_lines.append((width, half_y))
                    tick_lines.append((width - ruler_thickness * 0.5, half_y))

            t += tick_step_v

    # Draw unit label in the first corner where a horizontal and vertical ruler meet
    corner = None
    if show_top and show_left:
        corner = (left_x + 4, top_y - ruler_thickness + 4)
    elif show_top and show_right:
        corner = (width - ruler_thickness + 4, top_y - ruler_thickness + 4)
    elif show_bottom and show_left:
        corner = (left_x + 4, 4)
    elif show_bottom and show_right:
        corner = (width - ruler_thickness + 4, 4)
    if corner is not None:
        blf.size(0, 10)
        blf.position(0, *corner, 0)
        blf.color(0, *text_color, 0.7)
        blf.draw(0, unit_suffix)

    # Draw all tick lines
    if tick_lines:
        line_shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        batch_lines = batch_for_shader(line_shader, 'LINES', {"pos": tick_lines})
        line_shader.bind()
        line_shader.uniform_float("color", tick_color)
        batch_lines.draw(line_shader)

    gpu.state.blend_set('NONE')


class ViewportRulersProperties(bpy.types.PropertyGroup):
    enabled: bpy.props.BoolProperty(
        name="Rulers",
        description="Show rulers at selected edges of the viewport",
        default=True,
        update=lambda self, ctx: tag_redraw(ctx),
    )
    thickness: bpy.props.IntProperty(
        name="Ruler Thickness",
        description="Pixel thickness of the ruler bars",
        default=24,
        min=16,
        max=96,
    )
    show_top: bpy.props.BoolProperty(
        name="Top",
        description="Show a ruler along the top edge of the viewport",
        default=True,
        update=lambda self, ctx: tag_redraw(ctx),
    )
    show_left: bpy.props.BoolProperty(
        name="Left",
        description="Show a ruler along the left edge of the viewport",
        default=True,
        update=lambda self, ctx: tag_redraw(ctx),
    )
    show_right: bpy.props.BoolProperty(
        name="Right",
        description="Show a ruler along the right edge of the viewport",
        default=False,
        update=lambda self, ctx: tag_redraw(ctx),
    )
    show_bottom: bpy.props.BoolProperty(
        name="Bottom",
        description="Show a ruler along the bottom edge of the viewport",
        default=False,
        update=lambda self, ctx: tag_redraw(ctx),
    )
    use_left_offset: bpy.props.BoolProperty(
        name="Left Ruler Offset",
        description="Shift the left ruler right so the toolbar doesn't cover it",
        default=True,
        update=lambda self, ctx: tag_redraw(ctx),
    )
    left_offset: bpy.props.IntProperty(
        name="Left Offset",
        description="Shift the left ruler right so the toolbar doesn't cover it",
        default=46,
        min=0,
        max=300,
    )
    use_top_offset: bpy.props.BoolProperty(
        name="Top Ruler Offset",
        description="Shift the top ruler down so the header doesn't cover it",
        default=False,
        update=lambda self, ctx: tag_redraw(ctx),
    )
    top_offset: bpy.props.IntProperty(
        name="Top Offset",
        description="Shift the top ruler down so the header doesn't cover it",
        default=26,
        min=0,
        max=300,
    )
    bg_color: bpy.props.FloatVectorProperty(
        name="Background",
        subtype='COLOR',
        size=3,
        default=(0.15, 0.15, 0.15),
        min=0, max=1,
    )
    bg_alpha: bpy.props.FloatProperty(
        name="Background Opacity",
        default=0.85,
        min=0, max=1,
    )
    tick_color: bpy.props.FloatVectorProperty(
        name="Tick Color",
        subtype='COLOR',
        size=3,
        default=(0.7, 0.7, 0.7),
        min=0, max=1,
    )
    text_color: bpy.props.FloatVectorProperty(
        name="Text Color",
        subtype='COLOR',
        size=3,
        default=(0.8, 0.8, 0.8),
        min=0, max=1,
    )


def tag_redraw(context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def draw_overlay_toggle(self, context):
    layout = self.layout
    prefs = context.scene.viewport_rulers
    layout.prop(prefs, "enabled", text="Rulers", icon='FIXED_SIZE')


class VIEW3D_PT_viewport_rulers(bpy.types.Panel):
    bl_label = "Viewport Rulers"
    bl_idname = "VIEW3D_PT_viewport_rulers"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "View"
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        prefs = context.scene.viewport_rulers
        self.layout.prop(prefs, "enabled", text="")

    def draw(self, context):
        layout = self.layout
        prefs = context.scene.viewport_rulers
        layout.active = prefs.enabled

        layout.prop(prefs, "thickness")
        row = layout.row(align=True)
        row.prop(prefs, "show_top", toggle=True)
        row.prop(prefs, "show_left", toggle=True)
        row.prop(prefs, "show_right", toggle=True)
        row.prop(prefs, "show_bottom", toggle=True)
        layout.prop(prefs, "use_top_offset")
        row = layout.row()
        row.active = prefs.use_top_offset
        row.prop(prefs, "top_offset")
        layout.prop(prefs, "use_left_offset")
        row = layout.row()
        row.active = prefs.use_left_offset
        row.prop(prefs, "left_offset")
        layout.prop(prefs, "bg_color")
        layout.prop(prefs, "bg_alpha")
        layout.prop(prefs, "tick_color")
        layout.prop(prefs, "text_color")


classes = (
    ViewportRulersProperties,
    VIEW3D_PT_viewport_rulers,
)


def register():
    global _draw_handler
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.viewport_rulers = bpy.props.PointerProperty(type=ViewportRulersProperties)

    _draw_handler = SpaceView3D.draw_handler_add(draw_rulers, (), 'WINDOW', 'POST_PIXEL')

    # Add toggle to overlays popover
    bpy.types.VIEW3D_PT_overlay.append(draw_overlay_toggle)


def unregister():
    global _draw_handler
    bpy.types.VIEW3D_PT_overlay.remove(draw_overlay_toggle)

    if _draw_handler is not None:
        SpaceView3D.draw_handler_remove(_draw_handler, 'WINDOW')
        _draw_handler = None

    del bpy.types.Scene.viewport_rulers
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
