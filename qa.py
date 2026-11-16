# -*- coding: utf-8 -*-
"""自动化质量验证：独立判定器 vs 生产 pipeline，全部基于精确有理数判据。

子命令：
  python3 -I qa.py verify   重跑原 checks.py + 全部新增验证，写 evidence.json，
                            交叉核对 REPORT.md；退出码 0/1/2 按契约。
  python3 -I qa.py demo     打印样例交接、生产时长、独立区间、判定与两类变异差异。
"""
import json
import math
import os
import random
import subprocess
import sys
import time
import tracemalloc
from copy import deepcopy
from fractions import Fraction

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import oracle
import pipeline
import samples

TOL = Fraction(1, 10 ** 5)                 # 时长公开容差 1e-5 秒
HANDOFF_TOL = Fraction(1, 10 ** 10)        # 交接系数容差
ARTIFACT_DIR = os.path.join(ROOT, "qa_artifacts")
EVIDENCE_PATH = os.path.join(ROOT, "evidence.json")
FAIL_DIR = os.path.join(ARTIFACT_DIR, "failures")
SEEDS = (20261116, 777001, 424242, 909090, 31337)
STRESS_SEED = 5150200
STRESS_SEGMENTS = 200


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def frac_text(value):
    return oracle.rational_text(value)


def float_as_fraction(value):
    """把生产 float 按其二进制实际值精确解释为有理数。"""
    return Fraction(value)


def run_module(argv):
    """以隔离解释器经受信启动器运行仓库脚本（-I 下不注入脚本目录）。"""
    return subprocess.run([sys.executable, "-I",
                           os.path.join(ROOT, "_site.py"), *argv],
                          cwd=ROOT, capture_output=True, text=True)


def build_handoff_check(request, result):
    """交接核对：时间原文/条数/顺序/有效域 + 四项局部系数公开容差。"""
    threshold, parsed = oracle.parse_request(request)
    details = []
    passed = True
    curves = result["curves"]
    if len(curves) != len(parsed):
        return False, [{"issue": "curve-count-mismatch"}]
    for index, (segment, curve) in enumerate(zip(parsed, curves)):
        item = {"index": index}
        if curve["start"] != segment["start_text"] or \
                curve["end"] != segment["end_text"]:
            item["text_mismatch"] = True
            passed = False
        expected_h = segment["h"]
        actual_h = Fraction(curve.duration) if hasattr(curve, "duration") \
            else Fraction(Fraction(segment["end_text"])
                          - Fraction(segment["start_text"]))
        if actual_h != expected_h:
            item["domain_mismatch"] = True
            passed = False
        exact = oracle.local_coefficients(segment)
        h = segment["h"]
        ratios = []
        if len(curve["coefficients"]) != 4:
            item["coefficient_count"] = len(curve["coefficients"])
            passed = False
            details.append(item)
            continue
        for k, (produced, exact_k) in enumerate(
                zip(curve["coefficients"], exact)):
            b = float_as_fraction(produced)
            scale = h ** k
            term_exact = exact_k * scale
            produced_term = b * scale
            error = abs(produced_term - term_exact)
            bound = HANDOFF_TOL * max(Fraction(1), abs(term_exact))
            ratio = error / bound if bound else (
                Fraction(0) if error == 0 else Fraction(10 ** 12))
            ratios.append(str(ratio))
            if error > bound:
                item[f"c{k}_over_tolerance"] = True
                passed = False
        item["max_tolerance_ratio"] = ratios
        details.append(item)
    return passed, details


def classify_item(low, high, produced):
    """契约判定：dmax<=1e-5 通过；dmin>1e-5 阻断；其余未判定。"""
    if not math.isfinite(produced):
        return "block", {"reason": "non-finite"}
    r = float_as_fraction(produced)
    dmin = max(low - r, r - high, Fraction(0))
    dmax = max(abs(r - low), abs(r - high))
    metrics = {"dmin": str(dmin), "dmax": str(dmax)}
    if dmax <= TOL:
        return "pass", metrics
    if dmin > TOL:
        return "block", metrics
    return "undetermined", metrics


def evaluate_request(request, label, note=""):
    """贯通验证：生产结果 + 独立区间 + 交接核对，形成完整判定。"""
    record = {"label": label, "note": note, "request": deepcopy(request)}
    try:
        result = pipeline.analyze(request)
    except Exception as exc:  # 合法请求不得异常
        record.update({"verdict": "block", "fatal": f"{type(exc).__name__}: {exc}"})
        return record
    record["actual"] = {
        "curves": [
            {"start": c["start"], "end": c["end"],
             "coefficients": [repr(x) for x in c["coefficients"]]}
            for c in result["curves"]],
        "per_segment": [repr(x) for x in result["per_segment"]],
        "total": repr(result["total"]),
    }
    enclosed = oracle.enclose(request)
    record["oracle"] = enclosed
    # 区间结构与宽度契约自检
    threshold, parsed = oracle.parse_request(request)
    total_lo = total_hi = Fraction(0)
    for segment, (lo_text, hi_text) in zip(parsed, enclosed["per_segment"]):
        lo, hi = Fraction(lo_text), Fraction(hi_text)
        if not (0 <= lo <= hi <= segment["h"]):
            record.update({"verdict": "block", "fatal": "oracle-interval-out-of-domain"})
            return record
        total_lo += lo
        total_hi += hi
    stated_lo, stated_hi = map(Fraction, enclosed["total"])
    if (stated_lo, stated_hi) != (total_lo, total_hi) or \
            total_hi - total_lo > oracle.TOTAL_WIDTH_LIMIT:
        record.update({"verdict": "block", "fatal": "oracle-total-invariant"})
        return record

    handoff_ok, handoff_detail = build_handoff_check(request, result)
    record["handoff"] = {"pass": handoff_ok, "detail": handoff_detail}
    verdicts = []
    item_metrics = []
    for index, (produced, (lo_text, hi_text)) in enumerate(
            zip(result["per_segment"], enclosed["per_segment"])):
        verdict, metrics = classify_item(
            Fraction(lo_text), Fraction(hi_text), produced)
        verdicts.append(verdict)
        item_metrics.append({"index": index, "verdict": verdict, **metrics})
    total_verdict, total_metrics = classify_item(
        stated_lo, stated_hi, result["total"])
    item_metrics.append({"index": "total", "verdict": total_verdict,
                         **total_metrics})
    record["item_verdicts"] = item_metrics
    if not handoff_ok or "block" in verdicts or total_verdict == "block":
        verdict = "block"
    elif "undetermined" in verdicts or total_verdict == "undetermined":
        verdict = "undetermined"
    else:
        verdict = "pass"
    record["verdict"] = verdict
    return record


# --------------------------------------------------------------------------- #
# 边界手算案例（因式分解/可手算，独立核验判定器自身）
# --------------------------------------------------------------------------- #
def one(start, end, r0, r1, calib, threshold):
    return {"segments": [{
        "start": start, "end": end, "raw_start": r0, "raw_end": r1,
        "calibration": list(calib)}], "threshold": threshold}


def boundary_cases():
    cases = []
    cases.append(("B01-normal-two-segment", samples.normal(),
                  "样例：u 与 2-u，阈值 1，精确时长 [1,1] 总 2"))
    cases.append(("B02-constant", one("0", "4", "0", "0",
                                      ("3", "0", "0", "0"), "2"),
                  "常量曲线 3>2 整段暴露，精确 4"))
    cases.append(("B03-linear", one("0", "2", "0", "2",
                                    ("0", "1", "0", "0"), "1"),
                  "线性 u>1，精确 1，有理根"))
    cases.append(("B04-descending", one("0", "2", "1", "-1",
                                        ("0", "1", "0", "0"), "0"),
                  "下降采样 1-u>0，精确 1"))
    cases.append(("B05-cubic-rational", one("0", "2", "0", "2",
                                            ("0", "0", "0", "1"), "1"),
                  "u^3>1 精确 1（根 u=1）"))
    cases.append(("B06-tangent", one("0", "2", "0", "2",
                                     ("1", "-2", "1", "0"), "0"),
                  "相切 (u-1)^2>=0，严格 >0 全段 2，切点不扣时长"))
    cases.append(("B07-repeated-root", one("0", "2", "0", "2",
                                           ("-1", "3", "-3", "1"), "0"),
                  "三重根 (u-1)^3，u>1 精确 1"))
    cases.append(("B08-boundary-crossing", one("0", "2", "0", "2",
                                               ("0", "1", "0", "0"), "0"),
                  "边界交点 u=0：严格 >0 时长仍为 2"))
    cases.append(("B09-identity-threshold", one("0", "2", "0", "2",
                                                ("1", "0", "0", "0"), "1"),
                  "整段恒等阈值，时长 0"))
    cases.append(("B10-three-roots", one("0", "4", "0", "4",
                                         ("-6", "11", "-6", "1"), "0"),
                  "(u-1)(u-2)(u-3)>0 正区 (1,2)+(3,4) 精确 2"))
    cases.append(("B11-origin-shift", {
        "segments": [
            {"start": "100", "end": "102", "raw_start": "0", "raw_end": "2",
             "calibration": ["0", "1", "0", "0"]},
            {"start": "102", "end": "104", "raw_start": "2", "raw_end": "0",
             "calibration": ["0", "1", "0", "0"]}],
        "threshold": "1"}, "时间原点平移到 100，语义不变，各段 1"))
    cases.append(("B12-calibration-switch", {
        "segments": [
            {"start": "0", "end": "2", "raw_start": "0", "raw_end": "2",
             "calibration": ["0", "1", "0", "0"]},
            {"start": "2", "end": "4", "raw_start": "2", "raw_end": "4",
             "calibration": ["-4", "3", "0", "0"]}],
        "threshold": "1"},
        "换校准：段1 u>1 给 1；段2 P=3x-4>1 -> x>5/3，[2,4] 内给 2，总 3"))
    cases.append(("B13-near-roots", one("0", "1", "0.5", "1.5",
                                        ("-99.9999999999", "200", "-100", "0"),
                                        "0"),
                  "邻近交点窄暴露带，独立十进制真值 0.000002"))
    cases.append(("B14-irrational-quadratic", one("0", "2", "0", "2",
                                                  ("-2", "0", "1", "0"), "0"),
                  "u^2-2>0，真值 2-sqrt(2)，无理根夹逼"))
    return cases


# 手算案例对判定器自身的精确期望（零宽区间）与独立真值
EXACT_EXPECTATIONS = {
    "B01-normal-two-segment": (["1", "1"], "2"),
    "B02-constant": (["4"], "4"),
    "B03-linear": (["1"], "1"),
    "B04-descending": (["1"], "1"),
    "B05-cubic-rational": (["1"], "1"),
    "B06-tangent": (["2"], "2"),
    "B07-repeated-root": (["1"], "1"),
    "B08-boundary-crossing": (["2"], "2"),
    "B09-identity-threshold": (["0"], "0"),
    "B10-three-roots": (["2"], "2"),
    "B11-origin-shift": (["1", "1"], "2"),
    "B12-calibration-switch": (["1", "2"], "3"),
}


def self_check_oracle():
    """判定器自身核验：手算精确期望 + 无理/近根独立十进制真值。"""
    results = []
    for label, request, _note in boundary_cases():
        enclosed = oracle.enclose(request)
        entry = {"label": label, "oracle": enclosed}
        if label in EXACT_EXPECTATIONS:
            expect_seg, expect_total = EXACT_EXPECTATIONS[label]
            widths = [Fraction(b) - Fraction(a)
                      for a, b in enclosed["per_segment"]]
            ok = enclosed["per_segment"] == [[x, x] for x in expect_seg] and \
                enclosed["total"] == [expect_total, expect_total] and \
                all(w == 0 for w in widths)
            entry["exact_match"] = ok
            if not ok:
                entry["failure"] = "exact-expectation-mismatch"
        results.append(entry)
    # 无理根：Decimal 50 位独立真值（不来自生产，也不来自判定器）
    from decimal import Decimal, localcontext
    with localcontext() as ctx:
        ctx.prec = 50
        sqrt2 = Decimal(2).sqrt()
        true_value = Decimal(2) - sqrt2
        entry = next(e for e in results
                     if e["label"] == "B14-irrational-quadratic")
        lo, hi = (Decimal(Fraction(t).numerator)
                  / Decimal(Fraction(t).denominator)
                  for t in entry["oracle"]["per_segment"][0])
        entry["decimal_true_value"] = str(true_value)
        entry["decimal_contains"] = bool(lo <= true_value <= hi)
        entry["decimal_width"] = str(hi - lo)
        # 近根例
        near = next(e for e in results if e["label"] == "B13-near-roots")
        nlo, nhi = (Decimal(Fraction(t).numerator)
                    / Decimal(Fraction(t).denominator)
                    for t in near["oracle"]["per_segment"][0])
        near_true = Decimal("0.000002")
        near["decimal_true_value"] = "0.000002"
        near["decimal_contains"] = bool(nlo <= near_true <= nhi)
        near["decimal_width"] = str(nhi - nlo)
    ok = all(e.get("exact_match", True) and
             e.get("decimal_contains", True) for e in results)
    return ok, results


# --------------------------------------------------------------------------- #
# 判定规则与交接容差的单元证据（合成有理数，不依赖生产结果分布）
# --------------------------------------------------------------------------- #
def rule_unit_checks():
    details = []
    ok = True

    def record(name, low, high, produced, wanted):
        verdict, metrics = classify_item(low, high, produced)
        actual_r = Fraction(produced)
        dmin = max(low - actual_r, actual_r - high, Fraction(0))
        dmax = max(abs(actual_r - low), abs(actual_r - high))
        entry = {"name": name, "interval": [str(low), str(high)],
                 "r_binary_exact": str(actual_r), "verdict": verdict,
                 "expected": wanted, "dmin": str(dmin), "dmax": str(dmax),
                 "match": verdict == wanted}
        details.append(entry)
        return entry["match"]

    unit = Fraction(1, 10 ** 6)
    # 以生产 float 的二进制精确值为锚点构造区间，边界条件严格成立。
    r_exact = Fraction(1.5)                  # 1.5 本身可二进制精确表示
    produced = 1.5
    # 零宽区间精确命中 R
    ok &= record("rule-pass-exact", r_exact, r_exact, produced, "pass")
    # dmax 恰好等于 1e-5：零宽区间放在 R-TOL 处
    ok &= record("rule-pass-at-tolerance", r_exact - TOL, r_exact - TOL,
                 produced, "pass")
    # R 位于区间内部，dmax=0.5e-6 <= 容差
    ok &= record("rule-pass-inside-interval",
                 r_exact - unit / 2, r_exact + unit / 2,
                 produced, "pass")
    # 未判定：dmin=5e-6 <= 1e-5 < dmax=1.2e-5
    ok &= record("rule-undetermined-near",
                 r_exact - Fraction(12, 10 ** 6), r_exact - 5 * unit,
                 produced, "undetermined")
    # 未判定边界：dmin 恰好等于 1e-5（不满足严格大于），dmax 超出
    ok &= record("rule-undetermined-at-dmin-boundary",
                 r_exact - TOL - unit, r_exact - TOL,
                 produced, "undetermined")
    # 阻断：dmin = 1e-5+1e-6 严格大于容差
    ok &= record("rule-block-beyond-band",
                 r_exact - 3 * TOL, r_exact - TOL - unit,
                 produced, "block")
    # 反向阻断：R 在区间下方且 dmin 越界
    ok &= record("rule-block-below",
                 r_exact + TOL + unit, r_exact + 3 * TOL,
                 produced, "block")
    # 非有限值直接阻断
    verdict, _ = classify_item(r_exact, r_exact, float("nan"))
    details.append({"name": "rule-block-nan", "verdict": verdict,
                    "expected": "block", "match": verdict == "block"})
    ok &= verdict == "block"

    # 交接容差合成核验：B03 线性请求 h=2，精确 b=(0,1,0,0)
    request = one("0", "2", "0", "2", ("0", "1", "0", "0"), "1")
    base_curve = {"start": "0", "end": "2",
                  "coefficients": [0.0, 1.0, 0.0, 0.0]}
    passed_ok, _ = build_handoff_check(
        request, {"curves": [dict(base_curve,
                                  coefficients=[5e-11, 1.0, 0.0, 0.0])]})
    failed_c0, _ = build_handoff_check(
        request, {"curves": [dict(base_curve,
                                  coefficients=[2e-10, 1.0, 0.0, 0.0])]})
    passed_c1, _ = build_handoff_check(
        request, {"curves": [dict(base_curve,
                                  coefficients=[0.0, 1.0 + 5e-11, 0.0, 0.0])]})
    failed_c1, _ = build_handoff_check(
        request, {"curves": [dict(base_curve,
                                  coefficients=[0.0, 1.0 + 2e-10, 0.0, 0.0])]})
    handoff = {"within_c0_pass": passed_ok, "over_c0_block": not failed_c0,
               "within_c1_pass": passed_c1, "over_c1_block": not failed_c1}
    handoff["match"] = all(handoff[k] for k in
                           ("within_c0_pass", "over_c0_block",
                            "within_c1_pass", "over_c1_block"))
    details.append({"name": "rule-handoff-tolerance", **handoff})
    ok &= handoff["match"]
    return ok, details


# --------------------------------------------------------------------------- #
# 固定种子组合
# --------------------------------------------------------------------------- #
def _decimal4(rng, low, high):
    return f"{rng.uniform(low, high):.4f}"


def make_random_request(rng):
    n = rng.randint(1, 6)
    widths = [rng.randint(1, 2000) / 1000 for _ in range(n)]
    starts = []
    cursor = 0.0
    for w in widths:
        starts.append(cursor)
        cursor += w
    segments = []
    for i, (s, w) in enumerate(zip(starts, widths)):
        r0 = _decimal4(rng, -50, 50)
        r1 = _decimal4(rng, -50, 50)
        calib = [_decimal4(rng, -20, 20) for _ in range(4)]
        segments.append({
            "start": f"{s:.4f}".rstrip("0").rstrip(".") or "0",
            "end": f"{s + w:.4f}".rstrip("0").rstrip("."),
            "raw_start": r0, "raw_end": r1, "calibration": calib})
    threshold = _decimal4(rng, -30, 30)
    return {"segments": segments, "threshold": threshold}


def seeded_suite():
    records = []
    for seed in SEEDS:
        rng = random.Random(seed)
        for combo in range(6):
            request = make_random_request(rng)
            record = evaluate_request(
                request, f"S-seed{seed}-combo{combo}",
                f"固定种子 {seed} 第 {combo} 组随机合法请求")
            record["seed"] = seed
            record["combo"] = combo
            records.append(record)
    return records


# --------------------------------------------------------------------------- #
# 200 段压力
# --------------------------------------------------------------------------- #
def stress_suite():
    rng = random.Random(STRESS_SEED)
    widths = []
    for _ in range(STRESS_SEGMENTS - 1):
        widths.append(Fraction(rng.randint(1000, 3000), 1000))
    remaining = Fraction(86400) - sum(widths)
    widths.append(remaining)
    if widths[-1] < Fraction(1, 1000):
        raise RuntimeError("压力负载时长构造失败")
    segments = []
    cursor = Fraction(0)
    for w in widths:
        end = cursor + w
        segments.append({
            "start": frac_text(cursor), "end": frac_text(end),
            "raw_start": _decimal4(rng, -100, 100),
            "raw_end": _decimal4(rng, -100, 100),
            "calibration": [_decimal4(rng, -100, 100) for _ in range(4)]})
        cursor = end
    request = {"segments": segments,
               "threshold": _decimal4(rng, -50, 50)}
    # 先校验确为合法请求
    oracle.parse_request(request)

    tracemalloc.start()
    t0 = time.perf_counter()
    record = evaluate_request(request, "P200-stress",
                              "200 段固定种子压力负载")
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    enclosed = record["oracle"]
    total_width = Fraction(enclosed["total"][1]) - Fraction(enclosed["total"][0])
    segment_widths = [Fraction(b) - Fraction(a)
                      for a, b in enclosed["per_segment"]]
    record["resources"] = {
        "elapsed_seconds": elapsed,
        "peak_memory_bytes": peak,
        "segments": STRESS_SEGMENTS,
        "seed": STRESS_SEED,
        "oracle_total_width": str(total_width),
        "max_segment_width": str(max(segment_widths)),
    }
    stress_path = os.path.join(ARTIFACT_DIR, "stress_request.json")
    return record, stress_path, request


# --------------------------------------------------------------------------- #
# 变异验证（独立进程，真实调用链，异常不算检出）
# --------------------------------------------------------------------------- #
def mutation_suite():
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    m1_request = {
        "segments": [{"start": "10", "end": "12", "raw_start": "0",
                      "raw_end": "2", "calibration": ["0", "1", "0", "0"]}],
        "threshold": "1"}
    m2_request = one("0", "4", "0", "4", ("-6", "11", "-6", "1"), "0")
    specs = [("M1", "local-time-coordinate-misuse", m1_request,
              "m1_request.json"),
             ("M2", "missed-second-crossing", m2_request,
              "m2_request.json")]
    results = []
    for code, name, request, filename in specs:
        path = os.path.join(ARTIFACT_DIR, filename)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(request, handle, ensure_ascii=False)
        # 未变异基线
        baseline = pipeline.analyze(request)
        proc = run_module(["mrunner.py", code, path])
        entry = {"code": code, "name": name, "request_path": path,
                 "baseline": {
                     "per_segment": baseline["per_segment"],
                     "total": baseline["total"]}}
        if proc.returncode != 0:
            entry.update({"detected": False,
                          "reason": f"mutant-exit-{proc.returncode}",
                          "stdout": proc.stdout, "stderr": proc.stderr})
            results.append(entry)
            continue
        mutated = json.loads(proc.stdout)
        if any(not math.isfinite(x) for x in mutated["per_segment"]) or \
                not math.isfinite(mutated["total"]):
            entry.update({"detected": False, "reason": "non-finite-mutant"})
            results.append(entry)
            continue
        enclosed = oracle.enclose(request)
        item_verdicts = []
        for index, produced in enumerate(mutated["per_segment"]):
            lo, hi = map(Fraction, enclosed["per_segment"][index])
            verdict, metrics = classify_item(lo, hi, produced)
            item_verdicts.append({"index": index, "verdict": verdict,
                                  **metrics})
        total_verdict, total_metrics = classify_item(
            *map(Fraction, enclosed["total"]), mutated["total"])
        detected = any(v["verdict"] == "block" for v in item_verdicts) or \
            total_verdict == "block"
        entry.update({
            "detected": detected,
            "mutated": {
                "curves": [
                    {"start": c["start"], "end": c["end"],
                     "coefficients": [repr(x) for x in c["coefficients"]]}
                    for c in mutated["curves"]],
                "per_segment": [repr(x) for x in mutated["per_segment"]],
                "total": repr(mutated["total"])},
            "oracle": enclosed,
            "item_verdicts": item_verdicts
            + [{"index": "total", "verdict": total_verdict, **total_metrics}],
        })
        results.append(entry)
    return results


# --------------------------------------------------------------------------- #
# 报告交叉核对
# --------------------------------------------------------------------------- #
def refresh_report_resources(evidence):
    """用本次实测值刷新 REPORT.md 资源块与发布意见标记（标记之间内容）。"""
    report_path = os.path.join(ROOT, "REPORT.md")
    text = open(report_path, encoding="utf-8").read()
    stress = evidence["stress"]["record"]["resources"]
    counts = evidence["verdict_counts"]
    start = "QA-RESOURCE-BLOCK:START"
    end = "QA-RESOURCE-BLOCK:END"
    head, rest = text.split(start, 1)
    _, tail = rest.split(end, 1)
    block = (
        "\n（本块由 `qa.py verify` 每次重算后自动刷新，不以静态历史数字为门槛）\n"
        f"- 压力负载：{stress['segments']} 段，种子 {stress['seed']}\n"
        f"- 贯通验证耗时（含生产分析与独立判定，秒）：{stress['elapsed_seconds']:.6f}\n"
        f"- tracemalloc 峰值内存（字节）：{stress['peak_memory_bytes']}\n"
        f"- 判定器总区间宽度（秒）：{stress['oracle_total_width']}\n"
        f"- 单段最大区间宽度（秒）：{stress['max_segment_width']}\n"
        f"- 边界判定计数（通过/阻断/未判定）：{counts['boundary']}\n"
        f"- 固定种子判定计数：{counts['seeded']}\n"
        f"- 压力判定计数：{counts['stress']}\n"
    )
    text = head + start + block + end + tail
    overall = evidence["overall_verdict"].upper()
    for state in ("PASS", "BLOCK", "UNDETERMINED"):
        text = text.replace(f"QA-RELEASE-OPINION:{state}",
                            f"QA-RELEASE-OPINION:__STATE__")
    text = text.replace("QA-RELEASE-OPINION:__STATE__",
                        f"QA-RELEASE-OPINION:{overall}")
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write(text)


def check_report(evidence):
    report_path = os.path.join(ROOT, "REPORT.md")
    text = open(report_path, encoding="utf-8").read()
    problems = []
    required_markers = [
        "QA-M1:DETECTED", "QA-M2:DETECTED",
        "QA-BOUNDARY:PASS", "QA-SEEDED:PASS", "QA-STRESS:PASS",
        "QA-SELF-ORACLE:PASS", "QA-BASELINE:PASS",
        "QA-RULE-UNITS:PASS",
        "QA-CASE:B01-normal-two-segment",
        "QA-CASE:B06-tangent", "QA-CASE:B07-repeated-root",
        "QA-CASE:B08-boundary-crossing", "QA-CASE:B09-identity-threshold",
        "QA-CASE:B13-near-roots", "QA-CASE:B14-irrational-quadratic",
        "QA-RESOURCE-BLOCK:START", "QA-RESOURCE-BLOCK:END",
        f"QA-RELEASE-OPINION:{evidence['overall_verdict'].upper()}",
    ]
    for marker in required_markers:
        if marker not in text:
            problems.append(f"报告缺少标记 {marker}")
    # 发布意见不得出现与总体判定矛盾的标记
    for state in ("PASS", "BLOCK", "UNDETERMINED"):
        marker = f"QA-RELEASE-OPINION:{state}"
        if state != evidence["overall_verdict"].upper() and marker in text:
            problems.append(f"报告发布意见与证据矛盾: {marker}")
    # 报告引用的案例判定须与证据一致
    boundary_verdicts = {r["label"]: r["verdict"]
                         for r in evidence["boundary_records"]}
    for label, verdict in boundary_verdicts.items():
        marker = f"QA-CASE:{label}:{verdict.upper()}"
        if marker not in text:
            problems.append(f"报告案例判定不一致或缺失: {marker}")
    # 变异标记须与证据一致
    for entry in evidence["mutations"]:
        expect = "DETECTED" if entry["detected"] else "MISSED"
        if f"QA-{entry['code']}:{expect}" not in text:
            problems.append(f"报告变异结论不一致: {entry['code']} {expect}")
    # 基线与自检标记须与证据一致
    if not evidence["baseline_checks"]["returncode"] == 0 and \
            "QA-BASELINE:PASS" in text:
        problems.append("基线回归失败但报告标记通过")
    if not evidence["oracle_self_checks"]["pass"] and \
            "QA-SELF-ORACLE:PASS" in text:
        problems.append("判定器自检失败但报告标记通过")
    if not evidence["rule_unit_checks"]["pass"] and \
            "QA-RULE-UNITS:PASS" in text:
        problems.append("规则单元证据失败但报告标记通过")
    return problems


def summarize_verdicts(records):
    counts = {"pass": 0, "block": 0, "undetermined": 0}
    for record in records:
        counts[record["verdict"]] += 1
    return counts


def verify():
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    os.makedirs(FAIL_DIR, exist_ok=True)
    evidence = {"schema": "qa-evidence-v1"}

    # 1) 原 checks.py 必须原样通过
    baseline_proc = run_module(["checks.py"])
    evidence["baseline_checks"] = {
        "argv": [sys.executable, "-I", "_site.py", "checks.py"],
        "returncode": baseline_proc.returncode,
        "stdout": baseline_proc.stdout.strip(),
        "stderr": baseline_proc.stderr.strip()}
    baseline_ok = baseline_proc.returncode == 0

    # 2) 判定器自身手算核验
    self_ok, self_results = self_check_oracle()
    evidence["oracle_self_checks"] = {"pass": self_ok, "cases": self_results}

    # 2b) 判定三分支与交接容差单元证据
    rules_ok, rule_details = rule_unit_checks()
    evidence["rule_unit_checks"] = {"pass": rules_ok, "cases": rule_details}

    # 3) 边界贯通
    boundary_records = [evaluate_request(req, label, note)
                        for label, req, note in boundary_cases()]
    evidence["boundary_records"] = boundary_records

    # 4) 固定种子组合
    seeded_records = seeded_suite()
    evidence["seeded_records"] = seeded_records

    # 5) 200 段压力
    stress_record, stress_path, stress_request = stress_suite()
    with open(stress_path, "w", encoding="utf-8") as handle:
        json.dump(stress_request, handle, ensure_ascii=False)
    evidence["stress"] = {"record": stress_record,
                          "request_path": os.path.relpath(stress_path, ROOT)}

    # 6) 变异
    mutations = mutation_suite()
    evidence["mutations"] = mutations

    all_product = boundary_records + seeded_records + [stress_record]
    evidence["verdict_counts"] = {
        "boundary": summarize_verdicts(boundary_records),
        "seeded": summarize_verdicts(seeded_records),
        "stress": summarize_verdicts([stress_record])}
    product_blocked = any(r["verdict"] == "block" for r in all_product)
    product_undetermined = any(r["verdict"] == "undetermined"
                               for r in all_product)
    mutants_detected = all(m["detected"] for m in mutations)
    evidence["mutants_all_detected"] = mutants_detected

    if product_blocked or not baseline_ok or not self_ok or not rules_ok \
            or not mutants_detected:
        overall = "block"
    elif product_undetermined:
        overall = "undetermined"
    else:
        overall = "pass"
    evidence["overall_verdict"] = overall

    # 失败证据保留输入
    failures = [r for r in all_product if r["verdict"] == "block"]
    for index, record in enumerate(failures):
        path = os.path.join(FAIL_DIR, f"{record['label']}-{index}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2,
                      default=_json_default)
    evidence["failure_paths"] = sorted(
        os.path.relpath(os.path.join(FAIL_DIR, p), ROOT)
        for p in os.listdir(FAIL_DIR))

    with open(EVIDENCE_PATH, "w", encoding="utf-8") as handle:
        json.dump(evidence, handle, ensure_ascii=False, indent=2,
                  default=_json_default)

    # 用本次实测刷新报告资源块与发布意见，再做交叉核对
    refresh_report_resources(evidence)
    report_problems = check_report(evidence)
    evidence["report_problems"] = report_problems
    with open(EVIDENCE_PATH, "w", encoding="utf-8") as handle:
        json.dump(evidence, handle, ensure_ascii=False, indent=2,
                  default=_json_default)

    print(json.dumps({
        "baseline_checks": baseline_ok,
        "oracle_self_checks": self_ok,
        "rule_unit_checks": rules_ok,
        "boundary": evidence["verdict_counts"]["boundary"],
        "seeded": evidence["verdict_counts"]["seeded"],
        "stress": evidence["verdict_counts"]["stress"],
        "mutants_detected": mutants_detected,
        "overall_verdict": overall,
        "report_problems": report_problems,
        "evidence": os.path.relpath(EVIDENCE_PATH, ROOT),
    }, ensure_ascii=False, indent=2))

    if report_problems or not baseline_ok or not self_ok or not rules_ok \
            or not mutants_detected or product_blocked:
        return 1
    if overall == "undetermined":
        return 2
    return 0


def _json_default(obj):
    if isinstance(obj, Fraction):
        return str(obj)
    raise TypeError(f"不可序列化: {type(obj)}")


# --------------------------------------------------------------------------- #
# demo
# --------------------------------------------------------------------------- #
def demo():
    request = samples.normal()
    result = pipeline.analyze(request)
    enclosed = oracle.enclose(request)
    record = evaluate_request(request, "DEMO-normal", "真实演示样例")
    print("=" * 72)
    print("演示样例（sample.json / samples.normal）：两段 u 与 2-u，阈值 1")
    print("-" * 72)
    for index, curve in enumerate(result["curves"]):
        print(f"段{index} 时间原文 {curve['start']}..{curve['end']} "
              f"局部系数(按二进制精确解释):")
        for k, coeff in enumerate(curve["coefficients"]):
            print(f"    b{k} = {coeff!r} = {Fraction(coeff)}")
        lo, hi = enclosed["per_segment"][index]
        produced = result["per_segment"][index]
        verdict = record["item_verdicts"][index]
        print(f"    生产时长 = {produced!r} = {Fraction(produced)}")
        print(f"    独立区间 = [{lo}, {hi}]  判定 = {verdict['verdict']} "
              f"(dmin={verdict['dmin']}, dmax={verdict['dmax']})")
    print(f"总生产时长 = {result['total']!r}；独立总区间 = {enclosed['total']}")
    print(f"交接核对 = {'通过' if record['handoff']['pass'] else '阻断'}；"
          f"总体判定 = {record['verdict']}")

    print("=" * 72)
    print("变异 M1：局部时间系数被误当绝对时间 t 系数（真实调用链补丁）")
    print("-" * 72)
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    m1_path = os.path.join(ARTIFACT_DIR, "demo_m1.json")
    m1_request = {
        "segments": [{"start": "10", "end": "12", "raw_start": "0",
                      "raw_end": "2", "calibration": ["0", "1", "0", "0"]}],
        "threshold": "1"}
    with open(m1_path, "w", encoding="utf-8") as handle:
        json.dump(m1_request, handle)
    proc = run_module(["mrunner.py", "M1", m1_path])
    mutated = json.loads(proc.stdout)
    base = pipeline.analyze(m1_request)
    print(f"基线局部系数 = {base['curves'][0]['coefficients']}")
    print(f"变异局部系数 = {tuple(mutated['curves'][0]['coefficients'])}")
    print(f"基线时长 = {base['per_segment']}；变异时长 = {mutated['per_segment']}")
    verdict, metrics = classify_item(
        *map(Fraction, oracle.enclose(m1_request)["per_segment"][0]),
        mutated["per_segment"][0])
    print(f"独立判据对变异结果 = {verdict}（{metrics}）")

    print("=" * 72)
    print("变异 M2：时长统计漏掉同段第二次穿越 (u-1)(u-2)(u-3)，u∈[0,4]")
    print("-" * 72)
    m2_path = os.path.join(ARTIFACT_DIR, "demo_m2.json")
    m2_request = one("0", "4", "0", "4", ("-6", "11", "-6", "1"), "0")
    with open(m2_path, "w", encoding="utf-8") as handle:
        json.dump(m2_request, handle)
    proc = run_module(["mrunner.py", "M2", m2_path])
    mutated = json.loads(proc.stdout)
    base = pipeline.analyze(m2_request)
    enc = oracle.enclose(m2_request)
    print(f"基线时长 = {base['per_segment'][0]}（真值区间 {enc['per_segment'][0]}）")
    print(f"变异时长 = {mutated['per_segment'][0]}（漏计 (3,4) 暴露带）")
    verdict, metrics = classify_item(
        *map(Fraction, enc["per_segment"][0]),
        mutated["per_segment"][0])
    print(f"独立判据对变异结果 = {verdict}（{metrics}）")
    print("=" * 72)
    return 0


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"verify", "demo"}:
        print("usage: python3 -I qa.py verify | demo", file=sys.stderr)
        return 2
    return verify() if sys.argv[1] == "verify" else demo()


if __name__ == "__main__":
    raise SystemExit(main())
