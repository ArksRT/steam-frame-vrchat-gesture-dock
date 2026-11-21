"""Windows installer and background entry points for the VRChat gesture dock."""

import queue
import sys
import threading


LANGUAGES = {"en": "English", "zh-CN": "简体中文"}
TEXT = {
    "en": {
        "title": "VRChat Gesture Dock",
        "prereqs": "Run this on the PC you use to stream VRChat. Connect your PC and headset "
                   "to the same local network. On the headset, enable SSH, Developer Mode, "
                   "and CEF Remote Debugging.",
        "host": "Headset hostname or IP",
        "user": "SSH username",
        "password": "SSH password",
        "missing_host": "Enter the headset hostname or IP address.",
        "missing_user": "Enter your SteamOS SSH username.",
        "missing_password": "Enter your SteamOS SSH password.",
        "idle": "The headset button uses your selected language. You can manage the PC service "
                "in SteamVR’s Startup / Shutdown settings.",
        "connecting": "Connecting to the headset…",
        "install": "Install or update",
        "licenses": "Third-party licenses",
        "trust_title": "Verify the headset’s SSH fingerprint",
        "trust": "First connection to {device}. Check the headset’s {algorithm} host key "
                 "fingerprint.\n\n{fingerprint}\n\nTrust this key and continue installing?",
        "failed": "Installation incomplete",
        "retry": "Check the error details, then try again.",
        "busy": "Installation in progress",
        "wait": "Wait for installation to finish before closing this window.",
        "save_failed": "Language changed for this session. Windows could not save your preference.",
    },
    "zh-CN": {
        "title": "VRChat 手势按钮",
        "prereqs": "请在串流 VRChat 的电脑上运行。头显与电脑需在同一局域网，"
                   "并在头显上开启 SSH、开发者模式和 CEF 远程调试。",
        "host": "头显主机名或 IP",
        "user": "SSH 用户名",
        "password": "SSH 密码",
        "missing_host": "请输入头显主机名或 IP 地址。",
        "missing_user": "请输入 SteamOS 的 SSH 用户名。",
        "missing_password": "请输入 SteamOS 的 SSH 密码。",
        "idle": "头显按钮会使用所选语言。电脑端服务可在 SteamVR 的「启动/关闭」设置中管理。",
        "connecting": "正在连接头显…",
        "install": "安装或更新",
        "licenses": "第三方许可",
        "trust_title": "确认头显 SSH 指纹",
        "trust": "首次连接 {device}。请核对头显的 {algorithm} 主机密钥指纹。"
                 "\n\n{fingerprint}\n\n信任这个密钥并继续安装吗？",
        "failed": "安装未完成",
        "retry": "请根据错误提示检查后重试。",
        "busy": "安装进行中",
        "wait": "请等安装完成后再关闭窗口。",
        "save_failed": "已切换本次会话的语言。Windows 无法保存你的选择。",
    },
}


def run_installer(smoke_test=False):
    import os
    from pathlib import Path
    import tkinter as tk
    from tkinter import messagebox, ttk

    from dock_i18n import preferred_language, save_language
    from release_setup import defaults, install

    root = tk.Tk()
    if smoke_test:
        root.withdraw()
    root.resizable(True, False)
    root.minsize(500, 1)
    style = ttk.Style(root)
    style.configure("Title.TLabel", font=("Microsoft YaHei UI", 17, "bold"))
    style.configure("Caption.TLabel", foreground="#485563")

    page = ttk.Frame(root, padding=(24, 20, 24, 18))
    page.grid(sticky="nsew")
    root.columnconfigure(0, weight=1)
    page.columnconfigure(1, weight=1)
    language = preferred_language()
    labels = {key: tk.StringVar() for key in ("title", "prereqs", "host", "user", "password",
                                              "install", "licenses")}
    ttk.Label(page, textvariable=labels["title"], style="Title.TLabel").grid(
        row=0, column=0, columnspan=2, sticky="w")
    ttk.Label(page, textvariable=labels["prereqs"], style="Caption.TLabel", wraplength=490).grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(4, 15))
    ttk.Label(page, text="Language / 语言").grid(row=2, column=0, sticky="w", padx=(0, 14), pady=5)
    selector = ttk.Combobox(page, values=tuple(LANGUAGES.values()), state="readonly", width=16)
    selector.grid(row=2, column=1, sticky="w", pady=5)
    selector.set(LANGUAGES[language])

    default_host, default_user = defaults()
    host = tk.StringVar(value=default_host)
    username = tk.StringVar(value=default_user)
    password = tk.StringVar()
    entries = []
    for row, (key, variable) in enumerate(
            (("host", host), ("user", username), ("password", password)), start=3):
        ttk.Label(page, textvariable=labels[key]).grid(row=row, column=0, sticky="w", padx=(0, 14), pady=5)
        entry = ttk.Entry(page, width=38, textvariable=variable,
                          show="●" if variable is password else "")
        entry.grid(row=row, column=1, sticky="ew", pady=5)
        entries.append(entry)

    status = tk.StringVar()
    progress_bar = ttk.Progressbar(page, mode="indeterminate")
    progress_bar.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(16, 10))
    status_label = ttk.Label(page, textvariable=status, wraplength=490, justify="left")
    status_label.grid(row=7, column=0, columnspan=2, sticky="w", pady=(0, 14))

    events = queue.Queue()
    running = False

    def localize():
        root.title("Steam Frame · " + TEXT[language]["title"])
        for key, label in labels.items():
            label.set(TEXT[language][key])
        status.set(TEXT[language]["idle"])

    def choose_language(_event):
        nonlocal language
        if running:
            return
        language = tuple(LANGUAGES)[selector.current()]
        localize()
        try:
            save_language(language)
        except OSError:
            status.set(TEXT[language]["save_failed"])

    selector.bind("<<ComboboxSelected>>", choose_language)

    def set_running(value):
        nonlocal running
        running = value
        for widget in (*entries, install_button, selector):
            widget.state(["disabled"] if running else ["!disabled"])
        if running:
            progress_bar.start(11)
        else:
            progress_bar.stop()

    def accept_key(device, algorithm, fingerprint):
        reply = queue.Queue(maxsize=1)
        events.put(("host_key", (device, algorithm, fingerprint, reply)))
        return reply.get()

    def worker(device, user, secret, install_language):
        try:
            install(host=device, username=user, password=secret,
                    progress=lambda text: events.put(("progress", text)),
                    confirm_host_key=accept_key, language=install_language)
        except Exception as error:
            detail = str(error).strip() or type(error).__name__
            events.put(("failed", detail.replace(secret, "••••") if secret else detail))
        else:
            events.put(("done", None))

    def start(event=None):
        if running or (event is not None and event.widget is selector):
            return
        device, user, secret = host.get().strip(), username.get().strip(), password.get()
        for value, field, key in zip((device, user, secret), entries, ("host", "user", "password")):
            if not value:
                status.set(TEXT[language]["missing_" + key])
                field.focus_set()
                return
        set_running(True)
        password.set("")
        status.set(TEXT[language]["connecting"])
        threading.Thread(target=worker, args=(device, user, secret, language), daemon=True).start()

    def handle_events():
        while True:
            try:
                event, payload = events.get_nowait()
            except queue.Empty:
                break
            if event == "host_key":
                device, algorithm, fingerprint, reply = payload
                trusted = messagebox.askyesno(
                    TEXT[language]["trust_title"],
                    TEXT[language]["trust"].format(device=device, algorithm=algorithm, fingerprint=fingerprint),
                    default="no", parent=root,
                )
                reply.put(trusted)
            elif event == "progress":
                status.set(payload)
            elif event == "failed":
                set_running(False)
                status.set(TEXT[language]["retry"])
                messagebox.showerror(TEXT[language]["failed"], payload, parent=root)
                entries[2].focus_set()
            elif event == "done":
                set_running(False)
        root.after(100, handle_events)

    def close():
        if running:
            messagebox.showinfo(TEXT[language]["busy"], TEXT[language]["wait"], parent=root)
        else:
            root.destroy()

    install_button = ttk.Button(page, textvariable=labels["install"], command=start, default="active")
    install_button.grid(row=8, column=1, sticky="e")
    licenses = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "THIRD-PARTY-NOTICES.txt"
    if licenses.is_file():
        ttk.Button(page, textvariable=labels["licenses"], command=lambda: os.startfile(licenses)).grid(
            row=8, column=0, sticky="w"
        )
    localize()
    root.protocol("WM_DELETE_WINDOW", close)
    root.bind("<Return>", start)
    (entries[2] if default_host and default_user else entries[0]).focus_set()
    if smoke_test:
        for language in LANGUAGES:
            selector.set(LANGUAGES[language])
            localize()
            root.update_idletasks()
            if labels["install"].get() != TEXT[language]["install"]:
                raise RuntimeError("Installer translation is unavailable")
        root.destroy()
        return 0
    root.after(100, handle_events)
    root.mainloop()
    return 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--bridge"]:
        from pc_bridge import main as bridge_main
        return bridge_main()
    if args == ["--self-test"]:
        from release_check import run_self_test
        return run_self_test()
    if args == ["--gui-smoke-test"]:
        return run_installer(smoke_test=True)
    if args:
        return 2
    return run_installer()


if __name__ == "__main__":
    raise SystemExit(main())
