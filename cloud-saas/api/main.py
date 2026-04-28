"""
AeroSync Cloud API - FastAPI 主入口
提供：预签名URL生成、上传通知、任务查询、租户管理
"""
import os
import uuid
import time
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Depends, HTTPException, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from api.core.database import get_db, Base, engine
from api.core.config import settings
from api.core.logging_config import setup_logging, get_logger
from api.models import FileTask, TenantConfig
from api.services.storage import get_storage
from api.connectors.manager import connector_manager
from worker.celery_worker import process_file_task

# 初始化日志
setup_logging(os.getenv("LOG_LEVEL", "INFO"))
logger = get_logger(__name__)


# ============== Pydantic 请求模型 ==============

class UploadUrlRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    size: int = Field(..., gt=0, lt=1024 * 1024 * 1024)  # 最大 1GB


class NotifyRequest(BaseModel):
    object_key: str = Field(..., min_length=1)
    filename: str = Field(..., min_length=1)
    size: int = Field(..., ge=0)


class TenantConfigUpdate(BaseModel):
    name: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    rules: Optional[List[dict]] = None
    custom_prompt: Optional[str] = None
    hitl_config: Optional[Dict[str, Any]] = None
    pipeline: Optional[Dict[str, Any]] = None
    connectors: Optional[List[Dict[str, Any]]] = None
    enabled: Optional[int] = None


class TaskListResponse(BaseModel):
    tasks: List[dict]
    total: int
    page: int
    page_size: int


# ============== Lifespan 生命周期管理 ==============

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # Startup: 初始化数据库表 + 存储客户端
    Base.metadata.create_all(bind=engine)
    try:
        storage = get_storage()
        logger.info(f"[Storage] 存储引擎初始化成功: {type(storage).__name__}")
    except Exception as e:
        logger.warning(f"[Storage] 存储引擎初始化失败（不影响API启动）: {e}")
    yield
    # Shutdown: 清理资源
    logger.info("[API] 服务关闭")


# ============== FastAPI 应用实例 ==============

app = FastAPI(
    title="AeroSync Cloud API",
    description="航空业务文档智能解析与推送平台",
    version="1.1.0",
    lifespan=lifespan
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件：管理后台前端
static_dir = os.path.join(os.path.dirname(__file__), "static", "admin")
if os.path.isdir(static_dir):
    app.mount("/admin", StaticFiles(directory=static_dir, html=True), name="admin")
    logger.info(f"[AdminUI] 管理后台挂载于 /admin")


# ============== 请求日志中间件 ==============

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """记录请求方法、路径、耗时"""
    start_time = time.time()
    method = request.method
    path = request.url.path
    client = request.client.host if request.client else "unknown"

    try:
        response = await call_next(request)
    except Exception as exc:
        duration = (time.time() - start_time) * 1000
        logger.error(f"{method} {path} - {client} - 异常: {exc} - {duration:.2f}ms")
        raise

    duration = (time.time() - start_time) * 1000
    logger.info(f"{method} {path} - {response.status_code} - {client} - {duration:.2f}ms")
    return response


# ============== 全局异常处理器 ==============

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """捕获 HTTPException 并返回统一格式"""
    logger.warning(f"HTTPException {exc.status_code} 在 {request.url.path}: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "status_code": exc.status_code}
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """捕获请求参数校验错误"""
    logger.warning(f"ValidationError 在 {request.url.path}: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={
            "detail": "请求参数校验失败",
            "errors": exc.errors(),
            "status_code": 422
        }
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """捕获所有未处理异常，返回通用 500"""
    logger.exception(f"未处理异常 在 {request.url.path}: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "detail": "服务器内部错误，请稍后重试",
            "status_code": 500
        }
    )


# ============== 鉴权依赖 ==============

def verify_token(authorization: str = Header(...)):
    """简化鉴权：校验 Bearer Token"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization[7:]
    if token != settings.API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    return token


# ============== API 路由 ==============

@app.post("/api/v1/upload-url")
def get_upload_url(
    req: UploadUrlRequest,
    x_tenant_id: str = Header(...),
    authorization: str = Header(...)
):
    """
    生成存储预签名上传 URL
    - PC 端先调用此接口获取上传地址
    - 然后直传对象存储（不经过本服务器）
    """
    verify_token(authorization)

    try:
        storage = get_storage()
        object_key, upload_url, headers = storage.generate_upload_url(
            filename=req.filename,
            content_type="application/octet-stream",
            tenant_id=x_tenant_id
        )
        expires = 15 * 60
        logger.info(f"生成上传URL: tenant={x_tenant_id}, key={object_key}")
        return {
            "upload_url": upload_url,
            "object_key": object_key,
            "content_type": "application/octet-stream",
            "expires_in": expires,
            "headers": headers
        }
    except Exception as e:
        logger.error(f"生成上传URL失败: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate upload URL: {str(e)}")


@app.post("/api/v1/notify")
def notify_upload(
    req: NotifyRequest,
    x_tenant_id: str = Header(...),
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    """
    PC 端上传完成后通知，创建异步处理任务
    - 校验 object_key 所有权
    - 写入数据库任务表
    - 投递 Celery 异步队列
    """
    verify_token(authorization)

    # 安全检查：确保 object_key 属于当前租户
    if not req.object_key.startswith(f"uploads/{x_tenant_id}/"):
        raise HTTPException(status_code=403, detail="Object key does not belong to tenant")

    # 获取租户配置
    tenant_cfg = db.query(TenantConfig).filter(
        TenantConfig.tenant_id == x_tenant_id
    ).first()

    # 写入任务
    file_type = os.path.splitext(req.filename)[1].lower().replace('.', '')
    task = FileTask(
        tenant_id=x_tenant_id,
        filename=req.filename,
        object_key=req.object_key,
        file_size=req.size,
        file_type=file_type,
        status="pending",
        webhook_url=tenant_cfg.webhook_url if tenant_cfg else settings.DEFAULT_WEBHOOK_URL,
        webhook_secret=tenant_cfg.webhook_secret if tenant_cfg else settings.DEFAULT_WEBHOOK_SECRET,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    # 投递 Celery 异步任务
    process_file_task.delay(task.id)
    logger.info(f"任务创建完成: task_id={task.id}, tenant={x_tenant_id}, file={req.filename}")

    return {
        "task_id": task.id,
        "status": "queued",
        "message": "文件已接收，正在异步处理"
    }


@app.get("/api/v1/tasks/{task_id}")
def get_task(
    task_id: int,
    x_tenant_id: str = Header(...),
    db: Session = Depends(get_db)
):
    """查询单个任务状态"""
    task = db.query(FileTask).filter(
        FileTask.id == task_id,
        FileTask.tenant_id == x_tenant_id
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task.to_dict()


@app.get("/api/v1/tasks")
def list_tasks(
    x_tenant_id: str = Header(...),
    status: Optional[str] = Query(None, description="按状态过滤"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """查询任务列表（分页）"""
    query = db.query(FileTask).filter(FileTask.tenant_id == x_tenant_id)

    if status:
        query = query.filter(FileTask.status == status)

    total = query.count()
    tasks = query.order_by(FileTask.created_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size).all()

    return {
        "tasks": [t.to_dict() for t in tasks],
        "total": total,
        "page": page,
        "page_size": page_size
    }


@app.get("/api/v1/health")
def health_check():
    """健康检查端点"""
    return {
        "status": "ok",
        "version": "1.1.0",
        "timestamp": datetime.utcnow().isoformat()
    }


# ============== Phase 2: 管理后台接口 ==============

@app.get("/api/v1/admin/tenants/{tenant_id}/config")
def get_tenant_config(
    tenant_id: str,
    db: Session = Depends(get_db)
):
    """获取租户配置"""
    cfg = db.query(TenantConfig).filter(
        TenantConfig.tenant_id == tenant_id
    ).first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Tenant config not found")
    return cfg.to_dict()


@app.put("/api/v1/admin/tenants/{tenant_id}/config")
def update_tenant_config(
    tenant_id: str,
    update: TenantConfigUpdate,
    db: Session = Depends(get_db)
):
    """更新租户配置（规则引擎、Webhook 等）"""
    cfg = db.query(TenantConfig).filter(
        TenantConfig.tenant_id == tenant_id
    ).first()

    if not cfg:
        cfg = TenantConfig(tenant_id=tenant_id)
        db.add(cfg)

    update_data = update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(cfg, field, value)

    db.commit()
    db.refresh(cfg)
    logger.info(f"租户配置更新: tenant_id={tenant_id}")
    return cfg.to_dict()


@app.post("/api/v1/admin/tenants/{tenant_id}/config")
def create_tenant_config(
    tenant_id: str,
    update: TenantConfigUpdate,
    db: Session = Depends(get_db)
):
    """创建租户配置"""
    existing = db.query(TenantConfig).filter(
        TenantConfig.tenant_id == tenant_id
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Tenant config already exists")

    cfg = TenantConfig(tenant_id=tenant_id, **update.model_dump(exclude_unset=True))
    db.add(cfg)
    db.commit()
    db.refresh(cfg)
    logger.info(f"租户配置创建: tenant_id={tenant_id}")
    return cfg.to_dict()


@app.get("/api/v1/admin/tasks")
def admin_list_tasks(
    status: Optional[str] = Query(None),
    tenant_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """管理后台：查看所有任务（支持过滤）"""
    query = db.query(FileTask)

    if status:
        query = query.filter(FileTask.status == status)
    if tenant_id:
        query = query.filter(FileTask.tenant_id == tenant_id)

    total = query.count()
    tasks = query.order_by(FileTask.created_at.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size).all()

    return {
        "tasks": [t.to_dict() for t in tasks],
        "total": total,
        "page": page,
        "page_size": page_size
    }


@app.get("/api/v1/admin/stats")
def admin_stats(
    db: Session = Depends(get_db)
):
    """管理后台：统计看板"""
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    total_tasks = db.query(func.count(FileTask.id)).scalar()
    today_tasks = db.query(func.count(FileTask.id)).filter(FileTask.created_at >= today).scalar()
    pending_hitl = db.query(func.count(FileTask.id)).filter(FileTask.status == "hitl_review").scalar()
    failed_tasks = db.query(func.count(FileTask.id)).filter(FileTask.status == "failed").scalar()
    delivered_tasks = db.query(func.count(FileTask.id)).filter(FileTask.status == "delivered").scalar()

    # 按状态分布
    status_counts = db.query(FileTask.status, func.count(FileTask.id)).group_by(FileTask.status).all()

    # 按文件类型分布
    type_counts = db.query(FileTask.file_type, func.count(FileTask.id)).group_by(FileTask.file_type).all()

    return {
        "total_tasks": total_tasks,
        "today_tasks": today_tasks,
        "pending_hitl": pending_hitl,
        "failed_tasks": failed_tasks,
        "delivered_tasks": delivered_tasks,
        "success_rate": round(delivered_tasks / max(total_tasks, 1) * 100, 1),
        "status_distribution": {s: c for s, c in status_counts},
        "type_distribution": {t: c for t, c in type_counts},
    }


@app.post("/api/v1/admin/tasks/{task_id}/retry")
def retry_task(
    task_id: int,
    db: Session = Depends(get_db)
):
    """管理后台：手动重试失败任务"""
    task = db.query(FileTask).filter(FileTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status not in ["failed", "delivered"]:
        raise HTTPException(status_code=400, detail=f"Cannot retry task with status: {task.status}")

    task.status = "pending"
    task.error_msg = None
    task.retry_count = task.retry_count or 0
    db.commit()

    # 重新投递
    process_file_task.delay(task.id)
    logger.info(f"任务重试: task_id={task_id}")

    return {"message": "Task requeued", "task_id": task.id}


# ============== Phase 3: HITL 人工审查接口 ==============

class HITLApproveRequest(BaseModel):
    reviewer: str = Field(default="admin", min_length=1)
    comment: Optional[str] = None


class HITLModifyRequest(BaseModel):
    reviewer: str = Field(default="admin", min_length=1)
    comment: Optional[str] = None
    tags: Optional[List[str]] = None
    doc_type: Optional[str] = None
    summary: Optional[str] = None
    structured_data: Optional[Dict[str, Any]] = None
    priority: Optional[str] = None


def _auto_approve_timeout_if_needed(db: Session, task: FileTask, cfg: TenantConfig) -> bool:
    """检查 HITL 是否超时，若超时则自动通过"""
    if task.hitl_status != "pending":
        return False
    hitl_cfg = cfg.hitl_config if cfg and cfg.hitl_config else {}
    if not hitl_cfg.get("enabled"):
        return False

    from api.services.hitl_service import HITLService
    hitl = HITLService(hitl_cfg)
    if hitl.check_timeout(task.created_at):
        task.hitl_status = "auto_approved"
        task.hitl_reviewed_at = datetime.utcnow()
        task.hitl_comment = "超时自动通过"
        db.commit()
        logger.info(f"[Task {task.id}] HITL 超时自动通过")
        return True
    return False


@app.post("/api/v1/admin/tasks/{task_id}/hitl/approve")
def hitl_approve(
    task_id: int,
    req: HITLApproveRequest,
    db: Session = Depends(get_db)
):
    """确认 AI 分析结果，继续后续处理管道
    - 更新 HITL 状态为 approved
    - 投递 continue_post_hitl Celery 任务
    """
    task = db.query(FileTask).filter(FileTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != "hitl_review":
        raise HTTPException(status_code=400, detail=f"当前任务状态不是 hitl_review: {task.status}")
    if task.hitl_status != "pending":
        raise HTTPException(status_code=400, detail=f"当前 HITL 状态不是 pending: {task.hitl_status}")

    task.hitl_status = "approved"
    task.hitl_reviewed_by = req.reviewer
    task.hitl_reviewed_at = datetime.utcnow()
    task.hitl_comment = req.comment or "审核通过"
    db.commit()

    from worker.celery_worker import continue_post_hitl
    continue_post_hitl.delay(task.id)
    logger.info(f"[Task {task_id}] HITL 已确认，继续后续处理")

    return {"message": "HITL approved, processing continued", "task_id": task.id}


@app.post("/api/v1/admin/tasks/{task_id}/hitl/reject")
def hitl_reject(
    task_id: int,
    req: HITLApproveRequest,
    db: Session = Depends(get_db)
):
    """拒绝 AI 分析结果，标记任务为失败"""
    task = db.query(FileTask).filter(FileTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != "hitl_review":
        raise HTTPException(status_code=400, detail=f"当前任务状态不是 hitl_review: {task.status}")

    task.hitl_status = "rejected"
    task.hitl_reviewed_by = req.reviewer
    task.hitl_reviewed_at = datetime.utcnow()
    task.hitl_comment = req.comment or "审核拒绝"
    task.status = "failed"
    task.status_message = "HITL 审核拒绝"
    db.commit()
    logger.info(f"[Task {task_id}] HITL 已拒绝")

    return {"message": "HITL rejected", "task_id": task.id}


@app.post("/api/v1/admin/tasks/{task_id}/hitl/modify")
def hitl_modify(
    task_id: int,
    req: HITLModifyRequest,
    db: Session = Depends(get_db)
):
    """修改 AI 分析结果后确认，继续后续处理管道"""
    task = db.query(FileTask).filter(FileTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != "hitl_review":
        raise HTTPException(status_code=400, detail=f"当前任务状态不是 hitl_review: {task.status}")
    if task.hitl_status != "pending":
        raise HTTPException(status_code=400, detail=f"当前 HITL 状态不是 pending: {task.hitl_status}")

    # 基于原始 AI 结果进行修改
    original = task.ai_result or {}
    modified = original.copy()

    if req.tags is not None:
        modified["tags"] = req.tags
    if req.doc_type is not None:
        modified["doc_type"] = req.doc_type
    if req.summary is not None:
        modified["summary"] = req.summary
    if req.structured_data is not None:
        modified["structured_data"] = req.structured_data
    if req.priority is not None:
        modified["priority"] = req.priority

    task.hitl_modified_data = modified
    task.hitl_status = "approved"
    task.hitl_reviewed_by = req.reviewer
    task.hitl_reviewed_at = datetime.utcnow()
    task.hitl_comment = req.comment or "审核修改后通过"
    db.commit()

    from worker.celery_worker import continue_post_hitl
    continue_post_hitl.delay(task.id)
    logger.info(f"[Task {task_id}] HITL 已修改并确认，继续后续处理")

    return {"message": "HITL modified and approved, processing continued", "task_id": task.id}


@app.get("/api/v1/admin/tasks/{task_id}/hitl")
def get_hitl_detail(
    task_id: int,
    db: Session = Depends(get_db)
):
    """获取任务 HITL 审查详情（用于管理后台审核页面）"""
    task = db.query(FileTask).filter(FileTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    cfg = db.query(TenantConfig).filter(
        TenantConfig.tenant_id == task.tenant_id
    ).first()

    # 检查超时自动通过
    if _auto_approve_timeout_if_needed(db, task, cfg):
        # 超时后自动投递后续
        from worker.celery_worker import continue_post_hitl
        continue_post_hitl.delay(task.id)

    ai_result = task.ai_result or {}
    parsed = task.parsed_data or {}

    return {
        "task_id": task.id,
        "filename": task.filename,
        "status": task.status,
        "hitl_status": task.hitl_status,
        "suggested_tags": ai_result.get("tags", []),
        "suggested_doc_type": ai_result.get("doc_type", "未知"),
        "suggested_summary": ai_result.get("summary", ""),
        "suggested_structured_data": ai_result.get("structured_data", {}),
        "confidence": ai_result.get("confidence", 0),
        "priority": ai_result.get("priority", "normal"),
        "raw_preview": parsed.get("text", "")[:1000],
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "reviewed_by": task.hitl_reviewed_by,
        "reviewed_at": task.hitl_reviewed_at.isoformat() if task.hitl_reviewed_at else None,
        "comment": task.hitl_comment,
    }


# ============== Phase 8: 多源连接器 ==============

@app.post("/api/v1/admin/connectors/scan/{tenant_id}")
def admin_connector_scan(
    tenant_id: str,
    db: Session = Depends(get_db)
):
    """管理后台：手动触发连接器扫描"""
    try:
        count = connector_manager.scan_tenant(tenant_id, db)
        return {"tenant_id": tenant_id, "created_tasks": count}
    except Exception as e:
        logger.error(f"[Connector] 手动扫描失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/connectors/dingtalk/webhook")
def dingtalk_webhook(
    request: Request,
    db: Session = Depends(get_db)
):
    """钉钉机器人 Webhook 回调
不需要 Bearer Token，但需验证签名
"""
    try:
        payload = request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # 检查租户（可通过某些字段映射，这里使用默认租户）
    tenant_id = payload.get("tenant_id", "default")
    cfg = db.query(TenantConfig).filter(TenantConfig.tenant_id == tenant_id).first()
    if not cfg or not cfg.connectors:
        return {"message": "no connectors configured"}

    from api.connectors.dingtalk_connector import DingTalkConnector
    for c in cfg.connectors:
        if c.get("type") == "dingtalk":
            conn = DingTalkConnector(tenant_id, c)
            timestamp = request.headers.get("timestamp", "")
            sign = request.headers.get("sign", "")
            if not conn.verify_signature(timestamp, sign):
                raise HTTPException(status_code=403, detail="signature invalid")
            sf = conn.handle_webhook(payload)
            if sf:
                # 保存文件到存储并创建任务
                try:
                    storage = get_storage()
                    object_key, upload_url, headers = storage.generate_upload_url(
                        filename=sf.filename,
                        content_type=sf.content_type,
                        tenant_id=tenant_id
                    )
                    # 上传到存储
                    import requests as req_lib
                    put_resp = req_lib.put(upload_url, data=sf.raw_bytes, headers=headers)
                    put_resp.raise_for_status()

                    task = FileTask(
                        tenant_id=tenant_id,
                        filename=sf.filename,
                        object_key=object_key,
                        file_size=sf.size,
                        file_type=object_key.rsplit(".", 1)[-1] if "." in object_key else "unknown",
                        status="pending",
                        source="dingtalk",
                        meta={"dingtalk": sf.meta},
                    )
                    db.add(task)
                    db.commit()
                    db.refresh(task)
                    process_file_task.delay(task.id)
                    return {"message": "file accepted", "task_id": task.id}
                except Exception as e:
                    logger.error(f"[DingTalk] 保存文件失败: {e}")
                    raise HTTPException(status_code=500, detail="save file failed")
    return {"message": "ignored"}


@app.get("/")
def root():
    """API 根路径"""
    return {
        "service": "AeroSync Cloud API",
        "version": "1.1.0",
        "docs": "/docs"
    }
