# SPDX-License-Identifier: GPL-2.0-or-later
"""Per-armature finger picker bindings and pose selection (no pose transforms)."""

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator, PropertyGroup

SIDES = (('L', '左手', ''), ('R', '右手', ''))
FINGERS = (('thumb', '拇指', ''), ('index', '食指', ''), ('middle', '中指', ''),
           ('ring', '无名指', ''), ('pinky', '小指', ''))


def slot_key(side, finger, segment):
    return f'{side}:{finger}:{segment}'


def bound_bones(obj, side, finger='ALL', segment=0):
    """Resolve in finger/joint order; a bone bound more than once appears only once."""
    result = []
    for identifier, _label, _description in FINGERS:
        if finger not in {'ALL', identifier}:
            continue
        for joint in range(1, 4):
            if segment and joint != segment:
                continue
            entry = obj.island_finger_bindings.get(slot_key(side, identifier, joint))
            bone = obj.data.bones.get(entry.bone_name) if entry else None
            if bone and bone.name not in result:
                result.append(bone.name)
    return result


def redraw(context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in {'VIEW_3D', 'PROPERTIES', 'DOPESHEET_EDITOR', 'GRAPH_EDITOR'}:
                area.tag_redraw()


class IslandFingerBinding(PropertyGroup):
    name: StringProperty(name='Picker Slot', override={'LIBRARY_OVERRIDABLE'})
    bone_name: StringProperty(name='骨骼', override={'LIBRARY_OVERRIDABLE'})


class _PosePicker:
    @classmethod
    def poll(cls, context):
        return (context.object is not None and context.object.type == 'ARMATURE'
                and context.mode == 'POSE')


class POSE_OT_island_finger_bind(_PosePicker, Operator):
    """Bind any control bone to a finger button; leave the bone empty to clear the binding"""
    bl_idname = 'pose.island_finger_bind'
    bl_label = '绑定手指骨骼'
    bl_options = {'REGISTER', 'UNDO'}

    side: EnumProperty(items=SIDES, options={'HIDDEN'})
    finger: EnumProperty(items=FINGERS, options={'HIDDEN'})
    segment: IntProperty(default=1, min=1, max=3, options={'HIDDEN'})
    bone_name: StringProperty(name='骨骼')

    @classmethod
    def poll(cls, context):
        return super().poll(context) and context.object.is_editable

    def invoke(self, context, event):
        obj = context.object
        entry = obj.island_finger_bindings.get(slot_key(self.side, self.finger, self.segment))
        active = context.active_pose_bone
        self.bone_name = (active.name if active and active.id_data == obj
                          else entry.bone_name if entry else '')
        return context.window_manager.invoke_props_dialog(self, width=340)

    def draw(self, context):
        names = dict((key, label) for key, label, _ in FINGERS)
        self.layout.label(text=f'{"左手" if self.side == "L" else "右手"} · '
                               f'{names[self.finger]} · 第 {self.segment} 节')
        self.layout.prop_search(self, 'bone_name', context.object.data, 'bones', text='骨骼')
        self.layout.label(text='留空并确认可清除绑定', icon='INFO')

    def execute(self, context):
        obj = context.object
        if self.bone_name and self.bone_name not in obj.data.bones:
            self.report({'WARNING'}, '骨骼不存在，请重新选择')
            return {'CANCELLED'}
        key = slot_key(self.side, self.finger, self.segment)
        index = obj.island_finger_bindings.find(key)
        if not self.bone_name:
            if index >= 0:
                obj.island_finger_bindings.remove(index)
        else:
            entry = obj.island_finger_bindings[index] if index >= 0 else obj.island_finger_bindings.add()
            entry.name = key
            entry.bone_name = self.bone_name
        redraw(context)
        return {'FINISHED'}


class POSE_OT_island_finger_select(_PosePicker, Operator):
    """Select bound finger controls; Shift adds and Ctrl removes from the selection"""
    bl_idname = 'pose.island_finger_select'
    bl_label = '选择手指骨骼'
    bl_options = {'REGISTER', 'UNDO'}

    side: EnumProperty(items=SIDES, options={'HIDDEN'})
    finger: EnumProperty(items=(('ALL', '整手', ''),) + FINGERS, options={'HIDDEN'})
    segment: IntProperty(default=0, min=0, max=3, options={'HIDDEN'})
    mode: EnumProperty(items=(('REPLACE', '替换选择', ''), ('ADD', '加选', ''),
                              ('SUBTRACT', '减选', '')), default='REPLACE')

    @classmethod
    def description(cls, context, properties):
        obj = context.object
        if not obj or obj.type != 'ARMATURE':
            return '选择绑定的手指控制骨骼'
        names = bound_bones(obj, properties.side, properties.finger, properties.segment)
        return ('、'.join(names) if names else '尚未绑定有效骨骼') + '\nShift 加选，Ctrl 减选'

    def invoke(self, context, event):
        self.mode = ('SUBTRACT' if event.ctrl else 'ADD'
                     if event.shift or context.object.island_finger_picker_extend else 'REPLACE')
        return self.execute(context)

    def execute(self, context):
        obj = context.object
        # Context visibility includes bone collections, solo, and temporary pose hiding.
        visible = {pb.name for pb in (context.visible_pose_bones or ()) if pb.id_data == obj}
        names = bound_bones(obj, self.side, self.finger, self.segment)
        # Pose selection belongs to PoseBone in 5.3; selectability and the active bone
        # still belong to the underlying armature Bone.
        targets = [obj.pose.bones[name] for name in names
                   if name in visible and (self.mode == 'SUBTRACT' or not obj.data.bones[name].hide_select)]
        if not targets:
            self.report({'WARNING'}, '没有可选择的绑定骨骼：请检查绑定、隐藏或禁止选择状态')
            return {'CANCELLED'}
        # Resolve first: empty/invalid buttons must never discard the user's selection.
        if self.mode == 'REPLACE':
            for name in visible:
                obj.pose.bones[name].select = False
        for bone in targets:
            bone.select = self.mode != 'SUBTRACT'
        if self.mode != 'SUBTRACT':
            obj.data.bones.active = targets[0].bone
        elif obj.data.bones.active and not obj.pose.bones[obj.data.bones.active.name].select:
            obj.data.bones.active = next((bone.bone for bone in obj.pose.bones
                                          if bone.name in visible and bone.select), None)
        redraw(context)
        return {'FINISHED'}


classes = (IslandFingerBinding, POSE_OT_island_finger_bind, POSE_OT_island_finger_select)


def register():
    bpy.types.Object.island_finger_bindings = CollectionProperty(
        type=IslandFingerBinding, override={'LIBRARY_OVERRIDABLE', 'USE_INSERTION'})
    bpy.types.Object.island_finger_picker_bind_mode = BoolProperty(
        name='绑定模式', description='点击关节按钮，选择要绑定的控制骨骼',
        override={'LIBRARY_OVERRIDABLE'})
    bpy.types.Object.island_finger_picker_extend = BoolProperty(
        name='加选', description='点击时保留已有选择；也可按住 Shift 加选、Ctrl 减选',
        override={'LIBRARY_OVERRIDABLE'})


def unregister():
    for name in ('island_finger_picker_extend', 'island_finger_picker_bind_mode', 'island_finger_bindings'):
        delattr(bpy.types.Object, name)
