"""
AeroSync Desktop Agent - Main Entry
PC端守护进程：监控文件夹 → 扫描 → 上传 → 循环
"""
import time
import shutil
from datetime import datetime
from pathlib import Path
from uploader import CloudUploader
import yaml

SUPPORTED = {'.xlsx', '.xls', '.pdf', '.doc', '.docx'}


def load_config():
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def ensure_sent_folder(watch_folder: Path) -> Path:
    """确保归档文件夹存在"""
    sent_folder = watch_folder / "sent"
    sent_folder.mkdir(exist_ok=True)
    return sent_folder


def scan_and_upload(config: dict):
    folder = Path(config['watch_folder'])
    if not folder.exists():
        print(f"[ERR] 文件夹不存在: {folder}")
        return

    uploader = CloudUploader(config)
    sent_folder = ensure_sent_folder(folder)

    files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED]

    if not files:
        print("[INFO] 未发现待处理文件")
        return

    print(f"[INFO] 发现 {len(files)} 个文件")
    for f in sorted(files, key=lambda x: x.stat().st_mtime):
        success = uploader.upload_file(f)
        if success:
            # 归档：移动到 sent/时间戳_原文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_name = f"{timestamp}_{f.name}"
            try:
                shutil.move(str(f), str(sent_folder / archive_name))
                print(f"[ARCHIVE] 已归档到 sent/{archive_name}")
            except Exception as e:
                print(f"[WARN] 归档失败 {f.name}: {e}")


def main():
    config = load_config()
    mode = config.get('mode', 'auto')

    print(f"[AeroSync Desktop Agent v1.0]")
    print(f"[CONFIG] 租户: {config['tenant_id']}")
    print(f"[CONFIG] 监控目录: {config['watch_folder']}")
    print(f"[CONFIG] 支持格式: {SUPPORTED}")

    if mode == 'once':
        print("[MODE] 单次扫描模式")
        scan_and_upload(config)
    else:
        interval = config.get('scan_interval_seconds', 60)
        print(f"[MODE] 后台监控模式，间隔 {interval} 秒 (Ctrl+C 退出)")
        try:
            while True:
                scan_and_upload(config)
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n[INFO] 收到退出信号，正在关闭...")


if __name__ == "__main__":
    main()