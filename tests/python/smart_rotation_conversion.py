# SPDX-License-Identifier: GPL-2.0-or-later
"""Run with --background --factory-startup --python-exit-code 1 --python this_file.py."""
import math
import unittest
import bpy


def curves(obj):
    ad = obj.animation_data
    return ad.action.layers[0].strips[0].channelbag(ad.action_slot).fcurves


def quaternion(target):
    if target.rotation_mode == 'QUATERNION':
        return target.rotation_quaternion.normalized()
    if target.rotation_mode == 'AXIS_ANGLE':
        from mathutils import Quaternion
        a = target.rotation_axis_angle
        return Quaternion(a[1:], a[0])
    return target.rotation_euler.to_quaternion()


class SmartRotationConversionTest(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.obj = bpy.data.objects.new('Test', None)
        bpy.context.collection.objects.link(self.obj)
        self.obj.select_set(True)
        bpy.context.view_layer.objects.active = self.obj

    def check_animation(self, target, destination, keys, interpolation='LINEAR', source='XYZ'):
        from mathutils import Euler
        target.rotation_mode = source
        path = 'rotation_euler' if source not in ('QUATERNION', 'AXIS_ANGLE') else (
            'rotation_quaternion' if source == 'QUATERNION' else 'rotation_axis_angle')
        for frame, angles in keys:
            if source == 'QUATERNION':
                target.rotation_quaternion = Euler(angles).to_quaternion()
            elif source == 'AXIS_ANGLE':
                axis, angle = Euler(angles).to_quaternion().to_axis_angle()
                target.rotation_axis_angle = (angle, *axis)
            else:
                target.rotation_euler = angles
            target.keyframe_insert(path, frame=frame)
        for fc in curves(self.obj):
            for key in fc.keyframe_points:
                key.interpolation = interpolation
        start, end = keys[0][0], keys[-1][0]
        times = sorted({frame for frame, _ in keys} | set(range(math.ceil(start), math.floor(end) + 1)))
        original = []
        for frame in times:
            bpy.context.scene.frame_set(math.floor(frame), subframe=frame % 1)
            original.append(quaternion(target).copy())
        with bpy.context.temp_override(rotation_mode_target=target):
            self.assertEqual(bpy.ops.anim.rotation_mode_convert(mode=destination), {'FINISHED'})
        self.assertEqual(target.rotation_mode, destination)
        maximum = 0.0
        for frame, expected in zip(times, original):
            bpy.context.scene.frame_set(math.floor(frame), subframe=frame % 1)
            actual = quaternion(target)
            dot = min(1.0, abs(actual.dot(expected)))
            maximum = max(maximum, 2*math.acos(dot))
        self.assertLess(math.degrees(maximum), 0.12)
        rotation_curves = [fc for fc in curves(self.obj) if 'rotation_' in fc.data_path]
        for fc in rotation_curves:
            frames = {round(k.co.x, 4) for k in fc.keyframe_points}
            for frame, _ in keys:
                self.assertIn(frame, frames)
            original_times = {frame for frame, _ in keys}
            for frame in frames - original_times:
                self.assertEqual(frame, round(frame), "New keys must be on integer frames")
        return max(len(fc.keyframe_points) for fc in rotation_curves)

    def test_simple_motion_is_sparse(self):
        count = self.check_animation(self.obj, 'QUATERNION',
                                    [(-10, (0,0,0)), (110, (0,0,1))])
        self.assertLess(count, 25)

    def test_full_turns(self):
        self.check_animation(self.obj, 'QUATERNION',
                             [(1, (0,0,0)), (101, (0,0,4*math.pi))])

    def test_euler_order_with_easing(self):
        self.check_animation(self.obj, 'ZYX',
                             [(1, (0,0,0)), (100, (1.5,0.8,-1.0))], 'BEZIER')

    def test_axis_angle(self):
        self.check_animation(self.obj, 'AXIS_ANGLE',
                             [(1, (0,0,0)), (100, (1.5,0.8,-1.0))])

    def test_axis_angle_full_turns(self):
        self.check_animation(self.obj, 'AXIS_ANGLE',
                             [(1, (0,0,0)), (101, (0,0,4*math.pi))])

    def test_quaternion_to_euler(self):
        self.check_animation(self.obj, 'XYZ',
                             [(1, (0,0,0)), (100, (1.5,0.8,-1.0))], source='QUATERNION')

    def test_axis_angle_to_euler(self):
        self.check_animation(self.obj, 'XYZ',
                             [(1, (0,0,0)), (100, (1.5,0.8,-1.0))], source='AXIS_ANGLE')

    def test_fractional_original_keys_are_preserved(self):
        self.check_animation(self.obj, 'QUATERNION',
                             [(-10.5, (0,0,0)), (100.25, (1.2,0.8,-1.0))], 'BEZIER')

    def test_short_interval_adds_no_fractional_keys(self):
        count = self.check_animation(self.obj, 'QUATERNION',
                                    [(1, (0,0,0)), (2, (1.2,0.8,-1.0))])
        self.assertEqual(count, 2)

    def test_constant(self):
        count = self.check_animation(self.obj, 'QUATERNION',
                                    [(1, (0,0,0)), (100, (0,0,1))], 'CONSTANT')
        self.assertEqual(count, 2)

    def test_bone(self):
        bpy.ops.object.armature_add()
        self.obj = bpy.context.object
        bpy.ops.object.mode_set(mode='POSE')
        self.check_animation(self.obj.pose.bones[0], 'QUATERNION',
                             [(1, (0,0,0)), (100, (0.5,0.8,-1.0))])

    def test_same_mode_keeps_curves(self):
        self.obj.rotation_mode = 'XYZ'
        self.obj.keyframe_insert('rotation_euler', frame=1)
        before = [fc.as_pointer() for fc in curves(self.obj)]
        bpy.ops.anim.rotation_mode_convert(mode='XYZ')
        self.assertEqual(before, [fc.as_pointer() for fc in curves(self.obj)])

    def test_modifier_rejection_is_atomic(self):
        self.obj.rotation_mode = 'XYZ'
        self.obj.keyframe_insert('rotation_euler', frame=1)
        curves(self.obj)[0].modifiers.new('NOISE')
        before = [fc.as_pointer() for fc in curves(self.obj)]
        with self.assertRaises(RuntimeError):
            bpy.ops.anim.rotation_mode_convert(mode='QUATERNION')
        self.assertEqual(self.obj.rotation_mode, 'XYZ')
        self.assertEqual(before, [fc.as_pointer() for fc in curves(self.obj)])


if __name__ == '__main__':
    unittest.main(argv=[__file__])
