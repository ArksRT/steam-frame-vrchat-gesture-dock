"""Windows language preference and translations for installer and bridge messages."""
import ctypes
import json
import locale
import os
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path


SUPPORTED = ("en", "zh-CN")
_language = ContextVar("gesture_dock_language", default="zh-CN")


def _system_language():
    if os.name == "nt":
        # Windows' display language may differ from regional date/number formats.
        language_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return "zh-CN" if language_id & 0x3ff == 0x04 else "en"
    name = locale.getlocale()[0] or ""
    return "zh-CN" if name.lower().startswith("zh") else "en"


def _directory(directory):
    return Path(directory) if directory is not None else Path(os.environ["LOCALAPPDATA"]) / "VRChatGestureDock"


def saved_language(directory=None):
    directory = _directory(directory)
    for name in ("language.json", "connection.json"):
        try:
            data = json.loads((directory / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("language") in SUPPORTED:
            return data["language"]
    return None


def preferred_language(directory=None):
    return saved_language(directory) or _system_language()


def save_language(language, directory=None):
    if language not in SUPPORTED:
        raise ValueError("Unsupported language")
    directory = _directory(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        temporary.write_text(json.dumps({"language": language}) + "\n", encoding="utf-8")
        os.replace(temporary, directory / "language.json")
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def language_context(language):
    if language not in SUPPORTED:
        raise ValueError("Unsupported language")
    token = _language.set(language)
    try:
        yield
    finally:
        _language.reset(token)


def tr(english, **values):
    message = _CHINESE[english] if _language.get() == "zh-CN" else english
    return message.format(**values) if values else message


_CHINESE = {
    "Unsupported SteamVR controller type: {controller!r}":
        "不支持此 SteamVR 控制器类型：{controller!r}",
    "The current SteamVR binding does not identify VRChat.":
        "当前 SteamVR 绑定未标明所属应用为 VRChat。",
    "The global VRChat action set is missing.":
        "缺少 VRChat 的全局动作集。",
    "Could not read the connection settings on this PC.":
        "无法读取电脑上的连接配置。",
    "Enter the Steam Frame hostname or IP address.":
        "请输入 Steam Frame 的主机名或 IP 地址。",
    "Enter a valid SteamOS username, such as steamos.":
        "请输入有效的 SteamOS 用户名，例如 steamos。",
    "Install OpenSSH Client in Windows Optional Features, then run the installer again.":
        "请在 Windows 的「可选功能」中安装 OpenSSH 客户端，再运行安装程序。",
    "Another installer is configuring Gesture Dock. Wait for it to finish.":
        "另一个安装窗口正在配置快捷按钮。请等待它完成。",
    "Could not check the SSH trust record in {path}.":
        "无法核对 SSH 信任记录：{path}",
    "This device uses SSH host certificates. The installer supports pinned host keys.":
        "此设备配置了 SSH 主机证书验证。安装器目前支持固定主机公钥。",
    "The SSH trust record contains an unrecognized marker.":
        "SSH 信任记录含有无法识别的标记。",
    "The SSH host key record has an invalid format.":
        "SSH 主机公钥记录格式异常。",
    "The saved SSH host key type is unsupported.":
        "保存的主机公钥类型不受支持。",
    "The Steam Frame SSH host key has changed or was revoked. Verify the headset before reconnecting.":
        "Steam Frame 的 SSH 主机公钥与信任记录不符或已被撤销。请先核对设备。",
    "Connection canceled.":
        "已取消连接。",
    "SSH sign-in failed. Check the SteamOS username and password.":
        "SSH 登录失败。请检查 SteamOS 用户名和密码。",
    "Could not connect to {host} over SSH. Check the address, SSH setting and local network.":
        "无法通过 SSH 连接 {host}。请检查地址、SSH 设置和局域网连接。",
    "The existing tunnel key is not a valid Ed25519 key.":
        "现有转发密钥不是有效的 Ed25519 密钥。",
    "The Steam Frame could not finish the operation.":
        "Steam Frame 未完成操作。",
    "Check the address. The target must be a Steam Frame running ARM64 SteamOS.":
        "目标设备不是运行 ARM64 SteamOS 的 Steam Frame。请核对设备地址。",
    "The headset needs Python 3 with aiohttp. Update SteamOS or install aiohttp for its system Python.":
        "头显缺少 Python 3 或 aiohttp。请先更新 SteamOS，或为系统 Python 安装 aiohttp。",
    "Could not reach SteamOS user services. Connect as the SteamOS user signed in on the headset.":
        "无法访问 SteamOS 的用户服务。请用头显当前登录的 SteamOS 用户连接。",
    "Could not verify the Steam Frame environment.":
        "无法确认 Steam Frame 的运行环境。",
    "The headset could not reach the service on this PC. Check SSH port forwarding and make sure port 27656 on the headset is free.":
        "头显尚未连通这台电脑的后台服务。请检查 SSH 转发权限，并确认其他电脑没有占用头显的 27656 端口。",
    "Enter the SteamOS account password.":
        "请输入 SteamOS 账户密码。",
    "Run the packaged Windows installer.":
        "请从已打包的 Windows 程序安装。",
    "Checking the headset identity and signing in to Steam Frame…":
        "正在核对设备身份并登录 Steam Frame…",
    "Installing the Dashboard button on the headset…":
        "正在安装头显底栏按钮…",
    "Registering the SteamVR startup app…":
        "正在注册 SteamVR 启动项…",
    "Installation complete. Startup remains off. To enable it, open SteamVR Settings → Startup / Shutdown → Choose Startup Overlay Apps and turn on VRChat Gesture Dock.":
        "安装完成。已保留关闭状态。可在 SteamVR「设置 → 启动/关闭 → 选择启动时的叠加界面应用」中打开 VRChat Gesture Dock。",
    "Installation complete. The gesture service will connect to your headset the next time SteamVR starts.":
        "安装完成。下次启动 SteamVR 时将自动启动手势服务，并连接头显。",
    "Installation complete. Click Gestures in the headset Dashboard to switch modes while streaming VRChat.":
        "安装完成。头显底栏已显示「手势」，串流 VRChat 时点击即可切换。",
    "Installation complete. The headset is connected to this PC. Enable CEF Remote Debugging and open the Dashboard to see the Gestures button.":
        "安装完成，电脑与头显已连通。请开启 CEF 远程调试并打开 Dashboard，等待「手势」按钮出现。",
    "The SteamVR bindings API returned HTTP {code}.":
        "SteamVR 绑定接口返回 HTTP {code}。",
    "Connect the Frame and start SteamVR on the PC that runs VRChat.":
        "请先连接 Frame，并启动运行 VRChat 的 SteamVR。",
    "SteamVR returned unrecognized binding data.":
        "SteamVR 返回了无法识别的绑定数据。",
    "SteamVR rejected the binding request.":
        "SteamVR 未接受绑定请求。",
    "Connect the Frame controllers and run VRChat through SteamVR at least once.":
        "请连接 Frame 手柄并运行一次 SteamVR 版 VRChat。",
    "SteamVR could not read this VRChat controller binding.":
        "SteamVR 无法读取这套 VRChat 手柄绑定。",
    "Could not read the saved original binding.":
        "无法读取已保存的原绑定记录。",
    "SteamVR has not confirmed the switch. Check the current binding.":
        "SteamVR 尚未确认切换，请检查当前绑定。",
    "The binding changed. Try again using the current mode.":
        "绑定状态已经改变，请按当前状态重试。",
    "The SteamVR configuration file has an invalid format: {path}":
        "SteamVR 配置格式异常：{path}",
    "Run SteamVR once on this PC to create its OpenVR path settings.":
        "请先在这台电脑上运行一次 SteamVR，以建立 OpenVR 路径配置。",
    "Could not find the SteamVR Windows x64 OpenVR library at {path}.":
        "找不到 SteamVR 的 Windows x64 OpenVR 库：{path}",
    "SteamVR could not read application property {prop} (error {code}).":
        "SteamVR 无法读取应用属性 {prop}（{code}）。",
    "SteamVR is unavailable (OpenVR error {code}). Wait for startup or shutdown to finish and try again.":
        "SteamVR 暂时无法连接（OpenVR {code}）。请在启停完成后重试。",
    "Could not open the SteamVR startup application interface (OpenVR error {code}).":
        "无法打开 SteamVR 的启动应用接口（OpenVR {code}）。",
    "The SteamVR manifest_paths setting has an invalid format.":
        "SteamVR 的 manifest_paths 配置格式异常。",
    "SteamVR could not {operation} (application error {code}).":
        "SteamVR {operation}失败（应用错误 {code}）。",
    "remove the old manifest":
        "移除旧清单",
    "register the startup app":
        "注册启动应用",
    "set automatic startup":
        "设置自动启动",
    "SteamVR did not confirm VRChat Gesture Dock in its startup list.":
        "SteamVR 未确认启动列表中的 VRChat Gesture Dock。",
    "reload the manifest":
        "重新加载清单",
    "Another manifest has already registered this SteamVR app. Check the existing installation.":
        "已有其他清单注册了此 SteamVR 应用，请检查现有安装。",
}
