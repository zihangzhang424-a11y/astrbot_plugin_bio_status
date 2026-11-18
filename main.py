# -*- coding: utf-8 -*-
"""体征档案 —— 数值状态仪表盘插件（AstrBot 4.29.1）"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:
    from astrbot.core.agent.message import TextPart
except ImportError:  # pragma: no cover
    TextPart = None  # type: ignore

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path
except ImportError:  # pragma: no cover
    get_astrbot_plugin_data_path = None  # type: ignore

from .state import STAGE_ORDER, StateStore

PLUGIN_NAME = "astrbot_plugin_bio_status"


@register(
    PLUGIN_NAME,
    "",
    "体征档案：后台维护一组计数器并按时间推进，把读数拼进提示词，附可视化页面。",
    "1.0.0",
)
class BioStatusPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.cfg: Dict[str, Any] = dict(config or {}) if config else {}

        if get_astrbot_plugin_data_path is not None:
            base = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        else:  # 兜底：老版本没有该函数
            base = Path(__file__).resolve().parent / "data"

        self.store = StateStore(base, self.cfg)
        self._task: asyncio.Task | None = None

        # 每个接口单独注册，一个失败不影响另一个，也绝不静默
        for route, handler, methods, desc in (
            (f"/{PLUGIN_NAME}/state", self._api_state, ["GET"], "读取当前体征读数"),
            (f"/{PLUGIN_NAME}/action", self._api_action, ["POST"], "手动调整体征状态"),
        ):
            try:
                context.register_web_api(route, handler, methods, desc)
                logger.info(f"[bio-status] 接口已注册：{methods[0]} {route}")
            except Exception as e:
                logger.error(f"[bio-status] 接口注册失败 {route}：{e}")

    # ---------- 生命周期 ----------

    async def initialize(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
        logger.info("[bio-status] 已启动")

    async def terminate(self):
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.store.save_sync()
        logger.info("[bio-status] 已停止")

    async def _loop(self):
        interval = max(0.5, float(self.cfg.get("tickMs", 3000)) / 1000.0)
        try:
            while True:
                await asyncio.sleep(interval)
                if self.store.tick():
                    await self.store.save()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pragma: no cover
            logger.error(f"[bio-status] tick 循环异常：{e}")

    # ---------- 钩子 ----------

    @filter.on_llm_request()
    async def inject_state(self, event: AstrMessageEvent, req):
        try:
            hint = self.store.prompt_hint()
            block = self.store.readout()
            if hint:
                block = block + "\n" + hint
            if TextPart is not None:
                part = TextPart(text=block)
                if hasattr(part, "mark_as_temp"):
                    part = part.mark_as_temp()
                req.extra_user_content_parts.append(part)
            else:  # 兜底
                req.system_prompt = (req.system_prompt or "") + "\n" + block + "\n"
        except Exception as e:
            logger.warning(f"[bio-status] 注入失败：{e}")

    @filter.on_llm_response()
    async def observe_reply(self, event: AstrMessageEvent, resp, *args, **kwargs):
        try:
            text = getattr(resp, "completion_text", None)
            if not text:
                chain = getattr(resp, "result_chain", None)
                if chain is not None and hasattr(chain, "get_plain_text"):
                    text = chain.get_plain_text()
            if text and self.store.apply_text(text):
                await self.store.save()
        except Exception as e:
            logger.warning(f"[bio-status] 读取回复失败：{e}")

    # ---------- 接口与指令 ----------

    async def _api_state(self):
        from astrbot.api.web import json_response

        # 显式给 JSONResponse，确保中文以 UTF-8 输出，不被二次解码成乱码
        return json_response(self.store.snapshot())

    async def _api_action(self):
        """手动操作接口。请求体 JSON：{"action": "...", "value": n}"""
        try:
            from astrbot.api.web import request as web_request

            payload = await web_request.json({}) or {}
        except Exception as e:
            logger.warning(f"[bio-status] 读取请求体失败：{e}")
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        action = str(payload.get("action") or "")
        if not action:
            out = self.store.snapshot()
            out["message"] = "请求体里没有 action，可能被网关改写了，看服务端日志"
            return out
        try:
            if action == "set_desire":
                msg = self.store.set_desire(int(payload.get("value", 30)))
            elif action == "clear_residue":
                msg = self.store.clear_residue()
            elif action == "add_residue":
                raw = payload.get("value")
                msg = self.store.add_residue(None if raw is None else float(raw))
            elif action == "fill_residue":
                msg = self.store.add_residue(float(self.store.cfg["capacity"]))
            elif action == "satisfy":
                msg = self.store.mark_satisfied()
            elif action == "set_stage":
                stage = str(payload.get("stage") or "normal")
                if stage not in STAGE_ORDER:
                    stage = "normal"
                self.store.set_stage(stage)
                msg = f"档位已设为 {stage}"
            elif action == "reset":
                self.store.reset()
                msg = "已全部归零"
            else:
                msg = f"未知操作：{action}"
        except Exception as e:  # pragma: no cover
            logger.warning(f"[bio-status] 手动操作失败：{e}")
            msg = f"操作失败：{e}"

        out = self.store.snapshot()
        out["message"] = msg
        try:
            from astrbot.api.web import json_response

            return json_response(out)
        except Exception:
            return out


    @filter.command("体征")
    async def cmd_readout(self, event: AstrMessageEvent):
        yield event.plain_result(self.store.readout())

    @filter.command("体征归零")
    async def cmd_reset(self, event: AstrMessageEvent):
        self.store.reset()
        yield event.plain_result("已归零。" + self.store.readout())
