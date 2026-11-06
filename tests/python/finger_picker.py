# SPDX-License-Identifier: GPL-2.0-or-later
"""Run with Blender --background --factory-startup --python this_file.py."""
import os
import tempfile
import unittest

import bpy


FINGERS = ('thumb', 'index', 'middle', 'ring', 'pinky')


def bone_name(side, finger, segment):
    return f'{side}_{finger}_{segment}'


class FingerPickerTest(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        data = bpy.data.armatures.new('PickerRig')
        self.rig = bpy.data.objects.new('PickerRig', data)
        bpy.context.scene.collection.objects.link(self.rig)
        self.rig.select_set(True)
        bpy.context.view_layer.objects.active = self.rig
        bpy.ops.object.mode_set(mode='EDIT')
        names = ['Control'] + [bone_name(side, finger, segment)
                               for side in ('L', 'R') for finger in FINGERS
                               for segment in range(1, 4)]
        for index, name in enumerate(names):
            bone = data.edit_bones.new(name)
            bone.head = (index, 0, 0)
            bone.tail = (index, 1, 0)
        bpy.ops.object.mode_set(mode='POSE')
        # Most selection tests start with an already configured picker. Binding
        # operators themselves are exercised separately below.
        for side in ('L', 'R'):
            for finger in FINGERS:
                for segment in range(1, 4):
                    item = self.rig.island_finger_bindings.add()
                    item.name = f'{side}:{finger}:{segment}'
                    item.bone_name = bone_name(side, finger, segment)
        self.set_selection({'Control'}, 'Control')

    def set_selection(self, names, active=None, rig=None):
        rig = rig or self.rig
        for bone in rig.pose.bones:
            bone.select = bone.name in names
        rig.data.bones.active = rig.data.bones.get(active) if active else None

    def selected(self, rig=None):
        return {bone.name for bone in (rig or self.rig).pose.bones if bone.select}

    def bindings(self, rig=None):
        return {item.name: item.bone_name for item in (rig or self.rig).island_finger_bindings
                if item.bone_name}

    def pick(self, side='L', finger='index', segment=1, mode='REPLACE'):
        return bpy.ops.pose.island_finger_select(side=side, finger=finger,
                                                segment=segment, mode=mode)

    def assert_cancelled(self, operation):
        # Blender raises RuntimeError when an operator also reports ERROR.
        try:
            result = operation()
        except RuntimeError:
            return
        self.assertEqual(result, {'CANCELLED'})

    def assert_cancelled_preserves_selection(self, operation):
        before = self.selected()
        active = self.rig.data.bones.active
        self.assert_cancelled(operation)
        self.assertEqual(self.selected(), before)
        self.assertEqual(self.rig.data.bones.active, active)

    def test_bind_replace_and_clear_one_slot(self):
        original = self.bindings()
        self.assertEqual(bpy.ops.pose.island_finger_bind(
            side='L', finger='index', segment=1, bone_name='Control'), {'FINISHED'})
        expected = dict(original, **{'L:index:1': 'Control'})
        self.assertEqual(self.bindings(), expected)
        self.assertEqual(bpy.ops.pose.island_finger_bind(
            side='L', finger='index', segment=1, bone_name=''), {'FINISHED'})
        del expected['L:index:1']
        self.assertEqual(self.bindings(), expected)
        self.assertEqual(self.selected(), {'Control'})

    def test_invalid_bind_is_atomic(self):
        before = self.bindings()
        self.assert_cancelled(lambda: bpy.ops.pose.island_finger_bind(
            side='L', finger='index', segment=1, bone_name='MissingBone'))
        self.assertEqual(self.bindings(), before)
        self.assertEqual(self.selected(), {'Control'})

    def test_replace_sets_only_target_and_active(self):
        self.assertEqual(self.pick(), {'FINISHED'})
        self.assertEqual(self.selected(), {'L_index_1'})
        self.assertEqual(self.rig.data.bones.active.name, 'L_index_1')

    def test_add_and_subtract_preserve_unrelated_selection(self):
        self.assertEqual(self.pick(mode='ADD'), {'FINISHED'})
        self.assertEqual(self.selected(), {'Control', 'L_index_1'})
        self.assertEqual(self.rig.data.bones.active.name, 'L_index_1')
        self.assertEqual(self.pick(mode='SUBTRACT'), {'FINISHED'})
        self.assertEqual(self.selected(), {'Control'})
        self.assertEqual(self.rig.data.bones.active.name, 'Control')

    def test_subtract_last_selected_bone_clears_active(self):
        self.pick()
        self.assertEqual(self.pick(mode='SUBTRACT'), {'FINISHED'})
        self.assertEqual(self.selected(), set())
        self.assertIsNone(self.rig.data.bones.active)

    def test_whole_finger_selects_three_segments(self):
        self.assertEqual(self.pick(finger='ring', segment=0), {'FINISHED'})
        self.assertEqual(self.selected(), {'L_ring_1', 'L_ring_2', 'L_ring_3'})
        self.assertEqual(self.rig.data.bones.active.name, 'L_ring_1')

    def test_whole_hand_and_sides_are_isolated(self):
        for side in ('L', 'R'):
            self.assertEqual(self.pick(side=side, finger='ALL', segment=0), {'FINISHED'})
            self.assertEqual(self.selected(), {bone_name(side, finger, segment)
                                              for finger in FINGERS for segment in range(1, 4)})
            self.assertIn(self.rig.data.bones.active.name, self.selected())

    def test_empty_and_stale_slots_keep_previous_selection(self):
        self.rig.island_finger_bindings['L:index:1'].bone_name = ''
        self.assert_cancelled_preserves_selection(lambda: self.pick())
        self.rig.island_finger_bindings['L:index:1'].bone_name = 'DeletedBone'
        self.assert_cancelled_preserves_selection(lambda: self.pick())
        self.rig.island_finger_bindings.clear()
        self.assert_cancelled_preserves_selection(lambda: self.pick(finger='ALL', segment=0))

    def test_hidden_bone_keeps_previous_selection(self):
        self.rig.pose.bones['L_index_1'].hide = True
        self.assert_cancelled_preserves_selection(lambda: self.pick())

    def test_selection_locked_bone_keeps_previous_selection(self):
        self.rig.data.bones['L_index_1'].hide_select = True
        self.assert_cancelled_preserves_selection(lambda: self.pick())

    def test_hidden_collection_is_respected(self):
        collection = self.rig.data.collections.new('Hidden fingers')
        collection.assign(self.rig.data.bones['L_index_1'])
        collection.is_visible = False
        bpy.context.view_layer.update()
        self.assertNotIn('L_index_1', {bone.name for bone in bpy.context.visible_pose_bones})
        self.assert_cancelled_preserves_selection(lambda: self.pick())

    def test_collection_solo_is_respected(self):
        target = self.rig.data.collections.new('Fingers')
        target.assign(self.rig.data.bones['L_index_1'])
        solo = self.rig.data.collections.new('Solo controls')
        solo.assign(self.rig.data.bones['Control'])
        solo.is_solo = True
        bpy.context.view_layer.update()
        self.assertNotIn('L_index_1', {bone.name for bone in bpy.context.visible_pose_bones})
        self.assert_cancelled_preserves_selection(lambda: self.pick())

    def test_partial_finger_ignores_missing_hidden_and_locked_targets(self):
        self.rig.pose.bones['L_index_2'].hide = True
        self.rig.data.bones['L_index_3'].hide_select = True
        self.assertEqual(self.pick(segment=0), {'FINISHED'})
        self.assertEqual(self.selected(), {'L_index_1'})
        self.rig.island_finger_bindings['L:index:2'].bone_name = 'MissingBone'
        self.assertEqual(self.pick(segment=0), {'FINISHED'})
        self.assertEqual(self.selected(), {'L_index_1'})

    def test_multiple_pose_rigs_do_not_cross_select_or_clear(self):
        bpy.ops.object.mode_set(mode='OBJECT')
        other = self.rig.copy()
        other.data = self.rig.data.copy()
        other.name = 'OtherRig'
        bpy.context.scene.collection.objects.link(other)
        other.select_set(True)
        self.rig.select_set(True)
        bpy.context.view_layer.objects.active = self.rig
        bpy.ops.object.mode_set(mode='POSE')
        self.assertEqual(other.mode, 'POSE')
        self.set_selection({'R_pinky_3'}, 'R_pinky_3', rig=other)
        self.assertEqual(self.pick(), {'FINISHED'})
        self.assertEqual(self.selected(), {'L_index_1'})
        self.assertEqual(self.selected(other), {'R_pinky_3'})
        self.assertEqual(other.data.bones.active.name, 'R_pinky_3')

    def test_object_copy_has_independent_bindings(self):
        other = self.rig.copy()
        bpy.context.scene.collection.objects.link(other)
        other.island_finger_bindings['L:index:1'].bone_name = 'Control'
        self.assertEqual(self.bindings()['L:index:1'], 'L_index_1')
        self.assertEqual(self.bindings(other)['L:index:1'], 'Control')
        other.island_finger_bindings.clear()
        self.assertEqual(len(self.bindings()), 30)

    def test_save_reload_preserves_bindings_and_picker_settings(self):
        self.rig.island_finger_picker_bind_mode = True
        self.rig.island_finger_picker_extend = True
        expected = self.bindings()
        with tempfile.TemporaryDirectory(prefix='island-finger-picker-') as directory:
            path = os.path.join(directory, 'picker.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            self.rig = bpy.data.objects['PickerRig']
            self.assertEqual(self.bindings(), expected)
            self.assertTrue(self.rig.island_finger_picker_bind_mode)
            self.assertTrue(self.rig.island_finger_picker_extend)
            self.assertEqual(self.pick(side='R', finger='thumb', segment=2), {'FINISHED'})
            self.assertEqual(self.selected(), {'R_thumb_2'})

    def test_selection_never_changes_transforms_or_inserts_keys(self):
        self.rig.pose.bones['L_index_1'].location = (0.2, 0.3, 0.4)
        self.rig.pose.bones['L_index_1'].rotation_euler = (0.1, 0.2, 0.3)
        self.rig.pose.bones['L_index_1'].scale = (1.1, 1.2, 1.3)
        before = {bone.name: tuple(value for row in bone.matrix_basis for value in row)
                  for bone in self.rig.pose.bones}
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
        self.pick(finger='ALL', segment=0)
        self.pick(side='R', finger='ALL', segment=0, mode='ADD')
        self.pick(finger='index', segment=0, mode='SUBTRACT')
        after = {bone.name: tuple(value for row in bone.matrix_basis for value in row)
                 for bone in self.rig.pose.bones}
        self.assertEqual(after, before)
        self.assertIsNone(self.rig.animation_data)

    def test_operators_are_unavailable_outside_pose_mode(self):
        bpy.ops.object.mode_set(mode='OBJECT')
        self.assertFalse(bpy.ops.pose.island_finger_bind.poll())
        self.assertFalse(bpy.ops.pose.island_finger_select.poll())

    def test_sidebar_category_and_pose_children(self):
        parent = bpy.types.VIEW3D_PT_island_finger_picker
        children = (bpy.types.VIEW3D_PT_island_finger_left,
                    bpy.types.VIEW3D_PT_island_finger_right)
        self.assertEqual(parent.bl_category, '手指选择')
        self.assertTrue(parent.poll(bpy.context))
        for child in children:
            self.assertEqual(child.bl_category, '手指选择')
            self.assertTrue(child.poll(bpy.context))

    def test_sidebar_is_discoverable_without_active_object(self):
        bpy.ops.object.mode_set(mode='OBJECT')
        self.rig.select_set(False)
        bpy.context.view_layer.objects.active = None
        self.assertIsNone(bpy.context.object)
        self.assertTrue(bpy.types.VIEW3D_PT_island_finger_picker.poll(bpy.context))
        self.assertFalse(bpy.types.VIEW3D_PT_island_finger_left.poll(bpy.context))
        self.assertFalse(bpy.types.VIEW3D_PT_island_finger_right.poll(bpy.context))

    def test_sidebar_is_discoverable_with_mesh_and_object_mode(self):
        bpy.ops.object.mode_set(mode='OBJECT')
        for use_mesh in (False, True):
            if use_mesh:
                bpy.ops.mesh.primitive_cube_add()
                self.assertEqual(bpy.context.object.type, 'MESH')
            self.assertTrue(bpy.types.VIEW3D_PT_island_finger_picker.poll(bpy.context))
            self.assertFalse(bpy.types.VIEW3D_PT_island_finger_left.poll(bpy.context))
            self.assertFalse(bpy.types.VIEW3D_PT_island_finger_right.poll(bpy.context))


if __name__ == '__main__':
    unittest.main(argv=[__file__])
