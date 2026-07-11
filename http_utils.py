"""
Shared HTTP helper. Polymarket and Kalshi both rate-limit via Cloudflare,
which throttles (slows/queues) before it ever returns a 429. This means
naive "retry once and give up" logic will silently drop data. We do
exponential backoff with a capped number of retries and always surface
failures instead of swallowing them.
"""

import time
import requests

from config import REQUEST_TIMEOUT_SECONDS, MAX_RETRIES, BACKOFF_BASE_SECONDS


class ApiError(Exception):
    pass


def get_json(url: str, params: dict = None, headers: dict = None):
    """
    GET a URL and return parsed JSON, with retry + exponential backoff.
    Raises ApiError if all retries are exhausted.
    """
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(
                url, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                wait = BACKOFF_BASE_SECONDS ** (attempt + 1)
                time.sleep(wait)
                continue
            if resp.status_code == 422:
                # e.g. bad pagination params - not worth retrying
                raise ApiError(f"422 from {url}: {resp.text[:300]}")
            # Other errors (5xx etc.) - backoff and retry
            last_exc = ApiError(f"{resp.status_code} from {url}: {resp.text[:300]}")
            time.sleep(BACKOFF_BASE_SECONDS ** (attempt + 1))
        except requests.RequestException as e:
            last_exc = ApiError(f"Request failed for {url}: {e}")
            time.sleep(BACKOFF_BASE_SECONDS ** (attempt + 1))

    raise last_exc or ApiError(f"Failed to fetch {url} after {MAX_RETRIES} retries")
