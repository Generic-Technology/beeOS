"""
tests.test_chain_script - 链脚本自己的守卫

**链脚本的错不许盖住盒子的错**（2026-11-01 真链撞出来的）：

设计盒那跳模型返回了空串（`llm_parse`）→ 履约 failed → 没有产出 →
链脚本一句 `design["result"].get(...)` → AttributeError:
'NoneType' object has no attribute 'get'。

读者看到的是一句**不相干**的话，而真正的原因（模型输出不是合法 JSON）
被它盖住了。盒子的失败原因明明写得好好的（op 的 error、记录里的 error_reason），
链脚本没把它传出来。

所以这条守卫盯的是：**盒子没产出时，链脚本说的话里必须有盒子的真原因**。

另一族：**产出没了却继续跑**，算出来的「0 条落差」是**假绿**——
分析盒挂了，链照样报「没有落差」。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from runtime.store import TaskRunStore


_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_chain.py"


def _load_chain():
    spec = importlib.util.spec_from_file_location("run_chain_under_test", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


chain = _load_chain()


def _failed_run(task_run_id: str = "tr_1") -> dict:
    return {
        "task_run_id": task_run_id,
        "status": "failed",
        "acceptance_status": None,
        "result": None,
        "op_trace": [],
    }


def test_a_box_without_a_result_names_the_box_and_its_status(tmp_path: Path):
    """没说清是哪一跳，就等于让读者自己去猜"""
    store = TaskRunStore(path=tmp_path / "runs.jsonl")
    with pytest.raises(chain.ChainError) as exc:
        chain._require_result("设计盒", _failed_run(), store)
    message = str(exc.value)
    assert "设计盒" in message, message
    assert "failed" in message, message


def test_the_records_reason_is_passed_through(tmp_path: Path):
    """**盒子的失败原因要传出来**——它已经写好了，别浪费"""
    store = TaskRunStore(path=tmp_path / "runs.jsonl")
    store.append_record({
        "task_run_id": "tr_1",
        "status": "failed",
        "error_reason": "model output is not valid JSON: ''",
    })
    with pytest.raises(chain.ChainError) as exc:
        chain._require_result("设计盒", _failed_run(), store)
    assert "model output is not valid JSON" in str(exc.value), str(exc.value)


def test_without_a_record_reason_it_falls_back_to_the_op_error(tmp_path: Path):
    """记录里没写原因时，退到 op 的错误文本——总比「没记原因」强"""
    store = TaskRunStore(path=tmp_path / "runs.jsonl")
    run = _failed_run()
    run["op_trace"] = [{
        "op_id": "generate_model_elements",
        "status": "failed",
        "error": "model output is not valid JSON: ''",
    }]
    with pytest.raises(chain.ChainError) as exc:
        chain._require_result("设计盒", run, store)
    assert "generate_model_elements" in str(exc.value), str(exc.value)


def test_a_box_with_a_result_passes_through_untouched(tmp_path: Path):
    """有产出就原样给出去——这条路径不许被改坏"""
    store = TaskRunStore(path=tmp_path / "runs.jsonl")
    run = _failed_run()
    run["result"] = {"expectations": [{"wms_kind": "zone"}]}
    payload = chain._require_result("设计盒", run, store)
    assert payload == {"expectations": [{"wms_kind": "zone"}]}
