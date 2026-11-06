# SPDX-License-Identifier: GPL-2.0-or-later
"""The visible-curve filter must also keep hidden curves out of key editing."""
import unittest
import bpy


class SelectedGraphChannelsTest(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=False)
        self.obj = bpy.context.object
        self.obj.keyframe_insert('location', frame=1, group='Object Transforms')
        self.obj.keyframe_insert('location', frame=20, group='Object Transforms')
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
            # Exercise explicit channel-list operators while preserving the chosen selection.
            bpy.ops.anim.channels_select_all(action='INVERT')
            bpy.ops.anim.channels_select_all(action='INVERT')
            bpy.ops.graph.select_all(action='SELECT')
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
        self.assertEqual(self.selected_keys(set()), {0, 1, 2})

    def test_deselect_restores_all_curves(self):
        self.assertEqual(self.selected_keys({1}), {1})
        self.assertEqual(self.selected_keys(set()), {0, 1, 2})

    def test_no_channel_preserves_explicit_visibility(self):
        self.assertEqual(self.selected_keys(set(), hidden_axis=2), {0, 1})

    def test_canvas_selection_does_not_isolate(self):
        self.selected_keys(set())
        for fc in self.curves:
            fc.select = fc.array_index == 1
            for key in fc.keyframe_points:
                key.select_control_point = False
        with bpy.context.temp_override(area=self.area, region=self.region):
            bpy.ops.graph.select_all(action='SELECT')
        self.assertTrue(all(k.select_control_point for fc in self.curves for k in fc.keyframe_points))

    def test_canvas_click_keeps_all_curves_visible(self):
        self.selected_keys(set())
        with bpy.context.temp_override(area=self.area, region=self.region):
            bpy.ops.graph.select_all(action='DESELECT')
            x, y = self.region.view2d.view_to_region(1, 0, clip=False)
            result = bpy.ops.graph.clickselect(mouse_x=x, mouse_y=y, deselect_all=True,
                                               wait_to_deselect_others=False)
            self.assertIn('FINISHED', result)
            self.assertTrue(any(k.select_control_point for fc in self.curves for k in fc.keyframe_points))
            bpy.ops.graph.select_all(action='SELECT')
        self.assertTrue(all(k.select_control_point for fc in self.curves for k in fc.keyframe_points))

    def test_canvas_deselect_keeps_channel_focus(self):
        self.selected_keys({1})
        with bpy.context.temp_override(area=self.area, region=self.region):
            bpy.ops.graph.select_all(action='DESELECT')
            bpy.ops.graph.select_all(action='SELECT')
        self.assertEqual({fc.array_index for fc in self.curves
                          if any(k.select_control_point for k in fc.keyframe_points)}, {1})

    def test_channel_deselect_clears_display_focus(self):
        self.selected_keys({1})
        with bpy.context.temp_override(area=self.area, region=self.region):
            bpy.ops.anim.channels_select_all(action='DESELECT')
            bpy.ops.graph.select_all(action='SELECT')
        self.assertTrue(all(k.select_control_point for fc in self.curves for k in fc.keyframe_points))

    def test_selection_is_shared_across_objects(self):
        other = bpy.data.objects.new('Other', None)
        bpy.context.collection.objects.link(other)
        other.select_set(True)
        for frame in (1, 20):
            other.keyframe_insert('location', frame=frame, group='Other Transforms')
        other_ad = other.animation_data
        other_bag = other_ad.action.layers[0].strips[0].channelbag(other_ad.action_slot)
        other_curves = list(other_bag.fcurves)
        for group in other_bag.groups:
            group.select = False
        for fc in other_curves:
            fc.select = False
            for key in fc.keyframe_points:
                key.select_control_point = False
                key.select_left_handle = False
                key.select_right_handle = False
        self.assertEqual(self.selected_keys({0}), {0})
        self.assertFalse(any(k.select_control_point for fc in other_curves for k in fc.keyframe_points))
        self.assertEqual(self.selected_keys(set()), {0, 1, 2})
        self.assertTrue(all(k.select_control_point for fc in other_curves for k in fc.keyframe_points))

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
