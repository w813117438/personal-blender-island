# SPDX-License-Identifier: GPL-2.0-or-later
"""Run with Blender --background --factory-startup --python this_file.py."""
import os
import tempfile
import unittest

import bpy
from mathutils import Matrix


class SmartConstraintTest(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.scene = bpy.context.scene
        self.owner = self.empty('Owner', (2, 3, 4))
        self.target = self.empty('Target', (10, 0, 0))
        self.con = self.owner.constraints.new('SMART')
        self.con.target = self.target
        self.update()

    def empty(self, name, location):
        obj = bpy.data.objects.new(name, None)
        self.scene.collection.objects.link(obj)
        obj.location = location
        return obj

    def update(self):
        bpy.context.view_layer.update()

    def world(self, obj=None, bone=None):
        self.update()
        obj = obj or self.owner
        ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        return (ev.matrix_world @ ev.pose.bones[bone].matrix if bone else ev.matrix_world).copy()

    def assertMatrix(self, a, b, eps=3e-5):
        self.assertLess(max(abs(a[i][j] - b[i][j]) for i in range(4) for j in range(4)), eps)

    def switch(self, weight):
        before = self.world()
        self.con.influence = weight
        self.assertMatrix(self.world(), before)

    def test_initial_binding_preserves_transform(self):
        self.assertMatrix(self.world(), Matrix.Translation((2, 3, 4)))
        self.assertIsNone(self.owner.parent)

    def test_existing_constraint_influence_unchanged(self):
        self.owner.constraints.remove(self.con)
        con = self.owner.constraints.new('COPY_LOCATION')
        con.target = self.target
        con.influence = 0.5
        self.assertAlmostEqual(self.world().translation.x, 6)
        self.assertIsNone(self.owner.animation_data)

    def test_release_move_and_rebind(self):
        self.target.location.x += 5
        self.assertAlmostEqual(self.world().translation.x, 7)
        self.switch(0)
        self.target.location.x += 20
        self.assertAlmostEqual(self.world().translation.x, 7)
        self.owner.location.x += 3
        self.assertAlmostEqual(self.world().translation.x, 10)
        self.switch(1)
        self.target.location.x += 4
        self.assertAlmostEqual(self.world().translation.x, 14)

    def test_partial_influence(self):
        self.switch(0.5)
        self.target.location.x += 10
        self.assertAlmostEqual(self.world().translation.x, 7)
        self.switch(0)
        self.target.location.x += 10
        self.assertAlmostEqual(self.world().translation.x, 7)

    def test_rotation_scale_no_jump(self):
        self.target.rotation_euler = (0.3, 0.5, 0.7)
        self.target.scale = (1.5, 1.5, 1.5)
        self.switch(0)
        held = self.world()
        self.target.rotation_euler = (1.1, 0.2, -0.8)
        self.target.scale = (0.7, 0.7, 0.7)
        self.assertMatrix(self.world(), held)
        self.switch(1)

    def test_nonuniform_parent_and_partial_rebind(self):
        parent = self.empty('Parent', (3, -2, 1))
        parent.rotation_euler = (0.2, 0.6, -0.3)
        parent.scale = (2, 0.7, 1.3)
        self.owner.parent = parent
        self.owner.rotation_euler = (0.3, -0.4, 0.7)
        self.target.rotation_euler = (0.1, 0.2, 0.3)
        self.target.scale = (1.2, 0.8, 1.5)
        self.switch(0.5)
        self.switch(0)
        self.target.rotation_euler.z += 0.4
        self.switch(1)

    def animate_switches(self):
        self.owner.location = (2, 0, 0)
        self.target.location = (10, 0, 0)
        for frame in (1, 11, 21, 31):
            self.target.location.x = 10 + frame - 1
            self.target.keyframe_insert('location', frame=frame)
        ad = self.target.animation_data
        for fc in ad.action.layers[0].strips[0].channelbag(ad.action_slot).fcurves:
            for key in fc.keyframe_points:
                key.interpolation = 'LINEAR'
        self.scene.frame_set(11)
        self.switch(0)
        self.scene.frame_set(21)
        self.switch(1)

    def test_jump_reverse_repeat(self):
        self.animate_switches()
        for frame in (31, 1, 11, 5, 21, 15, 25, 31, 5):
            self.scene.frame_set(frame)
            expected = 2 + (frame - 1 if frame < 11 else 10 if frame < 21 else frame - 11)
            self.assertAlmostEqual(self.world().translation.x, expected, places=4)

    def test_own_animation_after_release(self):
        self.animate_switches()
        self.scene.frame_set(11)
        self.owner.location.x = 2
        self.owner.keyframe_insert('location', frame=11)
        self.owner.location.x = 5
        self.owner.keyframe_insert('location', frame=20)
        self.scene.frame_set(20)
        self.assertAlmostEqual(self.world().translation.x, 15, places=4)

    def test_save_reload(self):
        self.animate_switches()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'smart.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            self.scene = bpy.context.scene
            self.owner = bpy.data.objects['Owner']
            for frame, x in ((31, 22), (1, 2), (15, 12), (25, 16)):
                self.scene.frame_set(frame)
                self.assertAlmostEqual(self.world().translation.x, x, places=4)

    def test_pose_bone_owner_and_target(self):
        bpy.ops.object.armature_add()
        rig = bpy.context.object
        rig.name = 'Rig'
        rig.location = (4, -3, 2)
        rig.rotation_euler = (0.2, 0.3, -0.4)
        bone = rig.pose.bones[0]
        con = bone.constraints.new('SMART')
        con.target = self.target
        before = self.world(rig, bone.name)
        self.target.location.x += 5
        followed = self.world(rig, bone.name)
        self.assertAlmostEqual(followed.translation.x - before.translation.x, 5, places=4)
        con.influence = 0
        self.assertMatrix(self.world(rig, bone.name), followed)
        self.target.location.x += 20
        self.assertMatrix(self.world(rig, bone.name), followed)
        con.influence = 1
        self.assertMatrix(self.world(rig, bone.name), followed)
        # Also bind an object to a pose-bone target.
        other = self.empty('Other', (1, 2, 3))
        other_con = other.constraints.new('SMART')
        other_con.target = rig
        other_con.subtarget = bone.name
        initial = self.world(other)
        bone.location.y += 2
        moved = self.world(other)
        self.assertGreater((initial.translation - moved.translation).length, 1)
        other_con.influence = 0
        self.assertMatrix(self.world(other), moved)

    def test_copy_has_independent_binding_data(self):
        self.target.location.x += 5
        self.switch(0)
        clone = self.owner.copy()
        self.scene.collection.objects.link(clone)
        self.assertMatrix(self.world(clone), self.world())
        self.owner.constraints.remove(self.con)
        self.assertAlmostEqual(self.world(clone).translation.x, 7, places=4)

    def test_switch_on_copy_does_not_edit_shared_action(self):
        self.target.location.x += 5
        self.switch(0)
        clone = self.owner.copy()
        self.scene.collection.objects.link(clone)
        self.update()
        clone.constraints[0].influence = 1
        self.update()
        self.assertIsNot(clone.animation_data.action, self.owner.animation_data.action)
        self.target.location.x += 4
        self.assertAlmostEqual(self.world(clone).translation.x, 11, places=4)
        self.assertAlmostEqual(self.world().translation.x, 7, places=4)

    def test_zero_scale_never_creates_nan(self):
        self.target.scale = (0, 0, 0)
        self.update()
        self.con.influence = 0
        import math
        self.assertTrue(all(math.isfinite(x) for row in self.world() for x in row))


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SmartConstraintTest)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError('Smart constraint regression failed')
