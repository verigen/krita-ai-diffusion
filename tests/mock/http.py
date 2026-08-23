"""Mock implementation of RequestManager for use in tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class RecordedCall:
    method: str
    url: str
    data: dict | None
    timeout: float | None
    bearer: str | None


class MockRequests:
    """Scriptable stand-in for RequestManager. Only get/http/post are used by LlmClient.

    Usage::

        requests = MockRequests()
        requests.responses.append({"message": {"content": "hi"}})
        # or raise an error:
        requests.responses.append(NetworkError(1, "boom", "http://x"))
    """

    def __init__(self):
        self.calls: list[RecordedCall] = []
        self.responses: list[Any] = []

    async def http(
        self,
        method: str,
        url: str,
        data: dict | None = None,
        timeout: float | None = None,
        bearer: str | None = None,
    ):
        self.calls.append(RecordedCall(method, url, data, timeout, bearer))
        if not self.responses:
            raise AssertionError("MockRequests: no more responses queued")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def get(self, url: str, timeout: float | None = None, bearer: str | None = None):
        return await self.http("GET", url, timeout=timeout, bearer=bearer)

    async def post(self, url: str, data: dict, bearer: str | None = None):
        return await self.http("POST", url, data, bearer=bearer)

    @property
    def last_call(self) -> RecordedCall:
        assert self.calls
        return self.calls[-1]
