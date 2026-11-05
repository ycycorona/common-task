#!/usr/bin/env python3
"""Self-checks for ai_compact.py. Run: python3 ai-compact/test_ai_compact.py"""

import argparse
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import ai_compact as ac  # pyright: ignore[reportImplicitRelativeImport]

FAILS = []


def check(name, fn):
    try:
        fn()
        print(f"ok   {name}")
    except AssertionError as exc:
        FAILS.append(name)
        print(f"FAIL {name}: {exc}")


def test_jsonc_load():
    text = '{\n  // comment\n  "a": 1,\n  "b": [1, 2,],\n  "c": "// not a comment",\n}'
    assert ac.jsonc_load(text) == {"a": 1, "b": [1, 2], "c": "// not a comment"}
    assert ac.jsonc_load('{"s": "a/*b*/c", "t": true}') == {"s": "a/*b*/c", "t": True}


def test_jsonc_set_insert():
    text = '{\n  "mcp": {"x": 1},\n}\n'
    out = ac.jsonc_set_top_key(text, "compaction", {"auto": True, "buffer": 100})
    assert ac.jsonc_load(out)["compaction"] == {"auto": True, "buffer": 100}
    assert ac.jsonc_load(out)["mcp"] == {"x": 1}
    assert "// keep" not in out


def test_jsonc_set_replace_keeps_comments():
    text = '{\n  // keep me\n  "compaction": {"auto": false, "prune": true},\n  "other": 1\n}'
    out = ac.jsonc_set_top_key(text, "compaction", {"auto": True, "prune": True, "buffer": 7})
    assert "// keep me" in out, out
    parsed = ac.jsonc_load(out)
    assert parsed["compaction"] == {"auto": True, "prune": True, "buffer": 7}, parsed
    assert parsed["other"] == 1


def test_jsonc_set_empty_doc():
    assert ac.jsonc_load(ac.jsonc_set_top_key("{}", "compaction", {"auto": True})) == {
        "compaction": {"auto": True}}


def test_toml_set():
    text = 'model = "gpt-6.1-sol"\nmodel_auto_compact_token_limit = 250000\n\n[model_providers.custom]\nname = "x"\n'
    out = ac.toml_set(text, "model_auto_compact_token_limit", 200000)
    assert "model_auto_compact_token_limit = 200000" in out
    assert out.count("model_auto_compact_token_limit") == 1
    import tomllib
    assert tomllib.loads(out)["model_auto_compact_token_limit"] == 200000
    assert tomllib.loads(out)["model_providers"]["custom"]["name"] == "x"


def test_toml_set_insert():
    text = '[profiles.a]\nmodel = "x"\n'
    out = ac.toml_set(text, "model_auto_compact_token_limit", 123)
    import tomllib
    parsed = tomllib.loads(out)
    assert parsed["model_auto_compact_token_limit"] == 123
    assert parsed["profiles"]["a"]["model"] == "x"
    # inserted above the table header, not inside it
    assert out.splitlines()[0].startswith("model_auto_compact_token_limit")


def _tmp_root() -> Path:
    return Path(tempfile.mkdtemp(prefix="ai-compact-test-"))


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) if not isinstance(payload, str) else payload)


def test_claude_set_and_read():
    root = _tmp_root()
    settings = root / ".claude" / "settings.json"
    _write(settings, {"model": "opus", "autoCompactEnabled": True, "autoCompactWindow": 250000,
                      "modelSettings": {"opus": {"effortLevel": "high"}}})
    adapter = ac.Claude(root)
    changes = adapter.apply(300000, "opus", True, None)
    assert changes, changes
    data = json.loads(settings.read_text())
    assert data["autoCompactWindow"] == 250000, "per-model write must not touch the global window"
    assert data["modelSettings"]["opus"]["autoCompactWindow"] == 300000
    assert data["modelSettings"]["opus"]["effortLevel"] == "high"
    scopes = {s.scope: s for s in adapter.settings()}
    assert scopes["global"].threshold == 250000
    assert scopes["model:opus"].threshold == 300000
    ok, lines = ac._check_one(adapter, "opus", 300000, None, native=False)
    assert ok, lines
    adapter.apply(200000, None, None, None)
    scopes = {s.scope: s for s in adapter.settings()}
    assert scopes["global"].threshold == 200000
    assert scopes["model:opus"].threshold == 300000
    # out of range
    try:
        adapter.apply(50, None, None, None)
    except SystemExit:
        pass
    else:
        raise AssertionError("expected SystemExit for out-of-range window")


def test_codex_set_and_read():
    root = _tmp_root()
    config = root / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('model = "gpt-6.1-sol"\nmodel_auto_compact_token_limit = 250000\n'
                      '# keep this comment\n[projects."/tmp"]\ntrust_level = "trusted"\n')
    adapter = ac.Codex(root)
    adapter.apply(200000, None, None, None)
    text = config.read_text()
    assert "model_auto_compact_token_limit = 200000" in text
    assert "# keep this comment" in text
    setting = ac.resolve(adapter, None)
    assert setting is not None and setting.threshold == 200000
    ok, lines = ac._check_one(adapter, None, 200000, None, native=False)
    assert ok, lines
    rejected: list[tuple[str | None, bool | None]] = [("x", None), (None, True)]
    for model_arg, enabled_arg in rejected:
        try:
            adapter.apply(None, model_arg, enabled_arg, None)
        except SystemExit:
            pass
        else:
            raise AssertionError(f"expected SystemExit for model={model_arg} enabled={enabled_arg}")


def test_pi_roundtrip_with_context_lookup():
    root = _tmp_root()
    agent_dir = root / ".pi" / "agent"
    _write(agent_dir / "settings.json", {
        "defaultProvider": "magpie",
        "defaultModel": "auto/gpt-6.1-sol",
        "compaction": {"enabled": True, "reserveTokens": 250000, "keepRecentTokens": 30000,
                       "modelOverrides": {"magpie/auto/gpt-6.1-sol": {"reserveTokens": 250000}}},
    })
    _write(agent_dir / "models.json", {"providers": {"magpie": {"models": [
        {"id": "auto/gpt-6.1-sol", "contextWindow": 500000}]}}})
    adapter = ac.Pi(root)
    assert adapter.context(None) == 500000
    scopes = {s.scope: s for s in adapter.settings()}
    assert scopes["global"].threshold == 250000, scopes["global"]
    assert scopes["model:magpie/auto/gpt-6.1-sol"].threshold == 250000

    # global: threshold 200000 -> reserve 300000
    adapter.apply(200000, None, None, None)
    data = json.loads((agent_dir / "settings.json").read_text())
    assert data["compaction"]["reserveTokens"] == 300000
    # per-model, explicit context
    adapter.apply(400000, "magpie/auto/gpt-6.1-sol", None, 500000)
    data = json.loads((agent_dir / "settings.json").read_text())
    assert data["compaction"]["modelOverrides"]["magpie/auto/gpt-6.1-sol"]["reserveTokens"] == 100000
    ok, lines = ac._check_one(adapter, "magpie/auto/gpt-6.1-sol", 400000, None, native=False)
    assert ok, lines


def test_pi_context_from_models_store():
    root = _tmp_root()
    agent_dir = root / ".pi" / "agent"
    _write(agent_dir / "settings.json", {"defaultProvider": "meta", "defaultModel": "spark",
                                         "compaction": {"reserveTokens": 1000}})
    _write(agent_dir / "models-store.json", {"meta": {"models": [
        {"id": "spark", "contextWindow": 1048576}]}})
    adapter = ac.Pi(root)
    assert adapter.context(None) == 1048576
    assert adapter.settings()[0].threshold == 1047576


def test_pi_requires_context():
    root = _tmp_root()
    _write(root / ".pi" / "agent" / "settings.json", {"compaction": {}})
    try:
        ac.Pi(root).apply(100000, None, None, None)
    except SystemExit as exc:
        assert "context" in str(exc)
    else:
        raise AssertionError("expected SystemExit without context")


def test_opencode_set_and_read():
    root = _tmp_root()
    config = root / ".config" / "opencode" / "opencode.jsonc"
    config.parent.mkdir(parents=True)
    config.write_text('{\n  "model": "m1",\n  "provider": {"p": {"models": '
                      '{"m1": {"limit": {"context": 500000}}}}},\n}\n')
    adapter = ac.OpenCode(root)
    adapter.apply(300000, None, True, None)
    text = config.read_text()
    assert "// " not in text
    data = ac.jsonc_load(text)
    assert data["compaction"] == {"auto": True, "buffer": 200000}, data
    assert data["provider"]["p"]["models"]["m1"]["limit"]["context"] == 500000
    ok, lines = ac._check_one(adapter, None, 300000, True, native=False)
    assert ok, lines
    # second write keeps auto flag and updates buffer only
    adapter.apply(400000, None, None, 500000)
    assert ac.jsonc_load(config.read_text())["compaction"] == {"auto": True, "buffer": 100000}
    try:
        adapter.apply(100000, "some-model", None, 500000)
    except SystemExit:
        pass
    else:
        raise AssertionError("expected SystemExit for per-model opencode write")


def test_opencode_json_fallback():
    """opencode.json (the common filename) must be picked when there is no .jsonc."""
    root = _tmp_root()
    config = root / ".config" / "opencode" / "opencode.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"compaction": {"auto": True, "buffer": 650000}}))
    adapter = ac.OpenCode(root)
    assert adapter.config_path() == config, adapter.config_path()
    assert adapter.settings(1000000)[0].threshold == 350000
    adapter.apply(400000, None, None, 1000000)
    assert json.loads(config.read_text())["compaction"]["buffer"] == 600000
    assert not (config.parent / "opencode.jsonc").exists()


def test_scan_passes_context():
    root = _tmp_root()
    _write(root / ".config" / "opencode" / "opencode.json",
           {"compaction": {"auto": True, "buffer": 650000}})
    original, ac.registry = ac.registry, lambda *_a, **_k: {"opencode": ac.OpenCode(root)}
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = ac.cmd_scan(argparse.Namespace(agent="opencode", json=False, context=1000000))
    finally:
        ac.registry = original
    assert code == 0 and "350000" in out.getvalue(), out.getvalue()


def test_pi_unset_by_pattern():
    root = _tmp_root()
    agent_dir = root / ".pi" / "agent"
    _write(agent_dir / "settings.json", {
        "defaultProvider": "p", "defaultModel": "m",
        "compaction": {"enabled": True, "reserveTokens": 1000, "modelOverrides": {
            "coding-plan/a": {"reserveTokens": 1},
            "coding-plan/b": {"reserveTokens": 2},
            "magpie/c": {"reserveTokens": 3}}},
    })
    adapter = ac.Pi(root)
    changes = adapter.apply(None, "coding-plan/*", None, None, unset=True)
    assert len(changes) == 2, changes
    data = json.loads((agent_dir / "settings.json").read_text())
    assert list(data["compaction"]["modelOverrides"]) == ["magpie/c"], data
    assert data["compaction"]["reserveTokens"] == 1000
    # last entry removal drops the empty map
    adapter.apply(None, "magpie/*", None, None, unset=True)
    data = json.loads((agent_dir / "settings.json").read_text())
    assert "modelOverrides" not in data["compaction"], data
    # no match is an error, not a silent success
    (agent_dir / "settings.json").write_text(json.dumps({"compaction": {}}))
    try:
        adapter.apply(None, "nope/*", None, None, unset=True)
    except SystemExit as exc:
        assert "没有匹配" in str(exc)
    else:
        raise AssertionError("expected SystemExit when nothing matches")


def test_pi_unset_requires_model():
    root = _tmp_root()
    _write(root / ".pi" / "agent" / "settings.json", {"compaction": {}})
    try:
        ac.Pi(root).apply(None, None, None, None, unset=True)
    except SystemExit as exc:
        assert "--model" in str(exc)
    else:
        raise AssertionError("expected SystemExit without --model")


def test_cursor_read_only():
    root = _tmp_root()
    _write(root / ".cursor" / "cli-config.json", {"model": {"modelId": "grok-4.7"}})
    adapter = ac.CursorAgent(root)
    settings = adapter.settings()
    assert settings[0].threshold is None and settings[0].enabled is None
    assert settings[0].native["model"] == "grok-4.7"
    try:
        adapter.apply(100000, None, None, None)
    except SystemExit:
        pass
    else:
        raise AssertionError("expected SystemExit for cursor-agent write")


def test_verify_detects_mismatch():
    root = _tmp_root()
    _write(root / ".claude" / "settings.json", {"autoCompactEnabled": True, "autoCompactWindow": 250000})
    adapter = ac.Claude(root)
    ok, lines = ac._check_one(adapter, None, 200000, None, native=False)
    assert not ok and any("✗" in line for line in lines), lines


for name, fn in list(globals().items()):
    if name.startswith("test_"):
        check(name, fn)

if FAILS:
    print(f"\n{len(FAILS)} 个检查失败: {', '.join(FAILS)}")
    sys.exit(1)
print("\n全部检查通过")
