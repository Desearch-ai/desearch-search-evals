from utils import load_env

__all__ = ["load_env"]


TIMING_HEADERS = ("server-timing", "x-response-time", "x-request-duration", "x-envoy-upstream-service-time", "x-process-time")


def timing_headers(headers):
    return {k.lower(): v for k, v in headers.items() if k.lower() in TIMING_HEADERS}
