# SPDX-License-Identifier: GPL-2.0-or-later
"""Built-in controls for inserting and removing empty animation frames."""

from bpy.types import Panel


def draw_inbetween_controls(layout, context):
    settings = context.scene.island_inbetween

    layout.use_property_split = True
    layout.use_property_decorate = False
    column = layout.column(align=True)
    column.prop(settings, "frames", text="帧数")
    column.prop(settings, "mode", text="位置")
    column.prop(settings, "scope", text="范围")

    layout.separator()
    row = layout.row(align=True)
    row.scale_y = 1.2
    for operation, label, icon in (
        ('INSERT', "插入空白帧", 'ADD'),
        ('REMOVE', "移除空白帧", 'REMOVE'),
    ):
        operator = row.operator("anim.island_inbetween", text=label, icon=icon)
        operator.operation = operation
        operator.frames = settings.frames
        operator.mode = settings.mode
        operator.scope = settings.scope

    layout.separator()
    column = layout.column(align=True)
    if settings.mode == 'CURRENT':
        column.label(text="调整当前帧所在的关键帧间隔")
        column.label(text="右侧关键帧随之移动")
    else:
        column.label(text="逐个调整所选关键帧之间的间隔")
        column.label(text="后续关键帧随之移动")
    if settings.scope == 'CONTEXT' and context.area.type == 'VIEW_3D':
        column.label(text="作用于所选物体或姿态骨骼")
    column.separator()
    column.label(text="移除不会覆盖或删除关键帧", icon='INFO')
    column.label(text="仅支持活动动作，不支持 NLA")


class _IslandInbetweenPanel:
    bl_label = "插入 / 移除空白帧"
    bl_region_type = 'UI'
    bl_category = "动画工具"

    def draw(self, context):
        draw_inbetween_controls(self.layout, context)


class _DopeSheetInbetween:
    bl_space_type = 'DOPESHEET_EDITOR'

    @classmethod
    def poll(cls, context):
        return context.space_data.mode in {'DOPESHEET', 'ACTION', 'SHAPEKEY', 'TIMELINE'}


class _GraphInbetween:
    bl_space_type = 'GRAPH_EDITOR'

    @classmethod
    def poll(cls, context):
        return context.space_data.mode == 'FCURVES'


class DOPESHEET_PT_island_inbetween(_DopeSheetInbetween, _IslandInbetweenPanel, Panel):
    pass


class GRAPH_PT_island_inbetween(_GraphInbetween, _IslandInbetweenPanel, Panel):
    pass


class VIEW3D_PT_island_inbetween(_IslandInbetweenPanel, Panel):
    bl_space_type = 'VIEW_3D'


class DOPESHEET_PT_island_inbetween_popover(_DopeSheetInbetween, _IslandInbetweenPanel, Panel):
    bl_region_type = 'HEADER'
    bl_ui_units_x = 18


class GRAPH_PT_island_inbetween_popover(_GraphInbetween, _IslandInbetweenPanel, Panel):
    bl_region_type = 'HEADER'
    bl_ui_units_x = 18


classes = (
    DOPESHEET_PT_island_inbetween,
    GRAPH_PT_island_inbetween,
    VIEW3D_PT_island_inbetween,
    DOPESHEET_PT_island_inbetween_popover,
    GRAPH_PT_island_inbetween_popover,
)
