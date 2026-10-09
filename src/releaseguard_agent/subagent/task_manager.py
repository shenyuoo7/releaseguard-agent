"""Background TaskManager for asynchronous subagent task lifecycle and notifications."""

import asyncio
from collections.abc import Awaitable
import time
from typing import Any

from releaseguard_agent.subagent.models import BackgroundTask


class TaskManager:
    """Manages asynchronous background subagent tasks and notification delivery."""

    def __init__(self) -> None:
        self.tasks: dict[str, BackgroundTask] = {}
        self.notify_queue: asyncio.Queue[BackgroundTask] = asyncio.Queue()
        self._asyncio_tasks: dict[str, asyncio.Task[Any]] = {}

    def launch(
        self,
        task_id: str,
        name: str,
        coro: Awaitable[str],
    ) -> BackgroundTask:
        """Launch an asynchronous subagent task in the background."""
        task = BackgroundTask(id=task_id, name=name, status="running")
        self.tasks[task_id] = task

        async def _worker() -> None:
            try:
                result = await coro
                task.status = "completed"
                task.result = result
            except asyncio.CancelledError:
                task.status = "failed"
                task.error = "Task was cancelled."
            except Exception as e:
                task.status = "failed"
                task.error = str(e)
            finally:
                task.end_time = time.time()
                await self.notify_queue.put(task)

        t = asyncio.create_task(_worker())
        self._asyncio_tasks[task_id] = t
        return task

    def adopt_running(
        self,
        task_id: str,
        name: str,
        running_task: asyncio.Task[str],
    ) -> BackgroundTask:
        """Adopt an in-flight foreground task into the background."""
        task = BackgroundTask(id=task_id, name=name, status="running")
        self.tasks[task_id] = task

        async def _monitor() -> None:
            try:
                result = await running_task
                task.status = "completed"
                task.result = result
            except asyncio.CancelledError:
                task.status = "failed"
                task.error = "Adopted task was cancelled."
            except Exception as e:
                task.status = "failed"
                task.error = str(e)
            finally:
                task.end_time = time.time()
                await self.notify_queue.put(task)

        t = asyncio.create_task(_monitor())
        self._asyncio_tasks[task_id] = t
        return task

    def get_pending_notifications(self) -> list[str]:
        """Drain all completed task notifications without blocking."""
        notifications: list[str] = []
        while not self.notify_queue.empty():
            try:
                t = self.notify_queue.get_nowait()
                notifications.append(t.to_xml())
            except asyncio.QueueEmpty:
                break
        return notifications

    def get_task(self, task_id: str) -> BackgroundTask | None:
        """Retrieve task by id."""
        return self.tasks.get(task_id)

    def list_tasks(self) -> list[BackgroundTask]:
        """List all tracked tasks."""
        return list(self.tasks.values())

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task."""
        t = self._asyncio_tasks.get(task_id)
        if t and not t.done():
            t.cancel()
            return True
        return False
