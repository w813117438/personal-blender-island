# SPDX-License-Identifier: GPL-2.0-or-later
"""Insert/remove empty time between key columns, rippling subsequent animation."""

from bisect import bisect_right
from collections import defaultdict
from math import floor, isfinite
from struct import pack, unpack

import bpy
from bpy.props import EnumProperty, IntProperty, PointerProperty
from bpy.types import Operator, PropertyGroup


MODES = (
    ('CURRENT', '当前帧', '调整当前帧所在的关键帧间隔，移动当前帧之后的关键帧'),
    ('SELECTED', '所选关键帧之间', '在相邻的所选关键帧列之间逐段调整间隔，后续关键帧累计顺延'),
)
SCOPES = (
    ('CONTEXT', '当前选择 / 编辑器', '动画编辑器中使用可编辑曲线；3D 视口中使用所选物体或姿态骨骼'),
    ('SCENE', '整个场景', '使用当前场景及其引用数据的活动动作，忽略物体选择和编辑器过滤'),
)
MAX_FRAME = 1048574.0
# Match BKE_fcurve_deduplicate_keys(), which is called by FCurve.update().
KEY_MERGE_DISTANCE = 0.01


class InbetweenError(ValueError):
    pass


def _related_ids(roots, user_map, allowed_objects):
    """Follow ID dependencies, never unrelated/orphan Actions or other Action slots."""
    dependencies = defaultdict(set)
    for dependency, users in user_map.items():
        if isinstance(dependency, (bpy.types.Action, bpy.types.Library)):
            continue
        for user in users:
            dependencies[user].add(dependency)
    pending = list(roots)
    seen = set()
    while pending:
        owner = pending.pop()
        if owner is None or owner in seen:
            continue
        # Constraint/node references must not bring another object's animation or
        # owned data into the selection. Likewise, stop at other scenes.
        if isinstance(owner, bpy.types.Object) and owner not in allowed_objects:
            continue
        if isinstance(owner, bpy.types.Scene) and owner not in roots:
            continue
        seen.add(owner)
        pending.extend(dependencies[owner] - seen)
        # Embedded node trees are not necessarily keys in BlendData.user_map().
        tree = getattr(owner, 'node_tree', None)
        if tree is not None:
            pending.append(tree)
    return seen


def _assigned_curves(owner):
    adt = getattr(owner, 'animation_data', None)
    if not adt or not adt.action or not adt.action_slot:
        return
    for layer in adt.action.layers:
        for strip in layer.strips:
            if strip.type != 'KEYFRAME':
                continue
            bag = strip.channelbag(adt.action_slot)
            if bag:
                yield from bag.fcurves


def collect_curves(context, scope):
    user_map = bpy.data.user_map()
    space = context.space_data
    editor = space and space.type in {'DOPESHEET_EDITOR', 'GRAPH_EDITOR'}
    if scope == 'CONTEXT' and editor:
        candidates = list(context.editable_fcurves or ())
    else:
        pose = scope == 'CONTEXT' and context.mode == 'POSE'
        if pose:
            bones_by_object = defaultdict(list)
            for bone in context.selected_pose_bones or ():
                path = bone.path_from_id()
                bones_by_object[bone.id_data].extend((path + '.', path + '['))
            candidates = [curve for owner, prefixes in bones_by_object.items()
                          if owner.is_editable
                          for curve in _assigned_curves(owner)
                          if curve.data_path.startswith(tuple(prefixes))]
        else:
            if scope == 'SCENE':
                roots = [context.scene]
                # Master collections and their objects may be embedded ID dependencies.
                roots.extend(context.scene.objects)
            else:
                roots = list(context.selected_objects or ())
            allowed_objects = set(context.scene.objects) if scope == 'SCENE' else set(roots)
            owners = _related_ids(roots, user_map, allowed_objects)
            candidates = [curve for owner in owners
                          if owner.is_editable
                          for curve in _assigned_curves(owner)]

    curves = {}
    for curve in candidates:
        action = curve.id_data
        if (not isinstance(action, bpy.types.Action) or not action.is_editable
                or action.override_library or curve.lock or (curve.group and curve.group.lock)
                or not curve.keyframe_points):
            continue
        curves[curve.as_pointer()] = curve

    actions = {curve.id_data for curve in curves.values()}
    # An Action shared with a tweaked NLA user can have a different time mapping.
    # Do not silently move its local key times as though they were scene times.
    owners = set(user_map)
    owners.update(owner for users in user_map.values() for owner in users)
    for owner in owners:
        adt = getattr(owner, 'animation_data', None)
        if adt and adt.action in actions and adt.use_tweak_mode:
            raise InbetweenError('请先退出 NLA 微调模式，再调整中间帧')
    return list(curves.values())


def _plan(curves, mode, current_frame, frames, operation):
    times = sorted({float(key.co.x) for curve in curves for key in curve.keyframe_points})
    if any(not isfinite(time) for time in times):
        raise InbetweenError('关键帧时间无效，未修改动画')
    if mode == 'SELECTED':
        selected = sorted({float(key.co.x) for curve in curves for key in curve.keyframe_points
                           if key.select_control_point})
        if len(selected) < 2:
            raise InbetweenError('请至少选择两个不同时间的关键帧，或改用“当前帧”')
        anchors = selected[:-1]
    else:
        anchors = [current_frame]

    events = []
    limited = False
    for anchor in anchors:
        right = bisect_right(times, anchor)
        if right == 0 or right == len(times):
            continue
        amount = frames
        if operation == 'REMOVE':
            # Remove whole empty frames only; preserve subframes and >=1-frame gaps.
            available = max(0, floor(times[right] - times[right - 1] - 1.0 + 1e-6))
            amount = min(amount, available)
            limited |= amount < frames
            amount = -amount
        if amount:
            events.append((anchor, amount))
    if not events:
        if operation == 'REMOVE':
            raise InbetweenError('没有可移除的空白帧：关键帧之间至少保留一帧距离')
        raise InbetweenError('当前位置两侧需要有关键帧，或请选择一段关键帧')

    event_times = [anchor for anchor, _ in events]
    cumulative = [0]
    for _, amount in events:
        cumulative.append(cumulative[-1] + amount)
    plans = []
    for curve in curves:
        changes = []
        destinations = []
        for key in curve.keyframe_points:
            time = float(key.co.x)
            # A key ON an anchor stays put with respect to that event.
            index = bisect_right(event_times, time)
            if index and event_times[index - 1] == time:
                index -= 1
            offset = cumulative[index]
            destination = time + offset
            if not -MAX_FRAME <= destination <= MAX_FRAME:
                raise InbetweenError('操作会超出 Blender 支持的帧范围，未修改动画')
            # Check the actual float32 storage precision before changing any curve.
            destinations.append(unpack('f', pack('f', destination))[0])
            if offset:
                changes.append((key, destination, key.handle_left.x + offset, key.handle_right.x + offset))
        if changes:
            destinations.sort()
            if any(b - a <= KEY_MERGE_DISTANCE for a, b in zip(destinations, destinations[1:])):
                raise InbetweenError('曲线存在重合或过近的关键帧，操作可能合并关键帧，未修改动画')
            plans.append((curve, changes))
    return plans, limited


class IslandInbetweenSettings(PropertyGroup):
    frames: IntProperty(name='帧数', description='每个间隔要插入或移除的空白帧数', default=1, min=1, max=100000)
    mode: EnumProperty(name='方式', items=MODES, default='CURRENT')
    scope: EnumProperty(name='范围', items=SCOPES, default='CONTEXT')


class ANIM_OT_island_inbetween(Operator):
    """插入或移除关键帧之间的空白时间，后续关键帧随之移动，支持撤销"""
    bl_idname = 'anim.island_inbetween'
    bl_label = '插入 / 移除中间帧'
    bl_options = {'REGISTER', 'UNDO'}

    operation: EnumProperty(name='操作', items=(('INSERT', '插入空白帧', ''), ('REMOVE', '移除空白帧', '')))
    frames: IntProperty(name='帧数', default=1, min=1, max=100000)
    mode: EnumProperty(name='方式', items=MODES, default='CURRENT')
    scope: EnumProperty(name='范围', items=SCOPES, default='CONTEXT')

    @classmethod
    def poll(cls, context):
        if not context.scene or not context.scene.is_editable:
            return False
        space = context.space_data
        if space and space.type == 'GRAPH_EDITOR':
            return space.mode == 'FCURVES'
        if space and space.type == 'DOPESHEET_EDITOR':
            return space.mode in {'DOPESHEET', 'ACTION', 'SHAPEKEY', 'TIMELINE'}
        return True

    def invoke(self, context, event):
        settings = context.scene.island_inbetween
        for name in ('frames', 'mode', 'scope'):
            if not self.properties.is_property_set(name):
                setattr(self, name, getattr(settings, name))
        return self.execute(context)

    def execute(self, context):
        try:
            curves = collect_curves(context, self.scope)
            if not curves:
                raise InbetweenError('没有可编辑的关键帧，请检查选择、曲线锁定和活动动作')
            plans, limited = _plan(curves, self.mode, context.scene.frame_current + context.scene.frame_subframe,
                                   self.frames, self.operation)
        except InbetweenError as error:
            self.report({'WARNING'}, str(error))
            return {'CANCELLED'}

        actions = set()
        for curve, changes in plans:
            for key, time, left, right in changes:
                key.co.x = time
                key.handle_left.x = left
                key.handle_right.x = right
            curve.update()
            actions.add(curve.id_data)
        for action in actions:
            action.update_tag()
        scene = context.scene
        scene.frame_set(scene.frame_current, subframe=scene.frame_subframe)
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
        if limited:
            self.report({'INFO'}, '已按可用空白帧收紧间隔，保留所有关键帧')
        return {'FINISHED'}


classes = (IslandInbetweenSettings, ANIM_OT_island_inbetween)


def register():
    bpy.types.Scene.island_inbetween = PointerProperty(type=IslandInbetweenSettings)


def unregister():
    del bpy.types.Scene.island_inbetween
