"""Build a VRChat Touch-style gesture preset from the selected SteamVR binding."""

from copy import deepcopy


_VRCHAT_KEYS = {"steam.app.438100", "application.generated.unity.vrchat.exe"}
_GESTURE_SETS = {"/actions/global", "/actions/action_menu", "/actions/one_hand"}
_SKELETON_OUTPUTS = {"/actions/global/in/skeletonlefthand",
                     "/actions/global/in/skeletonrighthand"}


def _is_gesture_input(binding):
    # Keep toggles, movement, menus and avatar interactions on their original inputs.
    action = binding.get("output", "").lower().rsplit("/", 1)[-1]
    return (
        action in {"gesture_grip_axis", "gesture_trigger_axis", "disable_gesture_tracked"}
        or action.startswith(("gesture_activator_", "gesture_direct_", "gesture_wheel_"))
    )


def _quest_sources(action_set, controller):
    # VRChat's bindings_oculus_touch.json uses these five gesture actions.
    # For frame_controller, Valve's frame_controller_remapping.json maps the
    # Touch face buttons to the D-pad and ABXY, and joystick to thumbstick.
    if controller == "frame_controller":
        faces = {"left": ("dpad_up", "dpad_down", "dpad_left", "dpad_right"),
                 "right": ("a", "b", "x", "y")}
        stick = "thumbstick"
    else:
        faces = {"left": ("x", "y"), "right": ("a", "b")}
        stick = "joystick"

    for hand, buttons in faces.items():
        controls = [
            ("grip", "trigger", {"pull": "gesture_grip_axis", "touch": "gesture_activator_grip"}),
            ("trigger", "trigger", {"pull": "gesture_trigger_axis", "touch": "gesture_activator_index"}),
            (stick, "joystick", {"touch": "gesture_activator_thumb"}),
        ]
        controls.extend((button, "button", {"touch": "gesture_activator_thumb"}) for button in buttons)
        for control, mode, actions in controls:
            yield {
                "path": f"/user/hand/{hand}/input/{control}",
                "mode": mode,
                "inputs": {key: {"output": f"{action_set}/in/{action}"} for key, action in actions.items()},
            }


def make_quest_binding(current: dict) -> dict:
    """Return a new binding, leaving the user's original binding untouched.

    Pass the resolved source returned by SteamVR for the Frame controllers.
    Native Frame and SteamVR's Oculus Touch fallback each keep their own
    controller type and paths, so the runtime can apply its usual remapping.
    """
    controller = current.get("controller_type")
    if controller not in {"frame_controller", "oculus_touch"}:
        raise ValueError(f"Unsupported SteamVR controller type: {controller!r}")
    if current.get("app_key") not in _VRCHAT_KEYS:
        raise ValueError("The current SteamVR binding does not identify VRChat")

    preset = deepcopy(current)
    sets = preset.get("bindings", {})
    if "/actions/global" not in {name.lower() for name in sets}:
        raise ValueError("VRChat's global action set is missing")
    for set_name, group in sets.items():
        if "skeleton" in group:
            group["skeleton"] = [entry for entry in group["skeleton"]
                                 if entry.get("output", "").lower() not in _SKELETON_OUTPUTS]
        if set_name.lower() not in _GESTURE_SETS:
            continue
        if "chords" in group:
            group["chords"] = [chord for chord in group["chords"] if not _is_gesture_input(chord)]

        sources = []
        for source in group.get("sources", []):
            inputs = source.get("inputs", {})
            if not any(_is_gesture_input(binding) for binding in inputs.values()):
                sources.append(source)
                continue
            source["inputs"] = {key: binding for key, binding in inputs.items()
                                if not _is_gesture_input(binding)}
            if source["inputs"]:
                sources.append(source)
        sources.extend(_quest_sources(set_name.lower(), controller))
        group["sources"] = sources
    preset["name"] = "VRChat - Quest gestures"
    preset["description"] = "Quest Touch gestures driven by grip, trigger and thumb inputs."
    return preset
