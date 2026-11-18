# -*- coding: utf-8 -*-
"""数值状态机与持久化。纯本地计算，不依赖任何外部服务。"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List


STAGE_ORDER = ["normal", "mild", "full", "pounce"]
STATE_ORDER = ["none", "moving", "inserted", "pulled"]

DEFAULT_STAGE_LABELS: Dict[str, str] = {
    "normal": "平静",
    "mild": "微热",
    "full": "高涨",
    "pounce": "难耐",
}

DEFAULT_STATE_LABELS: Dict[str, str] = {
    "none": "无",
    "moving": "抽动中",
    "inserted": "在位",
    "pulled": "已退出",
}

DEFAULT_THEME: Dict[str, str] = {
    "bg0": "#08060f",
    "bg1": "#100a20",
    "line": "#33215c",
    "line2": "#4d2f8a",
    "fg": "#ece5ff",
    "dim": "#9683c0",
    "faint": "#624a92",
    "accent": "#a86dff",
    "accent2": "#ff6fd8",
    "accent3": "#5fe8ff",
}

DEFAULT_PROMPT_HINT = "按上面这组读数决定你说话的分寸，不要自己报数字。"

DEFAULTS: Dict[str, Any] = {
    "tickMs": 3000,
    "capacity": 200,
    "absorbPerHour": 4,
    "injectAmount": 12,
    "desireNormal": 30,
    "desireMild": 50,
    "desireFull": 90,
    "desirePounce": 100,
    "tempBase": 37.2,
    "tempRise": 3.3,
    "stageDecaySec": 180,
    "thrustHoldSec": 20,
    "outHoldSec": 30,
    "heatWords": ["亲密", "靠近", "心动", "想你", "脸红", "抱", "贴", "亲近"],
    "pounceWords": ["扑上来", "扑倒", "忍不住了"],
    "injectWords": ["灌", "注入", "填满", "装不下"],
    "penWords": ["进来了", "深入", "没入"],
    "exitWords": ["退出", "结束", "停下"],
    "satisfiedWords": ["满足了", "够了", "舒服"],
    "stageLabels": dict(DEFAULT_STAGE_LABELS),
    "stateLabels": dict(DEFAULT_STATE_LABELS),
    "promptHint": DEFAULT_PROMPT_HINT,
    "theme": dict(DEFAULT_THEME),
}

# 这些配置是字典，合并时按 key 覆盖，缺的键用默认补齐
_DICT_KEYS = ("stageLabels", "stateLabels", "theme")


def _merge_cfg(base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """把用户配置合进默认值。字典项逐键合并，空值忽略。"""
    out = dict(base)
    for k, v in (incoming or {}).items():
        if k not in out or v is None:
            continue
        if k in _DICT_KEYS:
            merged = dict(out[k]) if isinstance(out[k], dict) else {}
            if isinstance(v, dict):
                for kk, vv in v.items():
                    if vv is not None and str(vv).strip():
                        merged[kk] = vv
            out[k] = merged
        else:
            out[k] = v
    return out


def _now() -> float:
    return time.time()


@dataclass
class BioState:
    desire: int = 30
    stage: str = "normal"
    residue: float = 0.0
    insertState: str = "none"
    insertChangedAt: float = 0.0
    lastStageChangeAt: float = 0.0
    lastSatisfiedAt: float = 0.0
    pounceLocked: bool = False
    lastSaid: str = ""
    events: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "BioState":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (raw or {}).items() if k in known})


class StateStore:
    def __init__(self, data_dir: Path, config: Dict[str, Any] | None = None):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "state.json"
        self.cfg: Dict[str, Any] = _merge_cfg(DEFAULTS, config or {})
        self.state = BioState()
        self._lock = asyncio.Lock()
        self.load()

    # ---------- 持久化 ----------

    def load(self) -> None:
        if not self.path.exists():
            self.state = BioState(
                desire=int(self.cfg["desireNormal"]),
                stage="normal",
                lastStageChangeAt=_now(),
            )
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.state = BioState.from_dict(raw)
        except Exception:
            self.state = BioState(
                desire=int(self.cfg["desireNormal"]),
                stage="normal",
                lastStageChangeAt=_now(),
            )

    def save_sync(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    async def save(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self.save_sync)

    # ---------- 读数 ----------

    @property
    def temperature(self) -> float:
        return self.cfg["tempBase"] + self.cfg["tempRise"] * (self.state.desire / 100.0)

    def hours_to_empty(self) -> float:
        rate = float(self.cfg["absorbPerHour"] or 1)
        return self.state.residue / rate if rate else 0.0

    def snapshot(self) -> Dict[str, Any]:
        s = self.state
        return {
            "desire": s.desire,
            "stage": s.stage,
            "temperature": round(self.temperature, 2),
            "residue": round(s.residue, 2),
            "capacity": self.cfg["capacity"],
            "absorbPerHour": self.cfg["absorbPerHour"],
            "hoursToEmpty": round(self.hours_to_empty(), 2),
            "insertState": s.insertState,
            "pounceLocked": s.pounceLocked,
            "lastSaid": s.lastSaid,
            "events": s.events[-20:],
            "sinceStage": int(_now() - s.lastStageChangeAt),
            "sinceSatisfied": int(_now() - s.lastSatisfiedAt) if s.lastSatisfiedAt else None,
            "allEvents": s.events[-500:],
            # 前端用来替换写死的映射，全部可在配置里改
            "stageLabels": self.stage_labels(),
            "stateLabels": self.state_labels(),
            "theme": self.theme(),
            "tempBase": self.cfg["tempBase"],
            "tempRise": self.cfg["tempRise"],
            "injectAmount": self.cfg["injectAmount"],
        }

    def stage_labels(self) -> Dict[str, str]:
        """档位显示名，保证四个键齐全。"""
        raw = self.cfg.get("stageLabels") or {}
        return {k: str(raw.get(k) or DEFAULT_STAGE_LABELS[k]) for k in STAGE_ORDER}

    def state_labels(self) -> Dict[str, str]:
        """状态显示名，保证四个键齐全。"""
        raw = self.cfg.get("stateLabels") or {}
        return {k: str(raw.get(k) or DEFAULT_STATE_LABELS[k]) for k in STATE_ORDER}

    def theme(self) -> Dict[str, str]:
        """页面配色，保证所有键齐全。"""
        raw = self.cfg.get("theme") or {}
        return {k: str(raw.get(k) or v) for k, v in DEFAULT_THEME.items()}

    def prompt_hint(self) -> str:
        """跟在读数后面的那句提示语。"""
        v = self.cfg.get("promptHint")
        return str(v) if v else DEFAULT_PROMPT_HINT

    def readout(self) -> str:
        d = self.snapshot()
        stage_label = self.stage_labels().get(d["stage"], d["stage"])
        state_label = self.state_labels().get(d["insertState"], d["insertState"])
        parts = [
            f"欲望 {d['desire']}/100（{stage_label}）",
            f"温度 {d['temperature']}℃",
            f"残留 {int(d['residue'])}/{d['capacity']}（约 {d['hoursToEmpty']:.1f} 小时后归零）",
            f"状态 {state_label}",
        ]
        if d["sinceSatisfied"] is not None:
            parts.append(f"距上次更新 {d['sinceSatisfied'] // 60} 分钟")
        return "【体征】" + "｜".join(parts)

    # ---------- 事件 ----------

    def _log(self, kind: str, detail: str = "") -> None:
        self.state.events.append({"t": int(_now()), "kind": kind, "detail": detail})
        if len(self.state.events) > 200:
            self.state.events = self.state.events[-200:]

    def _set_stage(self, stage: str) -> None:
        if self.state.stage == stage:
            return
        self.state.stage = stage
        self.state.desire = int(self.cfg.get(f"desire{stage.capitalize()}", 30))
        self.state.lastStageChangeAt = _now()
        self._log("stage", stage)

    def apply_text(self, text: str) -> bool:
        """对一段文本做关键词计数并推进状态。返回是否发生变化。"""
        if not text:
            return False
        before = (self.state.stage, self.state.desire, round(self.state.residue, 2),
                  self.state.insertState)
        s = self.state
        cfg = self.cfg
        said = text.strip().replace("\n", " ")[:200]
        s.lastSaid = said

        def hits(key: str) -> int:
            words = cfg.get(key) or []
            strong = 0
            single = 0
            for w in words:
                if not w or w not in text:
                    continue
                if len(w) == 1:
                    single = 1
                else:
                    strong += 1
            if single and len(text) > 40:
                single = 0
            return strong + single

        n_heat = hits("heatWords")
        n_pounce = hits("pounceWords")
        n_inject = hits("injectWords")
        n_pen = hits("penWords")
        n_exit = hits("exitWords")
        n_sat = hits("satisfiedWords")

        if n_pounce:
            s.pounceLocked = True
            self._set_stage("pounce")
        elif n_heat >= 3:
            self._set_stage("full")
        elif n_heat >= 1:
            if STAGE_ORDER.index("mild") > STAGE_ORDER.index(s.stage):
                self._set_stage("mild")

        if n_inject:
            cap = float(cfg["capacity"])
            s.residue = min(cap, s.residue + float(cfg["injectAmount"]))
            s.insertState = "moving"
            s.insertChangedAt = _now()
            self._log("inject", f"+{cfg['injectAmount']}")
        elif n_pen:
            s.insertState = "moving"
            s.insertChangedAt = _now()
            self._log("pen", "")

        if n_exit:
            s.insertState = "pulled"
            s.insertChangedAt = _now()
            self._log("exit", "")

        if n_sat:
            s.pounceLocked = False
            s.lastSatisfiedAt = _now()
            s.insertState = "none"
            self._set_stage("normal")
            self._log("satisfied", "")

        after = (s.stage, s.desire, round(s.residue, 2), s.insertState)
        return before != after

    def tick(self) -> bool:
        """时间推进：残留吸收、插拔超时、档位回落。"""
        s = self.state
        cfg = self.cfg
        before = (s.stage, round(s.residue, 2), s.insertState)

        dt = float(cfg["tickMs"]) / 1000.0
        if s.residue > 0:
            s.residue = max(0.0, s.residue - float(cfg["absorbPerHour"]) * dt / 3600.0)

        now = _now()
        if s.insertState == "moving" and now - s.insertChangedAt >= float(cfg["thrustHoldSec"]):
            s.insertState = "inserted"
            s.insertChangedAt = now
        elif s.insertState == "pulled" and now - s.insertChangedAt >= float(cfg["outHoldSec"]):
            s.insertState = "none"
            s.insertChangedAt = now

        if not s.pounceLocked and now - s.lastStageChangeAt >= float(cfg["stageDecaySec"]):
            idx = STAGE_ORDER.index(s.stage)
            if idx > 0:
                self._set_stage(STAGE_ORDER[idx - 1])

        after = (s.stage, round(s.residue, 2), s.insertState)
        return before != after

    def reset(self) -> None:
        self.state = BioState(
            desire=int(self.cfg["desireNormal"]),
            stage="normal",
            lastStageChangeAt=_now(),
        )
        self.save_sync()

    # ---------- 手动操作 ----------

    def set_desire(self, value: int) -> str:
        value = max(0, min(100, int(value)))
        self.state.desire = value
        # 按数值反推档位
        cfg = self.cfg
        if value >= int(cfg["desirePounce"]):
            stage = "pounce"
        elif value >= int(cfg["desireFull"]):
            stage = "full"
        elif value >= int(cfg["desireMild"]):
            stage = "mild"
        else:
            stage = "normal"
        self.state.stage = stage
        self.state.lastStageChangeAt = _now()
        self._log("stage", f"手动设为 {value}")
        self.save_sync()
        return f"欲望已设为 {value}（{stage}）"

    def set_stage(self, stage: str) -> None:
        if stage not in STAGE_ORDER:
            stage = "normal"
        self._set_stage(stage)
        self.save_sync()

    def clear_residue(self) -> str:
        self.state.residue = 0.0
        self._log("exit", "清空残留")
        self.save_sync()
        return "残留已清空"

    def add_residue(self, amount: float = None) -> str:
        cap = float(self.cfg["capacity"])
        step = float(self.cfg["injectAmount"]) if amount is None else float(amount)
        before = self.state.residue
        self.state.residue = min(cap, self.state.residue + step)
        self.state.insertState = "moving"
        self.state.insertChangedAt = _now()
        self._log("inject", f"手动 +{round(self.state.residue - before, 1)}")
        self.save_sync()
        return f"残留 +{round(self.state.residue - before, 1)}（{round(self.state.residue, 1)}/{int(cap)}）"

    def mark_satisfied(self) -> str:
        self.state.pounceLocked = False
        self.state.lastSatisfiedAt = _now()
        self.state.insertState = "none"
        self._set_stage("normal")
        self._log("satisfied", "手动")
        self.save_sync()
        return "已记为满足"
