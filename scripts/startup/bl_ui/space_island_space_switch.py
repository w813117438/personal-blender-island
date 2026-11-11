# SPDX-License-Identifier: GPL-2.0-or-later
"""Built-in UI for seamless Child Of constraint space switching."""

from bpy.types import Panel


def _find_constraint_owner(context, con):
    ob = getattr(context, 'object', None) or getattr(context, 'active_object', None)
    if ob is None or con is None:
        return None, None, 'AUTO', ()
    ptr = con.as_pointer()
    for item in ob.constraints:
        if item.as_pointer() == ptr:
            childofs = tuple(
                c for c in ob.constraints if c.type == 'CHILD_OF' and not c.mute and c.target is not None
            )
            return ob, None, 'OBJECT', childofs
    if ob.pose is not None:
        for pchan in ob.pose.bones:
            for item in pchan.constraints:
                if item.as_pointer() == ptr:
                    childofs = tuple(
                        c for c in pchan.constraints if c.type == 'CHILD_OF' and not c.mute and c.target is not None
                    )
                    return ob, pchan, 'BONE', childofs
    return ob, None, 'AUTO', ()


def _assign_switch_op_props(op, con_name: str, owner_type: str, action: str, settings, bone_name: str = ''):
    op.action = action
    op.constraint_name = con_name
    op.bone_name = bone_name
    op.owner_type = owner_type
    if settings is not None:
        op.mode = settings.mode
        op.target_influence = settings.target_influence
        op.use_guard_key = settings.use_guard_key
        op.frame_start = settings.frame_start
        op.frame_end = settings.frame_end


def _target_summary(con) -> str:
    if con.target is None:
        return '未选择目标'
    if con.target.type == 'ARMATURE' and con.subtarget:
        return f'{con.target.name} / {con.subtarget}'
    return con.target.name


def _draw_common_settings(layout, scene):
    settings = getattr(scene, 'island_space_switch', None)
    if settings is None:
        return None
    col = layout.column(align=True)
    col.use_property_split = True
    col.use_property_decorate = False
    col.prop(settings, 'mode', text='切换范围')
    if settings.mode == 'RANGE':
        row = col.row(align=True)
        row.prop(settings, 'frame_start', text='起止帧')
        row.prop(settings, 'frame_end', text='')
        quick = layout.row(align=True)
        quick.use_property_split = False
        quick.operator(
            'anim.island_space_switch_set_range', text='取所选关键帧', icon='KEYTYPE_KEYFRAME_VEC'
        ).source = 'SELECTED_KEYS'
        quick.operator(
            'anim.island_space_switch_set_range', text='取场景范围', icon='PREVIEW_RANGE'
        ).source = 'SCENE'
    col2 = layout.column(align=True)
    col2.use_property_split = True
    col2.use_property_decorate = False
    col2.prop(settings, 'use_guard_key', text='保护边界帧')
    col2.prop(settings, 'auto_influence_compensate', text='拖动“影响”自动保持空间')
    return settings


def draw_childof_constraint_ui(layout, context, con):
    """Draw seamless space-switch controls inside a Child Of constraint panel."""
    scene = getattr(context, 'scene', None)
    settings = getattr(scene, 'island_space_switch', None) if scene is not None else None
    _ob, _pchan, owner_type, active_childofs = _find_constraint_owner(context, con)
    bone_name = _pchan.name if _pchan is not None else ''
    enabled = not con.mute and con.target is not None

    layout.separator()
    box = layout.box()
    header = box.row(align=True)
    header.use_property_split = False
    header.label(text='空间切换（保持世界变换）', icon='ORIENTATION_GLOBAL')

    btn_row = box.row(align=True)
    btn_row.use_property_split = False
    btn_row.enabled = enabled
    op_on = btn_row.operator(
        'constraint.island_childof_space_switch',
        text='跟随 (1)',
        icon='LINKED',
        depress=(con.influence > 0.999),
    )
    _assign_switch_op_props(op_on, con.name, owner_type, 'ENABLE', settings, bone_name=bone_name)

    op_off = btn_row.operator(
        'constraint.island_childof_space_switch',
        text='松开 (0)',
        icon='UNLINKED',
        depress=(con.influence < 0.001),
    )
    _assign_switch_op_props(op_off, con.name, owner_type, 'DISABLE', settings, bone_name=bone_name)

    if len(active_childofs) >= 2:
        op_ex = btn_row.operator(
            'constraint.island_childof_space_switch',
            text='接管跟随',
            icon='FILE_REFRESH',
        )
        _assign_switch_op_props(op_ex, con.name, owner_type, 'EXCLUSIVE', settings, bone_name=bone_name)

    if settings is not None:
        custom_row = box.row(align=True)
        custom_row.use_property_split = False
        custom_row.enabled = enabled
        custom_row.prop(settings, 'target_influence', text='目标权重', slider=True)
        op_custom = custom_row.operator(
            'constraint.island_childof_space_switch',
            text='应用',
            icon='CHECKMARK',
        )
        _assign_switch_op_props(op_custom, con.name, owner_type, 'CUSTOM', settings, bone_name=bone_name)
        _draw_common_settings(box, scene)


def _draw_owner_childof_cards(layout, childofs, owner_type: str, settings, bone_name: str = ''):
    active_valid = [c for c in childofs if not c.mute and c.target is not None]
    for con in childofs:
        box = layout.box()
        top = box.row(align=True)
        top.label(text=f'{con.name}  →  {_target_summary(con)}', icon='CON_CHILDOF')
        if con.mute:
            top.label(text='已隐藏', icon='HIDE_ON')

        inf_row = box.row(align=True)
        inf_row.prop(con, 'influence', text='当前权重', slider=True)

        enabled = not con.mute and con.target is not None
        btn_row = box.row(align=True)
        btn_row.enabled = enabled
        btn_row.scale_y = 1.15
        op_on = btn_row.operator(
            'constraint.island_childof_space_switch',
            text='跟随 (1)',
            icon='LINKED',
            depress=(con.influence > 0.999),
        )
        _assign_switch_op_props(op_on, con.name, owner_type, 'ENABLE', settings, bone_name=bone_name)

        op_off = btn_row.operator(
            'constraint.island_childof_space_switch',
            text='松开 (0)',
            icon='UNLINKED',
            depress=(con.influence < 0.001),
        )
        _assign_switch_op_props(op_off, con.name, owner_type, 'DISABLE', settings, bone_name=bone_name)

        if len(active_valid) >= 2:
            op_ex = btn_row.operator(
                'constraint.island_childof_space_switch',
                text='接管跟随',
                icon='FILE_REFRESH',
            )
            _assign_switch_op_props(op_ex, con.name, owner_type, 'EXCLUSIVE', settings, bone_name=bone_name)

        if settings is not None:
            op_custom = btn_row.operator(
                'constraint.island_childof_space_switch',
                text='设目标值',
            )
            _assign_switch_op_props(op_custom, con.name, owner_type, 'CUSTOM', settings, bone_name=bone_name)

    if len(active_valid) >= 2:
        rel_row = layout.row(align=True)
        op_rel = rel_row.operator(
            'constraint.island_childof_space_switch',
            text='全部子级约束松开归 0（保持世界变换）',
            icon='UNLINKED',
        )
        _assign_switch_op_props(
            op_rel, active_valid[0].name, owner_type, 'RELEASE_ALL', settings, bone_name=bone_name
        )


def draw_space_switch_panel(layout, context):
    scene = context.scene
    settings = getattr(scene, 'island_space_switch', None)
    ob = getattr(context, 'object', None) or getattr(context, 'active_object', None)

    if ob is None:
        layout.label(text='请先选择物体或姿态骨骼', icon='INFO')
        return

    is_pose = context.mode == 'POSE'
    pchan = getattr(context, 'active_pose_bone', None) if is_pose else None

    if settings is not None:
        top_col = layout.column(align=True)
        top_col.use_property_split = True
        top_col.use_property_decorate = False
        top_col.prop(settings, 'target_influence', text='目标权重', slider=True)
        _draw_common_settings(layout, scene)
        layout.separator()

    drawn_any = False
    if is_pose and ob.pose is not None:
        candidates = []
        seen_names = set()
        if pchan is not None and pchan.id_data == ob:
            candidates.append(pchan)
            seen_names.add(pchan.name)
        for pb in ob.pose.bones:
            if pb.select and pb.name not in seen_names:
                candidates.append(pb)
                seen_names.add(pb.name)

        bones_with_childof = [
            pb for pb in candidates if any(c.type == 'CHILD_OF' for c in pb.constraints)
        ]
        if bones_with_childof:
            for idx, pb in enumerate(bones_with_childof):
                if idx > 0:
                    layout.separator()
                bone_childofs = [c for c in pb.constraints if c.type == 'CHILD_OF']
                layout.label(text=f'姿态骨骼：{ob.name} / {pb.name}', icon='BONE_DATA')
                _draw_owner_childof_cards(
                    layout, bone_childofs, 'BONE', settings, bone_name=pb.name
                )
                drawn_any = True
        elif candidates:
            layout.label(text=f'活动骨骼：{ob.name} / {candidates[0].name}', icon='BONE_DATA')
            layout.label(text='该姿态骨骼没有子级约束')
        else:
            layout.label(text='请先选择一个包含子级约束的姿态骨骼')

        obj_childofs = [c for c in ob.constraints if c.type == 'CHILD_OF']
        if obj_childofs:
            layout.separator()
            layout.label(text=f'骨架物体：{ob.name}', icon='OBJECT_DATA')
            _draw_owner_childof_cards(layout, obj_childofs, 'OBJECT', settings)
            drawn_any = True
    else:
        obj_childofs = [c for c in ob.constraints if c.type == 'CHILD_OF']
        layout.label(text=f'活动物体：{ob.name}', icon='OBJECT_DATA')
        if obj_childofs:
            _draw_owner_childof_cards(layout, obj_childofs, 'OBJECT', settings)
            drawn_any = True
        else:
            layout.label(text='该物体没有子级约束（Child Of）')

    if drawn_any:
        layout.separator()
        hint = layout.column(align=True)
        hint.label(text='不修改逆矩阵，支持反复多次切换', icon='INFO')
        hint.label(text='切换后自动写入变换与权重关键帧')


class _IslandSpaceSwitchPanel:
    bl_label = '子级约束空间切换'
    bl_region_type = 'UI'
    bl_category = '动画工具'

    def draw(self, context):
        draw_space_switch_panel(self.layout, context)


class VIEW3D_PT_island_space_switch(_IslandSpaceSwitchPanel, Panel):
    bl_space_type = 'VIEW_3D'


class DOPESHEET_PT_island_space_switch(_IslandSpaceSwitchPanel, Panel):
    bl_space_type = 'DOPESHEET_EDITOR'

    @classmethod
    def poll(cls, context):
        return context.space_data.mode in {'DOPESHEET', 'ACTION', 'TIMELINE'}


class GRAPH_PT_island_space_switch(_IslandSpaceSwitchPanel, Panel):
    bl_space_type = 'GRAPH_EDITOR'

    @classmethod
    def poll(cls, context):
        return context.space_data.mode == 'FCURVES'


classes = (
    VIEW3D_PT_island_space_switch,
    DOPESHEET_PT_island_space_switch,
    GRAPH_PT_island_space_switch,
)
