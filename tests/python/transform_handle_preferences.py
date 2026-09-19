# SPDX-License-Identifier: GPL-2.0-or-later
"""Run with Blender --background --factory-startup --python this_file.py."""
import os
import subprocess
import tempfile
import unittest

import bpy


class TransformHandlePreferencesTest(unittest.TestCase):
    def tearDown(self):
        bpy.context.preferences.view.transform_gizmo_size = 1.0
        bpy.context.preferences.view.transform_gizmo_speed = 1.0

    def test_defaults_and_bounds(self):
        view = bpy.context.preferences.view
        for name, minimum, maximum in (("transform_gizmo_size", 1.0, 2.0),
                                        ("transform_gizmo_speed", 0.1, 1.0)):
            self.assertEqual(getattr(view, name), 1.0)
            for value, expected in ((0.01, minimum), (3.0, maximum),
                                     ((minimum + maximum) / 2, (minimum + maximum) / 2),
                                     (1.0, 1.0)):
                setattr(view, name, value)
                self.assertAlmostEqual(getattr(view, name), expected, places=6)

    def test_explicit_values_are_not_scaled(self):
        obj = bpy.context.active_object
        for speed in (0.1, 0.5, 1.0):
            bpy.context.preferences.view.transform_gizmo_speed = speed
            obj.location = (0, 0, 0)
            obj.rotation_euler = (0, 0, 0)
            obj.scale = (1, 1, 1)
            bpy.ops.transform.translate(value=(1, 0, 0), use_gizmo_sensitivity=True)
            self.assertAlmostEqual(obj.location.x, 1.0)
            bpy.ops.transform.rotate(value=0.75, orient_axis='Z', use_gizmo_sensitivity=True)
            self.assertAlmostEqual(obj.rotation_euler.z, 0.75, places=5)
            bpy.ops.transform.resize(value=(1.2, 1.2, 1.2), use_gizmo_sensitivity=True)
            self.assertAlmostEqual(obj.scale.x, 1.2, places=5)

    def test_preferences_round_trip_in_isolated_config(self):
        with tempfile.TemporaryDirectory(prefix="blender-handle-prefs-") as directory:
            env = dict(os.environ, BLENDER_USER_CONFIG=directory)
            save = (
                "import bpy; "
                "bpy.context.preferences.view.transform_gizmo_size=1.7; "
                "bpy.context.preferences.view.transform_gizmo_speed=0.4; "
                "bpy.ops.wm.save_userpref()"
            )
            check = (
                "import bpy; v=bpy.context.preferences.view; "
                "assert abs(v.transform_gizmo_size-1.7)<1e-6; "
                "assert abs(v.transform_gizmo_speed-0.4)<1e-6"
            )
            common = [bpy.app.binary_path, "--background", "--python-exit-code", "1"]
            subprocess.run(common + ["--factory-startup", "--python-expr", save],
                           env=env, check=True, capture_output=True)
            subprocess.run(common + ["--python-expr", check],
                           env=env, check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main(argv=[__file__])
