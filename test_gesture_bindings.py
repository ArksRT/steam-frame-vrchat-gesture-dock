import unittest
from copy import deepcopy

from gesture_bindings import make_quest_binding


class QuestBindingTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            "app_key": "steam.app.438100",
            "controller_type": "frame_controller",
            "name": "Custom bindings",
            "options": {"example": "keep"},
            "bindings": {
                "/actions/global": {
                    "skeleton": [
                        {"output": "/actions/global/in/skeletonLeftHand", "path": "left"},
                        {"output": "/actions/global/in/skeletonRightHand", "path": "right"},
                    ],
                    "sources": [{
                        "mode": "button", "path": "/user/hand/right/input/y",
                        "parameters": {"haptic_amplitude": "0"},
                        "inputs": {"click": {"output": "/actions/global/in/menu"},
                                   "touch": {"output": "/actions/global/in/gesture_activator_thumb_outer"}},
                    }],
                    "chords": [{"output": "/actions/global/in/safe_mode"},
                               {"output": "/actions/global/in/gesture_direct_peace_right"}],
                },
                "/actions/action_menu": {"sources": []},
                "/actions/one_hand": {"sources": []},
                "/actions/drone": {"sources": [{"mode": "button"}]},
            },
        }

    def test_preserves_original_and_unrelated_bindings(self):
        original = deepcopy(self.base)
        preset = make_quest_binding(self.base)
        self.assertEqual(self.base, original)
        self.assertEqual(preset["options"], original["options"])
        self.assertEqual(preset["bindings"]["/actions/drone"], original["bindings"]["/actions/drone"])
        group = preset["bindings"]["/actions/global"]
        self.assertEqual(group["skeleton"], [])
        self.assertEqual(group["chords"], [{"output": "/actions/global/in/safe_mode"}])
        self.assertEqual(group["sources"][0]["inputs"], {"click": {"output": "/actions/global/in/menu"}})
        self.assertEqual(group["sources"][0]["parameters"], {"haptic_amplitude": "0"})
        self.assertEqual(make_quest_binding(preset), preset)

    def test_physical_frame_gesture_controls_across_all_supported_action_sets(self):
        preset = make_quest_binding(self.base)
        for name in ("/actions/global", "/actions/action_menu", "/actions/one_hand"):
            signals = {(s["path"], key): binding["output"] for s in preset["bindings"][name]["sources"]
                       for key, binding in s["inputs"].items()}
            for hand, face in (("left", "dpad_right"), ("right", "y")):
                path = f"/user/hand/{hand}/input/"
                self.assertEqual(signals[path + face, "touch"], name + "/in/gesture_activator_thumb")
                self.assertEqual(signals[path + "thumbstick", "touch"], name + "/in/gesture_activator_thumb")
                self.assertEqual(signals[path + "trigger", "touch"], name + "/in/gesture_activator_index")
                self.assertEqual(signals[path + "grip", "pull"], name + "/in/gesture_grip_axis")
                self.assertEqual(signals[path + "trigger", "pull"], name + "/in/gesture_trigger_axis")
                self.assertEqual(signals[path + "grip", "touch"], name + "/in/gesture_activator_grip")

    def test_oculus_source_uses_touch_paths_for_steamvr_remapping(self):
        self.base.update(controller_type="oculus_touch", app_key="application.generated.unity.vrchat.exe")
        preset = make_quest_binding(self.base)
        self.assertEqual(preset["controller_type"], "oculus_touch")
        paths = {source["path"] for source in preset["bindings"]["/actions/action_menu"]["sources"]}
        self.assertIn("/user/hand/left/input/x", paths)
        self.assertIn("/user/hand/right/input/joystick", paths)
        self.assertNotIn("/user/hand/left/input/dpad_down", paths)

    def test_rejects_other_app_or_controller(self):
        for field, invalid in (("app_key", "steam.app.250820"), ("controller_type", "knuckles")):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    make_quest_binding(dict(self.base, **{field: invalid}))


if __name__ == "__main__":
    unittest.main()
