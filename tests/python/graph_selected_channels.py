# SPDX-License-Identifier: GPL-2.0-or-later
"""The visible-curve filter must also keep hidden curves out of key editing."""
from pathlib import Path
import tempfile
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

    def test_only_selected_keyframe_handles_enabled_by_default(self):
        space = self.area.spaces.active
        self.assertTrue(space.show_handles)
        self.assertTrue(space.use_only_selected_keyframe_handles)
        anim_screen = bpy.data.screens.get('Animation')
        if anim_screen is not None:
            dope_area = next((ar for ar in anim_screen.areas if ar.type == 'DOPESHEET_EDITOR'), None)
            if dope_area is not None:
                dope_area.type = 'GRAPH_EDITOR'
        graph_spaces = [
            sp
            for scr in bpy.data.screens
            for ar in scr.areas
            for sp in ar.spaces
            if sp.type == 'GRAPH_EDITOR'
        ]
        self.assertTrue(graph_spaces)
        self.assertTrue(all(sp.use_only_selected_keyframe_handles for sp in graph_spaces))

    def test_explicit_disable_only_selected_handles_persists_across_save_reload(self):
        space = self.area.spaces.active
        self.assertTrue(space.use_only_selected_keyframe_handles)
        space.use_only_selected_keyframe_handles = False
        with tempfile.TemporaryDirectory() as tmpdir:
            blend_path = str(Path(tmpdir) / 'explicit_handles_off.blend')
            bpy.ops.wm.save_as_mainfile(filepath=blend_path)
            bpy.ops.wm.open_mainfile(filepath=blend_path, load_ui=True)
            reloaded_graph_spaces = [
                ar.spaces.active
                for ar in bpy.context.screen.areas
                if ar.type == 'GRAPH_EDITOR'
            ]
            self.assertTrue(reloaded_graph_spaces)
            self.assertFalse(reloaded_graph_spaces[0].use_only_selected_keyframe_handles)

    def test_unselected_keyframe_handles_cannot_be_clicked_or_box_selected_until_key_selected(self):
        # Isolate X curve and give key 0 a distinct Free right handle away from the curve body.
        self.selected_keys({0})
        fc = next(c for c in self.curves if c.array_index == 0)
        k0, k1 = fc.keyframe_points[0], fc.keyframe_points[1]
        k0.co = (2.0, 0.0)
        k1.co = (18.0, 0.0)
        fc.update()
        k0 = fc.keyframe_points[0]
        k0.handle_left_type = 'FREE'
        k0.handle_right_type = 'FREE'
        k0.handle_right = (8.0, 4.0)

        with bpy.context.temp_override(area=self.area, region=self.region):
            bpy.ops.graph.select_all(action='DESELECT')
            hx, hy = self.region.view2d.view_to_region(8.0, 4.0, clip=False)
            # 1. Unselected keyframe's handle must ignore clicks (no accidental misclick).
            bpy.ops.graph.clickselect(
                mouse_x=hx, mouse_y=hy, deselect_all=True, wait_to_deselect_others=False
            )
            self.assertFalse(k0.select_control_point)
            self.assertFalse(k0.select_left_handle)
            self.assertFalse(k0.select_right_handle)

            # 2. Box-select around the unselected keyframe's handle tip (above the curve) must ignore it.
            bx0, by0 = self.region.view2d.view_to_region(7.5, 3.6, clip=False)
            bx1, by1 = self.region.view2d.view_to_region(8.5, 4.4, clip=False)
            bpy.ops.graph.select_box(
                xmin=min(bx0, bx1),
                xmax=max(bx0, bx1),
                ymin=min(by0, by1),
                ymax=max(by0, by1),
                mode='SET',
                include_handles=True,
                use_curve_selection=False,
            )
            self.assertFalse(k0.select_control_point)
            self.assertFalse(k0.select_right_handle)

            # 3. Clicking the keyframe itself selects it and exposes its handles for clicking.
            kx, ky = self.region.view2d.view_to_region(2.0, 0.0, clip=False)
            res = bpy.ops.graph.clickselect(
                mouse_x=kx, mouse_y=ky, deselect_all=True, wait_to_deselect_others=False
            )
            self.assertIn('FINISHED', res)
            self.assertTrue(k0.select_control_point)

            res_h = bpy.ops.graph.clickselect(
                mouse_x=hx, mouse_y=hy, deselect_all=True, wait_to_deselect_others=False
            )
            self.assertIn('FINISHED', res_h)
            self.assertTrue(k0.select_right_handle)

    def test_deselecting_keyframe_clears_handles_when_only_selected_handles_active(self):
        self.selected_keys({0})
        fc = next(c for c in self.curves if c.array_index == 0)
        k0 = fc.keyframe_points[0]
        k0.co = (2.0, 0.0)
        k0.handle_left_type = 'FREE'
        k0.handle_right_type = 'FREE'
        k0.handle_right = (8.0, 4.0)

        with bpy.context.temp_override(area=self.area, region=self.region):
            # Select All sets control point + both handles selected.
            bpy.ops.graph.select_all(action='SELECT')
            self.assertTrue(k0.select_control_point and k0.select_left_handle and k0.select_right_handle)

            # Shift+Click on keyframe center must clear both handles too (no ghost handles left behind).
            kx, ky = self.region.view2d.view_to_region(2.0, 0.0, clip=False)
            bpy.ops.graph.clickselect(
                mouse_x=kx,
                mouse_y=ky,
                extend=True,
                deselect_all=False,
                wait_to_deselect_others=False,
            )
            self.assertFalse(k0.select_control_point)
            self.assertFalse(k0.select_left_handle)
            self.assertFalse(k0.select_right_handle)

            # Ctrl+Box deselect over just the keyframe center must also clear its handles.
            bpy.ops.graph.select_all(action='SELECT')
            bx0, by0 = self.region.view2d.view_to_region(1.5, -0.5, clip=False)
            bx1, by1 = self.region.view2d.view_to_region(2.5, 0.5, clip=False)
            bpy.ops.graph.select_box(
                xmin=min(bx0, bx1),
                xmax=max(bx0, bx1),
                ymin=min(by0, by1),
                ymax=max(by0, by1),
                mode='SUB',
                include_handles=True,
                use_curve_selection=False,
            )
            self.assertFalse(k0.select_control_point)
            self.assertFalse(k0.select_left_handle)
            self.assertFalse(k0.select_right_handle)


if __name__ == '__main__':
    unittest.main(argv=[__file__])
