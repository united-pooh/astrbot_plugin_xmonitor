"""
APScheduler 管理器

此模块提供了一个为 asyncio 和特定时区配置的 APScheduler 单例管理器。
"""

import asyncio
from typing import Any, Callable

import pytz  # type: ignore[import-untyped]
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from astrbot.api import logger


class SchedulerManager:
    """
    APScheduler 的管理器类。
    它使用 Asia/Shanghai 时区初始化调度器，并提供添加、删除和管理任务的方法。
    """

    _instance = None
    _lock = asyncio.Lock()

    def __new__(cls, *args, **kwargs):
        # 伪单例实现，确保只存在一个调度器实例。
        if cls._instance is None:
            cls._instance = super(SchedulerManager, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        """
        初始化 SchedulerManager。
        每次调用 SchedulerManager() 时都会调用此方法，但调度器本身只创建一次。
        """
        if not hasattr(self, "scheduler"):
            self.scheduler = AsyncIOScheduler(timezone=pytz.timezone("Asia/Shanghai"))
            self.scheduler.start()
            logger.info("调度器已初始化并在 Asia/Shanghai 时区启动.")

    def add_job(self, func: Callable, trigger: str, **kwargs: Any) -> str | None:
        """
        向调度器添加一个任务。

        Args:
            func (Callable): 要执行的异步函数。
            trigger (str): 触发器类型（例如：'interval'、'cron'、'date'）。
            **kwargs: 触发器的参数（例如：seconds=30, hour=8, minute=0）。

        Returns:
            str | None: 添加的任务ID，如果失败则返回 None。
        """
        try:
            job = self.scheduler.add_job(func, trigger, **kwargs)
            return job.id
        except Exception as e:
            logger.error(f"Error adding job: {e}")
            return None

    def cancel_job(self, job_id: str):
        """
        根据任务ID取消任务。

        Args:
            job_id (str): 要取消的任务的ID。
        """
        try:
            self.scheduler.remove_job(job_id)
            logger.info(f"定时任务{job_id}已取消.")
        except JobLookupError:
            logger.warning(f"未找到定时任务{job_id}")
        except Exception as e:
            logger.error(f"取消任务失败{job_id}: {e}")

    def shutdown(self):
        """
        关闭调度器。
        """
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("调度器已关闭.")
