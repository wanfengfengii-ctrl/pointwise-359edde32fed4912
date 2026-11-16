"""Public import API and recorded-request entry point."""
import json
import sys
from dataclasses import asdict
from pathlib import Path
from calibration import parse_request, calibrate
from exposure import summarize

def analyze(request):
    segments, threshold = parse_request(request)
    curves = [calibrate(segment) for segment in segments]
    result = summarize(curves, threshold)
    return {"curves": [asdict(curve) for curve in curves], **result}

def main():
    try:
        if len(sys.argv) != 2:
            raise ValueError("usage: python3 pipeline.py REQUEST.json | --sample")
        if sys.argv[1] == "--sample":
            from samples import normal
            request = normal()
        else:
            request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        output = analyze(request)
    except (ValueError, TypeError, KeyError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(output, ensure_ascii=False, allow_nan=False, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
