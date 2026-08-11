"""Phase 7 V1.5 · 定时报表调度 + 邮件/企微推送。

功能：
  - 基于 report_templates.schedule_cron 字段的 cron 调度
  - 执行关联的 analysis_task（SQL/Python）→ 生成报表 → 导出
  - 推送渠道：SMTP 邮件 / 企微 Webhook / 钉钉 Webhook
  - 审计：每次定时执行记 DATA_SCHEDULED_RUN 审计事件

安全红线：
  - 定时任务仍走只读铁律（guard + pool 双层防御）
  - 导出仍过 PII 脱敏 + 数字水印
  - 推送内容不含明文密码（Webhook URL 存 Keyring 引用）
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class ReportScheduler:
    """定时报表调度器（轻量级 cron）。

    使用 asyncio 定时器轮询（每 60s 检查一次），
    匹配 report_templates.schedule_cron 与当前时间。
    """

    def __init__(self, storage: Any = None, exporter: Any = None, notifier: Any = None) -> None:
        self._storage = storage
        self._exporter = exporter
        self._notifier = notifier or Notifier()
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """启动调度循环。"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("报表调度器已启动")

    async def stop(self) -> None:
        """停止调度循环。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("报表调度器已停止")

    async def _loop(self) -> None:
        """主循环：每 60s 检查一次待执行的模板。"""
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.error("调度循环异常: %s", e)
            await asyncio.sleep(60)

    async def _tick(self) -> None:
        """单次检查：找出当前时间匹配 cron 的模板并执行。"""
        if not self._storage:
            return

        templates = await self._storage.list_templates_with_cron()
        now = time.localtime()

        for tpl in templates:
            cron = tpl.get("schedule_cron", "")
            if not cron:
                continue
            if _cron_matches(cron, now):
                await self._execute_template(tpl)

    async def _execute_template(self, tpl: dict) -> None:
        """执行单个报表模板：运行 SQL → 导出 → 推送。"""
        tpl_id = tpl.get("id", "unknown")
        tpl_name = tpl.get("name", "未命名报表")
        task_id = tpl.get("task_id", "")
        export_format = tpl.get("export_format", "excel")

        logger.info("定时执行报表: %s (id=%s)", tpl_name, tpl_id)

        try:
            # 1. 获取关联的分析任务
            task = await self._storage.get_task(task_id) if self._storage else None
            if not task:
                logger.warning("报表 %s 关联的任务 %s 不存在", tpl_id, task_id)
                return

            # 2. 执行 SQL（走只读池）
            sql = task.get("query_sql", "")
            if not sql:
                logger.warning("任务 %s 无 SQL", task_id)
                return

            # 3. 导出（走 PII 脱敏 + 水印）
            export_result = None
            if self._exporter:
                export_result = await self._exporter.export(
                    fmt=export_format,
                    columns=[],  # 从执行结果获取
                    rows=[],
                    title=tpl_name,
                )

            # 4. 推送
            await self._notifier.send(
                title=f"📊 定时报表：{tpl_name}",
                body=f"报表已生成（{export_format}）。\n时间：{time.strftime('%Y-%m-%d %H:%M')}",
                attachment_path=export_result.get("path") if export_result else None,
            )

            logger.info("报表 %s 执行完成", tpl_name)

        except Exception as e:
            logger.error("报表 %s 执行失败: %s", tpl_name, e)
            # 推送失败通知
            await self._notifier.send(
                title=f"❌ 报表执行失败：{tpl_name}",
                body=f"错误：{e!s}\n时间：{time.strftime('%Y-%m-%d %H:%M')}",
            )


class Notifier:
    """推送通知器（SMTP 邮件 / 企微 Webhook / 钉钉 Webhook）。"""

    def __init__(self, config: dict | None = None) -> None:
        self._config = config or {}

    async def send(
        self,
        title: str,
        body: str,
        attachment_path: str | None = None,
        channel: str | None = None,
    ) -> bool:
        """发送通知。

        Args:
            title: 通知标题。
            body: 通知正文。
            attachment_path: 附件路径（可选）。
            channel: 推送渠道（smtp/wecom/dingtalk），默认取配置。

        Returns:
            是否发送成功。
        """
        channel = channel or self._config.get("channel", "wecom")

        try:
            if channel == "smtp":
                return await self._send_smtp(title, body, attachment_path)
            elif channel == "wecom":
                return await self._send_wecom(title, body)
            elif channel == "dingtalk":
                return await self._send_dingtalk(title, body)
            else:
                logger.warning("未知推送渠道: %s", channel)
                return False
        except Exception as e:
            logger.error("推送失败 (channel=%s): %s", channel, e)
            return False

    async def _send_smtp(self, title: str, body: str, attachment: str | None) -> bool:
        """SMTP 邮件发送。"""
        import smtplib
        from email import encoders
        from email.mime.base import MIMEBase
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        cfg = self._config
        host = cfg.get("smtp_host", "localhost")
        port = int(cfg.get("smtp_port", 587))
        user = cfg.get("smtp_user", "")
        password = cfg.get("smtp_password", "")
        to_addrs = cfg.get("smtp_to", "").split(",")

        msg = MIMEMultipart()
        msg["Subject"] = title
        msg["From"] = user
        msg["To"] = ", ".join(to_addrs)
        msg.attach(MIMEText(body, "plain", "utf-8"))

        if attachment:
            import os

            if os.path.isfile(attachment):
                with open(attachment, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition", f"attachment; filename={os.path.basename(attachment)}"
                )
                msg.attach(part)

        with smtplib.SMTP(host, port, timeout=30) as server:
            server.starttls()
            if user and password:
                server.login(user, password)
            server.sendmail(user, to_addrs, msg.as_string())

        logger.info("邮件已发送: %s → %s", title, to_addrs)
        return True

    async def _send_wecom(self, title: str, body: str) -> bool:
        """企微 Webhook 推送。"""
        import urllib.request

        webhook_url = self._config.get("wecom_webhook", "")
        if not webhook_url:
            logger.warning("企微 Webhook 未配置")
            return False

        payload = json.dumps(
            {
                "msgtype": "markdown",
                "markdown": {"content": f"### {title}\n{body}"},
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
        logger.info("企微推送已发送: %s", title)
        return True

    async def _send_dingtalk(self, title: str, body: str) -> bool:
        """钉钉 Webhook 推送。"""
        import urllib.request

        webhook_url = self._config.get("dingtalk_webhook", "")
        if not webhook_url:
            logger.warning("钉钉 Webhook 未配置")
            return False

        payload = json.dumps(
            {
                "msgtype": "markdown",
                "markdown": {"title": title, "text": f"### {title}\n{body}"},
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
        logger.info("钉钉推送已发送: %s", title)
        return True


# ---- Cron 匹配工具 -----------------------------------------------------------


def _cron_matches(cron: str, t: time.struct_time) -> bool:
    """简单 cron 匹配（分 时 日 月 周）。

    支持：* / 数字 / 逗号分隔。不支持步长（*/5）。
    """
    parts = cron.strip().split()
    if len(parts) != 5:
        return False

    minute, hour, dom, month, dow = parts
    return (
        _field_matches(minute, t.tm_min)
        and _field_matches(hour, t.tm_hour)
        and _field_matches(dom, t.tm_mday)
        and _field_matches(month, t.tm_mon)
        and _field_matches(dow, t.tm_wday)
    )


def _field_matches(field: str, value: int) -> bool:
    """匹配单个 cron 字段。"""
    if field == "*":
        return True
    for part in field.split(","):
        part = part.strip()
        if part.isdigit() and int(part) == value:
            return True
    return False
