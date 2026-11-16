"""Frozen ordinary regressions. This is not the requested QA suite."""
import copy
import json
import math
from pipeline import analyze
from samples import normal, one_segment

def main():
    request = normal()
    saved = copy.deepcopy(request)
    result = analyze(request)
    assert request == saved
    assert result["curves"][0] == {
        "start": "10", "end": "12", "coefficients": (0.0, 1.0, 0.0, 0.0)}
    assert result["curves"][1] == {
        "start": "12", "end": "14", "coefficients": (2.0, -1.0, 0.0, -0.0)}
    assert result["per_segment"] == [1.0, 1.0]
    assert result["total"] == 2.0
    constant = analyze(one_segment(end="4", calibration=("3", "0", "0", "0"),
                                   threshold="2"))
    assert constant["total"] == 4.0
    cubic = analyze(one_segment(end="2", raw_end="2",
                               calibration=("0", "0", "0", "1"), threshold="1"))
    assert math.isclose(cubic["total"], 1.0, abs_tol=1e-12)
    malformed = normal()
    malformed["segments"][1]["start"] = "12.01"
    try:
        analyze(malformed)
    except ValueError:
        pass
    else:
        raise AssertionError("gap was accepted")
    print(json.dumps({"normal_total": result["total"],
                      "per_segment": result["per_segment"],
                      "constant_total": constant["total"],
                      "cubic_total": cubic["total"],
                      "rejected_gap": True}, sort_keys=True))

if __name__ == "__main__":
    main()
