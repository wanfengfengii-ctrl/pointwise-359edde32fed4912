# -*- coding: utf-8 -*-
"""变异运行器：在独立进程内对 pipeline 调用链打运行时补丁，不改冻结文件。

两类实际业务变异（契约要求）：
  M1 local-time-coordinate-misuse：把局部 u 系数误当绝对时间 t 系数求值，
     再展回 pipeline 所要求的局部系数格式——模拟端点回归式的坐标误用。
  M2 missed-second-crossing：时长统计中只保留第一个内部转折点/割点，
     同段第二次穿越被丢弃——模拟漏掉短暂暴露。

用法：
  python3 -I mrunner.py M1 request.json
  python3 -I mrunner.py M2 request.json
输出为单行 JSON（结构同 pipeline.analyze），调用真实的 calibration->exposure
链；无异常退出路径（出错则输出含 error 的 JSON 并以 2 退出，由 qa 判失败）。
"""
import json
import os
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import calibration
import exposure
import pipeline


@dataclass(frozen=True)
class _MutCurve:
    start: str
    end: str
    coefficients: tuple

    @property
    def duration(self):
        return float(Fraction(self.end) - Fraction(self.start))


def _patch_calibrate_misuse(segment):
    """M1：正确局部系数先被当作绝对 t 的多项式，再在局部坐标重展。"""
    original = calibration.calibrate(segment)
    a, b, c, d = original.coefficients
    s = float(Fraction(segment.start))
    # q(t)=a+b t+c t^2+d t^3，令 t=s+u：
    # q(s+u) 的局部系数
    local = (
        ((d * s + c) * s + b) * s + a,
        b + s * (2 * c + 3 * d * s),
        c + 3 * d * s,
        d,
    )
    return _MutCurve(original.start, original.end, local)


def _buggy_duration_miss_second(curve, threshold):
    """M2：复制生产算法的二分与割点逻辑，但每个单调段只保留第一个割点，
    从而丢弃同段第二次穿越（真实业务变异，非标签修改）。"""
    import math
    coefficients = list(curve.coefficients)
    coefficients[0] -= float(threshold)
    h = curve.duration

    def value(u):
        a, b, c, d = coefficients
        return ((d * u + c) * u + b) * u + a

    _, b, c, d = coefficients
    aa, bb, cc = 3 * d, 2 * c, b
    turns = []
    if aa == 0:
        if bb != 0:
            turns.append(-cc / bb)
    else:
        discriminant = bb * bb - 4 * aa * cc
        if discriminant >= 0:
            q = -0.5 * (bb + math.copysign(math.sqrt(discriminant), bb))
            if q == 0:
                turns.append(-bb / (2 * aa))
            else:
                turns.extend((q / aa, cc / q))
    turns = sorted(set(t for t in turns if 0 < t < h))
    monotone = [0.0] + turns + [h]
    cuts = {0.0, h}
    found_crossing = False
    for left, right in zip(monotone, monotone[1:]):
        fl, fr = value(left), value(right)
        if fl == 0:
            cuts.add(left)
        if fr == 0:
            cuts.add(right)
        if (fl < 0 < fr) or (fr < 0 < fl):
            if found_crossing:
                continue                       # 变异：漏掉同段第二次穿越
            found_crossing = True
            lo, hi, flo = left, right, fl
            for _ in range(80):
                mid = (lo + hi) / 2
                if mid == lo or mid == hi:
                    break
                fm = value(mid)
                if fm == 0:
                    lo = hi = mid
                    break
                if (flo < 0) == (fm < 0):
                    lo, flo = mid, fm
                else:
                    hi = mid
            cuts.add((lo + hi) / 2)
    ordered = sorted(cuts)
    return math.fsum(
        right - left for left, right in zip(ordered, ordered[1:])
        if value((left + right) / 2) > 0
    )


def run(mutation, request):
    if mutation == "M1":
        pipeline.calibrate = _patch_calibrate_misuse
    elif mutation == "M2":
        exposure.duration_above = _buggy_duration_miss_second
    else:
        raise ValueError(f"未知变异: {mutation}")
    return pipeline.analyze(request)


def main():
    try:
        if len(sys.argv) != 3 or sys.argv[1] not in {"M1", "M2"}:
            raise ValueError("usage: python3 mrunner.py M1|M2 REQUEST.json")
        request = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        output = run(sys.argv[1], request)
    except (ValueError, TypeError, KeyError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(output, ensure_ascii=False, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
