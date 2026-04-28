"""
AeroSync Desktop Agent - Cloud Uploader
PC端文件上传器：支持预签名URL直传OSS + 云端任务通知
"""
import yaml
import requests
from pathlib import Path
from typing import Dict, Optional


class CloudUploader:
    def __init__(self, config: dict):
        self.base_url = config['cloud_base_url'].rstrip('/')
        self.token = config['api_token']
        self.tenant_id = config['tenant_id']
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Tenant-ID": self.tenant_id
        }

    def get_upload_url(self, filename: str, size: int) -> Dict:
        """向云端申请预签名URL"""
        resp = requests.post(
            f"{self.base_url}/upload-url",
            headers=self.headers,
            json={"filename": filename, "size": size},
            timeout=10
        )
        resp.raise_for_status()
        return resp.json()

    def upload_file(self, file_path: Path) -> bool:
        """直传OSS + 通知云端"""
        try:
            print(f"[UPLOAD] 正在处理: {file_path.name}")

            # 1. 申请预签名URL
            presigned = self.get_upload_url(
                file_path.name,
                file_path.stat().st_size
            )

            # 2. 直传对象存储（不经过云端服务器）
            with open(file_path, 'rb') as f:
                put_resp = requests.put(
                    presigned['upload_url'],
                    data=f,
                    headers={"Content-Type": presigned.get('content_type', 'application/octet-stream')},
                    timeout=120
                )
                put_resp.raise_for_status()

            # 3. 通知云端开始处理
            notify_resp = requests.post(
                f"{self.base_url}/notify",
                headers=self.headers,
                json={
                    "object_key": presigned['object_key'],
                    "filename": file_path.name,
                    "size": file_path.stat().st_size
                },
                timeout=10
            )
            notify_resp.raise_for_status()

            result = notify_resp.json()
            print(f"[OK] {file_path.name} 已提交，任务ID: {result.get('task_id')}")
            return True

        except requests.HTTPError as e:
            print(f"[ERR] {file_path.name} HTTP错误: {e.response.status_code} - {e.response.text}")
            return False
        except requests.ConnectionError:
            print(f"[ERR] {file_path.name} 连接失败，请检查云端服务是否运行")
            return False
        except requests.Timeout:
            print(f"[ERR] {file_path.name} 请求超时")
            return False
        except Exception as e:
            print(f"[ERR] {file_path.name} 失败: {e}")
            return False

    def get_task_status(self, task_id: int) -> Optional[Dict]:
        """查询任务状态"""
        try:
            resp = requests.get(
                f"{self.base_url}/tasks/{task_id}",
                headers=self.headers,
                timeout=10
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"[ERR] 查询任务 {task_id} 失败: {e}")
            return None