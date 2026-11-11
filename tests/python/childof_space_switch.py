# SPDX-License-Identifier: GPL-2.0-or-later
"""Regression tests for Island Child Of Space Switch.

Run with Blender 5.3 --background --factory-startup --python-exit-code 1
--python tests/python/childof_space_switch.py.
"""

import importlib.util
from math import acos, pi
from pathlib import Path
import sys
import tempfile
import unittest

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector


def register_source_modules():
    root = Path(__file__).resolve().parents[2]
    if not hasattr(bpy.types, 'CONSTRAINT_OT_island_childof_space_switch'):
        op_path = root / 'scripts/startup/bl_operators/island_space_switch.py'
        op_name = 'bl_operators.island_space_switch'
        spec = importlib.util.spec_from_file_location(op_name, op_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[op_name] = mod
        spec.loader.exec_module(mod)
        for cls in mod.classes:
            bpy.utils.register_class(cls)
        mod.register()
    if not hasattr(bpy.types, 'VIEW3D_PT_island_space_switch'):
        ui_path = root / 'scripts/startup/bl_ui/space_island_space_switch.py'
        ui_name = 'bl_ui.space_island_space_switch'
        spec = importlib.util.spec_from_file_location(ui_name, ui_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[ui_name] = mod
        spec.loader.exec_module(mod)
        for cls in mod.classes:
            bpy.utils.register_class(cls)


def channelbag(owner):
    adt = owner.animation_data
    if not adt or not adt.action or not adt.action_slot:
        return None
    for layer in adt.action.layers:
        for strip in layer.strips:
            if strip.type == 'KEYFRAME':
                bag = strip.channelbag(adt.action_slot)
                if bag is not None:
                    return bag
    return None


def find_curve(owner, data_path, index=0):
    bag = channelbag(owner)
    if bag is None:
        return None
    for fc in bag.fcurves:
        if fc.data_path == data_path and fc.array_index == index:
            return fc
    return None


class ChildOfSpaceSwitchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        register_source_modules()

    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.scene = bpy.context.scene
        self.scene.frame_start = 1
        self.scene.frame_end = 100

    def empty(self, name, location=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0)):
        ob = bpy.data.objects.new(name, None)
        self.scene.collection.objects.link(ob)
        ob.location = location
        ob.rotation_euler = rotation
        ob.scale = scale
        return ob

    def select_active(self, ob):
        for item in self.scene.objects:
            item.select_set(False)
        ob.select_set(True)
        bpy.context.view_layer.objects.active = ob
        bpy.context.view_layer.update()

    def world_matrix(self, ob, bone_name=None):
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        ev = ob.evaluated_get(depsgraph)
        if bone_name is not None:
            return (ev.matrix_world @ ev.pose.bones[bone_name].matrix).copy()
        return ev.matrix_world.copy()

    def set_frame(self, frame_value):
        whole = int(frame_value // 1.0)
        sub = float(frame_value - whole)
        self.scene.frame_set(whole, subframe=sub)
        bpy.context.view_layer.update()

    def assertTransformMatches(self, mat_a: Matrix, mat_b: Matrix, check_scale=False, pos_eps=2e-4, rot_eps=4e-4):
        pos_diff = (mat_a.translation - mat_b.translation).length
        self.assertLess(pos_diff, pos_eps, f'Position mismatch: {mat_a.translation} vs {mat_b.translation}')
        qa = mat_a.to_quaternion().normalized()
        qb = mat_b.to_quaternion().normalized()
        dot = min(1.0, abs(qa.dot(qb)))
        angle = 2.0 * acos(dot)
        self.assertLess(angle, rot_eps, f'Rotation mismatch: {angle} rad')
        if check_scale:
            scale_diff = (mat_a.to_scale() - mat_b.to_scale()).length
            self.assertLess(scale_diff, 2e-4, f'Scale mismatch: {mat_a.to_scale()} vs {mat_b.to_scale()}')

    def add_childof(self, owner, target, subtarget='', name='Child Of'):
        con = owner.constraints.new('CHILD_OF')
        con.name = name
        con.target = target
        if subtarget:
            con.subtarget = subtarget
        bpy.context.view_layer.update()
        return con

    def build_rig(self, name='Rig', connected_child=False):
        arm = bpy.data.armatures.new(name)
        ob = bpy.data.objects.new(name, arm)
        self.scene.collection.objects.link(ob)
        self.select_active(ob)
        bpy.ops.object.mode_set(mode='EDIT')
        root = arm.edit_bones.new('Root')
        root.head, root.tail, root.roll = (0.0, 0.0, 0.0), (0.0, 1.5, 0.5), 0.25
        child = arm.edit_bones.new('PropBone')
        child.head = root.tail if connected_child else (0.5, 2.0, 0.8)
        child.tail = (0.8, 3.2, 1.2)
        child.roll = -0.4
        child.parent = root
        child.use_connect = connected_child
        bpy.ops.object.mode_set(mode='OBJECT')
        ob.location = (1.5, -2.0, 0.8)
        ob.rotation_euler = (0.2, -0.3, 0.4)
        ob.scale = (1.2, 1.2, 1.2)
        for pchan in ob.pose.bones:
            pchan.rotation_mode = 'XYZ'
        bpy.context.view_layer.update()
        return ob

    def test_builtin_registration_and_defaults(self):
        self.assertTrue(hasattr(bpy.types, 'CONSTRAINT_OT_island_childof_space_switch'))
        self.assertTrue(hasattr(bpy.types, 'ANIM_OT_island_space_switch_set_range'))
        self.assertTrue(hasattr(bpy.types, 'VIEW3D_PT_island_space_switch'))
        self.assertTrue(hasattr(bpy.types, 'DOPESHEET_PT_island_space_switch'))
        self.assertTrue(hasattr(bpy.types, 'GRAPH_PT_island_space_switch'))
        settings = self.scene.island_space_switch
        self.assertEqual(settings.mode, 'CURRENT')
        self.assertTrue(settings.use_guard_key)
        self.assertAlmostEqual(settings.target_influence, 0.0)

    def test_object_release_move_regrab_without_touching_inverse_matrix(self):
        hand = self.empty('Hand', (10.0, 0.0, 0.0))
        prop = self.empty('Prop', (12.0, 1.0, -0.5), (0.3, -0.2, 0.5))
        for frame, pos, rot in (
            (1, (10.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
            (11, (16.0, 3.0, 1.0), (0.4, 0.2, -0.6)),
            (21, (22.0, -4.0, 2.5), (-0.5, 0.7, 0.3)),
            (31, (26.0, -1.0, 4.0), (0.2, 0.9, -0.4)),
        ):
            hand.location = pos
            hand.rotation_euler = rot
            hand.keyframe_insert('location', frame=frame)
            hand.keyframe_insert('rotation_euler', frame=frame)

        self.set_frame(1)
        con = self.add_childof(prop, hand)
        initial_inv = con.inverse_matrix.copy()
        self.select_active(prop)

        # Record pre-switch trajectory on frames 1..11 (including motion-blur subframe 10.5).
        pre_worlds = {}
        for f in (1, 5, 10, 10.5, 11):
            self.set_frame(f)
            pre_worlds[f] = self.world_matrix(prop)

        # Switch 1 -> 0 at frame 11 (release).
        self.set_frame(11)
        res = bpy.ops.constraint.island_childof_space_switch(
            action='DISABLE', constraint_name=con.name, owner_type='OBJECT'
        )
        self.assertEqual(res, {'FINISHED'})
        self.assertEqual(con.inverse_matrix, initial_inv)

        # Frames 1..11 (including 10.5!) must be untouched; frames 12..20 stay still at frame 11's world pose.
        held_world = pre_worlds[11]
        for f in (1, 5, 10, 10.5, 11):
            self.set_frame(f)
            self.assertTransformMatches(self.world_matrix(prop), pre_worlds[f])
        for f in (12, 16, 20):
            self.set_frame(f)
            self.assertTransformMatches(self.world_matrix(prop), held_world)

        # Switch 0 -> 1 at frame 21 (re-grab hand at new offset without modifying inverse_matrix!).
        self.set_frame(21)
        world_at_21 = self.world_matrix(prop)
        self.assertTransformMatches(world_at_21, held_world)
        res = bpy.ops.constraint.island_childof_space_switch(
            action='ENABLE', constraint_name=con.name, owner_type='OBJECT'
        )
        self.assertEqual(res, {'FINISHED'})
        self.assertEqual(con.inverse_matrix, initial_inv)
        self.assertTransformMatches(self.world_matrix(prop), world_at_21)

        # Scrub out-of-order and verify frame 31 moved with Hand while earlier segments stayed intact.
        self.set_frame(31)
        world_at_31 = self.world_matrix(prop)
        self.assertGreater((world_at_31.translation - world_at_21.translation).length, 1.0)
        for f in (21, 5, 15, 11, 10.5, 1):
            self.set_frame(f)
            expected = pre_worlds[f] if f <= 11 else held_world
            self.assertTransformMatches(self.world_matrix(prop), expected)

        # Unscaled target must NOT create unnecessary scale F-Curves.
        self.assertIsNone(find_curve(prop, 'scale', 0))

    def test_bezier_curve_split_preserves_subframes_and_prevents_overshoot(self):
        target = self.empty('Target', (5.0, 0.0, 0.0))
        prop = self.empty('Prop', (5.0, 0.0, 0.0))
        con = self.add_childof(prop, target)
        self.select_active(prop)

        # Author a smooth Bezier curve on Prop from frame 1 to frame 30, plus target motion.
        for f, x in ((1, 0.0), (30, 6.0)):
            prop.location = (x, -0.5 * x, 0.25 * x)
            prop.keyframe_insert('location', frame=f)
        for f, tx in ((1, 5.0), (20, 15.0)):
            target.location = (tx, 2.0, -1.0)
            target.keyframe_insert('location', frame=f)

        sample_times = [1.0, 4.25, 9.5, 14.75, 18.5, 19.0, 20.0]
        expected_before = {}
        for t in sample_times:
            self.set_frame(t)
            expected_before[t] = self.world_matrix(prop)

        # Switch at frame 20 (mid-Bezier segment!).
        self.set_frame(20)
        # Remove keys > 20 on prop first to test clean forward ease-out without overshoot.
        bag = channelbag(prop)
        for fc in bag.fcurves:
            fc.keyframe_points.remove(fc.keyframe_points[-1])
            fc.update()
        # Re-insert frame 30 so we test splitting [1, 30] directly!
        prop.location = (6.0, -3.0, 1.5)
        prop.keyframe_insert('location', frame=30)
        for t in sample_times:
            self.set_frame(t)
            expected_before[t] = self.world_matrix(prop)

        self.set_frame(20)
        self.assertEqual(
            bpy.ops.constraint.island_childof_space_switch(action='DISABLE', constraint_name=con.name),
            {'FINISHED'},
        )

        # Every integer and fractional frame <= 19 AND frame 20 must match to < 1e-4!
        for t in sample_times:
            self.set_frame(t)
            self.assertTransformMatches(self.world_matrix(prop), expected_before[t])

        # Add a small move at frame 30 (+1.0 in X) and verify no Bezier overshoot from the +10 jump at 19->20!
        self.set_frame(20)
        x20 = prop.location.x
        self.set_frame(30)
        prop.location.x = x20 + 1.0
        prop.keyframe_insert('location', index=0, frame=30)
        for f in range(20, 31):
            self.set_frame(f)
            self.assertGreaterEqual(prop.location.x, x20 - 1e-4)
            self.assertLessEqual(prop.location.x, x20 + 1.0 + 1e-4)

    def test_frame_start_skips_stray_frame_zero_key_unless_preroll_exists(self):
        target = self.empty('Target', (3.0, 4.0, 5.0))
        prop = self.empty('Prop', (4.0, 2.0, 1.0))
        con = self.add_childof(prop, target)
        self.select_active(prop)

        self.set_frame(1)
        before = self.world_matrix(prop)
        self.assertEqual(
            bpy.ops.constraint.island_childof_space_switch(action='DISABLE', constraint_name=con.name),
            {'FINISHED'},
        )
        self.assertTransformMatches(self.world_matrix(prop), before)
        fc_inf = find_curve(prop, con.path_from_id('influence'))
        self.assertEqual([round(k.co.x, 3) for k in fc_inf.keyframe_points], [1.0])

        # With pre-roll animation at frame -5, switching at frame 1 DOES guard frame 0.
        target.location.x = 10.0
        target.keyframe_insert('location', frame=-5)
        target.location.x = 3.0
        target.keyframe_insert('location', frame=1)
        self.set_frame(1)
        self.assertEqual(
            bpy.ops.constraint.island_childof_space_switch(action='ENABLE', constraint_name=con.name),
            {'FINISHED'},
        )
        self.assertEqual([round(k.co.x, 3) for k in fc_inf.keyframe_points], [0.0, 1.0])

    def test_consecutive_frames_and_same_frame_reswitch_and_unkeyed_pose(self):
        target = self.empty('Target', (2.0, 0.0, 0.0))
        prop = self.empty('Prop', (3.0, 1.0, 0.0))
        con = self.add_childof(prop, target)
        self.select_active(prop)

        for f in (1, 10, 11, 12, 20):
            target.location = (2.0 + f, -0.5 * f, 0.2 * f)
            target.rotation_euler = (0.05 * f, -0.03 * f, 0.08 * f)
            target.keyframe_insert('location', frame=f)
            target.keyframe_insert('rotation_euler', frame=f)

        # Consecutive switches on frames 10, 11, 12.
        snaps = {}
        for f, act in ((10, 'DISABLE'), (11, 'ENABLE'), (12, 'DISABLE')):
            self.set_frame(f)
            snaps[f] = self.world_matrix(prop)
            self.assertEqual(
                bpy.ops.constraint.island_childof_space_switch(action=act, constraint_name=con.name),
                {'FINISHED'},
            )
            self.assertTransformMatches(self.world_matrix(prop), snaps[f])

        for f in (10, 11, 12):
            self.set_frame(f)
            self.assertTransformMatches(self.world_matrix(prop), snaps[f])

        # Repeated switches on the SAME frame (frame 15) with custom weights and unkeyed pose edit.
        self.set_frame(15)
        prop.location += Vector((1.5, -2.0, 0.7))
        prop.rotation_euler.z += 0.6
        live_world = self.world_matrix(prop)
        for act, w in (('CUSTOM', 0.4), ('ENABLE', 1.0), ('CUSTOM', 0.75), ('DISABLE', 0.0)):
            self.assertEqual(
                bpy.ops.constraint.island_childof_space_switch(
                    action=act, target_influence=w, constraint_name=con.name
                ),
                {'FINISHED'},
            )
            self.assertTransformMatches(self.world_matrix(prop), live_world)

    def test_pose_bone_and_multi_childof_exclusive_handoff(self):
        rig = self.build_rig('HeroRig', connected_child=False)
        hand_l = self.empty('Hand_L', (-2.0, 3.0, 1.0), (0.3, 0.1, -0.4))
        hand_r = self.empty('Hand_R', (4.0, 2.0, 1.5), (-0.2, 0.5, 0.3))
        table = self.empty('Table', (0.0, 5.0, 0.5), (0.0, 0.0, 1.2))

        for f in (1, 10, 20, 30):
            hand_l.location.x = -2.0 + 0.3 * f
            hand_l.rotation_euler.z = -0.4 + 0.04 * f
            hand_l.keyframe_insert('location', frame=f)
            hand_l.keyframe_insert('rotation_euler', frame=f)
            hand_r.location.y = 2.0 + 0.25 * f
            hand_r.rotation_euler.x = -0.2 + 0.03 * f
            hand_r.keyframe_insert('location', frame=f)
            hand_r.keyframe_insert('rotation_euler', frame=f)

        self.select_active(rig)
        bpy.ops.object.mode_set(mode='POSE')
        pchan = rig.pose.bones['PropBone']
        rig.data.bones.active = pchan.bone
        pchan.select = True

        self.set_frame(1)
        con_l = self.add_childof(pchan, hand_l, name='ChildOf_L')
        con_r = self.add_childof(pchan, hand_r, name='ChildOf_R')
        con_t = self.add_childof(pchan, table, name='ChildOf_Table')
        con_r.influence = 0.0
        con_t.influence = 0.0
        con_l.keyframe_insert('influence', frame=1)
        con_r.keyframe_insert('influence', frame=1)
        con_t.keyframe_insert('influence', frame=1)

        # Also add an Object-level Child Of with the SAME name on Rig to verify pointer/owner_type disambiguation!
        obj_driver = self.empty('StagePlatform', (0.0, 0.0, 0.0))
        obj_con = self.add_childof(rig, obj_driver, name='ChildOf_R')
        obj_con.influence = 1.0

        # Frame 10: Handoff from Hand_L -> Hand_R via EXCLUSIVE.
        self.set_frame(10)
        w10 = self.world_matrix(rig, 'PropBone')
        self.assertEqual(
            bpy.ops.constraint.island_childof_space_switch(
                action='EXCLUSIVE', constraint_name='ChildOf_R', owner_type='BONE'
            ),
            {'FINISHED'},
        )
        self.assertAlmostEqual(con_l.influence, 0.0)
        self.assertAlmostEqual(con_r.influence, 1.0)
        self.assertAlmostEqual(con_t.influence, 0.0)
        self.assertAlmostEqual(obj_con.influence, 1.0)
        self.assertTransformMatches(self.world_matrix(rig, 'PropBone'), w10)

        # Frame 20: Handoff from Hand_R -> Table via EXCLUSIVE.
        self.set_frame(20)
        w20 = self.world_matrix(rig, 'PropBone')
        self.assertEqual(
            bpy.ops.constraint.island_childof_space_switch(
                action='EXCLUSIVE', constraint_name='ChildOf_Table', owner_type='BONE'
            ),
            {'FINISHED'},
        )
        self.assertTransformMatches(self.world_matrix(rig, 'PropBone'), w20)

        # Frame 30: Release ALL bone Child Of constraints via RELEASE_ALL.
        self.set_frame(30)
        w30 = self.world_matrix(rig, 'PropBone')
        self.assertEqual(
            bpy.ops.constraint.island_childof_space_switch(action='RELEASE_ALL', owner_type='BONE'),
            {'FINISHED'},
        )
        self.assertAlmostEqual(con_l.influence, 0.0)
        self.assertAlmostEqual(con_r.influence, 0.0)
        self.assertAlmostEqual(con_t.influence, 0.0)
        self.assertTransformMatches(self.world_matrix(rig, 'PropBone'), w30)

        # Re-verify earlier switch frames 10 & 20 stayed intact!
        self.set_frame(10)
        self.assertTransformMatches(self.world_matrix(rig, 'PropBone'), w10)
        self.set_frame(20)
        self.assertTransformMatches(self.world_matrix(rig, 'PropBone'), w20)

    def test_connected_bone_succeeds_and_locked_bone_rolls_back(self):
        rig = self.build_rig('ConnectedRig', connected_child=True)
        target = self.empty('LookTarget', (5.0, -3.0, 2.0), (0.6, -0.4, 0.8))
        self.select_active(rig)
        bpy.ops.object.mode_set(mode='POSE')
        pchan = rig.pose.bones['PropBone']
        rig.data.bones.active = pchan.bone

        con = self.add_childof(pchan, target, name='HeadOrient')
        target.location = (12.0, 8.0, -4.0)
        target.rotation_euler = (-0.5, 0.9, 0.3)
        bpy.context.view_layer.update()
        self.set_frame(12)
        before = self.world_matrix(rig, 'PropBone')
        self.assertEqual(
            bpy.ops.constraint.island_childof_space_switch(
                action='DISABLE', constraint_name='HeadOrient', owner_type='BONE'
            ),
            {'FINISHED'},
        )
        self.assertTransformMatches(self.world_matrix(rig, 'PropBone'), before)
        self.assertIsNone(find_curve(rig, pchan.path_from_id('location'), 0))

        # Locking bone rotation when rotation compensation is required must cancel cleanly with zero keys.
        bpy.ops.object.mode_set(mode='OBJECT')
        rig2 = self.build_rig('LockedBoneRig', connected_child=True)
        self.select_active(rig2)
        bpy.ops.object.mode_set(mode='POSE')
        pchan2 = rig2.pose.bones['PropBone']
        rig2.data.bones.active = pchan2.bone
        target.rotation_euler = (0.0, 0.0, 0.0)
        con2 = self.add_childof(pchan2, target, name='LockedChildOf')
        target.rotation_euler = (0.9, -0.7, 0.5)
        pchan2.lock_rotation = (True, True, True)
        bpy.context.view_layer.update()
        before_fail = self.world_matrix(rig2, 'PropBone')
        self.assertEqual(
            bpy.ops.constraint.island_childof_space_switch(
                action='DISABLE', constraint_name='LockedChildOf', owner_type='BONE'
            ),
            {'CANCELLED'},
        )
        self.assertAlmostEqual(con2.influence, 1.0)
        self.assertIsNone(rig2.animation_data)
        self.assertTransformMatches(self.world_matrix(rig2, 'PropBone'), before_fail)

    def test_partial_axes_scaled_target_and_rotation_modes(self):
        target = self.empty('ScaledTarget', (3.0, -2.0, 4.0), (0.4, -0.5, 0.7), (1.6, 1.6, 1.6))
        for rot_mode in ('XYZ', 'ZXY', 'QUATERNION', 'AXIS_ANGLE'):
            target.location = (3.0, -2.0, 4.0)
            target.rotation_euler = (0.4, -0.5, 0.7)
            target.scale = (1.6, 1.6, 1.6)
            bpy.context.view_layer.update()

            prop = self.empty(f'Prop_{rot_mode}', (4.0, 1.0, 2.0))
            prop.rotation_mode = rot_mode
            if rot_mode == 'QUATERNION':
                prop.rotation_quaternion = (-0.8, 0.3, -0.4, 0.31622776)
            con = self.add_childof(prop, target)
            con.use_location_z = False
            self.select_active(prop)

            target.location = (8.0, 3.0, 9.0)
            target.rotation_euler = (-0.7, 0.6, 1.1)
            target.scale = (2.1, 2.1, 2.1)
            self.set_frame(15)
            before = self.world_matrix(prop)

            # Partial influence 0.55 then 0.0 then 1.0, checking scale preservation too!
            for act, w in (('CUSTOM', 0.55), ('DISABLE', 0.0), ('ENABLE', 1.0)):
                self.assertEqual(
                    bpy.ops.constraint.island_childof_space_switch(
                        action=act, target_influence=w, constraint_name=con.name
                    ),
                    {'FINISHED'},
                )
                self.assertTransformMatches(self.world_matrix(prop), before, check_scale=True)
            self.assertIsNotNone(find_curve(prop, 'scale', 0))

    def test_range_mode_preserves_entire_timeline_animation(self):
        hand = self.empty('HandRange', (0.0, 0.0, 0.0))
        sword = self.empty('SwordRange', (1.0, 0.5, 0.0))
        con = self.add_childof(sword, hand)
        self.select_active(sword)

        for f in (1, 15, 30, 45):
            hand.location = (0.4 * f, -0.2 * f, 0.1 * f)
            hand.rotation_euler = (0.03 * f, 0.05 * f, -0.04 * f)
            hand.keyframe_insert('location', frame=f)
            hand.keyframe_insert('rotation_euler', frame=f)
            sword.location = (1.0 + 0.05 * f, 0.5, -0.03 * f)
            sword.rotation_euler = (0.0, 0.02 * f, 0.1 * f)
            sword.keyframe_insert('location', frame=f)
            sword.keyframe_insert('rotation_euler', frame=f)

        con.influence = 1.0
        con.keyframe_insert('influence', frame=1)

        # Record exact world matrices across the ENTIRE timeline [1..45] before switching [15..30]!
        baseline = {}
        for f in range(1, 46):
            self.set_frame(f)
            baseline[f] = self.world_matrix(sword)

        # Test helper operator for range selection from selected keys.
        bag = channelbag(sword)
        for fc in bag.fcurves:
            for k in fc.keyframe_points:
                k.select_control_point = int(round(k.co.x)) in {15, 30}
        self.assertEqual(bpy.ops.anim.island_space_switch_set_range(source='SELECTED_KEYS'), {'FINISHED'})
        self.assertEqual(self.scene.island_space_switch.frame_start, 15)
        self.assertEqual(self.scene.island_space_switch.frame_end, 30)

        self.set_frame(22)
        self.assertEqual(
            bpy.ops.constraint.island_childof_space_switch(
                action='DISABLE',
                constraint_name=con.name,
                mode='RANGE',
                frame_start=15,
                frame_end=30,
            ),
            {'FINISHED'},
        )
        self.assertEqual(self.scene.frame_current, 22)

        # Verify weights AND world transforms across all 45 frames!
        for f in range(1, 46):
            self.set_frame(f)
            expected_inf = 0.0 if 15 <= f <= 30 else 1.0
            self.assertAlmostEqual(con.influence, expected_inf, places=5, msg=f'Frame {f} influence')
            self.assertTransformMatches(self.world_matrix(sword), baseline[f])

    def test_safety_guards_undo_and_blend_persistence(self):
        bpy.ops.wm.read_factory_settings()
        self.scene = bpy.context.scene
        bpy.context.preferences.edit.use_global_undo = True

        target = self.empty('SafeTarget', (4.0, 0.0, 0.0))
        prop = self.empty('SafeProp', (5.0, 2.0, 0.0))
        con = self.add_childof(prop, target)
        self.select_active(prop)

        target.location.x = 12.0
        self.set_frame(10)
        before = self.world_matrix(prop)

        # 1. Locked X location blocks compensation and rolls back cleanly without creating Action.
        prop.lock_location[0] = True
        self.assertEqual(
            bpy.ops.constraint.island_childof_space_switch(action='DISABLE', constraint_name=con.name),
            {'CANCELLED'},
        )
        self.assertAlmostEqual(con.influence, 1.0)
        self.assertIsNone(prop.animation_data)
        prop.lock_location[0] = False

        # 2. Muted constraint rejected cleanly.
        con.mute = True
        self.assertEqual(
            bpy.ops.constraint.island_childof_space_switch(action='DISABLE', constraint_name=con.name),
            {'CANCELLED'},
        )
        con.mute = False

        # 3. Driven influence rejected cleanly.
        drv = con.driver_add('influence')
        drv.driver.expression = '1.0'
        self.assertEqual(
            bpy.ops.constraint.island_childof_space_switch(action='DISABLE', constraint_name=con.name),
            {'CANCELLED'},
        )
        con.driver_remove('influence')
        con.influence = 1.0

        # 4. Successful switch + Undo + Redo + Save/Reload persistence.
        bpy.ops.ed.undo_push(message='Initial Space Switch Fixture')
        bpy.ops.ed.undo_push(message='Before Space Switch')
        self.assertEqual(
            bpy.ops.constraint.island_childof_space_switch(
                'EXEC_DEFAULT', True, action='DISABLE', constraint_name=con.name
            ),
            {'FINISHED'},
        )
        self.assertAlmostEqual(con.influence, 0.0)
        self.assertTransformMatches(self.world_matrix(prop), before)

        self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
        self.scene = bpy.context.scene
        prop = bpy.data.objects['SafeProp']
        con = prop.constraints['Child Of']
        self.set_frame(10)
        self.assertAlmostEqual(con.influence, 1.0)

        self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
        self.scene = bpy.context.scene
        prop = bpy.data.objects['SafeProp']
        con = prop.constraints['Child Of']
        self.set_frame(10)
        self.assertAlmostEqual(con.influence, 0.0)
        self.assertTransformMatches(self.world_matrix(prop), before)

        with tempfile.TemporaryDirectory() as tmp:
            blend_path = str(Path(tmp) / 'space_switch_test.blend')
            bpy.ops.wm.save_as_mainfile(filepath=blend_path)
            bpy.ops.wm.open_mainfile(filepath=blend_path)
            reloaded_prop = bpy.data.objects['SafeProp']
            reloaded_con = reloaded_prop.constraints['Child Of']
            bpy.context.scene.frame_set(10)
            self.assertAlmostEqual(reloaded_con.influence, 0.0)
            self.assertTransformMatches(self.world_matrix(reloaded_prop), before)

    def test_intra_armature_bone_to_bone_handoff_and_inbetween_compatibility(self):
        arm = bpy.data.armatures.new('CharRig')
        rig = bpy.data.objects.new('CharRig', arm)
        self.scene.collection.objects.link(rig)
        self.select_active(rig)
        bpy.ops.object.mode_set(mode='EDIT')
        for bname, head, tail in (
            ('Hand_L', (-1.5, 0.0, 1.0), (-1.5, 0.5, 1.0)),
            ('Hand_R', (1.5, 0.0, 1.0), (1.5, 0.5, 1.0)),
            ('Weapon', (-1.4, 0.2, 1.1), (-1.4, 1.0, 1.1)),
        ):
            eb = arm.edit_bones.new(bname)
            eb.head, eb.tail = head, tail
        bpy.ops.object.mode_set(mode='POSE')

        hand_l = rig.pose.bones['Hand_L']
        hand_r = rig.pose.bones['Hand_R']
        weapon = rig.pose.bones['Weapon']
        for pchan in (hand_l, hand_r, weapon):
            pchan.rotation_mode = 'XYZ'

        for f in (1, 11, 21):
            hand_l.location = (0.2 * f, 0.1 * f, 0.0)
            hand_l.rotation_euler = (0.0, 0.0, 0.05 * f)
            hand_l.keyframe_insert('location', frame=f)
            hand_l.keyframe_insert('rotation_euler', frame=f)
            hand_r.location = (-0.15 * f, 0.2 * f, 0.05 * f)
            hand_r.rotation_euler = (0.04 * f, 0.0, -0.03 * f)
            hand_r.keyframe_insert('location', frame=f)
            hand_r.keyframe_insert('rotation_euler', frame=f)

        self.set_frame(1)
        con_l = self.add_childof(weapon, rig, subtarget='Hand_L', name='ChildOf_HandL')
        con_r = self.add_childof(weapon, rig, subtarget='Hand_R', name='ChildOf_HandR')
        con_r.influence = 0.0
        con_l.keyframe_insert('influence', frame=1)
        con_r.keyframe_insert('influence', frame=1)

        rig.data.bones.active = weapon.bone
        for pb in rig.pose.bones:
            pb.select = True

        self.set_frame(11)
        w11_before = self.world_matrix(rig, 'Weapon')
        self.assertEqual(
            bpy.ops.constraint.island_childof_space_switch(
                action='EXCLUSIVE', constraint_name='ChildOf_HandR', owner_type='BONE'
            ),
            {'FINISHED'},
        )
        self.assertTransformMatches(self.world_matrix(rig, 'Weapon'), w11_before)

        # Insert 3 empty inbetween frames at frame 5 using Island Inbetween: switch pair (10, 11) -> (13, 14)!
        if hasattr(bpy.ops, 'anim') and hasattr(bpy.ops.anim, 'island_inbetween'):
            self.set_frame(5)
            self.assertEqual(
                bpy.ops.anim.island_inbetween(operation='INSERT', frames=3, mode='CURRENT', scope='CONTEXT'),
                {'FINISHED'},
            )
            self.set_frame(14)
            self.assertAlmostEqual(con_l.influence, 0.0)
            self.assertAlmostEqual(con_r.influence, 1.0)
            self.assertTransformMatches(self.world_matrix(rig, 'Weapon'), w11_before)

    def test_ui_draw_helpers_execute_without_errors(self):
        from bl_ui.space_island_space_switch import draw_childof_constraint_ui, draw_space_switch_panel

        class MockOpProps:
            def __init__(self, idname):
                self._idname = idname

        class MockLayout:
            def __init__(self, ops_log):
                self.ops_log = ops_log
                self.use_property_split = False
                self.use_property_decorate = False
                self.enabled = True
                self.scale_y = 1.0

            def row(self, align=False):
                return MockLayout(self.ops_log)

            def column(self, align=False):
                return MockLayout(self.ops_log)

            def box(self):
                return MockLayout(self.ops_log)

            def separator(self):
                pass

            def label(self, text='', icon='NONE'):
                pass

            def prop(self, data, prop_name, text='', slider=False):
                getattr(data, prop_name)

            def operator(self, idname, text='', icon='NONE', depress=False):
                op = MockOpProps(idname)
                self.ops_log.append(op)
                return op

        t1 = self.empty('T1', (1, 0, 0))
        t2 = self.empty('T2', (2, 0, 0))
        prop = self.empty('UIProp', (0, 0, 0))
        c1 = self.add_childof(prop, t1, name='CO_1')
        self.add_childof(prop, t2, name='CO_2')
        self.select_active(prop)

        for mode in ('CURRENT', 'RANGE'):
            self.scene.island_space_switch.mode = mode
            ops_log = []
            draw_childof_constraint_ui(MockLayout(ops_log), bpy.context, c1)
            actions = [getattr(op, 'action', None) for op in ops_log if op._idname == 'constraint.island_childof_space_switch']
            self.assertEqual(actions, ['ENABLE', 'DISABLE', 'EXCLUSIVE', 'CUSTOM'])

            panel_ops = []
            draw_space_switch_panel(MockLayout(panel_ops), bpy.context)
            panel_actions = [
                getattr(op, 'action', None)
                for op in panel_ops
                if op._idname == 'constraint.island_childof_space_switch'
            ]
            self.assertIn('RELEASE_ALL', panel_actions)
            self.assertIn('EXCLUSIVE', panel_actions)

    def test_direct_native_influence_slider_auto_compensation_and_drag_zero_drift(self):
        import bl_operators.island_space_switch as ss_mod

        # 0. Raw Python script assignment (from_python=True without UI simulation) must NOT mutate local channels
        # so importers, Rigify, and other Python tools behave identically to standard Blender.
        rig = self.build_rig('StaticFPSRig', connected_child=False)
        mag = self.empty('MagStatic', (2.5, -1.0, 1.2), (0.3, -0.4, 0.5))
        self.select_active(rig)
        bpy.ops.object.mode_set(mode='POSE')
        hand = rig.pose.bones['PropBone']
        hand.rotation_mode = 'QUATERNION'
        con = self.add_childof(hand, mag, name='Child Of')
        con.owner_space = 'POSE'
        bpy.context.view_layer.update()

        mag.location = (6.0, 2.5, -0.5)
        mag.rotation_euler = (-0.6, 0.5, 0.9)
        bpy.context.view_layer.update()

        raw_loc_before = Vector(hand.location)
        con.influence = 0.0
        self.assertEqual(Vector(hand.location), raw_loc_before)
        con.influence = 1.0

        # Enable UI-slider C++ hook simulation for headless testing of interactive slider drags/inputs.
        ss_mod._FORCE_PYTHON_AUTO_CHILDOF_SWITCH = True
        try:
            # 1. Static unkeyed setup with Auto-Keying OFF: compensates in-place WITHOUT creating unwanted Action.
            self.scene.tool_settings.use_keyframe_insert_auto = False
            static_world = self.world_matrix(rig, 'PropBone')
            con.influence = 0.0
            self.assertTransformMatches(self.world_matrix(rig, 'PropBone'), static_world, pos_eps=1e-5, rot_eps=1e-5)
            self.assertIsNone(rig.animation_data)
            con.influence = 1.0
            self.assertTransformMatches(self.world_matrix(rig, 'PropBone'), static_world, pos_eps=1e-5, rot_eps=1e-5)
            self.assertIsNone(rig.animation_data)

            # 2. Animated rig with Auto-Keying ON + multi-step slider drag at frame 11: zero cumulative drift + guard key!
            self.scene.tool_settings.use_keyframe_insert_auto = True
            for f, pos, rot in (
                (1, (2.5, -1.0, 1.2), (0.3, -0.4, 0.5)),
                (10, (5.0, 1.5, 2.0), (0.5, 0.2, -0.3)),
                (11, (5.4, 1.8, 1.7), (0.55, 0.28, -0.2)),
                (20, (9.0, -2.0, 0.3), (-0.4, 0.7, 0.6)),
            ):
                mag.location = pos
                mag.rotation_euler = rot
                mag.keyframe_insert('location', frame=f)
                mag.keyframe_insert('rotation_euler', frame=f)
            self.set_frame(1)
            hand.keyframe_insert('location', frame=1)
            hand.keyframe_insert('rotation_quaternion', frame=1)
            self.set_frame(10)
            hand.keyframe_insert('location', frame=10)
            hand.keyframe_insert('rotation_quaternion', frame=10)

            pre_switch = {}
            for t in (1, 5, 10, 10.5, 11):
                self.set_frame(t)
                pre_switch[t] = self.world_matrix(rig, 'PropBone')

            # Simulate continuous mouse drag across Influence slider at frame 11: 1.0 -> 0.85 -> 0.55 -> 0.2 -> 0.0
            self.set_frame(11)
            for drag_val in (0.85, 0.55, 0.2, 0.0):
                con.influence = drag_val
                self.assertTransformMatches(
                    self.world_matrix(rig, 'PropBone'), pre_switch[11], pos_eps=2e-5, rot_eps=2e-5
                )

            fc_inf = find_curve(rig, con.path_from_id('influence'))
            self.assertIsNotNone(fc_inf)
            self.assertEqual([round(k.co.x, 3) for k in fc_inf.keyframe_points], [10.0, 11.0])
            self.assertTrue(all(k.interpolation == 'CONSTANT' for k in fc_inf.keyframe_points))

            for t in (1, 5, 10, 10.5, 11):
                self.set_frame(t)
                self.assertTransformMatches(self.world_matrix(rig, 'PropBone'), pre_switch[t], pos_eps=5e-5, rot_eps=5e-5)
            for t in (12, 16, 20):
                self.set_frame(t)
                self.assertTransformMatches(self.world_matrix(rig, 'PropBone'), pre_switch[11], pos_eps=5e-5, rot_eps=5e-5)

            # Re-grab at frame 20 via direct con.influence = 1.0!
            self.set_frame(20)
            con.influence = 1.0
            self.assertTransformMatches(self.world_matrix(rig, 'PropBone'), pre_switch[11], pos_eps=5e-5, rot_eps=5e-5)

            # Opting out via auto_influence_compensate = False leaves local channels uncompensated when desired.
            self.scene.island_space_switch.auto_influence_compensate = False
            loc_before_raw = Vector(hand.location)
            con.influence = 0.0
            self.assertLess((Vector(hand.location) - loc_before_raw).length, 1e-6)
            self.scene.island_space_switch.auto_influence_compensate = True
        finally:
            ss_mod._FORCE_PYTHON_AUTO_CHILDOF_SWITCH = False

    def test_library_override_rig_box_selected_bone_and_null_target_sibling(self):
        from bl_ui.space_island_space_switch import draw_space_switch_panel

        with tempfile.TemporaryDirectory() as tmp:
            lib_path = str(Path(tmp) / 'fp_arms_lib.blend')

            # Author library rig in a clean file and save it.
            bpy.ops.wm.read_factory_settings(use_empty=True)
            arm = bpy.data.armatures.new('FP_Arms_Data')
            rig_src = bpy.data.objects.new('Armature', arm)
            bpy.context.scene.collection.objects.link(rig_src)
            bpy.context.view_layer.objects.active = rig_src
            rig_src.select_set(True)
            bpy.ops.object.mode_set(mode='EDIT')
            for bname, head, tail, parent_name in (
                ('Root', (0.0, 0.0, 0.0), (0.0, 0.0, 0.5), None),
                ('Hand.L', (-0.3, 0.4, 1.2), (-0.3, 0.55, 1.2), 'Root'),
                ('Thumb.L', (-0.25, 0.55, 1.2), (-0.2, 0.62, 1.2), 'Hand.L'),
                ('Hand.R', (0.3, 0.4, 1.2), (0.3, 0.55, 1.2), 'Root'),
            ):
                eb = arm.edit_bones.new(bname)
                eb.head, eb.tail = head, tail
                if parent_name:
                    eb.parent = arm.edit_bones[parent_name]
            bpy.ops.object.mode_set(mode='POSE')
            for pb in rig_src.pose.bones:
                pb.rotation_mode = 'QUATERNION'
            bpy.ops.object.mode_set(mode='OBJECT')
            coll = bpy.data.collections.new('RigCol')
            coll.objects.link(rig_src)
            bpy.context.scene.collection.children.link(coll)
            bpy.context.scene.collection.objects.unlink(rig_src)
            bpy.ops.wm.save_as_mainfile(filepath=lib_path)

            # Link collection into a fresh animation scene and create a Library Override.
            bpy.ops.wm.read_factory_settings(use_empty=True)
            self.scene = bpy.context.scene
            with bpy.data.libraries.load(lib_path, link=True) as (data_from, data_to):
                data_to.collections = ['RigCol']
            linked_coll = data_to.collections[0]
            self.scene.collection.children.link(linked_coll)
            bpy.context.view_layer.update()
            overridden_coll = linked_coll.override_hierarchy_create(
                self.scene, bpy.context.view_layer, do_fully_editable=True
            )
            bpy.context.view_layer.update()
            rig = next(ob for ob in overridden_coll.objects if ob.type == 'ARMATURE')
            self.assertIsNotNone(rig.override_library)
            self.assertTrue(rig.is_editable)

            mag = self.empty('mag', (-0.25, 0.45, 1.15), (0.1, -0.2, 0.3))
            weapon = self.empty('weapon', (0.25, 0.45, 1.15), (0.0, 0.1, -0.1))

            self.select_active(rig)
            bpy.ops.object.mode_set(mode='POSE')
            hand_l = rig.pose.bones['Hand.L']
            hand_r = rig.pose.bones['Hand.R']
            thumb_l = rig.pose.bones['Thumb.L']

            con_l = self.add_childof(hand_l, mag, name='Child Of')
            con_l.owner_space = 'POSE'
            con_r_valid = self.add_childof(hand_r, weapon, name='子级')
            con_r_valid.owner_space = 'POSE'
            con_r_null = hand_r.constraints.new('CHILD_OF')
            con_r_null.name = 'Child Of'
            con_r_null.influence = 0.0
            bpy.context.view_layer.update()

            for f, mpos, mrot in ((1, (-0.25, 0.45, 1.15), (0.1, -0.2, 0.3)), (11, (-0.5, 0.2, 0.85), (0.4, 0.2, -0.5))):
                mag.location = mpos
                mag.rotation_euler = mrot
                mag.keyframe_insert('location', frame=f)
                mag.keyframe_insert('rotation_euler', frame=f)

            self.scene.tool_settings.use_keyframe_insert_auto = True
            self.set_frame(1)
            hand_l.keyframe_insert('location', frame=1)
            hand_l.keyframe_insert('rotation_quaternion', frame=1)

            # Reproduce box-selection state: bones.active is Thumb.L while ONLY Hand.L is selected!
            for pb in rig.pose.bones:
                pb.select = False
            hand_l.select = True
            rig.data.bones.active = thumb_l.bone
            bpy.context.view_layer.update()

            self.set_frame(11)
            w_hand_before = self.world_matrix(rig, 'Hand.L')
            w_thumb_before = self.world_matrix(rig, 'Thumb.L')

            # A) Operator with AUTO owner_type and no explicit bone_name still resolves box-selected Hand.L!
            self.assertEqual(
                bpy.ops.constraint.island_childof_space_switch(action='DISABLE', owner_type='AUTO'),
                {'FINISHED'},
            )
            self.assertAlmostEqual(con_l.influence, 0.0)
            self.assertTransformMatches(self.world_matrix(rig, 'Hand.L'), w_hand_before, pos_eps=2e-5, rot_eps=2e-5)
            self.assertTransformMatches(self.world_matrix(rig, 'Thumb.L'), w_thumb_before, pos_eps=2e-5, rot_eps=2e-5)

            # B) Direct native slider edit back to 1.0 on Library Override rig also preserves world pose!
            import bl_operators.island_space_switch as ss_mod
            ss_mod._FORCE_PYTHON_AUTO_CHILDOF_SWITCH = True
            try:
                con_l.influence = 1.0
            finally:
                ss_mod._FORCE_PYTHON_AUTO_CHILDOF_SWITCH = False
            self.assertTransformMatches(self.world_matrix(rig, 'Hand.L'), w_hand_before, pos_eps=2e-5, rot_eps=2e-5)

            # C) N-panel draw finds Hand.L despite active_pose_bone == Thumb.L and binds bone_name='Hand.L'.
            class QuickOp:
                def __init__(self, idname):
                    self._idname = idname

            class QuickLayout:
                def __init__(self, ops):
                    self.ops = ops
                    self.use_property_split = False
                    self.use_property_decorate = False
                    self.enabled = True
                    self.scale_y = 1.0

                def row(self, align=False):
                    return QuickLayout(self.ops)

                def column(self, align=False):
                    return QuickLayout(self.ops)

                def box(self):
                    return QuickLayout(self.ops)

                def separator(self):
                    pass

                def label(self, text='', icon='NONE'):
                    pass

                def prop(self, data, prop_name, text='', slider=False):
                    getattr(data, prop_name)

                def operator(self, idname, text='', icon='NONE', depress=False):
                    op = QuickOp(idname)
                    self.ops.append(op)
                    return op

            panel_ops = []
            draw_space_switch_panel(QuickLayout(panel_ops), bpy.context)
            bone_names = [
                getattr(op, 'bone_name', '')
                for op in panel_ops
                if op._idname == 'constraint.island_childof_space_switch'
            ]
            self.assertTrue(bone_names and all(b == 'Hand.L' for b in bone_names))

            # D) RELEASE_ALL on Hand.R ignores the sibling Child Of with target=None.
            w_hand_r_before = self.world_matrix(rig, 'Hand.R')
            self.assertEqual(
                bpy.ops.constraint.island_childof_space_switch(
                    action='RELEASE_ALL', owner_type='BONE', bone_name='Hand.R'
                ),
                {'FINISHED'},
            )
            self.assertAlmostEqual(con_r_valid.influence, 0.0)
            self.assertTransformMatches(self.world_matrix(rig, 'Hand.R'), w_hand_r_before, pos_eps=2e-5, rot_eps=2e-5)

    def test_disable_keep_transform_operator_on_childof(self):
        rig = self.build_rig('DisableKeepRig', connected_child=False)
        mag = self.empty('MagDisable', (1.0, 2.0, 3.0), (0.2, -0.3, 0.4))
        self.select_active(rig)
        bpy.ops.object.mode_set(mode='POSE')
        hand = rig.pose.bones['PropBone']
        hand.rotation_mode = 'QUATERNION'
        con = self.add_childof(hand, mag, name='Child Of')
        con.owner_space = 'POSE'

        for f, pos, rot in ((1, (1.0, 2.0, 3.0), (0.2, -0.3, 0.4)), (12, (4.5, -1.5, 2.2), (-0.5, 0.6, 0.2))):
            mag.location = pos
            mag.rotation_euler = rot
            mag.keyframe_insert('location', frame=f)
            mag.keyframe_insert('rotation_euler', frame=f)
        self.set_frame(1)
        hand.keyframe_insert('location', frame=1)
        hand.keyframe_insert('rotation_quaternion', frame=1)

        self.set_frame(11)
        w11 = self.world_matrix(rig, 'PropBone')
        self.set_frame(12)
        w12 = self.world_matrix(rig, 'PropBone')

        with bpy.context.temp_override(object=rig, active_object=rig, pose_bone=hand, constraint=con):
            self.assertEqual(bpy.ops.constraint.disable_keep_transform(), {'FINISHED'})

        self.assertAlmostEqual(con.influence, 0.0)
        self.assertTransformMatches(self.world_matrix(rig, 'PropBone'), w12, pos_eps=2e-5, rot_eps=2e-5)
        self.set_frame(11)
        self.assertTransformMatches(self.world_matrix(rig, 'PropBone'), w11, pos_eps=2e-5, rot_eps=2e-5)


if __name__ == '__main__':
    argv = [sys.argv[0]]
    if '--' in sys.argv:
        argv.extend(sys.argv[sys.argv.index('--') + 1:])
    unittest.main(argv=argv)
