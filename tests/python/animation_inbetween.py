# SPDX-License-Identifier: GPL-2.0-or-later
"""Regression tests for Island Insert/Remove Inbetween.

Run with Blender 5.3 --background --factory-startup --python-exit-code 1
--python tests/python/animation_inbetween.py. The source module is loaded when
testing an executable that does not yet include this built-in tool.
"""

import importlib.util
from contextlib import contextmanager
from pathlib import Path
import sys
import tempfile
import unittest

import bpy


def register_source_module():
    if hasattr(bpy.types, 'ANIM_OT_island_inbetween'):
        return
    source = Path(__file__).resolve().parents[2] / 'scripts/startup/bl_operators/island_inbetween.py'
    name = 'bl_operators.island_inbetween'
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    for cls in module.classes:
        bpy.utils.register_class(cls)
    module.register()


def channelbag(owner):
    animation = owner.animation_data
    for layer in animation.action.layers:
        for strip in layer.strips:
            bag = strip.channelbag(animation.action_slot)
            if bag:
                return bag
    raise AssertionError('The fixture has no assigned channelbag')


def times(curve):
    return [round(key.co.x, 5) for key in curve.keyframe_points]


def select_keys(curve, selected):
    for key in curve.keyframe_points:
        key.select_control_point = key.co.x in selected
        key.select_left_handle = False
        key.select_right_handle = False


class InbetweenTest(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.scene = bpy.context.scene
        self.scene.frame_set(5)

    def object(self, name='Animated', *, scene=None, data=None):
        obj = bpy.data.objects.new(name, data)
        (scene or self.scene).collection.objects.link(obj)
        if scene is None or scene == self.scene:
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
        return obj

    def animate(self, owner, frames=(1, 10, 20), *, path=None, index=-1):
        if path is None:
            owner['inbetween_test'] = 0.0
            path = '["inbetween_test"]'
        for frame in frames:
            owner.keyframe_insert(data_path=path, index=index, frame=frame)
        curve = channelbag(owner).fcurves.find(path, index=max(index, 0))
        self.assertIsNotNone(curve)
        select_keys(curve, ())
        return curve

    def run_operation(self, *, operation='INSERT', frames=1, mode='CURRENT', scope='CONTEXT'):
        return bpy.ops.anim.island_inbetween(operation=operation, frames=frames, mode=mode, scope=scope)

    @contextmanager
    def editor(self, editor_type, mode=None):
        # This changes only the screen belonging to this background test process.
        window = bpy.context.window
        area = next(area for area in window.screen.areas if area.type == 'VIEW_3D')
        previous_type = area.type
        try:
            area.type = editor_type
            space = area.spaces.active
            space.mode = mode or ('FCURVES' if editor_type == 'GRAPH_EDITOR' else 'DOPESHEET')
            region = next(region for region in area.regions if region.type == 'WINDOW')
            with bpy.context.temp_override(window=window, area=area, region=region):
                space.dopesheet.show_only_selected = True
                bpy.context.view_layer.update()
                bpy.ops.anim.channels_expand(all=True)
                yield space
        finally:
            area.type = previous_type

    def focus_channel(self, owner, selected):
        """Exercise the left-channel-list focus, not merely key/curve selection."""
        bag = channelbag(owner)
        for group in bag.groups:
            group.select = False
        for curve in bag.fcurves:
            curve.select = curve == selected
        # The custom Graph Editor stores display focus when channel-list operators
        # run. Two inversions leave the intended X-only channel selection intact.
        bpy.ops.anim.channels_select_all(action='INVERT')
        bpy.ops.anim.channels_select_all(action='INVERT')

    def assert_cancelled(self, **kwargs):
        # WARNING cancellations return normally; ERROR cancellations raise.
        try:
            result = self.run_operation(**kwargs)
        except RuntimeError:
            return
        self.assertEqual(result, {'CANCELLED'})

    def test_current_insert_ripples_all_later_keys(self):
        curve = self.animate(self.object())
        self.assertEqual(self.run_operation(frames=2), {'FINISHED'})
        self.assertEqual(times(curve), [1, 12, 22])
        self.assertEqual(self.scene.frame_current, 5)

    def test_key_at_playhead_stays_fixed(self):
        curve = self.animate(self.object())
        self.scene.frame_set(10)
        self.run_operation(frames=3)
        self.assertEqual(times(curve), [1, 10, 23])

    def test_outside_key_range_does_nothing(self):
        curve = self.animate(self.object())
        for frame in (-5, 20, 50):
            self.scene.frame_set(frame)
            self.assert_cancelled()
            self.assertEqual(times(curve), [1, 10, 20])

    def test_current_uses_subframe(self):
        curve = self.animate(self.object(), (1, 1.25, 4))
        self.scene.frame_set(1, subframe=0.5)
        self.run_operation(frames=2)
        self.assertEqual(times(curve), [1, 1.25, 6])

    def test_render_time_remapping_does_not_move_edit_anchor(self):
        curve = self.animate(self.object(), (1, 7, 10))
        self.scene.render.frame_map_old = 100
        self.scene.render.frame_map_new = 200
        self.scene.frame_set(5, subframe=0.5)
        self.assertNotEqual(self.scene.frame_current_final, 5.5)
        self.assertEqual(self.run_operation(frames=2), {'FINISHED'})
        self.assertEqual(times(curve), [1, 9, 12])
        self.assertEqual(self.scene.frame_current, 5)
        self.assertAlmostEqual(self.scene.frame_subframe, 0.5)

    def test_remove_clamps_to_available_empty_frames(self):
        curve = self.animate(self.object(), (1, 4, 12))
        self.scene.frame_set(2)
        self.assertEqual(self.run_operation(operation='REMOVE', frames=20), {'FINISHED'})
        self.assertEqual(times(curve), [1, 2, 10])
        self.assertEqual(len(curve.keyframe_points), 3)

    def test_remove_cannot_collapse_adjacent_or_subframe_keys(self):
        curve = self.animate(self.object(), (1, 2, 10))
        self.scene.frame_set(1)
        self.assert_cancelled(operation='REMOVE', frames=20)
        self.assertEqual(times(curve), [1, 2, 10])
        curve.keyframe_points[1].co.x = 1.5
        curve.update()
        self.assert_cancelled(operation='REMOVE', frames=1)
        self.assertEqual(times(curve), [1, 1.5, 10])

    def test_selected_adds_per_interval_and_ripples_tail(self):
        curve = self.animate(self.object(), (1, 5, 10, 20))
        select_keys(curve, (1, 5, 10))
        self.run_operation(frames=2, mode='SELECTED')
        self.assertEqual(times(curve), [1, 7, 14, 24])

    def test_selected_sparse_range_moves_intervening_unselected_keys(self):
        curve = self.animate(self.object(), (1, 5, 10, 20))
        select_keys(curve, (1, 10))
        self.run_operation(frames=2, mode='SELECTED')
        self.assertEqual(times(curve), [1, 7, 12, 22])

    def test_shared_selected_times_count_once_across_curves(self):
        obj = self.object()
        first = self.animate(obj, (1, 5, 10, 20))
        second = self.animate(obj, (1, 5, 10, 20), path='location', index=0)
        for curve in (first, second):
            select_keys(curve, (1, 5, 10))
        self.run_operation(frames=2, mode='SELECTED')
        self.assertEqual(times(first), [1, 7, 14, 24])
        self.assertEqual(times(second), [1, 7, 14, 24])

    def test_selected_columns_apply_to_other_target_curves(self):
        first = self.animate(self.object('First'), (1, 5))
        second = self.animate(self.object('Second'), (3, 8))
        select_keys(first, (1, 5))
        self.run_operation(mode='SELECTED')
        self.assertEqual(times(first), [1, 6])
        self.assertEqual(times(second), [4, 9])

    def test_selected_remove_clamps_each_interval(self):
        curve = self.animate(self.object(), (1, 4, 10, 20))
        select_keys(curve, (1, 4, 10))
        self.run_operation(operation='REMOVE', frames=3, mode='SELECTED')
        self.assertEqual(times(curve), [1, 2, 5, 15])
        self.assertEqual(len(curve.keyframe_points), 4)

    def test_selected_remove_preserves_unselected_obstacle(self):
        curve = self.animate(self.object(), (1, 2, 10, 20))
        select_keys(curve, (1, 10))
        self.assert_cancelled(operation='REMOVE', frames=9, mode='SELECTED')
        self.assertEqual(times(curve), [1, 2, 10, 20])

    def test_selected_requires_two_distinct_control_points(self):
        curve = self.animate(self.object())
        for selection in ((), (10,)):
            select_keys(curve, selection)
            self.assert_cancelled(mode='SELECTED')
            self.assertEqual(times(curve), [1, 10, 20])
        select_keys(curve, ())
        for key in curve.keyframe_points:
            key.select_left_handle = True
        self.assert_cancelled(mode='SELECTED')
        self.assertEqual(times(curve), [1, 10, 20])

    def test_values_free_handles_and_selection_survive(self):
        curve = self.animate(self.object())
        for index, key in enumerate(curve.keyframe_points):
            key.handle_left_type = 'FREE'
            key.handle_right_type = 'FREE'
            key.co.y = index * 3.5
            key.handle_left = (key.co.x - 0.4, key.co.y - 0.8)
            key.handle_right = (key.co.x + 0.3, key.co.y + 1.2)
            key.interpolation = 'BEZIER'
            key.select_control_point = index == 1
        before = [(tuple(k.co), tuple(k.handle_left), tuple(k.handle_right), k.select_control_point)
                  for k in curve.keyframe_points]
        self.run_operation(frames=3)
        for key, (co, left, right, selected) in zip(curve.keyframe_points, before):
            offset = 3 if co[0] > 5 else 0
            self.assertAlmostEqual(key.co.x, co[0] + offset)
            self.assertAlmostEqual(key.co.y, co[1])
            self.assertAlmostEqual(key.handle_left.x, left[0] + offset, places=5)
            self.assertAlmostEqual(key.handle_right.x, right[0] + offset, places=5)
            self.assertAlmostEqual(key.handle_left.y, left[1], places=5)
            self.assertAlmostEqual(key.handle_right.y, right[1], places=5)
            self.assertEqual(key.select_control_point, selected)
            self.assertEqual((key.handle_left_type, key.handle_right_type), ('FREE', 'FREE'))

    def test_context_excludes_unselected_objects(self):
        selected = self.object('Selected')
        first = self.animate(selected)
        other = self.object('Other')
        second = self.animate(other)
        other.select_set(False)
        bpy.context.view_layer.objects.active = selected
        self.run_operation()
        self.assertEqual(times(first), [1, 11, 21])
        self.assertEqual(times(second), [1, 10, 20])

    def test_constraint_target_does_not_include_unselected_owned_data(self):
        selected = self.object('Selected')
        selected_curve = self.animate(selected)
        target = self.object('Constraint target', data=bpy.data.cameras.new('Target camera'))
        target_curve = self.animate(target)
        data_curve = self.animate(target.data, path='lens')
        constraint = selected.constraints.new('COPY_LOCATION')
        constraint.target = target
        target.select_set(False)
        bpy.context.view_layer.objects.active = selected
        self.run_operation()
        self.assertEqual(times(selected_curve), [1, 11, 21])
        self.assertEqual(times(target_curve), [1, 10, 20])
        self.assertEqual(times(data_curve), [1, 10, 20])

    def test_scene_includes_unselected_objects(self):
        first = self.animate(self.object('First'))
        other = self.object('Unselected')
        second = self.animate(other)
        other.select_set(False)
        self.run_operation(scope='SCENE')
        self.assertEqual(times(first), [1, 11, 21])
        self.assertEqual(times(second), [1, 11, 21])

    def test_scene_excludes_other_scene_and_unassigned_actions(self):
        current = self.animate(self.object('Current'))
        other_scene = bpy.data.scenes.new('Other Scene')
        external = self.animate(self.object('Outside', scene=other_scene))
        orphan_owner = bpy.data.objects.new('Orphan', None)
        orphan = self.animate(orphan_owner)
        self.run_operation(scope='SCENE')
        self.assertEqual(times(current), [1, 11, 21])
        self.assertEqual(times(external), [1, 10, 20])
        self.assertEqual(times(orphan), [1, 10, 20])

    def test_scene_includes_data_shapes_material_world_and_node_trees(self):
        mesh = bpy.data.meshes.new('Mesh')
        mesh.from_pydata([(0, 0, 0)], [], [])
        obj = self.object(data=mesh)
        obj.shape_key_add(name='Basis')
        obj.shape_key_add(name='Expression')
        material = bpy.data.materials.new('Material')
        material.use_nodes = True
        mesh.materials.append(material)
        world = bpy.data.worlds.new('World')
        world.use_nodes = True
        self.scene.world = world
        owners = (self.scene, obj, mesh, mesh.shape_keys, material, material.node_tree, world, world.node_tree)
        curves = [self.animate(owner) for owner in owners]
        self.run_operation(scope='SCENE')
        for owner, curve in zip(owners, curves):
            with self.subTest(owner=owner.name):
                self.assertEqual(times(curve), [1, 11, 21])

    def test_only_assigned_slot_of_shared_action_is_edited(self):
        first = self.object('First')
        first_curve = self.animate(first)
        second = self.object('Second')
        action = first.animation_data.action
        second.animation_data_create()
        second.animation_data.action = action
        second.animation_data.action_slot = action.slots.new('OBJECT', second.name)
        second_curve = self.animate(second)
        second.select_set(False)
        bpy.context.view_layer.objects.active = first
        self.run_operation()
        self.assertEqual(times(first_curve), [1, 11, 21])
        self.assertEqual(times(second_curve), [1, 10, 20])

    def test_shared_action_slot_is_shifted_once(self):
        first = self.object('First')
        curve = self.animate(first)
        second = self.object('Second')
        second.animation_data_create()
        second.animation_data.action = first.animation_data.action
        second.animation_data.action_slot = first.animation_data.action_slot
        self.run_operation(scope='SCENE', frames=2)
        self.assertEqual(times(curve), [1, 12, 22])

    def test_scene_does_not_modify_unused_slot(self):
        obj = self.object()
        curve = self.animate(obj)
        action = obj.animation_data.action
        slot = action.slots.new('OBJECT', 'Unused')
        bag = action.layers[0].strips[0].channelbags.new(slot)
        unused = bag.fcurves.new('location', index=0)
        unused.keyframe_points.insert(1, 0)
        unused.keyframe_points.insert(10, 5)
        self.run_operation(scope='SCENE')
        self.assertEqual(times(curve), [1, 11, 21])
        self.assertEqual(times(unused), [1, 10])

    def test_locked_curves_and_locked_groups_are_unchanged(self):
        obj = self.object()
        editable = self.animate(obj)
        locked = self.animate(obj, path='location', index=0)
        grouped = self.animate(obj, path='location', index=1)
        locked.lock = True
        group = channelbag(obj).groups.new('Locked group')
        grouped.group = group
        group.lock = True
        self.run_operation(scope='SCENE')
        self.assertEqual(times(editable), [1, 11, 21])
        self.assertEqual(times(locked), [1, 10, 20])
        self.assertEqual(times(grouped), [1, 10, 20])

    def test_explicit_graph_editor_scope_respects_hidden_locked_and_filtered_curves(self):
        obj = self.object('Selected')
        editable = self.animate(obj)
        hidden = self.animate(obj, path='location', index=0)
        locked = self.animate(obj, path='location', index=1)
        hidden.hide = True
        locked.lock = True
        other = self.object('Filtered object')
        filtered = self.animate(other)
        other.select_set(False)
        bpy.context.view_layer.objects.active = obj
        with self.editor('GRAPH_EDITOR'):
            visible = list(bpy.context.editable_fcurves)
            self.assertIn(editable, visible)
            for curve in (hidden, locked, filtered):
                self.assertNotIn(curve, visible)
            self.assertEqual(self.run_operation(scope='EDITOR'), {'FINISHED'})
        self.assertEqual(times(editable), [1, 11, 21])
        for curve in (hidden, locked, filtered):
            self.assertEqual(times(curve), [1, 10, 20])

    def test_explicit_dopesheet_editor_scope_respects_selection_filter_and_locks(self):
        obj = self.object('Selected')
        editable = self.animate(obj)
        locked = self.animate(obj, path='location', index=0)
        locked.lock = True
        other = self.object('Filtered object')
        filtered = self.animate(other)
        other.select_set(False)
        bpy.context.view_layer.objects.active = obj
        with self.editor('DOPESHEET_EDITOR'):
            visible = list(bpy.context.editable_fcurves)
            self.assertIn(editable, visible)
            self.assertNotIn(locked, visible)
            self.assertNotIn(filtered, visible)
            self.assertEqual(self.run_operation(scope='EDITOR'), {'FINISHED'})
        self.assertEqual(times(editable), [1, 11, 21])
        self.assertEqual(times(locked), [1, 10, 20])
        self.assertEqual(times(filtered), [1, 10, 20])

    def test_default_graph_scope_moves_all_selected_object_channels_with_x_focused(self):
        obj = self.object('Selected')
        curves = [self.animate(obj, path=path, index=axis)
                  for path in ('location', 'rotation_euler', 'scale') for axis in range(3)]
        curves.append(self.animate(obj))
        other = self.object('Unselected')
        untouched = self.animate(other)
        other.select_set(False)
        bpy.context.view_layer.objects.active = obj
        with self.editor('GRAPH_EDITOR'):
            self.focus_channel(obj, curves[0])
            self.assertEqual(list(bpy.context.editable_fcurves), [curves[0]])
            # Omit scope intentionally: this is the user-facing default contract.
            self.assertEqual(bpy.ops.anim.island_inbetween(frames=2), {'FINISHED'})
            for curve in curves:
                self.assertEqual(times(curve), [1, 12, 22])
            self.assertEqual(times(untouched), [1, 10, 20])
            self.assertEqual(bpy.ops.anim.island_inbetween(operation='REMOVE', frames=2), {'FINISHED'})
        for curve in curves + [untouched]:
            self.assertEqual(times(curve), [1, 10, 20])

    def test_default_graph_scope_ignores_visibility_and_search_but_respects_locks(self):
        obj = self.object('Selected')
        curves = [self.animate(obj, path='location', index=axis) for axis in range(3)]
        hidden = self.animate(obj, path='rotation_euler', index=0)
        hidden.hide = True
        curves[1].lock = True
        group = channelbag(obj).groups.new('Protected')
        curves[2].group = group
        group.lock = True
        other = self.object('Unselected')
        untouched = self.animate(other)
        other.select_set(False)
        bpy.context.view_layer.objects.active = obj
        with self.editor('GRAPH_EDITOR') as space:
            self.focus_channel(obj, curves[0])
            space.dopesheet.filter_fcurve_name = 'no curve can match this filter'
            self.assertEqual(list(bpy.context.editable_fcurves or ()), [])
            self.assertEqual(bpy.ops.anim.island_inbetween(), {'FINISHED'})
        for curve in (curves[0], hidden):
            self.assertEqual(times(curve), [1, 11, 21])
        for curve in (curves[1], curves[2], untouched):
            self.assertEqual(times(curve), [1, 10, 20])

    def test_explicit_editor_scope_keeps_single_axis_focus(self):
        obj = self.object()
        curves = [self.animate(obj, path='location', index=axis) for axis in range(3)]
        with self.editor('GRAPH_EDITOR'):
            self.focus_channel(obj, curves[0])
            self.assertEqual(list(bpy.context.editable_fcurves), [curves[0]])
            self.assertEqual(self.run_operation(scope='EDITOR'), {'FINISHED'})
        self.assertEqual(times(curves[0]), [1, 11, 21])
        for curve in curves[1:]:
            self.assertEqual(times(curve), [1, 10, 20])

    def test_default_selected_key_mode_ripples_unselected_axes(self):
        obj = self.object()
        curves = [self.animate(obj, (1, 5, 10, 20), path='location', index=axis) for axis in range(3)]
        select_keys(curves[0], (1, 5, 10))
        with self.editor('GRAPH_EDITOR'):
            self.focus_channel(obj, curves[0])
            self.assertEqual(list(bpy.context.editable_fcurves), [curves[0]])
            self.assertEqual(bpy.ops.anim.island_inbetween(mode='SELECTED', frames=2), {'FINISHED'})
            for curve in curves:
                self.assertEqual(times(curve), [1, 7, 14, 24])
            self.assertEqual(bpy.ops.anim.island_inbetween(
                operation='REMOVE', mode='SELECTED', frames=2), {'FINISHED'})
        for curve in curves:
            self.assertEqual(times(curve), [1, 5, 10, 20])
        for curve in curves[1:]:
            self.assertFalse(any(key.select_control_point for key in curve.keyframe_points))

    def assert_pose_scope_ignores_editor_filter(self, editor_type, mode=None):
        rig = self.object(data=bpy.data.armatures.new('Rig'))
        bpy.ops.object.mode_set(mode='EDIT')
        for name in ('Selected', 'Other'):
            bone = rig.data.edit_bones.new(name)
            bone.head = (0, 0, 0)
            bone.tail = (0, 0, 1)
        bpy.ops.object.mode_set(mode='POSE')
        selected = rig.pose.bones['Selected']
        other = rig.pose.bones['Other']
        selected.select = True
        other.select = False
        selected['stretch'] = 1.0
        selected_curves = [self.animate(rig, path=selected.path_from_id('location'), index=axis)
                           for axis in range(3)]
        selected_curves.append(self.animate(rig, path=selected.path_from_id() + '["stretch"]'))
        untouched = [self.animate(rig, path=other.path_from_id('location'), index=axis)
                     for axis in range(3)]
        untouched.append(self.animate(rig, path='location', index=0))
        rig.data.bones.active = selected.bone
        with self.editor(editor_type, mode) as space:
            if editor_type == 'GRAPH_EDITOR':
                self.focus_channel(rig, selected_curves[0])
                self.assertEqual(list(bpy.context.editable_fcurves), [selected_curves[0]])
            # Channel filtering must not alter which pose bones the context owns.
            self.assertEqual({bone.name for bone in bpy.context.selected_pose_bones}, {'Selected'})
            selected_curves[1].hide = True
            if editor_type == 'GRAPH_EDITOR':
                space.dopesheet.filter_fcurve_name = 'exclude every visible curve'
            else:
                space.dopesheet.filter_text = 'exclude every visible channel'
            self.assertEqual(list(bpy.context.editable_fcurves or ()), [])
            self.assertEqual(bpy.ops.anim.island_inbetween(), {'FINISHED'})
        for curve in selected_curves:
            self.assertEqual(times(curve), [1, 11, 21])
        for curve in untouched:
            self.assertEqual(times(curve), [1, 10, 20])

    def test_default_graph_scope_moves_all_channels_of_selected_pose_bones(self):
        self.assert_pose_scope_ignores_editor_filter('GRAPH_EDITOR')

    def test_default_dopesheet_scope_moves_all_channels_of_selected_pose_bones(self):
        self.assert_pose_scope_ignores_editor_filter('DOPESHEET_EDITOR')

    def test_default_timeline_scope_moves_all_channels_of_selected_pose_bones(self):
        self.assert_pose_scope_ignores_editor_filter('DOPESHEET_EDITOR', 'TIMELINE')

    def test_scope_values_preserve_saved_scene_settings(self):
        self.assertEqual(self.scene.island_inbetween.scope, 'CONTEXT')
        expected = {0: 'CONTEXT', 1: 'SCENE', 2: 'EDITOR'}
        for number in expected:
            scene = bpy.data.scenes.new(f'Scope {number}')
            # Version 0.1.3 files persist enum values as integers. Inserting EDITOR
            # between the existing menu items must not reinterpret SCENE=1.
            scene.island_inbetween['scope'] = number
        with tempfile.TemporaryDirectory(prefix='island-inbetween-scope-') as directory:
            path = str(Path(directory) / 'scope-settings.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            for number, identifier in expected.items():
                self.assertEqual(bpy.data.scenes[f'Scope {number}'].island_inbetween.scope, identifier)
            self.scene = bpy.context.scene
            self.assertEqual(self.scene.island_inbetween.scope, 'CONTEXT')

    def test_scene_scope_ignores_graph_visibility_and_object_filter(self):
        obj = self.object('Selected')
        editable = self.animate(obj)
        hidden = self.animate(obj, path='location', index=0)
        locked = self.animate(obj, path='location', index=1)
        hidden.hide = True
        locked.lock = True
        other = self.object('Filtered object')
        filtered = self.animate(other)
        other.select_set(False)
        bpy.context.view_layer.objects.active = obj
        with self.editor('GRAPH_EDITOR'):
            visible = list(bpy.context.editable_fcurves)
            self.assertNotIn(hidden, visible)
            self.assertNotIn(filtered, visible)
            self.assertEqual(self.run_operation(scope='SCENE'), {'FINISHED'})
        for curve in (editable, hidden, filtered):
            self.assertEqual(times(curve), [1, 11, 21])
        self.assertEqual(times(locked), [1, 10, 20])

    def test_frame_limit_cancellation_is_atomic_across_curves(self):
        editable = self.animate(self.object('Normal'))
        limit = self.animate(self.object('At frame limit'), (1, 1048574))
        self.assert_cancelled(frames=1, scope='SCENE')
        self.assertEqual(times(editable), [1, 10, 20])
        self.assertEqual(times(limit), [1, 1048574])

    def test_duplicate_key_cancellation_preserves_all_curves_and_keys(self):
        editable = self.animate(self.object('Normal'))
        duplicate = self.animate(self.object('Existing duplicate'))
        extra = duplicate.keyframe_points.insert(11, 99, options={'FAST'})
        extra.co.x = 10
        # Deliberately do not update: Blender would deduplicate these keys.
        self.assertEqual(times(duplicate), [1, 10, 10, 20])
        before = [(tuple(key.co), tuple(key.handle_left), tuple(key.handle_right))
                  for key in duplicate.keyframe_points]
        self.assert_cancelled(frames=1, scope='SCENE')
        self.assertEqual(times(editable), [1, 10, 20])
        after = [(tuple(key.co), tuple(key.handle_left), tuple(key.handle_right))
                 for key in duplicate.keyframe_points]
        self.assertEqual(after, before)
        self.assertEqual(len(duplicate.keyframe_points), 4)

    def test_nla_tweak_mode_cancels_before_changing_other_actions(self):
        editable = self.animate(self.object('Normal'))
        obj = self.object('Tweaked')
        tweaked = self.animate(obj)
        animation = obj.animation_data
        track = animation.nla_tracks.new()
        strip = track.strips.new('Tweaked strip', 1, animation.action)
        strip.action_slot = animation.action_slot
        track.select = True
        strip.select = True
        animation.nla_tracks.active = track
        animation.use_tweak_mode = True
        self.assertTrue(animation.use_tweak_mode)
        try:
            self.assert_cancelled(scope='SCENE')
            self.assertEqual(times(editable), [1, 10, 20])
            self.assertEqual(times(tweaked), [1, 10, 20])
        finally:
            animation.use_tweak_mode = False

    def test_pose_context_only_changes_selected_bones(self):
        rig = self.object(data=bpy.data.armatures.new('Rig'))
        bpy.ops.object.mode_set(mode='EDIT')
        for name in ('Selected', 'Other'):
            bone = rig.data.edit_bones.new(name)
            bone.head = (0, 0, 0)
            bone.tail = (0, 0, 1)
        bpy.ops.object.mode_set(mode='POSE')
        curves = []
        for name in ('Selected', 'Other'):
            bone = rig.pose.bones[name]
            bone.select = name == 'Selected'
            curves.append(self.animate(rig, path=bone.path_from_id('location'), index=0))
        object_curve = self.animate(rig, path='location', index=0)
        rig.data.bones.active = rig.data.bones['Selected']
        self.run_operation()
        self.assertEqual(times(curves[0]), [1, 11, 21])
        self.assertEqual(times(curves[1]), [1, 10, 20])
        self.assertEqual(times(object_curve), [1, 10, 20])

    def test_pose_custom_properties_follow_selected_bones(self):
        rig = self.object(data=bpy.data.armatures.new('Rig'))
        bpy.ops.object.mode_set(mode='EDIT')
        names = ('Selected "control"', 'Other')
        for name in names:
            bone = rig.data.edit_bones.new(name)
            bone.head = (0, 0, 0)
            bone.tail = (0, 0, 1)
        bpy.ops.object.mode_set(mode='POSE')
        curves = []
        for name in names:
            bone = rig.pose.bones[name]
            bone.select = name == names[0]
            bone['stretch'] = 1.0
            curves.append(self.animate(rig, path=bone.path_from_id() + '["stretch"]'))
        rig.data.bones.active = rig.data.bones[names[0]]
        self.run_operation()
        self.assertEqual(times(curves[0]), [1, 11, 21])
        self.assertEqual(times(curves[1]), [1, 10, 20])

    def test_unassigned_and_empty_selection_cancel(self):
        self.assert_cancelled()
        obj = self.object()
        self.assert_cancelled()
        curve = self.animate(obj)
        obj.select_set(False)
        bpy.context.view_layer.objects.active = None
        self.assert_cancelled()
        self.assertEqual(times(curve), [1, 10, 20])

    def test_default_operation_supports_undo_and_redo_for_all_axes(self):
        obj = self.object('Undo object')
        curves = [self.animate(obj, path='location', index=axis) for axis in range(3)]
        bpy.context.preferences.edit.use_global_undo = True

        def restored_times():
            # Undo can replace the IDs and their nested RNA data.
            owner = bpy.data.objects['Undo object']
            return [times(channelbag(owner).fcurves.find('location', index=axis)) for axis in range(3)]

        # Keep global undo in the factory context. A newly created background
        # Graph Editor cannot initialize its redo HUD without a window eventstate.
        # The focused Graph Editor's default channel scope is tested separately.
        bpy.ops.ed.undo_push(message='Initial inbetween fixture')
        bpy.ops.ed.undo_push(message='Before inserting empty frames')
        result = bpy.ops.anim.island_inbetween(
            'EXEC_DEFAULT', True, operation='INSERT', frames=2, mode='CURRENT')
        self.assertEqual(result, {'FINISHED'})
        self.assertEqual(restored_times(), [[1, 12, 22]] * 3)
        self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
        self.assertEqual(restored_times(), [[1, 10, 20]] * 3)
        self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
        self.assertEqual(restored_times(), [[1, 12, 22]] * 3)


if __name__ == '__main__':
    register_source_module()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(InbetweenTest)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
