# SPDX-License-Identifier: GPL-2.0-or-later
"""Finger selection UI. Binding data belongs to each armature object."""

from bpy.types import Panel
from bl_operators.island_finger_picker import FINGERS, bound_bones, slot_key


class VIEW3D_PT_island_finger_picker(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = '手指选择'
    bl_label = '手指 Picker'

    @classmethod
    def poll(cls, context):
        # Keep the entry discoverable even while a mesh or no object is active.
        return True

    def draw(self, context):
        layout = self.layout
        obj = context.object
        if obj is None or obj.type != 'ARMATURE':
            layout.label(text='请选择骨架物体', icon='ARMATURE_DATA')
            layout.label(text='选中骨架后可绑定和选择手指')
            return
        layout.label(text=obj.name, icon='ARMATURE_DATA')
        if context.mode != 'POSE':
            if context.mode == 'OBJECT':
                layout.operator('object.mode_set', text='进入姿态模式', icon='POSE_HLT').mode = 'POSE'
            else:
                layout.label(text='请切换至姿态模式', icon='INFO')
            return
        row = layout.row(align=True)
        binding = row.row(align=True)
        binding.enabled = obj.is_editable
        binding.prop(obj, 'island_finger_picker_bind_mode', toggle=True, icon='LINKED')
        row.prop(obj, 'island_finger_picker_extend', toggle=True, icon='ADD')
        if obj.island_finger_picker_bind_mode:
            layout.label(text='点关节按钮绑定，留空可清除', icon='INFO')
        else:
            layout.label(text='Shift 加选 · Ctrl 减选')
        layout.label(text='1 根节 · 2 中节 · 3 末节')


class _FingerHand:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = '手指选择'
    bl_parent_id = 'VIEW3D_PT_island_finger_picker'

    @classmethod
    def poll(cls, context):
        return context.mode == 'POSE' and context.object is not None and context.object.type == 'ARMATURE'

    def draw(self, context):
        layout = self.layout
        obj = context.object
        bind_mode = obj.island_finger_picker_bind_mode
        names = bound_bones(obj, self.side)
        row = layout.row()
        row.enabled = bool(names) and not bind_mode
        op = row.operator('pose.island_finger_select', text='选择整手', icon='BONE_DATA')
        op.side = self.side
        op.finger = 'ALL'
        # Labels double as whole-finger selection buttons; numbered cells select each joint.
        for finger, label, _ in FINGERS:
            row = layout.row(align=True)
            row.scale_y = 1.3
            finger_names = bound_bones(obj, self.side, finger)
            group = row.row(align=True)
            group.enabled = bool(finger_names) and not bind_mode
            op = group.operator('pose.island_finger_select', text=label,
                                depress=bool(finger_names) and all(obj.pose.bones[n].select for n in finger_names))
            op.side, op.finger = self.side, finger
            for joint in range(1, 4):
                entry = obj.island_finger_bindings.get(slot_key(self.side, finger, joint))
                bone = obj.data.bones.get(entry.bone_name) if entry else None
                cell = row.row(align=True)
                cell.enabled = obj.is_editable if bind_mode else bone is not None
                if bind_mode:
                    op = cell.operator('pose.island_finger_bind', text=str(joint),
                                       icon='LINKED' if bone else 'ERROR' if entry else 'ADD')
                else:
                    op = cell.operator('pose.island_finger_select', text=str(joint),
                                       depress=bool(bone and obj.pose.bones[bone.name].select),
                                       icon='ERROR' if entry and not bone else 'NONE')
                op.side, op.finger, op.segment = self.side, finger, joint


class VIEW3D_PT_island_finger_left(_FingerHand, Panel):
    bl_label = '左手'
    side = 'L'


class VIEW3D_PT_island_finger_right(_FingerHand, Panel):
    bl_label = '右手'
    side = 'R'


classes = (VIEW3D_PT_island_finger_picker, VIEW3D_PT_island_finger_left, VIEW3D_PT_island_finger_right)
