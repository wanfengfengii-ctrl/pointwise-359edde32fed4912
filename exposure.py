"""Production floating-point exposure calculation; no certified interval oracle."""
import math

def _value(coefficients, u):
    a, b, c, d = coefficients
    return ((d * u + c) * u + b) * u + a

def _turns(coefficients, h):
    _, b, c, d = coefficients
    a, b, c = 3 * d, 2 * c, b
    roots = []
    if a == 0:
        if b != 0:
            roots.append(-c / b)
    else:
        discriminant = b * b - 4 * a * c
        if discriminant >= 0:
            q = -0.5 * (b + math.copysign(math.sqrt(discriminant), b))
            if q == 0:
                roots.append(-b / (2 * a))
            else:
                roots.extend((q / a, c / q))
    return sorted(set(t for t in roots if 0 < t < h))

def duration_above(curve, threshold):
    coefficients = list(curve.coefficients)
    coefficients[0] -= float(threshold)
    h = curve.duration
    monotone = [0.0] + _turns(coefficients, h) + [h]
    cuts = set(monotone)
    for left, right in zip(monotone, monotone[1:]):
        fl, fr = _value(coefficients, left), _value(coefficients, right)
        if fl == 0:
            cuts.add(left)
        if fr == 0:
            cuts.add(right)
        if (fl < 0 < fr) or (fr < 0 < fl):
            lo, hi, flo = left, right, fl
            for _ in range(80):
                mid = (lo + hi) / 2
                if mid == lo or mid == hi:
                    break
                fm = _value(coefficients, mid)
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
        if _value(coefficients, (left + right) / 2) > 0
    )

def summarize(curves, threshold):
    per_segment = [duration_above(curve, threshold) for curve in curves]
    return {"per_segment": per_segment, "total": math.fsum(per_segment)}
