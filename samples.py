"""Input builders only: no numerical oracle."""
from copy import deepcopy

_NORMAL = {
    "segments": [
        {"start": "10", "end": "12", "raw_start": "0", "raw_end": "2",
         "calibration": ["0", "1", "0", "0"]},
        {"start": "12", "end": "14", "raw_start": "2", "raw_end": "0",
         "calibration": ["0", "1", "0", "0"]}
    ],
    "threshold": "1"
}

def normal():
    return deepcopy(_NORMAL)

def one_segment(start="0", end="1", raw_start="0", raw_end="1",
                calibration=("0", "1", "0", "0"), threshold="0"):
    return {"segments": [{
        "start": start, "end": end, "raw_start": raw_start, "raw_end": raw_end,
        "calibration": list(calibration)
    }], "threshold": threshold}
