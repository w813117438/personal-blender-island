# SPDX-License-Identifier: GPL-2.0-or-later
"""The visible-curve filter must also keep hidden curves out of key editing."""
import unittest
import bpy


class SelectedGraphChannelsTest(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=False)
        self.obj = bpy.context.object
        self.obj.keyframe_insert('location', frame=1)
        self.obj.keyframe_insert('location', frame=20)
        ad = self.obj.animation_data
        self.bag = ad.action.layers[0].strips[0].channelbag(ad.action_slot)
        self.curves = list(self.bag.fcurves)
        self.area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
        self.area.type = 'GRAPH_EDITOR'
        self.region = next(r for r in self.area.regions if r.type == 'WINDOW')

    def selected_keys(self, axes, group_selected=False, hidden_axis=None):
        for group in self.bag.groups:
            group.select = group_selected
        for curve in self.curves:
            curve.select = curve.array_index in axes
            curve.hide = curve.array_index == hidden_axis
            for key in curve.keyframe_points:
                key.select_control_point = False
                key.select_left_handle = False
                key.select_right_handle = False
        with bpy.context.temp_override(area=self.area, region=self.region):
            if axes or group_selected:
                bpy.ops.graph.select_all(action='SELECT')
            else:
                self.assertFalse(bpy.ops.graph.select_all.poll())
        return {fc.array_index for fc in self.curves
                if any(k.select_control_point for k in fc.keyframe_points)}

    def test_one_channel(self):
        self.assertEqual(self.selected_keys({0}), {0})

    def test_switch_channel(self):
        self.assertEqual(self.selected_keys({0}), {0})
        self.assertEqual(self.selected_keys({2}), {2})

    def test_multiple_channels(self):
        self.assertEqual(self.selected_keys({0, 2}), {0, 2})

    def test_no_channel(self):
        self.assertEqual(self.selected_keys(set()), set())

    def test_group(self):
        self.assertEqual(self.selected_keys(set(), group_selected=True), {0, 1, 2})

    def test_hidden_channel_stays_hidden(self):
        self.assertEqual(self.selected_keys({0, 2}, hidden_axis=2), {0})

    def test_channel_list_can_select_all_again(self):
        self.selected_keys({0})
        with bpy.context.temp_override(area=self.area, region=self.region):
            bpy.ops.anim.channels_select_all(action='SELECT')
        self.assertTrue(all(fc.select for fc in self.curves))

    def test_dopesheet_is_unaffected(self):
        self.selected_keys(set())
        self.area.type = 'DOPESHEET_EDITOR'
        region = next(r for r in self.area.regions if r.type == 'WINDOW')
        with bpy.context.temp_override(area=self.area, region=region):
            bpy.ops.action.select_all(action='SELECT')
        self.assertTrue(all(k.select_control_point
                            for fc in self.curves for k in fc.keyframe_points))


if __name__ == '__main__':
    unittest.main(argv=[__file__])
