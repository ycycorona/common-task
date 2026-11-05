#!/usr/bin/env python3
"""Probe and set automatic context compaction for the AI agents installed on this Mac.

`threshold` always means: the context size (tokens) at which auto-compaction triggers.
Each agent exposes a different native knob, so writes convert:

  claude        autoCompactWindow              = threshold        (100000..1000000, or "auto")
                modelSettings.<model>.autoCompactWindow  = per model
  codex         model_auto_compact_token_limit = threshold        (global only)
  pi            compaction.reserveTokens       = context - threshold
                compaction.modelOverrides.<provider/model>.reserveTokens  = per model
  opencode      compaction.buffer              = context - threshold
  cursor-agent  no auto-compaction knob (read-only report)

Run from the repo root:

Usage:
  python3 ai-compact/ai_compact.py scan                     # probe all agents
  python3 ai-compact/ai_compact.py scan --json
  python3 ai-compact/ai_compact.py set 200000               # every agent
  python3 ai-compact/ai_compact.py set 200000 -a pi         # one agent
  python3 ai-compact/ai_compact.py set 200000 -a pi -m magpie/auto/gpt-6.1-sol
  python3 ai-compact/ai_compact.py set --disable -a claude  # toggle auto-compaction off
  python3 ai-compact/ai_compact.py verify -a codex --expect 200000
  python3 ai-compact/ai_compact.py scan --no-native-check   # skip calling the agents' own parsers

`set` writes, then verifies (re-read + the agent's own config parser) and exits 1 on mismatch.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HOME = Path.home()
native_enabled = True


# ---------------------------------------------------------------- helpers


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".ai-compact.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def jsonc_load(text: str):
    """Parse JSON that may contain // and /* */ comments and trailing commas."""
    out, i, n, in_str, esc = [], 0, len(text), False, False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        out.append(c)
        i += 1
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", "".join(out)))


def toml_set(text: str, key: str, value) -> str:
    """Set a top-level `key = value` line, inserting before the first table header."""
    lines = text.split("\n")
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key} = {value}"
            return "\n".join(lines)
    idx = next((i for i, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines))
    while idx > 0 and not lines[idx - 1].strip():
        idx -= 1
    lines.insert(idx, f"{key} = {value}")
    return "\n".join(lines)


def _json_value_span(text: str, start: int) -> int:
    """End index (exclusive) of the JSON value beginning at `start`."""
    if text[start] in "{[":
        depth, i, in_str, esc = 0, start, False, False
        while i < len(text):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        raise ValueError(f"unbalanced JSON around offset {start}")
    j = start
    while j < len(text) and text[j] not in ",}]\n":
        j += 1
    return j


def jsonc_set_top_key(text: str, key: str, value) -> str:
    """Insert or replace a top-level key in a JSON/JSONC document, preserving the rest."""
    rendered = json.dumps(value, ensure_ascii=False)
    start = text.index("{")
    depth, i, in_str, esc = 0, start, False, False
    while i < len(text):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            if depth == 1:
                j = _json_value_span_string_end(text, i)
                name = json.loads(text[i:j])
                if name == key:
                    k = j
                    while text[k] not in ":":
                        k += 1
                    k += 1
                    while text[k] in " \t":
                        k += 1
                    end = _json_value_span(text, k)
                    return text[:k] + rendered + text[end:]
                i = j
                continue
            in_str = True
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        i += 1
    insert_at = text.index("{") + 1
    trailing = "" if text[insert_at:].lstrip().startswith("}") else ","
    return text[:insert_at] + f'\n  "{key}": {rendered}{trailing}' + text[insert_at:]


def _json_value_span_string_end(text: str, start: int) -> int:
    i, esc = start + 1, False
    while i < len(text):
        c = text[i]
        if esc:
            esc = False
        elif c == "\\":
            esc = True
        elif c == '"':
            return i + 1
        i += 1
    raise ValueError("unterminated string")


def run(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return proc.returncode, proc.stdout + proc.stderr


# ---------------------------------------------------------------- model


@dataclass
class Setting:
    agent: str
    scope: str                      # "global" or "model:<name>"
    enabled: bool | None
    threshold: int | None           # trigger tokens, None when context unknown
    native: dict[str, Any] = field(default_factory=dict)
    origin: str = ""                # where the value comes from
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "scope": self.scope,
            "enabled": self.enabled,
            "threshold": self.threshold,
            "native": self.native,
            "origin": self.origin,
            "note": self.note,
        }


class Adapter:
    name = ""
    label = ""
    binary = ""
    knob = ""
    supports_model = False
    supports_toggle = True

    def __init__(self, root: Path = HOME):
        self.root = root

    # -- paths
    def config_path(self) -> Path:
        raise NotImplementedError

    def installed(self) -> bool:
        return bool(shutil.which(self.binary)) or self.config_path().exists()

    # -- read
    def settings(self, context: int | None = None) -> list[Setting]:
        raise NotImplementedError

    # -- write
    def apply(self, threshold: int | None, model: str | None, enabled: bool | None,
              context: int | None, unset: bool = False) -> list[str]:
        raise NotImplementedError

    # -- native parser check
    def native_check(self) -> tuple[bool, str]:
        return True, "该 Agent 无独立配置校验命令（仅按文件解析与字段校验）"


# ---------------------------------------------------------------- claude


class Claude(Adapter):
    name = "claude"
    label = "Claude Code"
    binary = "claude"
    knob = "autoCompactWindow (触发阈值) + autoCompactEnabled (开关)；模型级 modelSettings.<model>.autoCompactWindow"
    supports_model = True
    WINDOW_MIN, WINDOW_MAX = 100_000, 1_000_000

    def config_path(self) -> Path:
        return self.root / ".claude" / "settings.json"

    def _data(self) -> dict[str, Any]:
        path = self.config_path()
        return json.loads(path.read_text()) if path.exists() else {}

    def settings(self, context: int | None = None) -> list[Setting]:
        data = self._data()
        env = data.get("env", {}) if isinstance(data.get("env"), dict) else {}
        notes = []
        for key in ("CLAUDE_CODE_AUTO_COMPACT_WINDOW", "DISABLE_AUTO_COMPACT", "DISABLE_COMPACT"):
            if key in env:
                notes.append(f"settings.env 设置 {key}={env[key]}（会覆盖设置项）")
            elif os.environ.get(key):
                notes.append(f"环境变量 {key}={os.environ[key]}（会覆盖设置项）")
        global_window = data.get("autoCompactWindow")
        out = [Setting(
            agent=self.name,
            scope="global",
            enabled=data.get("autoCompactEnabled"),
            threshold=global_window if isinstance(global_window, int) else None,
            native={"autoCompactWindow": global_window, "autoCompactEnabled": data.get("autoCompactEnabled")},
            origin=str(self.config_path()),
            note="；".join(notes) or "阈值单位 tokens，取值 100000–1000000 或 \"auto\"",
        )]
        per_model = data.get("modelSettings") or {}
        for model, values in per_model.items():
            if not isinstance(values, dict) or "autoCompactWindow" not in values:
                continue
            window = values["autoCompactWindow"]
            out.append(Setting(
                agent=self.name,
                scope=f"model:{model}",
                enabled=None,
                threshold=window if isinstance(window, int) else None,
                native={"autoCompactWindow": window},
                origin=f"{self.config_path()} modelSettings.{model}",
                note="模型级设置覆盖全局 autoCompactWindow",
            ))
        return out

    def apply(self, threshold, model, enabled, context, unset=False):
        if unset:
            raise SystemExit("claude: 暂不支持 --unset（如需删除模型级条目请手工编辑 modelSettings）")
        if enabled is False:
            return self._set(enabled=False, model=None, threshold=None)
        if threshold is not None and not self.WINDOW_MIN <= threshold <= self.WINDOW_MAX:
            raise SystemExit(f"claude: autoCompactWindow 只接受 {self.WINDOW_MIN}–{self.WINDOW_MAX} tokens")
        return self._set(enabled=enabled, model=model, threshold=threshold)

    def _set(self, enabled, model, threshold) -> list[str]:
        path = self.config_path()
        data = self._data()
        changes = []
        if enabled is not None:
            old = data.get("autoCompactEnabled")
            data["autoCompactEnabled"] = enabled
            changes.append(f"autoCompactEnabled: {old} -> {enabled}")
        if threshold is not None:
            if model:
                target = data.setdefault("modelSettings", {}).setdefault(model, {})
                where = f"modelSettings.{model}.autoCompactWindow"
            else:
                target, where = data, "autoCompactWindow"
            old = target.get("autoCompactWindow")
            target["autoCompactWindow"] = threshold
            changes.append(f"{where}: {old} -> {threshold}")
        if not changes:
            raise SystemExit("claude: 没有需要写入的改动（给 --threshold / --enable / --disable）")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        return changes


# ---------------------------------------------------------------- codex


class Codex(Adapter):
    name = "codex"
    label = "OpenAI Codex CLI"
    binary = "codex"
    knob = "model_auto_compact_token_limit (触发阈值)；另有 model_context_window / model_auto_compact_token_limit_scope"
    supports_toggle = False

    def config_path(self) -> Path:
        return self.root / ".codex" / "config.toml"

    def _data(self) -> dict[str, Any]:
        import tomllib

        path = self.config_path()
        return tomllib.loads(path.read_text()) if path.exists() else {}

    def settings(self, context: int | None = None) -> list[Setting]:
        data = self._data()
        limit = data.get("model_auto_compact_token_limit")
        return [Setting(
            agent=self.name,
            scope="global",
            enabled=None,
            threshold=limit if isinstance(limit, int) else None,
            native={
                "model": data.get("model"),
                "model_auto_compact_token_limit": limit,
                "model_context_window": data.get("model_context_window"),
                "model_auto_compact_token_limit_scope": data.get("model_auto_compact_token_limit_scope"),
            },
            origin=str(self.config_path()),
            note="无开关字段；未设置时用该模型的内置默认。模型级不支持（profile 内同名字段会被忽略）",
        )]

    def apply(self, threshold, model, enabled, context, unset=False):
        if unset:
            raise SystemExit("codex: 暂不支持 --unset")
        if enabled is not None:
            raise SystemExit("codex: 没有启用/禁用开关，只能设置阈值（--threshold）或删除该键")
        if model:
            raise SystemExit("codex: 不支持模型级自动压缩设置（[profiles.*] 中的同名键会被忽略）")
        path = self.config_path()
        text = path.read_text() if path.exists() else ""
        old = self._data().get("model_auto_compact_token_limit")
        atomic_write(path, toml_set(text, "model_auto_compact_token_limit", threshold))
        return [f"model_auto_compact_token_limit: {old} -> {threshold}"]

    def native_check(self) -> tuple[bool, str]:
        env = os.environ.copy()
        env["CODEX_HOME"] = str(self.root / ".codex")
        try:
            proc = subprocess.run([self.binary, "doctor"], capture_output=True, text=True,
                                  timeout=120, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"codex doctor 无法运行: {exc}"
        out = proc.stdout + proc.stderr
        if "config.toml parse" in out and "config.toml parse        ok" not in out:
            return False, "codex doctor: config.toml 解析失败"
        if "unrecognized configuration setting" in out:
            return False, "codex doctor: 存在未识别的配置项（可能拼错或已被废弃）"
        return True, "codex doctor: config.toml parse ok，无未识别配置项"


# ---------------------------------------------------------------- pi


class Pi(Adapter):
    name = "pi"
    label = "pi (@earendil-works/pi)"
    binary = "pi"
    knob = "compaction.enabled / compaction.reserveTokens (context - reserveTokens = 触发阈值) / compaction.keepRecentTokens / compaction.modelOverrides.<provider/model>.reserveTokens"
    supports_model = True

    def config_path(self) -> Path:
        return self.root / ".pi" / "agent" / "settings.json"

    def _data(self) -> dict[str, Any]:
        path = self.config_path()
        return json.loads(path.read_text()) if path.exists() else {}

    def context(self, model: str | None) -> int | None:
        """Context window from pi's model catalogs (models.json / models-store.json)."""
        if not model:
            data = self._data()
            provider, mid = data.get("defaultProvider"), data.get("defaultModel")
            model = f"{provider}/{mid}" if provider and mid else None
        if not model:
            return None
        provider, _, mid = model.partition("/") if model.count("/") >= 1 else ("", "", model)
        for name in ("models.json", "models-store.json"):
            path = self.root / ".pi" / "agent" / name
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            catalogs = data.get("providers", {})
            # models-store.json keeps providers at the top level
            catalogs = {**catalogs, **{k: v for k, v in data.items()
                                       if isinstance(v, dict) and "models" in v}}
            for prov, info in catalogs.items():
                for entry in info.get("models", []):
                    if provider and prov != provider:
                        continue
                    if entry.get("id") == mid and entry.get("contextWindow"):
                        return int(entry["contextWindow"])
        return None

    def settings(self, context: int | None = None) -> list[Setting]:
        data = self._data()
        compaction = data.get("compaction") or {}
        reserve = compaction.get("reserveTokens")
        context = context or self.context(None)
        threshold = context - reserve if (context and isinstance(reserve, int)) else None
        out = [Setting(
            agent=self.name,
            scope="global",
            enabled=compaction.get("enabled"),
            threshold=threshold,
            native={"reserveTokens": reserve, "keepRecentTokens": compaction.get("keepRecentTokens"),
                    "context": context},
            origin=str(self.config_path()),
            note=("阈值 = 模型上下文 - reserveTokens" if threshold else
                  "阈值需模型上下文；用 --context 指定"),
        )]
        for key, values in (compaction.get("modelOverrides") or {}).items():
            if not isinstance(values, dict) or not isinstance(values.get("reserveTokens"), int):
                continue
            ctx = self.context(key)
            out.append(Setting(
                agent=self.name,
                scope=f"model:{key}",
                enabled=None,
                threshold=(ctx - values["reserveTokens"]) if ctx else None,
                native={"reserveTokens": values["reserveTokens"], "context": ctx},
                origin=f"{self.config_path()} compaction.modelOverrides.{key}",
                note="模型级 reserveTokens 覆盖全局值",
            ))
        return out

    def apply(self, threshold, model, enabled, context, unset=False):
        path = self.config_path()
        data = self._data()
        compaction = data.setdefault("compaction", {})
        changes = []
        if unset:
            if not model:
                raise SystemExit("pi: --unset 需要 --model 指定要删的模型级条目（支持 * 通配）")
            overrides = compaction.get("modelOverrides") or {}
            hits = [key for key in overrides if fnmatch.fnmatch(key, model)]
            if not hits:
                raise SystemExit(f"pi: compaction.modelOverrides 里没有匹配 {model} 的条目")
            for key in hits:
                del overrides[key]
                changes.append(f"删除 compaction.modelOverrides.{key}")
            if not overrides:
                compaction.pop("modelOverrides", None)
            atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            return changes
        if enabled is not None:
            old = compaction.get("enabled")
            compaction["enabled"] = enabled
            changes.append(f"compaction.enabled: {old} -> {enabled}")
        if threshold is not None:
            ctx = context or self.context(model)
            if not ctx:
                raise SystemExit("pi: 需要模型上下文窗口来换算 reserveTokens；用 --context <tokens> 指定")
            reserve = ctx - threshold
            if reserve < 0:
                raise SystemExit(f"pi: 阈值 {threshold} 大于上下文 {ctx}，无法换算 reserveTokens")
            if model:
                target = compaction.setdefault("modelOverrides", {}).setdefault(model, {})
                where = f"compaction.modelOverrides.{model}.reserveTokens"
            else:
                target, where = compaction, "compaction.reserveTokens"
            old = target.get("reserveTokens")
            target["reserveTokens"] = reserve
            changes.append(f"{where}: {old} -> {reserve} (= 上下文 {ctx} - 阈值 {threshold})")
        if not changes:
            raise SystemExit("pi: 没有需要写入的改动")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        return changes


# ---------------------------------------------------------------- opencode


class OpenCode(Adapter):
    name = "opencode"
    label = "OpenCode"
    binary = "opencode"
    knob = "compaction.auto (开关) / compaction.buffer (context - buffer = 触发阈值) / compaction.keep.tokens"
    supports_model = False

    def config_path(self) -> Path:
        base = self.root / ".config" / "opencode"
        jsonc = base / "opencode.jsonc"
        return jsonc if jsonc.exists() else base / "opencode.json"

    def _data(self) -> dict[str, Any]:
        path = self.config_path()
        return jsonc_load(path.read_text()) if path.exists() else {}

    def _context(self) -> int | None:
        data = self._data()
        model = data.get("model")
        if isinstance(model, dict):
            model = model.get("modelID") or model.get("model")
        if not model:
            return None
        for provider in (data.get("provider") or {}).values():
            entry = (provider.get("models") or {}).get(model)
            if isinstance(entry, dict):
                ctx = (entry.get("limit") or {}).get("context")
                if isinstance(ctx, int):
                    return ctx
        return None

    def settings(self, context: int | None = None) -> list[Setting]:
        data = self._data()
        compaction = data.get("compaction") or {}
        buffer = compaction.get("buffer", compaction.get("reserved"))
        context = context or self._context()
        threshold = context - buffer if (context and isinstance(buffer, int)) else None
        raw_keep = compaction.get("keep")
        keep: dict[str, Any] = raw_keep if isinstance(raw_keep, dict) else {}
        note = "阈值 = 模型上下文 - buffer"
        if buffer is None:
            note = "未设置 buffer：默认保留 max(上下文 15%, 16000)，需 --context 才能给出阈值"
        elif not context:
            note += "（未配置 model，用 --context 指定上下文）"
        return [Setting(
            agent=self.name,
            scope="global",
            enabled=compaction.get("auto"),
            threshold=threshold,
            native={"auto": compaction.get("auto"), "buffer": buffer,
                    "keep.tokens": keep.get("tokens"), "prune": compaction.get("prune"),
                    "context": context},
            origin=str(self.config_path()),
            note=note,
        )]

    def apply(self, threshold, model, enabled, context, unset=False):
        if unset:
            raise SystemExit("opencode: 暂不支持 --unset")
        if model:
            raise SystemExit("opencode: 不支持模型级压缩设置（compaction 只有全局配置）")
        path = self.config_path()
        current = self._data()
        compaction = dict(current.get("compaction") or {})
        changes = []
        if enabled is not None:
            old = compaction.get("auto")
            compaction["auto"] = enabled
            changes.append(f"compaction.auto: {old} -> {enabled}")
        if threshold is not None:
            ctx = context or self._context()
            if not ctx:
                raise SystemExit("opencode: 需要模型上下文窗口来换算 buffer；用 --context <tokens> 指定")
            buffer = ctx - threshold
            if buffer < 0:
                raise SystemExit(f"opencode: 阈值 {threshold} 大于上下文 {ctx}，无法换算 buffer")
            old = compaction.get("buffer")
            compaction["buffer"] = buffer
            changes.append(f"compaction.buffer: {old} -> {buffer} (= 上下文 {ctx} - 阈值 {threshold})")
        if not changes:
            raise SystemExit("opencode: 没有需要写入的改动")
        text = path.read_text() if path.exists() else "{}"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, jsonc_set_top_key(text, "compaction", compaction))
        return changes

    def native_check(self) -> tuple[bool, str]:
        import time

        expected = self._data().get("compaction")
        out, last = "", None
        for _ in range(8):
            code, out = run([self.binary, "debug", "config"])
            if code != 0:
                return False, f"opencode debug config 失败: {out.strip()[:200]}"
            try:
                docs = json.loads(out[out.index("["):])
            except (ValueError, json.JSONDecodeError):
                return False, "opencode debug config 输出无法解析"
            for doc in docs:
                if doc.get("type") == "document" and doc.get("path", "").endswith(
                        ("opencode.jsonc", "opencode.json")):
                    last = (doc.get("info") or {}).get("compaction")
                    if last == expected:
                        return True, f"opencode debug config 解析通过（compaction={last}）"
            time.sleep(0.8)  # the background service reloads config files asynchronously
        if last is None:
            return False, "opencode debug config 未读到 compaction 键"
        return False, f"opencode debug config 读到 compaction={last}，与文件里的 {expected} 不一致"


# ---------------------------------------------------------------- cursor-agent


class CursorAgent(Adapter):
    name = "cursor-agent"
    label = "Cursor Agent CLI"
    binary = "cursor-agent"
    knob = "无自动压缩配置项（cli-config.json 无相关字段；按模型上下文上限自动压缩）"
    supports_toggle = False

    def config_path(self) -> Path:
        return self.root / ".cursor" / "cli-config.json"

    def settings(self, context: int | None = None) -> list[Setting]:
        data = {}
        path = self.config_path()
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                data = {}
        model = (data.get("model") or {}).get("modelId")
        return [Setting(
            agent=self.name,
            scope="global",
            enabled=None,
            threshold=None,
            native={"model": model},
            origin=str(self.config_path()),
            note="未暴露自动压缩开关/阈值，本脚本只能探测不能写入",
        )]

    def apply(self, threshold, model, enabled, context, unset=False):
        raise SystemExit("cursor-agent: 没有自动压缩开关或阈值可写")


ADAPTERS: list[type[Adapter]] = [Claude, Codex, Pi, OpenCode, CursorAgent]


def registry(root: Path = HOME) -> dict[str, Adapter]:
    return {cls.name: cls(root) for cls in ADAPTERS}


# ---------------------------------------------------------------- commands


def resolve(adapter: Adapter, model: str | None, context: int | None = None) -> Setting | None:
    wanted = "global" if not model else f"model:{model}"
    for setting in adapter.settings(context):
        if setting.scope == wanted:
            return setting
    return None


def cmd_scan(args) -> int:
    adapters = registry()
    names = [args.agent] if args.agent else [a.name for a in ADAPTERS]
    rows, unknown = [], []
    for name in names:
        adapter = adapters.get(name)
        if adapter is None:
            raise SystemExit(f"未知 Agent: {name}（已知: {', '.join(adapters)}）")
        if not adapter.installed():
            unknown.append(name)
            continue
        for setting in adapter.settings(args.context):
            rows.append(setting.as_dict() | {"label": adapter.label, "knob": adapter.knob})

    if args.json:
        print(json.dumps({"agents": rows, "not_installed": unknown}, indent=2, ensure_ascii=False))
        return 0

    for name in names:
        if name in unknown:
            print(f"== {name}: 未安装（跳过）")
            continue
        adapter = adapters[name]
        print(f"== {name} · {adapter.label} · 已安装")
        print(f"   配置: {adapter.config_path()}")
        print(f"   开关/阈值项: {adapter.knob}")
        for row in [r for r in rows if r["agent"] == name]:
            enabled = {True: "开", False: "关", None: "未设置/无开关"}[row["enabled"]]
            head = "   " if row["scope"] == "global" else "   模型级 "
            label = "全局" if row["scope"] == "global" else row["scope"].removeprefix("model:")
            threshold = f"{row['threshold']} tokens" if row["threshold"] else "未知（需模型上下文）"
            print(f"{head}{label}: 开关={enabled}  触发阈值={threshold}")
            native = {k: v for k, v in row["native"].items() if v is not None}
            if native:
                print(f"      原生值: {native}")
            if row["note"]:
                print(f"      说明: {row['note']}")
        print()
    return 0


def cmd_set(args) -> int:
    adapters = registry()
    names = [args.agent] if args.agent else [a.name for a in ADAPTERS]
    enabled = None if args.enable is None else args.enable
    failed = False
    for name in names:
        adapter = adapters.get(name)
        if adapter is None:
            raise SystemExit(f"未知 Agent: {name}（已知: {', '.join(adapters)}）")
        if not adapter.installed():
            print(f"-- {name}: 未安装，跳过")
            continue
        if enabled is not None and not adapter.supports_toggle:
            print(f"-- {name}: 该 Agent 无启用/禁用开关，跳过")
            continue
        try:
            changes = adapter.apply(args.threshold, args.model, enabled, args.context, args.unset)
        except SystemExit as exc:
            print(f"-- {name}: {exc}")
            failed = True
            continue
        print(f"== {name}: 已写入 {adapter.config_path()}")
        for change in changes:
            print(f"   {change}")
    if failed:
        print("\n有 Agent 未写入，见上面的提示。", file=sys.stderr)
        return 1
    if args.unset:
        print()
        return _verify_unset(names, args.model)
    print()
    return cmd_verify(argparse.Namespace(agent=args.agent, model=args.model, expect=args.threshold,
                                        native=not args.no_native_check, enabled=enabled,
                                        context=args.context))


def _verify_unset(names: list[str], pattern: str) -> int:
    adapters = registry()
    leftover = []
    for name in names:
        adapter = adapters.get(name)
        if adapter is None or not adapter.installed():
            continue
        remaining = [s.scope for s in adapter.settings()
                     if s.scope.startswith("model:")
                     and fnmatch.fnmatch(s.scope.removeprefix("model:"), pattern)]
        if remaining:
            leftover.append(name)
            print(f"== {name}: ✗ 仍存在匹配 {pattern} 的条目: {remaining}")
        else:
            print(f"== {name}: ✓ 已无匹配 {pattern} 的模型级条目")
    if leftover:
        print(f"\n验证失败: {', '.join(leftover)}", file=sys.stderr)
        return 1
    print("\n全部通过。")
    return 0


def _check_one(adapter: Adapter, model: str | None, expect: int | None,
               enabled: bool | None, native: bool,
               context: int | None = None) -> tuple[bool, list[str]]:
    setting = resolve(adapter, model, context)
    lines, ok = [], True
    if setting is None:
        return False, [f"读不到 {adapter.name} 的 {'模型级 ' + model if model else '全局'} 设置"]
    lines.append(f"读回: 开关={setting.enabled} 阈值={setting.threshold} 原生值={setting.native}")
    if expect is not None:
        if setting.threshold != expect:
            ok = False
            lines.append(f"✗ 阈值应为 {expect}，实际读回 {setting.threshold}")
        else:
            lines.append(f"✓ 阈值已生效: {expect} tokens")
    if enabled is not None:
        if setting.enabled != enabled:
            ok = False
            lines.append(f"✗ 开关应为 {enabled}，实际读回 {setting.enabled}")
        else:
            lines.append(f"✓ 开关已生效: {enabled}")
    if native and native_enabled:
        ok_native, message = adapter.native_check()
        ok = ok and ok_native
        lines.append(("✓ " if ok_native else "✗ ") + message)
    return ok, lines


def cmd_verify(args) -> int:
    global native_enabled
    native_enabled = getattr(args, "native", True)
    adapters = registry()
    names = [args.agent] if args.agent else [a.name for a in ADAPTERS]
    expect = getattr(args, "expect", None)
    enabled = getattr(args, "enabled", None)
    failed = []
    for name in names:
        adapter = adapters.get(name)
        if adapter is None:
            raise SystemExit(f"未知 Agent: {name}（已知: {', '.join(adapters)}）")
        if not adapter.installed():
            continue
        ok, lines = _check_one(adapter, args.model, expect, enabled, native_enabled,
                               getattr(args, "context", None))
        print(f"== {name}: {'通过' if ok else '失败'}")
        for line in lines:
            print(f"   {line}")
        if not ok:
            failed.append(name)
    if failed:
        print(f"\n验证失败: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("\n全部通过。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="探测/设置各 AI Agent 的自动上下文压缩阈值")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="探测已安装 Agent 的自动压缩设置")
    scan.add_argument("-a", "--agent")
    scan.add_argument("-c", "--context", type=int, help="模型上下文窗口 tokens（pi/opencode 换算用）")
    scan.add_argument("--json", action="store_true")
    scan.add_argument("--no-native-check", action="store_true", help="兼容占位（scan 不调用原生校验）")
    scan.set_defaults(func=cmd_scan)

    setter = sub.add_parser("set", help="写入阈值/开关，写完后自动验证")
    setter.add_argument("threshold", nargs="?", type=int, help="触发自动压缩的上下文 tokens")
    setter.add_argument("-a", "--agent")
    setter.add_argument("-m", "--model", help="只改该模型（claude/pi 支持）")
    setter.add_argument("-c", "--context", type=int, help="模型上下文窗口 tokens（pi/opencode 换算用）")
    setter.add_argument("--unset", action="store_true",
                        help="删除匹配的模型级条目（需 -m，支持 * 通配；目前 pi 支持）")
    toggle = setter.add_mutually_exclusive_group()
    toggle.add_argument("--enable", dest="enable", action="store_true", default=None)
    toggle.add_argument("--disable", dest="enable", action="store_false")
    setter.add_argument("--no-native-check", action="store_true", help="跳过 Agent 自带的配置解析校验")
    setter.set_defaults(func=cmd_set)

    verify = sub.add_parser("verify", help="复核配置并跑一次 Agent 原生解析")
    verify.add_argument("-a", "--agent")
    verify.add_argument("-m", "--model")
    verify.add_argument("--expect", type=int, help="期望的触发阈值")
    verify.add_argument("-c", "--context", type=int, help="模型上下文窗口 tokens（pi/opencode 换算用）")
    verify.add_argument("--no-native-check", action="store_true")
    verify.set_defaults(func=cmd_verify, native=True)

    args = parser.parse_args(argv)
    if getattr(args, "no_native_check", False):
        args.native = False
    if args.command == "set" and args.threshold is None and args.enable is None and not args.unset:
        parser.error("set 需要 threshold、--unset 或 --enable/--disable")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
