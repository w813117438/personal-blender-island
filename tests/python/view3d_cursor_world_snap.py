# SPDX-License-Identifier: GPL-2.0-or-later
"""Run with blender --background --factory-startup --python-exit-code 1 --python this_file."""

import unittest
from types import SimpleNamespace

import bpy
from mathutils import Euler, Matrix, Vector


class CursorWorldSnapTest(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.scene = bpy.context.scene
        self.area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
        self.region = next(r for r in self.area.regions if r.type == 'WINDOW')

    def object(self, name, location=(2, -3, 4), rotation=(0.3, -0.4, 0.6)):
        ob = bpy.data.objects.new(name, None)
        self.scene.collection.objects.link(ob)
        ob.location = location
        ob.rotation_euler = rotation
        return ob

    def select(self, *objects):
        for ob in self.scene.objects:
            ob.select_set(False)
        for ob in objects:
            ob.select_set(True)
        bpy.context.view_layer.objects.active = objects[-1] if objects else None
        bpy.context.view_layer.update()

    def snap(self, to_cursor, **kwargs):
        with bpy.context.temp_override(area=self.area, region=self.region):
            op = bpy.ops.view3d.snap_selected_to_cursor if to_cursor else bpy.ops.view3d.snap_cursor_to_selected
            result = op(**kwargs)
        bpy.context.view_layer.update()
        return result

    def assert_transform(self, matrix, location, rotation):
        self.assertLess((matrix.translation - Vector(location)).length, 0.0001)
        a = matrix.to_quaternion().normalized()
        b = rotation.normalized()
        self.assertLess(1.0 - abs(a.dot(b)), 0.00001)

    def set_cursor(self):
        self.scene.cursor.location = (-4, 7, 2)
        self.scene.cursor.rotation_euler = (0.7, 0.2, -1.1)
        return self.scene.cursor.matrix.copy()

    def rig(self, name='Rig', connected=False):
        arm = bpy.data.armatures.new(name)
        ob = bpy.data.objects.new(name, arm)
        self.scene.collection.objects.link(ob)
        self.select(ob)
        bpy.ops.object.mode_set(mode='EDIT')
        parent = arm.edit_bones.new('Parent')
        parent.head, parent.tail, parent.roll = (0, 0, 0), (0.4, 2, 0.5), 0.35
        middle = arm.edit_bones.new('Middle')
        middle.head, middle.tail, middle.roll = parent.tail, (1, 4, 0), -0.4
        middle.parent = parent
        child = arm.edit_bones.new('Child')
        child.head, child.tail, child.roll = middle.tail, (0, 6, 1), 0.7
        child.parent = middle
        child.use_connect = connected
        bpy.ops.object.mode_set(mode='OBJECT')
        ob.location = (3, -4, 2)
        ob.rotation_euler = (0.3, -0.5, 0.6)
        ob.scale = (1.5, 1.5, 1.5)
        for pchan in ob.pose.bones:
            pchan.rotation_mode = 'XYZ'
            pchan.rotation_euler = (0.2, 0.1, -0.3)
            pchan.location = (0.2, -0.1, 0.4)
        return ob

    def pose_select(self, selections):
        # [(armature_object, [selected bone names]), ...], last bone is active.
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        self.select(*(ob for ob, _names in selections))
        bpy.ops.object.mode_set(mode='POSE')
        for ob, names in selections:
            self.assertEqual(ob.mode, 'POSE')
            for pchan in ob.pose.bones:
                pchan.select = pchan.name in names
            ob.data.bones.active = ob.data.bones[names[-1]] if names else None
        bpy.context.view_layer.update()

    def pose_world(self, ob, name):
        evaluated = ob.evaluated_get(bpy.context.evaluated_depsgraph_get())
        return evaluated.matrix_world @ evaluated.pose.bones[name].matrix

    def test_pose_cursor_reads_world_transform_with_armature_parent(self):
        ob = self.rig()
        parent = self.object('Rig parent', (8, 2, -3), (-0.4, 0.3, 0.9))
        ob.parent = parent
        ob.matrix_parent_inverse = Matrix.Translation((2, 3, -1))
        self.pose_select([(ob, ['Child'])])
        expected = self.pose_world(ob, 'Child')
        for mode in ('XYZ', 'QUATERNION', 'AXIS_ANGLE'):
            self.scene.cursor.rotation_mode = mode
            self.assertEqual(self.snap(False, use_rotation=True), {'FINISHED'})
            self.assert_transform(self.scene.cursor.matrix, expected.translation, expected.to_quaternion())

    def test_pose_bone_to_cursor_world_transform_and_rotation_modes(self):
        ob = self.rig()
        self.pose_select([(ob, ['Child'])])
        pchan = ob.pose.bones['Child']
        pchan.scale = (0.8, 1.2, 1.5)
        scale = pchan.scale.copy()
        rest = ob.data.bones['Child'].matrix_local.copy()
        expected = self.set_cursor()
        for mode in ('XYZ', 'ZYX', 'QUATERNION', 'AXIS_ANGLE'):
            pchan.rotation_mode = mode
            self.assertEqual(self.snap(True, use_offset=False, use_rotation=True), {'FINISHED'})
            self.assert_transform(self.pose_world(ob, 'Child'), expected.translation, expected.to_quaternion())
            self.assertLess((pchan.scale - scale).length, 0.00001)
            self.assertEqual(ob.data.bones['Child'].matrix_local, rest)

    def test_pose_parent_child_selection_updates_intermediate_bone(self):
        ob = self.rig()
        self.pose_select([(ob, ['Parent', 'Child'])])
        expected = self.set_cursor()
        self.snap(True, use_offset=False, use_rotation=True)
        for name in ('Parent', 'Child'):
            self.assert_transform(self.pose_world(ob, name), expected.translation, expected.to_quaternion())

    def test_pose_multiple_armatures_in_both_directions(self):
        a = self.rig('Rig A')
        b = self.rig('Rig B')
        b.location = (-2, 8, 4)
        b.rotation_euler = (-0.8, 0.1, 0.4)
        self.pose_select([(a, ['Child']), (b, ['Parent'])])
        wa, wb = self.pose_world(a, 'Child'), self.pose_world(b, 'Parent')
        self.snap(False, use_rotation=True)
        self.assert_transform(self.scene.cursor.matrix, (wa.translation + wb.translation) / 2, wb.to_quaternion())
        expected = self.set_cursor()
        self.snap(True, use_offset=False, use_rotation=True)
        for ob, name in ((a, 'Child'), (b, 'Parent')):
            self.assert_transform(self.pose_world(ob, name), expected.translation, expected.to_quaternion())

    def test_pose_cursor_reads_constraint_evaluation(self):
        ob = self.rig()
        target = self.object('Constraint target', (9, -1, 7), (0.2, 0.5, -0.9))
        constraint = ob.pose.bones['Child'].constraints.new('COPY_TRANSFORMS')
        constraint.target = target
        self.pose_select([(ob, ['Child'])])
        expected = self.pose_world(ob, 'Child')
        self.snap(False, use_rotation=True)
        self.assert_transform(self.scene.cursor.matrix, expected.translation, expected.to_quaternion())

    def test_pose_child_of_compensates_target_and_inverse_matrix(self):
        ob = self.rig()
        target = self.object('Child Of target', (3, -2, 5), (0.4, -0.2, 0.8))
        target.scale = (1.25, 1.25, 1.25)
        pchan = ob.pose.bones['Child']
        constraint = pchan.constraints.new('CHILD_OF')
        constraint.target = target
        inverse = Matrix.Translation((1, -3, 2)) @ Euler((0.3, 0.1, -0.5)).to_matrix().to_4x4()
        constraint.inverse_matrix = inverse
        self.pose_select([(ob, ['Child'])])
        scale = pchan.scale.copy()
        expected = self.set_cursor()
        for owner_space in ('WORLD', 'POSE', 'LOCAL'):
            for influence in (0.0, 0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99, 1.0):
                with self.subTest(owner_space=owner_space, influence=influence):
                    constraint.owner_space = owner_space
                    constraint.influence = influence
                    self.snap(True, use_offset=False, use_rotation=True)
                    self.assert_transform(self.pose_world(ob, 'Child'), expected.translation, expected.to_quaternion())
                    self.assertEqual(constraint.inverse_matrix, inverse)
                    self.assertAlmostEqual(constraint.influence, influence, places=6)
                    self.assertFalse(constraint.mute)
                    self.assertEqual(pchan.scale, scale)
                    self.snap(True, use_offset=False, use_rotation=True)
                    self.assert_transform(self.pose_world(ob, 'Child'), expected.translation, expected.to_quaternion())

    def test_pose_child_of_bone_target_and_multiple_constraints(self):
        target_rig = self.rig('Target rig')
        ob = self.rig('Controlled rig')
        target = self.object('Second target', (5, 2, -1), (-0.3, 0.4, 0.2))
        pchan = ob.pose.bones['Child']
        first = pchan.constraints.new('CHILD_OF')
        first.target = target_rig
        first.subtarget = 'Child'
        first.owner_space = 'POSE'
        first.inverse_matrix = Matrix.Translation((-1, 2, 3))
        second = pchan.constraints.new('CHILD_OF')
        second.target = target
        second.inverse_matrix = Euler((0.2, 0.3, -0.1)).to_matrix().to_4x4()
        self.pose_select([(ob, ['Parent', 'Child'])])
        expected = self.set_cursor()
        self.snap(True, use_offset=False, use_rotation=True)
        for name in ('Parent', 'Child'):
            self.assert_transform(self.pose_world(ob, name), expected.translation, expected.to_quaternion())

    def test_pose_muted_child_of_does_not_apply_inverse(self):
        ob = self.rig()
        target = self.object('Muted target', (8, 9, 10))
        constraint = ob.pose.bones['Child'].constraints.new('CHILD_OF')
        constraint.target = target
        constraint.inverse_matrix = Matrix.Translation((10, 20, 30))
        constraint.mute = True
        self.pose_select([(ob, ['Child'])])
        expected = self.set_cursor()
        self.snap(True, use_offset=False, use_rotation=True)
        self.assert_transform(self.pose_world(ob, 'Child'), expected.translation, expected.to_quaternion())

    def test_pose_partial_copy_transforms(self):
        ob = self.rig()
        # Keep the requested rotation in the reachable slerp range even at 90% influence.
        target = self.object('Copy target', (2, -1, 4), (0.7, 0.2, -1.0))
        constraint = ob.pose.bones['Child'].constraints.new('COPY_TRANSFORMS')
        constraint.target = target
        self.pose_select([(ob, ['Child'])])
        expected = self.set_cursor()
        for influence in (0.1, 0.5, 0.9):
            with self.subTest(influence=influence):
                constraint.influence = influence
                self.snap(True, use_offset=False, use_rotation=True)
                self.assert_transform(self.pose_world(ob, 'Child'), expected.translation, expected.to_quaternion())

    def test_pose_failure_restores_all_selected_bones_without_keyframes(self):
        ob = self.rig()
        target = self.object('Override target', (100, 20, 30))
        constraint = ob.pose.bones['Child'].constraints.new('COPY_TRANSFORMS')
        constraint.target = target
        self.pose_select([(ob, ['Parent', 'Child'])])
        before = {p.name: p.matrix_basis.copy() for p in ob.pose.bones}
        self.scene.tool_settings.use_keyframe_insert_auto = True
        self.set_cursor()
        with self.assertRaises(RuntimeError):
            self.snap(True, use_offset=False, use_rotation=True)
        for pchan in ob.pose.bones:
            self.assertEqual(pchan.matrix_basis, before[pchan.name])
        self.assertTrue(ob.animation_data is None or ob.animation_data.action is None)

    def test_pose_connected_bone_failure_restores_pose(self):
        ob = self.rig(connected=True)
        self.pose_select([(ob, ['Child'])])
        expected = self.set_cursor()
        original = ob.pose.bones['Child'].matrix_basis.copy()
        with self.assertRaises(RuntimeError):
            self.snap(True, use_offset=False, use_rotation=True)
        self.assertEqual(ob.pose.bones['Child'].matrix_basis, original)
        self.assertTrue(ob.data.bones['Child'].use_connect)

    def test_pose_locks_and_hidden_bones_are_respected(self):
        ob = self.rig()
        self.pose_select([(ob, ['Parent', 'Child'])])
        hidden = ob.pose.bones['Parent']
        hidden.hide = True
        pchan = ob.pose.bones['Child']
        pchan.lock_location = (True, False, False)
        pchan.lock_rotation = (False, False, True)
        old_parent = hidden.matrix_basis.copy()
        old_child = pchan.matrix_basis.copy()
        old_x, old_z = pchan.location.x, pchan.rotation_euler.z
        self.set_cursor()
        with self.assertRaises(RuntimeError):
            self.snap(True, use_offset=False, use_rotation=True)
        self.assertEqual(hidden.matrix_basis, old_parent)
        self.assertEqual(pchan.matrix_basis, old_child)
        self.assertAlmostEqual(pchan.location.x, old_x, places=5)
        self.assertAlmostEqual(pchan.rotation_euler.z, old_z, places=5)

    def test_pose_inheritance_options(self):
        ob = self.rig()
        self.pose_select([(ob, ['Child'])])
        bone = ob.data.bones['Child']
        expected = self.set_cursor()
        for inherit_rotation in (True, False):
            for local_location in (True, False):
                bone.use_inherit_rotation = inherit_rotation
                bone.use_local_location = local_location
                self.snap(True, use_offset=False, use_rotation=True)
                self.assert_transform(self.pose_world(ob, 'Child'), expected.translation, expected.to_quaternion())

    def test_pose_empty_selection_does_not_change_cursor(self):
        ob = self.rig()
        self.pose_select([(ob, [])])
        expected = self.set_cursor()
        with self.assertRaises(RuntimeError):
            self.snap(False, use_rotation=True)
        self.assertEqual(self.snap(True, use_offset=False, use_rotation=True), {'CANCELLED'})
        self.assert_transform(self.scene.cursor.matrix, expected.translation, expected.to_quaternion())

    def test_pose_auto_keyframes_location_and_rotation(self):
        ob = self.rig()
        self.pose_select([(ob, ['Child'])])
        pchan = ob.pose.bones['Child']
        pchan.keyframe_insert('location', frame=1)
        pchan.keyframe_insert('rotation_euler', frame=1)
        self.scene.frame_set(10)
        self.scene.tool_settings.use_keyframe_insert_auto = True
        expected = self.set_cursor()
        self.snap(True, use_offset=False, use_rotation=True)
        self.scene.frame_set(1)
        self.assertGreater((self.pose_world(ob, 'Child').translation - expected.translation).length, 0.1)
        self.scene.frame_set(10)
        self.assert_transform(self.pose_world(ob, 'Child'), expected.translation, expected.to_quaternion())

    def test_cursor_copies_evaluated_parented_world_transform(self):
        parent = self.object('Parent', (6, 1, -2), (-0.2, 0.5, 0.8))
        parent.scale = (2, 2, 2)
        ob = self.object('Child')
        ob.parent = parent
        ob.matrix_parent_inverse = Matrix.Translation((1, -2, 3)) @ Euler((0.2, 0.4, -0.3)).to_matrix().to_4x4()
        self.select(ob)
        expected = ob.matrix_world.copy()
        for mode in ('XYZ', 'QUATERNION', 'AXIS_ANGLE'):
            self.scene.cursor.rotation_mode = mode
            self.assertEqual(self.snap(False, use_rotation=True), {'FINISHED'})
            self.assert_transform(self.scene.cursor.matrix, expected.translation, expected.to_quaternion())

    def test_cursor_reads_constraint_result(self):
        target = self.object('Constraint target', (8, 9, 10), (0.8, -0.3, -0.2))
        ob = self.object('Constrained')
        constraint = ob.constraints.new('COPY_TRANSFORMS')
        constraint.target = target
        self.select(ob)
        expected = ob.evaluated_get(bpy.context.evaluated_depsgraph_get()).matrix_world.copy()
        self.snap(False, use_rotation=True)
        self.assert_transform(self.scene.cursor.matrix, expected.translation, expected.to_quaternion())

    def test_multiple_selection_uses_world_center_and_active_rotation(self):
        a = self.object('A', (2, 4, 6))
        b = self.object('B', (8, -2, 10), (-0.5, 0.3, 1.2))
        self.select(a, b)
        self.snap(False, use_rotation=True)
        self.assert_transform(self.scene.cursor.matrix, (5, 1, 8), b.matrix_world.to_quaternion())

    def test_selection_to_cursor_with_parent_inverse_and_delta(self):
        parent = self.object('Parent', (4, -1, 6), (0.4, 0.5, 0.7))
        parent.scale = (2, 2, 2)
        ob = self.object('Child')
        ob.parent = parent
        ob.matrix_parent_inverse = Matrix.Translation((2, 3, -1)) @ Euler((0.1, -0.2, 0.4)).to_matrix().to_4x4()
        ob.delta_location = (1, -2, 0.5)
        ob.scale = (1.2, 0.8, 1.5)
        expected = self.set_cursor()
        for mode in ('XYZ', 'ZYX', 'QUATERNION', 'AXIS_ANGLE'):
            ob.rotation_mode = mode
            ob.delta_rotation_euler = (0.1, 0.3, -0.2)
            ob.delta_rotation_quaternion = Euler((0.2, -0.1, 0.3)).to_quaternion()
            self.select(ob)
            scale = ob.scale.copy()
            self.snap(True, use_offset=False, use_rotation=True)
            self.assert_transform(ob.matrix_world, expected.translation, expected.to_quaternion())
            self.assertLess((ob.scale - scale).length, 0.00001)

    def test_parent_child_selection_is_order_independent(self):
        child = self.object('A child')
        middle = self.object('B unselected middle', (1, 2, 3))
        parent = self.object('Z parent', (8, 9, 10))
        child.parent = middle
        middle.parent = parent
        self.select(child, parent)
        expected = self.set_cursor()
        self.snap(True, use_offset=False, use_rotation=True)
        for ob in (parent, child):
            self.assert_transform(ob.matrix_world, expected.translation, expected.to_quaternion())

    def test_original_cursor_snap_keeps_rotation(self):
        ob = self.object('Object')
        self.select(ob)
        expected = self.set_cursor().to_quaternion()
        self.snap(False, use_rotation=False)
        self.assert_transform(self.scene.cursor.matrix, ob.matrix_world.translation, expected)

    def test_nonuniform_parent_scale_keeps_world_position_exact(self):
        parent = self.object('Parent', (8, -2, 4))
        parent.scale = (2, 3, 0.5)
        ob = self.object('Child')
        ob.parent = parent
        ob.matrix_parent_inverse = Matrix.Translation((3, -2, 1))
        self.select(ob)
        expected = self.set_cursor()
        self.snap(True, use_offset=False, use_rotation=True)
        self.assert_transform(ob.matrix_world, expected.translation, expected.to_quaternion())

    def test_object_child_of_all_weights(self):
        parent = self.object('Parent', (1, 3, 2), (0.2, -0.4, 0.3))
        ob = self.object('Controlled')
        ob.parent = parent
        ob.delta_location = (0.3, -0.1, 0.2)
        ob.delta_rotation_euler = (0.1, 0.2, 0.3)
        target = self.object('Child Of target', (5, 1, -2), (-0.4, 0.2, 0.5))
        constraint = ob.constraints.new('CHILD_OF')
        constraint.target = target
        inverse = Matrix.Translation((2, 3, -1)) @ Euler((0.2, 0.3, -0.1)).to_matrix().to_4x4()
        constraint.inverse_matrix = inverse
        self.select(ob)
        expected = self.set_cursor()
        scale = ob.scale.copy()
        for influence in (0.0, 0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99, 1.0):
            constraint.influence = influence
            self.snap(True, use_offset=False, use_rotation=True)
            self.assert_transform(ob.matrix_world, expected.translation, expected.to_quaternion())
            self.assertEqual(constraint.inverse_matrix, inverse)
            self.assertEqual(ob.scale, scale)
            self.assertAlmostEqual(constraint.influence, influence, places=6)

    def test_object_failure_restores_all_objects_without_keyframes(self):
        free = self.object('A free')
        controlled = self.object('B controlled')
        target = self.object('Override target', (20, 30, 40))
        constraint = controlled.constraints.new('COPY_TRANSFORMS')
        constraint.target = target
        self.select(free, controlled)
        before = {ob.name: ob.matrix_basis.copy() for ob in (free, controlled)}
        self.scene.tool_settings.use_keyframe_insert_auto = True
        self.set_cursor()
        with self.assertRaises(RuntimeError):
            self.snap(True, use_offset=False, use_rotation=True)
        for ob in (free, controlled):
            self.assertEqual(ob.matrix_basis, before[ob.name])
            self.assertTrue(ob.animation_data is None or ob.animation_data.action is None)

    def test_transform_locks_are_respected(self):
        ob = self.object('Locked')
        self.select(ob)
        ob.lock_location = (True, False, False)
        ob.lock_rotation = (False, False, True)
        x, z_angle = ob.location.x, ob.rotation_euler.z
        original = ob.matrix_basis.copy()
        expected = self.set_cursor()
        with self.assertRaises(RuntimeError):
            self.snap(True, use_offset=False, use_rotation=True)
        self.assertAlmostEqual(ob.location.x, x, places=5)
        self.assertAlmostEqual(ob.rotation_euler.z, z_angle, places=5)
        self.assertEqual(ob.matrix_basis, original)

    def test_empty_selection_does_not_change_cursor(self):
        self.select()
        expected = self.set_cursor()
        with self.assertRaises(RuntimeError):
            self.snap(False, use_rotation=True)
        self.assert_transform(self.scene.cursor.matrix, expected.translation, expected.to_quaternion())

    def test_installed_snap_menus_expose_both_world_actions(self):
        from bl_ui.space_view3d import VIEW3D_MT_snap, VIEW3D_MT_snap_pie

        class Layout:
            def __init__(self):
                self.calls = []

            def operator(self, identifier, **kwargs):
                props = SimpleNamespace()
                self.calls.append((identifier, kwargs.get('text', ''), props))
                return props

            def separator(self):
                pass

            def column(self):
                return self

            def menu_pie(self):
                return self

        for menu in (VIEW3D_MT_snap, VIEW3D_MT_snap_pie):
            for mode in ('OBJECT', 'POSE', 'EDIT_MESH', 'EDIT_ARMATURE'):
                layout = Layout()
                menu.draw(SimpleNamespace(layout=layout), SimpleNamespace(mode=mode))
                world_actions = [c for c in layout.calls if 'World Location & Rotation' in c[1]]
                self.assertEqual(len(world_actions), 2 if mode in {'OBJECT', 'POSE'} else 0)
                for identifier, _text, props in world_actions:
                    self.assertTrue(props.use_rotation)
                    if identifier == 'view3d.snap_selected_to_cursor':
                        self.assertFalse(props.use_offset)
                    op_namespace, op_name = identifier.split('.')
                    rna = getattr(getattr(bpy.ops, op_namespace), op_name).get_rna_type()
                    self.assertIn('use_rotation', rna.properties)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CursorWorldSnapTest))
    if not result.wasSuccessful():
        raise RuntimeError('World cursor snapping regression tests failed')
