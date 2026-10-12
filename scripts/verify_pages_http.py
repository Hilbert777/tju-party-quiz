"""Verify a deployed static page's Gzip and conditional HTTP caching."""
import argparse
import gzip
import hashlib
import http.client
import json
import re
import zlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def fetch(url, headers, timeout):
    request = Request(url, headers={"User-Agent": "tju-party-quiz-http-check/4.3", **headers})
    try:
        try:
            response = urlopen(request, timeout=timeout)
        except HTTPError as error:
            response = error  # 304 and failed HTTP responses still need verification.
        with response:
            return {"status": response.code, "url": response.geturl(),
                    "headers": {key.lower(): value for key, value in response.headers.items()},
                    "body": response.read()}
    except (URLError, OSError, http.client.HTTPException) as error:
        return {"status": None, "url": url, "headers": {}, "body": b"", "error": str(error)}


def verify(url, expected_file=None, timeout=20):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use a public HTTP(S) page URL without credentials")
    report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "url": url,
              "requests": {}, "checks": [], "passed": False}

    def check(name, condition):
        report["checks"].append({"name": name, "passed": bool(condition)})

    def get(name, encoding, extra=None):
        response = fetch(url, {"Accept-Encoding": encoding, **(extra or {})}, timeout)
        report["requests"][name] = {key: value for key, value in response.items() if key != "body"}
        report["requests"][name]["body_bytes"] = len(response["body"])
        return response

    identity = get("identity", "identity")
    compressed = get("gzip", "gzip")
    check("identity returns HTTP 200 HTML", identity["status"] == 200 and
          identity["headers"].get("content-type", "").lower().startswith("text/html"))
    check("identity is uncompressed", identity["headers"].get("content-encoding", "identity") == "identity")
    check("gzip returns HTTP 200", compressed["status"] == 200)
    check("gzip is negotiated through Content-Encoding", compressed["headers"].get("content-encoding") == "gzip")
    check("encoding variants share the final URL", identity["url"] == compressed["url"])
    decoded = None
    try:
        decoded = gzip.decompress(compressed["body"])
    except (OSError, EOFError, zlib.error) as error:
        report["gzip_decode_error"] = str(error)
    check("gzip decodes to the exact uncompressed page", decoded is not None and decoded == identity["body"])
    check("gzip reduces transferred body bytes", 0 < len(compressed["body"]) < len(identity["body"]))
    report["identity_sha256"] = hashlib.sha256(identity["body"]).hexdigest()
    version = re.search(r'关于本网站 · <b>v([^<]+)</b>', identity["body"].decode("utf-8", errors="replace"))
    report["served_version"] = version[1] if version else None
    if decoded is not None:
        report["decoded_gzip_sha256"] = hashlib.sha256(decoded).hexdigest()
    if identity["body"]:
        report["gzip_saving_percent"] = round((1 - len(compressed["body"]) / len(identity["body"])) * 100, 2)

    for name, response in (("identity", identity), ("gzip", compressed)):
        headers = response["headers"]
        vary = {item.strip().lower() for item in headers.get("vary", "").split(",")}
        directives = [item.strip().lower() for item in headers.get("cache-control", "").split(",")]
        max_age = re.search(r'(?:^|,)\s*max-age\s*=\s*"?(\d+)', headers.get("cache-control", ""), re.I)
        check(name + " varies by Accept-Encoding", "accept-encoding" in vary)
        check(name + " allows finite HTTP caching", max_age is not None and int(max_age[1]) > 0 and "no-store" not in directives)
        check(name + " supplies ETag", bool(headers.get("etag")))
        check(name + " supplies Last-Modified", bool(headers.get("last-modified")))
        if headers.get("etag"):
            validated = get(name + "_etag", "gzip" if name == "gzip" else "identity",
                            {"If-None-Match": headers["etag"]})
            check(name + " ETag returns 304 without a response body", validated["status"] == 304 and not validated["body"])

    modified = compressed["headers"].get("last-modified")
    if modified:
        validated = get("gzip_last_modified", "gzip", {"If-Modified-Since": modified})
        check("Last-Modified returns 304 without a response body", validated["status"] == 304 and not validated["body"])
        outdated = get("outdated_etag", "gzip", {"If-None-Match": '"tju-quiz-outdated-validator"',
                                                 "If-Modified-Since": modified})
        check("an outdated ETag overrides If-Modified-Since and returns 200", outdated["status"] == 200)
        try:
            outdated_body = gzip.decompress(outdated["body"])
        except (OSError, EOFError, zlib.error):
            outdated_body = None
        check("an outdated ETag receives the current gzip page", outdated["headers"].get("content-encoding") == "gzip" and
              outdated_body is not None and outdated_body == identity["body"])

    if expected_file is not None:
        expected = Path(expected_file).read_bytes()
        report["expected_file"] = str(expected_file)
        report["expected_sha256"] = hashlib.sha256(expected).hexdigest()
        # Git checkout can change CRLF/LF; all other bytes must match the release.
        check("deployed content matches the expected release", identity["body"].replace(b"\r\n", b"\n") ==
              expected.replace(b"\r\n", b"\n"))
    report["passed"] = all(item["passed"] for item in report["checks"])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="The actual deployed HTTPS page URL")
    parser.add_argument("--expected-file", help="Also compare the deployment with this release HTML")
    parser.add_argument("--output", help="Save all headers and check results as JSON")
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args()
    report = verify(args.url, args.expected_file, args.timeout)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for item in report["checks"]:
        print(("PASS " if item["passed"] else "FAIL ") + item["name"])
    print(json.dumps({"passed": report["passed"], "checks": len(report["checks"]),
                      "served_version": report["served_version"],
                      "identity_bytes": report["requests"]["identity"]["body_bytes"],
                      "gzip_bytes": report["requests"]["gzip"]["body_bytes"],
                      "gzip_saving_percent": report.get("gzip_saving_percent")}, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
