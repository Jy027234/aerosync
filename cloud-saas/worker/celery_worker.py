"""
AeroSync Cloud - Celery 异步处理引擎
处理管道: 可编排 stage 组合
"""
from celery import Celery
from sqlalchemy.orm import sessionmaker

from api.core.database import engine
from api.core.config import settings
from api.core.logging_config import get_logger
from api.models import FileTask, TenantConfig

logger = get_logger("celery_worker")

app = Celery('aerosync', broker=settings.REDIS_URL)
app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    result_backend=settings.REDIS_URL,
    timezone='Asia/Shanghai',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=600,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        'connector-scan-all': {
            'task': 'worker.celery_worker.scan_connectors',
            'schedule': 60.0,  # 每 60 秒扫描一次
        },
    },
)

SessionLocal = sessionmaker(bind=engine)


@app.task(bind=True, max_retries=3)
def process_file_task(self, task_id: int):
    db = SessionLocal()
    try:
        task = db.query(FileTask).filter(FileTask.id == task_id).first()
        if not task:
            logger.warning(f"[Task {task_id}] 任务不存在")
            return

        cfg = db.query(TenantConfig).filter(
            TenantConfig.tenant_id == task.tenant_id
        ).first()

        from api.services.pipeline import run_pipeline
        finished = run_pipeline(db, task, cfg)

        if not finished:
            logger.info(f"[Task {task_id}] 管道暂停，等待 HITL 确认")

    except Exception as exc:
        db.rollback()
        task = db.query(FileTask).filter(FileTask.id == task_id).first()
        if task:
            task.status = "failed"
            task.status_message = f"处理失败: {str(exc)[:200]}"
            task.error_msg = str(exc)
            task.retry_count = (task.retry_count or 0) + 1
            db.commit()

        logger.error(f"[Task {task_id}] 处理失败: {exc}")

        if self.request.retries < self.max_retries:
            countdown = 60 * (2 ** self.request.retries)
            logger.warning(f"[Task {task_id}] 将在 {countdown} 秒后重试")
            raise self.retry(exc=exc, countdown=countdown)
        else:
            logger.error(f"[Task {task_id}] 已达最大重试次数")

    finally:
        db.close()


@app.task(bind=True, max_retries=2)
def continue_post_hitl(self, task_id: int):
    db = SessionLocal()
    try:
        task = db.query(FileTask).filter(FileTask.id == task_id).first()
        if not task:
            logger.warning(f"[Task {task_id}] 任务不存在")
            return

        if task.hitl_status not in ("approved", "auto_approved"):
            logger.warning(f"[Task {task_id}] HITL 状态为 {task.hitl_status}，无法继续")
            return

        cfg = db.query(TenantConfig).filter(
            TenantConfig.tenant_id == task.tenant_id
        ).first()

        from api.services.pipeline import PipelineRunner
        runner = PipelineRunner(db, task, cfg)

        # 从l hitl_review 之后的 stage 开始继续
        stages = runner.pipeline
        try:
            idx = stages.index("hitl_review")
            resume_stages = stages[idx + 1:]
        except ValueError:
            resume_stages = stages

        logger.info(f"[Task {task_id}] HITL 后续 stage: {resume_stages}")

        for stage in resume_stages:
            handler = getattr(runner, f"_stage_{stage}", None)
            if not handler:
                logger.warning(f"[Task {task_id}] 未知 stage: {stage}")
                continue
            result = handler()
            if result == "PAUSE":
                logger.info(f"[Task {task_id}] 管道再次暂停在 stage: {stage}")
                return

        logger.info(f"[Task {task_id}] HITL 后续处理完成")

    except Exception as exc:
        db.rollback()
        logger.error(f"[Task {task_id}] HITL 后续处理失败: {exc}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()


@app.task(bind=True, max_retries=2)
def scan_connectors(self):
    """定时扫描所有连接器，发现新文件并创建任务"""
    try:
        from api.connectors.manager import connector_manager
        results = connector_manager.scan_all()
        if results:
            logger.info(f"[ConnectorScan] 扫描结果: {results}")
        else:
            logger.debug("[ConnectorScan] 本次无新文件")
    except Exception as exc:
        logger.error(f"[ConnectorScan] 扫描失败: {exc}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=30)
