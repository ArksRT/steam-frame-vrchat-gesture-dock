"""Register the gesture bridge as a user-toggleable SteamVR startup overlay."""

import ctypes
import json
import os
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path


APP_KEY = "com.dhrthunder.vrchat-gesture-dock"
MANIFEST = "gesture-dock.vrmanifest"


def _read_json(path):
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"SteamVR 配置格式异常：{path}")
    return value


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            handle.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _json_bytes(doc):
    return (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _paths():
    default = Path(os.environ["LOCALAPPDATA"]) / "openvr" / "openvrpaths.vrpath"
    data = _read_json(Path(os.environ.get("VR_PATHREG_OVERRIDE") or default))
    runtime = os.environ.get("VR_OVERRIDE") or next(iter(data.get("runtime", [])), None)
    config = os.environ.get("VR_CONFIG_PATH") or next(iter(data.get("config", [])), None)
    if not runtime or not config:
        raise RuntimeError("请先在这台电脑上运行一次 SteamVR，以建立 OpenVR 路径配置。")
    dll = Path(runtime) / "bin/win64/openvr_api.dll"
    if not dll.is_file():
        raise RuntimeError(f"找不到 SteamVR 的 Windows x64 OpenVR 库：{dll}")
    return dll, Path(config)


class _Apps:
    """Valve openvr_capi.h, IVRApplications_008 function table (Windows x64)."""

    def __init__(self, table):
        text, flag, integer = ctypes.c_char_p, ctypes.c_bool, ctypes.c_int
        def bind(index, result, *args):
            return ctypes.WINFUNCTYPE(result, *args)(table[index])

        self.add = bind(0, integer, text, flag)
        self.remove = bind(1, integer, text)
        self.installed = bind(2, flag, text)
        self.get_bool = bind(15, flag, text, integer, ctypes.POINTER(integer))
        self.set_startup = bind(17, integer, text, flag)
        self.get_startup = bind(18, flag, text)

    def property(self, prop):
        error = ctypes.c_int()
        value = self.get_bool(APP_KEY.encode(), prop, ctypes.byref(error))
        if error.value:
            raise RuntimeError(f"SteamVR 无法读取应用属性 {prop}（{error.value}）。")
        return bool(value)


@contextmanager
def _applications(dll_path):
    # Background never starts SteamVR, unlike VRApplication_Overlay.
    dll = ctypes.CDLL(str(dll_path))
    error = ctypes.c_int()
    dll.VR_InitInternal.argtypes = (ctypes.POINTER(ctypes.c_int), ctypes.c_int)
    dll.VR_InitInternal.restype = ctypes.c_uint32
    dll.VR_InitInternal(ctypes.byref(error), 3)
    if error.value == 121:  # VRInitError_Init_NoServerForBackgroundApp
        yield None
        return
    if error.value:
        raise RuntimeError(f"SteamVR 暂时无法连接（OpenVR {error.value}）。请在启停完成后重试。")
    try:
        dll.VR_GetGenericInterface.argtypes = (ctypes.c_char_p, ctypes.POINTER(ctypes.c_int))
        dll.VR_GetGenericInterface.restype = ctypes.POINTER(ctypes.c_void_p)
        table = dll.VR_GetGenericInterface(b"FnTable:IVRApplications_008", ctypes.byref(error))
        if error.value or not table:
            raise RuntimeError(f"无法打开 SteamVR 的启动应用接口（OpenVR {error.value}）。")
        yield _Apps(table)
    finally:
        dll.VR_ShutdownInternal()


def _manifest(directory, executable, arguments):
    app = {
        "app_key": APP_KEY, "launch_type": "binary",
        "binary_path_windows": os.path.abspath(executable),
        "arguments": subprocess.list2cmdline([str(arg) for arg in arguments]),
        "working_directory": str(directory.resolve()),
        "is_dashboard_overlay": True, "is_internal": False, "is_hidden": False,
        "strings": {"en_us": {"name": "VRChat Gesture Dock"},
                    "zh_cn": {"name": "VRChat Gesture Dock"},
                    "zh_tw": {"name": "VRChat Gesture Dock"}},
    }
    return _json_bytes({"source": "builtin", "applications": [app]})


def _registered(paths, manifest):
    return any(isinstance(path, str) and os.path.normcase(os.path.normpath(path))
               == os.path.normcase(str(manifest)) for path in paths)


def _preferences(config):
    return config / "vrappconfig" / (APP_KEY + ".vrappconfig")


def _offline(config, manifest, registered, enabled):
    # These are SteamVR's persistent app registry and its per-app startup preference.
    # Preserve the other overlays and their configuration when registering offline.
    index = config / "appconfig.json"
    data = _read_json(index)
    paths = data.get("manifest_paths", [])
    if not isinstance(paths, list):
        raise ValueError("SteamVR 的 manifest_paths 配置格式异常。")
    prefs_path = _preferences(config)
    prefs = _read_json(prefs_path)
    if enabled is not None and prefs.get("autolaunch") is not enabled:
        prefs["autolaunch"] = enabled
        _write(prefs_path, _json_bytes(prefs))
    elif enabled is None and "autolaunch" in prefs:
        del prefs["autolaunch"]
        if prefs:
            _write(prefs_path, _json_bytes(prefs))
        else:
            prefs_path.unlink()
    if _registered(paths, manifest) != registered:
        data["manifest_paths"] = (paths + [str(manifest)] if registered else
                                  [path for path in paths if not _registered([path], manifest)])
        _write(index, _json_bytes(data))


def _checked(result, operation):
    if result:
        raise RuntimeError(f"SteamVR {operation}失败（应用错误 {result}）。")


def _sync_live(apps, path, registered, enabled):
    key = APP_KEY.encode()
    if not registered:
        if apps.installed(key):
            _checked(apps.remove(path), "移除旧清单")
        return
    if not apps.installed(key):
        _checked(apps.add(path, False), "注册启动应用")
    if bool(apps.get_startup(key)) != bool(enabled):
        _checked(apps.set_startup(key, bool(enabled)), "设置自动启动")
    if (not apps.installed(key) or bool(apps.get_startup(key)) != bool(enabled)
            or not apps.property(60) or apps.property(63) or apps.property(65)):
        raise RuntimeError("SteamVR 未确认启动列表中的 VRChat Gesture Dock。")


def _apply(dll, config, manifest, content, registered, enabled):
    key = APP_KEY.encode()
    path = str(manifest).encode("utf-8")
    old = manifest.read_bytes() if manifest.exists() else None
    with _applications(dll) as apps:
        if apps and apps.installed(key) and old != content and registered:
            _checked(apps.remove(path), "移除旧清单")
        if content is not None and old != content:
            _write(manifest, content)
        if apps is None:
            _offline(config, manifest, registered, enabled)
        else:
            _sync_live(apps, path, registered, enabled)
        if content is None:
            manifest.unlink(missing_ok=True)
    if apps is None:
        # If SteamVR started while the on-disk registry was being updated, it
        # may have already loaded the old registry. Reconcile its live cache.
        with _applications(dll) as now_running:
            if now_running is not None:
                if now_running.installed(key) and old != content and registered:
                    _checked(now_running.remove(path), "重新加载清单")
                _sync_live(now_running, path, registered, enabled)


def register(directory: Path, executable: Path, arguments):
    """Register the stable app key and return a rollback callback for the installer."""
    directory = Path(directory).resolve()
    executable = Path(executable)
    if not executable.is_file():
        raise FileNotFoundError(executable)
    dll, config = _paths()
    manifest = directory / MANIFEST
    old = manifest.read_bytes() if manifest.exists() else None
    saved_pref = _read_json(_preferences(config)).get("autolaunch")
    with _applications(dll) as apps:
        if apps is None:
            existing = _registered(_read_json(config / "appconfig.json").get("manifest_paths", []), manifest)
            old_enabled = saved_pref
        else:
            existing = bool(apps.installed(APP_KEY.encode()))
            old_enabled = bool(apps.get_startup(APP_KEY.encode())) if existing else saved_pref
    if existing and old is None:
        raise RuntimeError("已有其他清单注册了此 SteamVR 应用，请检查现有安装。")

    def rollback():
        _apply(dll, config, manifest, old, existing, old_enabled)

    try:
        _apply(dll, config, manifest, _manifest(directory, executable, arguments), True,
               bool(old_enabled) if existing else True)
    except Exception:
        rollback()
        raise
    return rollback


def startup_enabled():
    """Read the user's current setting, including changes made in SteamVR's UI."""
    dll, config = _paths()
    with _applications(dll) as apps:
        if apps is not None:
            return bool(apps.installed(APP_KEY.encode()) and apps.get_startup(APP_KEY.encode()))
        return _read_json(_preferences(config)).get("autolaunch") is True


def startup_status(directory: Path):
    """Read the same flags used by SteamVR's startup overlay list, without starting it."""
    dll, config = _paths()
    manifest = Path(directory).resolve() / MANIFEST
    key = APP_KEY.encode()
    with _applications(dll) as apps:
        if apps is not None:
            installed = bool(apps.installed(key))
            return {"live": True, "listed": installed and apps.property(60)
                    and not apps.property(63) and not apps.property(65),
                    "enabled": installed and bool(apps.get_startup(key))}
    listed = _registered(_read_json(config / "appconfig.json").get("manifest_paths", []), manifest)
    info = next((entry for entry in _read_json(manifest).get("applications", [])
                 if entry.get("app_key") == APP_KEY), {})
    return {"live": False, "listed": bool(listed and info.get("is_dashboard_overlay")
            and not info.get("is_internal") and not info.get("is_hidden")),
            "enabled": _read_json(_preferences(config)).get("autolaunch") is True}
