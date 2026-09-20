"""Provider adapters for the survey ``DetectorPort`` (Task 12).

Both ``MoondreamDetector`` (``moondream.py``) and ``GeminiDetector``
(``gemini.py``) convert their provider's raw response into the same
``Detection`` shape ``sentinel.survey.detector.normalise`` already produces
for a scripted or replayed frame, so nothing downstream of ``detect()`` can
tell which provider, if any, answered.

Nothing in this package ever makes a network call on its own. The only
production call site is ``scripts/provider_spike.py`` (Task 12 step 5),
which runs exactly once, only after the operator has explicitly granted
G-provider-spike for a stated frame count and cost. Every test in
``tests/survey/test_providers.py`` passes a fake, in-memory session and
never reaches a socket.

Adapter rules, unchanged from pipeline spec Sec 6.3 / this plan's Task 12:
pin the model version; bounded connect/read/total timeouts; cap image and
response size; validate status, content type and JSON shape; record the
provider-resolved model identity and fail loudly if it differs from the
pinned one; typed errors for timeout, rate limit, auth, transport and
schema failures; never log image bytes or credentials.
"""
from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any

from ..detector import DetectionBatch, NormaliseResult


class ProviderError(Exception):
    """Base for every typed adapter failure below. ``detect()`` catches
    this base class at its HTTP-call boundary and turns it into
    ``DetectionBatch(status="PROVIDER_ERROR")`` -- with one deliberate
    exception, ``ModelIdentityMismatch``, which is raised *after* that
    boundary and is never caught."""


class ProviderTimeout(ProviderError):
    """The connect, read or total timeout elapsed before a response came
    back."""


class ProviderRateLimited(ProviderError):
    """HTTP 429, or an equivalent provider-specific rate-limit signal."""


class ProviderAuthError(ProviderError):
    """HTTP 401 or 403: missing or rejected credentials."""


class ProviderTransportError(ProviderError):
    """A connection-level failure, or any other non-2xx HTTP status not
    covered by a more specific error above."""


class ProviderSchemaError(ProviderError):
    """The response was 2xx but was not JSON, was not shaped as the
    adapter expects, or was missing a field the adapter requires."""


class ProviderResponseTooLarge(ProviderError):
    """The response body exceeded the configured cap. Refused before it is
    parsed, so an oversized or hostile payload is never held in memory
    whole (adapter rule: cap response size)."""


class ModelIdentityMismatch(ProviderError):
    """The provider answered with a model identity other than the one this
    adapter pinned.

    Deliberately **not** part of the caught-into-``PROVIDER_ERROR`` set:
    silently trusting an unpinned model is exactly the failure this check
    exists to make loud (plan Task 12; spec Sec 6.3's "record the actual
    resolved model identity and fail loudly if it differs"). A caller that
    wants this survivable must catch it explicitly and decide what to do,
    rather than have it disappear into a routine empty-scene status.
    """


#: Bounds shared by both adapters. Generous enough for one detection call
#: on a single 960x720 still; not a substitute for provider-side limits.
CONNECT_TIMEOUT_S = 5.0
READ_TIMEOUT_S = 20.0
TOTAL_TIMEOUT_S = 25.0

#: A single Tello still, well above what a 960x720 JPEG needs -- generous
#: enough that this is a safety backstop, not a routine failure mode.
MAX_IMAGE_BYTES = 8 * 1024 * 1024

#: A detection list for one frame, not a novel. Checked against the raw
#: response body before it is parsed.
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def read_image_bytes(image_path: str | Path) -> bytes:
    """Reads the frame from disk, refusing anything over ``MAX_IMAGE_BYTES``
    before it is held in memory or sent anywhere (adapter rule: cap image
    size)."""
    path = Path(image_path)
    size = path.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise ProviderResponseTooLarge(
            f"image {path.name} is {size} bytes, over the {MAX_IMAGE_BYTES} cap")
    return path.read_bytes()


def post_json(session: Any, url: str, *, headers: dict[str, str], payload: dict,
              provider: str) -> dict:
    """Sends one request through ``session`` (a fake in every test; a real
    HTTP client at the one gated production call site) and returns its
    parsed JSON body, translating every failure into one of the typed
    errors above.

    Never logs ``headers`` or ``payload`` in any exception message --
    either may carry an image or a credential. Only the provider name and,
    where relevant, the HTTP status appear.
    """
    try:
        response = session.post(url, headers=headers, json=payload,
                                timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S, TOTAL_TIMEOUT_S))
    except TimeoutError as exc:
        raise ProviderTimeout(f"{provider}: request timed out") from exc
    except OSError as exc:
        raise ProviderTransportError(
            f"{provider}: transport failure ({type(exc).__name__})") from exc

    content = getattr(response, "content", b"")
    if len(content) > MAX_RESPONSE_BYTES:
        raise ProviderResponseTooLarge(
            f"{provider}: response is {len(content)} bytes, over the {MAX_RESPONSE_BYTES} cap")

    status = response.status_code
    if status in (401, 403):
        raise ProviderAuthError(f"{provider}: authentication failed (HTTP {status})")
    if status == 429:
        raise ProviderRateLimited(f"{provider}: rate-limited (HTTP 429)")
    if status >= 400:
        raise ProviderTransportError(f"{provider}: HTTP {status}")

    content_type = response.headers.get("content-type", "")
    if "json" not in content_type.lower():
        raise ProviderSchemaError(f"{provider}: content-type {content_type!r} is not JSON")

    try:
        body = response.json()
    except (ValueError, _json.JSONDecodeError) as exc:
        raise ProviderSchemaError(f"{provider}: response body was not valid JSON") from exc

    if not isinstance(body, dict):
        raise ProviderSchemaError(f"{provider}: response body was not a JSON object")
    return body


def to_detection_batch(result: NormaliseResult, raw: dict | None) -> DetectionBatch:
    """Assembles a ``DetectionBatch`` from a ``normalise()`` result exactly
    the way ``sentinel.survey.detector``'s scripted and replay doubles do
    (rev 3 R6's ``CLASS_MAP_MISS`` rule: a frame whose dropped labels
    outnumber its mapped ones is a loud miss, not a quiet empty scene) --
    reimplemented here rather than importing that module's private helper,
    so this package's only dependency on ``detector.py`` is its public
    surface (``DetectionBatch``, ``NormaliseResult``, ``normalise``).
    """
    detections = tuple(detection for detection, _band in result.pairs)
    bands = tuple(band for _detection, band in result.pairs)
    status = "CLASS_MAP_MISS" if len(result.dropped) > len(result.pairs) / 2 else "OK"
    return DetectionBatch(detections, status, bands, result.dropped, raw)
