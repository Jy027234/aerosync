"""
AeroSync Desktop Agent - 后台工作线程
支持启停控制、状态回调
"""
import time
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from uploader import CloudUploader

SUPPORTED = {'.xlsx', '.xls', '.pdf', '.doc', '.docx'}


class WatchWorker(threading.Thread):
    """
    文件监控工作线程
    - 后台轮询监控指定文件夹
    - 支持外部安全停止
    """

    def __init__(
        self,
        config: dict,
        on_status_change: Optional[Callable[[str, str], None]] = None,
        on_upload: Optional[Callable[[str, bool, str], None]] = None,
    ):
        super().__init__(daemon=True)
        self.config = config
        self._stop_event = threading.Event()
        self._running = False
        self.on_status_change = on_status_change
        self.on_upload = on_upload
        self._status = "stopped"

    @property
    def status(self) -> str:
        return self._status

    def _set_status(self, code: str, message: str = ""):
        self._status = code
        if self.on_status_change:
            try:
                self.on_status_change(code, message)
            except Exception:
                pass

    def run(self):
        self._running = True
        self._set_status("running", "监控中...")
        folder = Path(self.config['watch_folder'])
        sent_folder = folder / "sent"
        sent_folder.mkdir(exist_ok=True)

        uploader = CloudUploader(self.config)
        interval = self.config.get('scan_interval_seconds', 60)

        while not self._stop_event.is_set():
            if folder.exists():
                files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED]
                if files:
                    self._set_status("processing", f"发现 {len(files)} 个文件")
                    for f in sorted(files, key=lambda x: x.stat().st_mtime):
                        if self._stop_event.is_set():
                            break
                        success = uploader.upload_file(f)
                        if self.on_upload:
                            try:
                                self.on_upload(f.name, success, "")
                            except Exception:
                                pass
                        if success:
                            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                            archive_name = f"{timestamp}_{f.name}"
                            try:
                                shutil.move(str(f), str(sent_folder / archive_name))
                            except Exception as e:
                                if self.on_upload:
                                    self.on_upload(f.name, False, f"归档失败: {e}")
                else:
                    self._set_status("running", "等待新文件...")
            else:
                self._set_status("error", f"监控目录不存在: {folder}")

            # 等待下一轮，支持被中断
            self._stop_event.wait(interval)

        self._running = False
        self._set_status("stopped", "已停止")

    def stop(self):
        """停止监控"""
        self._set_status("stopping", "正在停止...")
        self._stop_event.set()

    def is_running(self) -> bool:
        return self._running and not self._stop_event.is_set()
