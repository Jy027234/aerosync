"""
AeroSync Desktop Agent - 系统托盘应用
支持：后台监控、设置窗口、最小化到托盘
"""
import os
import sys
import json
import threading
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageDraw

# ttkbootstrap 为可选依赖，未安装时使用标准 tkinter
try:
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import *
    TTKBOOTSTRAP = True
except ImportError:
    import tkinter.ttk as ttk
    TTKBOOTSTRAP = False

try:
    import pystray
    PYSTRAY = True
except ImportError:
    PYSTRAY = False

from worker import WatchWorker

CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config() -> dict:
    import yaml
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    return {}


def save_config(cfg: dict):
    import yaml
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)


def create_icon_image(size=64):
    """生成简单的托盘图标（蓝色圆角矩形 + A 字母）"""
    img = Image.new('RGB', (size, size), color=(25, 118, 210))
    dc = ImageDraw.Draw(img)
    # 绘制一个简单的飞机形状
    dc.polygon([(size//2, 8), (size-8, size-8), (8, size-8)], fill=(255, 255, 255))
    return img


class SettingsWindow:
    """设置窗口"""

    def __init__(self, config: dict, on_save: callable):
        self.config = config
        self.on_save = on_save
        self.root = ttk.Window(title="AeroSync 设置", themename="darkly") if TTKBOOTSTRAP else tk.Tk()
        if not TTKBOOTSTRAP:
            self.root.title("AeroSync 设置")
            self.root.geometry("500x450")
        else:
            self.root.geometry("520x480")
        self.root.resizable(False, False)

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self):
        frame = ttk.Frame(self.root, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)

        # 监控目录
        ttk.Label(frame, text="监控文件夹", font=("微软雅黑", 10, "bold")).pack(anchor=tk.W, pady=(0, 4))
        row1 = ttk.Frame(frame)
        row1.pack(fill=tk.X, pady=(0, 10))
        self.entry_folder = ttk.Entry(row1)
        self.entry_folder.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry_folder.insert(0, self.config.get("watch_folder", ""))
        ttk.Button(row1, text="浏览...", command=self._browse_folder, width=8).pack(side=tk.RIGHT, padx=(6, 0))

        # 云端配置
        ttk.Label(frame, text="云端地址", font=("微软雅黑", 10, "bold")).pack(anchor=tk.W, pady=(8, 4))
        self.entry_url = ttk.Entry(frame)
        self.entry_url.pack(fill=tk.X, pady=(0, 10))
        self.entry_url.insert(0, self.config.get("cloud_base_url", ""))

        ttk.Label(frame, text="Tenant ID", font=("微软雅黑", 10, "bold")).pack(anchor=tk.W, pady=(0, 4))
        self.entry_tenant = ttk.Entry(frame)
        self.entry_tenant.pack(fill=tk.X, pady=(0, 10))
        self.entry_tenant.insert(0, self.config.get("tenant_id", ""))

        ttk.Label(frame, text="API Token", font=("微软雅黑", 10, "bold")).pack(anchor=tk.W, pady=(0, 4))
        self.entry_token = ttk.Entry(frame, show="*")
        self.entry_token.pack(fill=tk.X, pady=(0, 10))
        self.entry_token.insert(0, self.config.get("api_token", ""))

        # 轮询间隔
        ttk.Label(frame, text="轮询间隔 (秒)", font=("微软雅黑", 10, "bold")).pack(anchor=tk.W, pady=(0, 4))
        self.entry_interval = ttk.Entry(frame)
        self.entry_interval.pack(fill=tk.X, pady=(0, 10))
        self.entry_interval.insert(0, str(self.config.get("scan_interval_seconds", 60)))

        # 底部按钮
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(btn_frame, text="保存并重启监控", command=self._save, bootstyle="primary" if TTKBOOTSTRAP else None).pack(side=tk.RIGHT)
        ttk.Button(btn_frame, text="取消", command=self.on_close).pack(side=tk.RIGHT, padx=(0, 8))

    def _browse_folder(self):
        folder = tk.filedialog.askdirectory()
        if folder:
            self.entry_folder.delete(0, tk.END)
            self.entry_folder.insert(0, folder)

    def _save(self):
        try:
            interval = int(self.entry_interval.get())
        except ValueError:
            interval = 60

        new_cfg = {
            **self.config,
            "watch_folder": self.entry_folder.get(),
            "cloud_base_url": self.entry_url.get(),
            "tenant_id": self.entry_tenant.get(),
            "api_token": self.entry_token.get(),
            "scan_interval_seconds": interval,
        }
        save_config(new_cfg)
        self.on_save(new_cfg)
        self.on_close()

    def on_close(self):
        self.root.destroy()

    def show(self):
        self.root.mainloop()


class TrayApp:
    """托盘应用主类"""

    def __init__(self):
        self.config = load_config()
        self.worker: WatchWorker = None
        self.icon = None
        self.settings_window = None

    def _status_handler(self, code: str, message: str):
        """工作线程状态回调"""
        if self.icon:
            title = "AeroSync - 监控中" if code in ("running", "processing") else "AeroSync - 已停止"
            self.icon.title = title

    def _upload_handler(self, filename: str, success: bool, error: str):
        """上传结果回调"""
        if self.icon:
            if success:
                self.icon.notify(f"已上传: {filename}", "AeroSync")
            else:
                self.icon.notify(f"上传失败: {filename}\n{error}", "AeroSync")

    def start_worker(self):
        if self.worker and self.worker.is_running():
            return
        self.worker = WatchWorker(
            self.config,
            on_status_change=self._status_handler,
            on_upload=self._upload_handler,
        )
        self.worker.start()

    def stop_worker(self):
        if self.worker:
            self.worker.stop()
            self.worker.join(timeout=3)

    def restart_worker(self, new_config: dict = None):
        if new_config:
            self.config = new_config
        self.stop_worker()
        self.start_worker()

    def open_settings(self):
        if self.settings_window and self.settings_window.root.winfo_exists():
            self.settings_window.root.lift()
            return
        self.settings_window = SettingsWindow(
            self.config,
            on_save=lambda cfg: self.restart_worker(cfg)
        )
        threading.Thread(target=self.settings_window.show, daemon=True).start()

    def build_menu(self):
        return pystray.Menu(
            pystray.MenuItem("打开设置", lambda: self.open_settings()),
            pystray.MenuItem("开始监控", lambda: self.start_worker(), checked=lambda item: self.worker is not None and self.worker.is_running()),
            pystray.MenuItem("停止监控", lambda: self.stop_worker()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", lambda: self.exit_app()),
        )

    def exit_app(self):
        self.stop_worker()
        if self.icon:
            self.icon.stop()
        os._exit(0)

    def run(self):
        if not PYSTRAY:
            print("[ERR] 未安装 pystray，无法启动托盘程序")
            print("请运行: pip install pystray Pillow")
            sys.exit(1)

        # 自动启动监控（如果配置为 auto 模式）
        if self.config.get("mode", "auto") == "auto":
            self.start_worker()

        self.icon = pystray.Icon(
            "AeroSync",
            icon=create_icon_image(),
            title="AeroSync - 航空文档同步助手",
            menu=self.build_menu()
        )
        print("[AeroSync Tray] 托盘程序已启动，右击图标进行操作")
        self.icon.run()


if __name__ == "__main__":
    app = TrayApp()
    app.run()
