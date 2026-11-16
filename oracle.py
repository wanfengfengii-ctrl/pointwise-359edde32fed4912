# -*- coding: utf-8 -*-
"""独立判定器：从十进制原文建立精确有理数模型，给出严格超阈时长闭区间。

本模块不导入任何生产模块（calibration/exposure/pipeline 等），不复制生产的
浮点求根实现，只使用 Python 标准库（fractions/re/json）。

数学依据（见 CONTRACT.md “精确业务不变量和交接”）：
  每段 x(u) = r0 + (r1-r0)*u/h，校准 P(x)=a0+a1 x+a2 x^2+a3 x^3，
  物理曲线 g(u)=P(x(u))-threshold 是 u 的次数不超过 3 的有理系数多项式。
  超阈时长 = {u in [0,h] : g(u)>0} 的勒贝格测度；单点（相切、边界交点）
  不贡献测度，g 恒为零贡献零。

实现路线：
  1. Fraction 精确展开 g(u)；
  2. g/gcd(g,g') 得到无重根多项式 q（根集合与 g 完全相同）；
  3. q 的 Sturm 链 + 有理中点二分，隔离 [0,h] 内全部互异实根，
     有理根可被中点精确命中（零宽区间），无理根被有理闭区间夹逼；
  4. 在根之间的空隙取有理样本点精确判号，按符号为正的区间长度求和，
     根的不确定性线性传播为时长闭区间 [L,U]。
"""
import json
import re
from fractions import Fraction

__all__ = ["enclose", "parse_request", "local_coefficients",
           "rational_text", "TOTAL_WIDTH_LIMIT", "ROOT_WIDTH"]

_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]{1,12})?\Z")
TOTAL_WIDTH_LIMIT = Fraction(1, 10 ** 6)
# 单根夹逼目标宽度。每段至多 3 个互异根，200 段总宽 <= 200*3*1e-12 = 6e-10，
# 远小于契约要求的总宽 1e-6。
ROOT_WIDTH = Fraction(1, 10 ** 12)


# --------------------------------------------------------------------------- #
# 独立输入解析（与生产同契约，但不调用生产代码）
# --------------------------------------------------------------------------- #
def _decimal(text, limit):
    if not isinstance(text, str) or not _DECIMAL.fullmatch(text):
        raise ValueError("oracle: 需要普通十进制字符串（至多 12 位小数）")
    value = Fraction(text)
    if abs(value) > limit:
        raise ValueError("oracle: 十进制数值越界")
    return value


def parse_request(request):
    """把合法请求解析为 Fraction 结构；非法输入抛 ValueError。"""
    if not isinstance(request, dict) or set(request) != {"segments", "threshold"}:
        raise ValueError("oracle: 顶层字段必须恰为 segments 与 threshold")
    threshold = _decimal(request["threshold"], 10 ** 9)
    rows = request["segments"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
        raise ValueError("oracle: segments 须为 1..200 项")
    segments = []
    previous = None
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
                "start", "end", "raw_start", "raw_end", "calibration"}:
            raise ValueError("oracle: 段字段不符契约")
        start = _decimal(row["start"], 86400)
        end = _decimal(row["end"], 86400)
        if start < 0 or end - start < Fraction(1, 1000):
            raise ValueError("oracle: 时间域非法")
        if previous is not None and start != previous:
            raise ValueError("oracle: 相邻段必须首尾相接")
        coeffs = row["calibration"]
        if not isinstance(coeffs, list) or len(coeffs) != 4:
            raise ValueError("oracle: 校准系数必须恰有四项")
        segments.append({
            "start_text": row["start"], "end_text": row["end"],
            "s": start, "e": end, "h": end - start,
            "r0": _decimal(row["raw_start"], 100),
            "r1": _decimal(row["raw_end"], 100),
            "a": tuple(_decimal(c, 1000) for c in coeffs),
        })
        previous = end
    return threshold, segments


# --------------------------------------------------------------------------- #
# 精确有理多项式工具（升幂系数表）
# --------------------------------------------------------------------------- #
def _trim(poly):
    poly = list(poly)
    while len(poly) > 1 and poly[-1] == 0:
        poly.pop()
    return poly


def _poly_add(p, q):
    n = max(len(p), len(q))
    out = [Fraction(0)] * n
    for i, c in enumerate(p):
        out[i] += c
    for i, c in enumerate(q):
        out[i] += c
    return _trim(out)


def _poly_mul(p, q):
    out = [Fraction(0)] * (len(p) + len(q) - 1)
    for i, ci in enumerate(p):
        for j, cj in enumerate(q):
            out[i + j] += ci * cj
    return _trim(out)


def _poly_eval(poly, x):
    value = Fraction(0)
    for coeff in reversed(poly):
        value = value * x + coeff
    return value


def _poly_deriv(poly):
    if len(poly) <= 1:
        return [Fraction(0)]
    return _trim([Fraction(k) * poly[k] for k in range(1, len(poly))])


def _poly_divrem(dividend, divisor):
    """返回 (商, 余式)，系数均为 Fraction。"""
    dividend, divisor = list(dividend), list(divisor)
    deg = len(divisor) - 1
    if len(dividend) - 1 < deg:
        return [Fraction(0)], _trim(dividend)
    quotient = [Fraction(0)] * (len(dividend) - len(divisor) + 1)
    remainder = list(dividend)
    inv_lead = Fraction(1, divisor[-1])
    for i in range(len(dividend) - len(divisor), -1, -1):
        factor = remainder[i + deg] * inv_lead
        quotient[i] = factor
        for j in range(deg + 1):
            remainder[i + j] -= factor * divisor[j]
    return _trim(quotient), _trim(remainder)


def _poly_gcd(p, q):
    while not (len(q) == 1 and q[0] == 0):
        _, remainder = _poly_divrem(p, q)
        p, q = q, remainder
    return [c / p[-1] for c in p]


def _sturm_chain(poly):
    chain = [poly, _poly_deriv(poly)]
    while not (len(chain[-1]) == 1 and chain[-1][0] == 0):
        _, remainder = _poly_divrem(chain[-2], chain[-1])
        chain.append([-c for c in remainder])
    return chain


def _variations(chain, x):
    """x 处 Sturm 链的符号变化数；零值按标准规则丢弃。"""
    signs = []
    for poly in chain:
        value = _poly_eval(poly, x)
        if value > 0:
            signs.append(1)
        elif value < 0:
            signs.append(-1)
    return sum(1 for i in range(1, len(signs)) if signs[i] != signs[i - 1])


def _count_open(chain, a, b):
    """开区间 (a,b) 内互异实根数。Sturm 公式给的是 (a,b]，b 为根时减一。"""
    count = _variations(chain, a) - _variations(chain, b)
    if _poly_eval(chain[0], b) == 0:
        count -= 1
    return count


# --------------------------------------------------------------------------- #
# 物理曲线与局部时间展开
# --------------------------------------------------------------------------- #
def _physical_poly(segment, threshold):
    """g(u) = P(x(u)) - threshold 的精确有理升幂系数。"""
    h = segment["h"]
    alpha = segment["r0"]
    beta = (segment["r1"] - segment["r0"]) / h
    a0, a1, a2, a3 = segment["a"]
    x_poly = [alpha, beta]                      # x(u) = alpha + beta*u
    g = [-threshold]
    x_power = [Fraction(1)]
    for coeff in (a0, a1, a2, a3):
        g = _poly_add(g, [coeff * c for c in x_power])
        x_power = _poly_mul(x_power, x_poly)
    return _trim(g)


def local_coefficients(segment):
    """独立精确展开局部时间 u 的四次以内系数 b0..b3（交接核对用）。"""
    alpha = segment["r0"]
    beta = (segment["r1"] - segment["r0"]) / segment["h"]
    a0, a1, a2, a3 = segment["a"]
    b0 = a0 + a1 * alpha + a2 * alpha ** 2 + a3 * alpha ** 3
    b1 = beta * (a1 + 2 * a2 * alpha + 3 * a3 * alpha ** 2)
    b2 = beta * beta * (a2 + 3 * a3 * alpha)
    b3 = a3 * beta ** 3
    return [b0, b1, b2, b3]


# --------------------------------------------------------------------------- #
# 根隔离与夹逼
# --------------------------------------------------------------------------- #
def _isolate_roots(square_free, h, target_width):
    """返回 [0,h] 内全部互异根的有序有理闭区间列表 (l,u)，l<=根<=u。

    有理根被精确命中时 l==u；无理根满足 u-l<=target_width。
    """
    chain = _sturm_chain(square_free)
    enclosures = []
    if _poly_eval(square_free, Fraction(0)) == 0:
        enclosures.append((Fraction(0), Fraction(0)))
    if h != 0 and _poly_eval(square_free, h) == 0:
        enclosures.append((h, h))
    stack = [(Fraction(0), h)]
    while stack:
        lo, hi = stack.pop()
        number = _count_open(chain, lo, hi)
        if number == 0:
            continue
        mid = (lo + hi) / 2
        if _poly_eval(square_free, mid) == 0:
            # 精确命中有理根：零宽区间，两侧继续隔离。
            enclosures.append((mid, mid))
            stack.append((lo, mid))
            stack.append((mid, hi))
        elif number == 1:
            while hi - lo > target_width:
                middle = (lo + hi) / 2
                if _poly_eval(square_free, middle) == 0:
                    lo = hi = middle
                    break
                if _count_open(chain, lo, middle) == 1:
                    hi = middle
                else:
                    lo = middle
            if lo != hi:
                enclosures.append((lo, hi))
        else:
            stack.append((lo, mid))
            stack.append((mid, hi))
    enclosures.sort(key=lambda pair: pair[0])
    # 不变量自检：区间两两不交且有序。
    for left, right in zip(enclosures, enclosures[1:]):
        if left[1] > right[0]:
            raise RuntimeError("oracle: 根隔离区间相交")
    return enclosures


def _segment_enclosure(g, h, target_width):
    """返回单段严格超阈真值时长的有理闭区间 (L,U)。"""
    if len(g) == 1 and g[0] == 0:
        return Fraction(0), Fraction(0)              # 整段恒等阈值
    gcd = _poly_gcd(g, _poly_deriv(g))
    square_free = g if len(gcd) == 1 else _poly_divrem(g, gcd)[0]
    roots = _isolate_roots(square_free, h, target_width)
    if not roots:
        if _poly_eval(g, h / 2) > 0:
            return h, h
        return Fraction(0), Fraction(0)

    lower = upper = Fraction(0)
    count = len(roots)
    for gap in range(count + 1):
        if gap == 0:
            left_coord, right_coord = Fraction(0), roots[0][0]
        elif gap == count:
            left_coord, right_coord = roots[-1][1], h
        else:
            left_coord, right_coord = roots[gap - 1][1], roots[gap][0]
        if right_coord <= left_coord:
            continue                                # 根恰好压在域端点
        sample = (left_coord + right_coord) / 2
        if _poly_eval(g, sample) <= 0:
            continue                                # 严格大于才算暴露
        if gap == 0:
            lower += roots[0][0]
            upper += roots[0][1]
        elif gap == count:
            lower += h - roots[-1][1]
            upper += h - roots[-1][0]
        else:
            lower += roots[gap][0] - roots[gap - 1][1]
            upper += roots[gap][1] - roots[gap - 1][0]
    if not (Fraction(0) <= lower <= upper <= h):
        raise RuntimeError("oracle: 单段区间越界")
    return lower, upper


# --------------------------------------------------------------------------- #
# 输出格式化
# --------------------------------------------------------------------------- #
def rational_text(value):
    """精确有理数字符串：整数、有限十进制或 n/d；均可被 Fraction 还原。"""
    if value.denominator == 1:
        return str(value.numerator)
    # 分母仅含因子 2、5 时可写为有限十进制（最多保留 15 位）。
    remaining = value.denominator
    places = 0
    while remaining % 2 == 0:
        remaining //= 2
        places += 1
    fives = 0
    while remaining % 5 == 0:
        remaining //= 5
        fives += 1
    places = max(places, fives)
    if remaining == 1 and places <= 15:
        scaled = value.numerator * (10 ** places // value.denominator)
        sign = "-" if scaled < 0 else ""
        scaled = abs(scaled)
        return f"{sign}{scaled // 10 ** places}.{scaled % 10 ** places:0{places}d}"
    return f"{value.numerator}/{value.denominator}"


def enclose(request, root_width=ROOT_WIDTH):
    """公开判定接口：返回每段与总超阈时长的精确有理闭区间。"""
    threshold, segments = parse_request(request)
    per_segment = []
    total_lower = total_upper = Fraction(0)
    for segment in segments:
        g = _physical_poly(segment, threshold)
        lower, upper = _segment_enclosure(g, segment["h"], root_width)
        per_segment.append([rational_text(lower), rational_text(upper)])
        total_lower += lower
        total_upper += upper
    if total_upper - total_lower > TOTAL_WIDTH_LIMIT:
        raise RuntimeError("oracle: 总区间宽度超过 1e-6 秒")
    return {
        "per_segment": per_segment,
        "total": [rational_text(total_lower),
                  rational_text(total_upper)],
    }


def _main():
    import sys
    from pathlib import Path
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(json.dumps(enclose(request), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    _main()
