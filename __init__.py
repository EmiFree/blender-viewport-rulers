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

    # Draw background quads
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')

    # Top ruler background
    verts_top = [
        (0, height), (width, height),
        (width, height - ruler_thickness), (0, height - ruler_thickness)
    ]
    indices_top = [(0, 1, 2), (0, 2, 3)]
    batch_top = batch_for_shader(shader, 'TRIS', {"pos": verts_top}, indices=indices_top)
    shader.bind()
    shader.uniform_float("color", bg_color)
    batch_top.draw(shader)

    # Left ruler background
    verts_left = [
        (left_offset, height - ruler_thickness), (left_offset + ruler_thickness, height - ruler_thickness),
        (left_offset + ruler_thickness, 0), (left_offset, 0)
    ]
    indices_left = [(0, 1, 2), (0, 2, 3)]
    batch_left = batch_for_shader(shader, 'TRIS', {"pos": verts_left}, indices=indices_left)
    shader.uniform_float("color", bg_color)
    batch_left.draw(shader)

    # Draw tick marks and labels
    # Horizontal ruler (top)
    tick_lines = []

    # Determine visible range in scene units along horizontal
    # Screen x=0 and x=width correspond to some scene coordinate
    # The display-unit coordinate at screen x is: (x - ox) / px_per_unit_h

    h_start = -ox / px_per_unit_h
    h_end = (width - ox) / px_per_unit_h

    first_tick_h = int(h_start / tick_step_h) * tick_step_h
    if first_tick_h < h_start:
        first_tick_h += tick_step_h

    t = first_tick_h
    while t <= h_end:
        t_snap = snap_value(t, tick_step_h)
        screen_x = ox + t_snap * px_per_unit_h
        if (left_offset + ruler_thickness) < screen_x < width:
            # Full tick
            tick_lines.append((screen_x, height))
            tick_lines.append((screen_x, height - ruler_thickness))
            # Label
            label = format_label(t_snap, unit_suffix)
            blf.size(0, 11)
            tw, th = blf.dimensions(0, label)
            blf.position(0, screen_x - tw / 2, height - ruler_thickness + 4, 0)
            blf.color(0, *text_color, 1.0)
            blf.draw(0, label)

        # Half ticks
        half_x = ox + (t + tick_step_h / 2) * px_per_unit_h
        if (left_offset + ruler_thickness) < half_x < width:
            tick_lines.append((half_x, height))
            tick_lines.append((half_x, height - ruler_thickness * 0.5))

        t += tick_step_h

    # Vertical ruler (left)
    v_start = -oy / px_per_unit_v
    v_end = (height - ruler_thickness - oy) / px_per_unit_v

    first_tick_v = int(v_start / tick_step_v) * tick_step_v
    if first_tick_v < v_start:
        first_tick_v += tick_step_v

    t = first_tick_v
    while t <= v_end:
        t_snap = snap_value(t, tick_step_v)
        screen_y = oy + t_snap * px_per_unit_v
        if 0 < screen_y < height - ruler_thickness:
            tick_lines.append((left_offset, screen_y))
            tick_lines.append((left_offset + ruler_thickness, screen_y))
            # Label (rotated is hard with blf, so draw horizontally)
            label = format_label(t_snap, unit_suffix)
            blf.size(0, 11)
            tw, th = blf.dimensions(0, label)
            blf.position(0, left_offset + 3, screen_y + 3, 0)
            blf.color(0, *text_color, 1.0)
            blf.draw(0, label)

        half_y = oy + (t + tick_step_v / 2) * px_per_unit_v
        if 0 < half_y < height - ruler_thickness:
            tick_lines.append((left_offset, half_y))
            tick_lines.append((left_offset + ruler_thickness * 0.5, half_y))

        t += tick_step_v

    # Draw unit label in the corner
    blf.size(0, 10)
    blf.position(0, left_offset + 4, height - ruler_thickness + 4, 0)
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
        max=48,
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
        max=200,
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
