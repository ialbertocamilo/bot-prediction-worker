"""Quick smoke-test for the API endpoints."""
import json
import sys
import time
import urllib.request
import urllib.error


BASE = "http://localhost:8000"


def _get(path: str, timeout: int = 120):
    url = f"{BASE}{path}"
    start = time.time()
    try:
        resp = urllib.request.urlopen(url, timeout=timeout)
        elapsed = time.time() - start
        body = resp.read().decode()
        data = json.loads(body) if body else {}
        return resp.status, elapsed, data, None
    except urllib.error.HTTPError as e:
        elapsed = time.time() - start
        body = e.read().decode()
        return e.code, elapsed, body, None
    except Exception as ex:
        elapsed = time.time() - start
        return None, elapsed, None, str(ex)


def main():
    tests = [
        ("Health", "/health/"),
        ("Predict match 609 (Premier League)", "/predict/609"),
        ("Predict match inexistente", "/predict/999999"),
        ("Predict match 1 (Peru pocos datos)", "/predict/1"),
        ("Upcoming 7 dias", "/predict/upcoming?days=7"),
    ]

    ok = 0
    fail = 0
    for name, path in tests:
        print(f"\n{'='*60}")
        print(f"TEST: {name}")
        print(f"  URL: {BASE}{path}")
        status, elapsed, data, err = _get(path)

        if err:
            print(f"  EXCEPTION: {err} ({elapsed:.1f}s)")
            fail += 1
            continue

        print(f"  Status: {status} ({elapsed:.1f}s)")

        if isinstance(data, dict):
            # Print a summary, not the full response
            keys = list(data.keys())[:10]
            print(f"  Keys: {keys}")
            if "detail" in data:
                print(f"  Detail: {data['detail']}")
            if "match_id" in data:
                print(f"  match_id: {data['match_id']}")
            if "p_home" in data:
                print(f"  p_home={data['p_home']}, p_draw={data['p_draw']}, p_away={data['p_away']}")
            if "count" in data:
                print(f"  count: {data['count']}")
        elif isinstance(data, str):
            print(f"  Body: {data[:200]}")

        if status == 500:
            print("  >>> FAIL: Internal Server Error <<<")
            fail += 1
        elif status in (200, 404, 422):
            print("  >>> OK <<<")
            ok += 1
        else:
            print(f"  >>> UNEXPECTED STATUS {status} <<<")
            fail += 1

    print(f"\n{'='*60}")
    print(f"RESULTS: {ok} OK, {fail} FAIL out of {len(tests)} tests")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
