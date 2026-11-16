"""Production calibration: raw linear segments -> local-time cubic curves."""
from dataclasses import dataclass
from fractions import Fraction
import re

_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]{1,12})?\Z")

def number(text, limit):
    if not isinstance(text, str) or not _DECIMAL.fullmatch(text):
        raise ValueError("expected a plain decimal string with <=12 fractional digits")
    value = Fraction(text)
    if abs(value) > limit:
        raise ValueError("decimal out of range")
    return value

@dataclass(frozen=True)
class Segment:
    start: str
    end: str
    raw_start: Fraction
    raw_end: Fraction
    calibration: tuple

@dataclass(frozen=True)
class Curve:
    start: str
    end: str
    coefficients: tuple

    @property
    def duration(self):
        return float(Fraction(self.end) - Fraction(self.start))

def parse_request(request):
    if not isinstance(request, dict) or set(request) != {"segments", "threshold"}:
        raise ValueError("request fields")
    threshold = number(request["threshold"], 10**9)
    rows = request["segments"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200:
        raise ValueError("expected 1..200 segments")
    parsed = []
    previous = None
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "start", "end", "raw_start", "raw_end", "calibration"
        }:
            raise ValueError("segment fields")
        start = number(row["start"], 86400)
        end = number(row["end"], 86400)
        if start < 0 or end - start < Fraction(1, 1000):
            raise ValueError("time domain")
        if previous is not None and start != previous:
            raise ValueError("segments must be contiguous and ordered")
        coefficients = row["calibration"]
        if not isinstance(coefficients, list) or len(coefficients) != 4:
            raise ValueError("four calibration coefficients required")
        parsed.append(Segment(
            row["start"], row["end"],
            number(row["raw_start"], 100), number(row["raw_end"], 100),
            tuple(number(c, 1000) for c in coefficients)
        ))
        previous = end
    return parsed, threshold

def calibrate(segment):
    h = float(Fraction(segment.end) - Fraction(segment.start))
    x = float(segment.raw_start)
    slope = (float(segment.raw_end) - x) / h
    a, b, c, d = map(float, segment.calibration)
    return Curve(segment.start, segment.end, (
        a + x * (b + x * (c + x * d)),
        slope * (b + x * (2 * c + 3 * d * x)),
        slope * slope * (c + 3 * d * x),
        d * slope * slope * slope,
    ))
