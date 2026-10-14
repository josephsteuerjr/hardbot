"""Read-only full-route probe and failover decision for a single origin.

Does not mutate DNS or activate SQLite replicas. Deployment must provide fencing,
an independent standby and a DNS adapter before this plan can change production.
"""
import asyncio
import json
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from aiohttp import ClientSession, ClientTimeout


async def probe(url, *, timeout=3, allow_http=False):
    parsed = urlsplit(url)
    if parsed.scheme != "https" and not (allow_http and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}):
        raise ValueError("TLS is required for route probes")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Unsafe route URL")
    try:
        async with ClientSession(timeout=ClientTimeout(total=timeout), trust_env=False) as session:
            async with session.get(url.rstrip("/") + "/ready.json", allow_redirects=False) as response:
                return response.status == 200 and (await response.json()).get("ok") is True
    except (Exception, asyncio.TimeoutError):
        return False


@dataclass
class Controller:
    origin: str
    active: str
    threshold: int = 3
    failures: int = 0
    cooldown_observations: int = 0
    healthy: dict = field(default_factory=dict)

    def observe(self, results):
        if self.cooldown_observations:
            self.cooldown_observations -= 1
        for route, ok in results.items():
            self.healthy[route] = self.healthy.get(route, 0) + 1 if ok else 0
        self.failures = 0 if results.get(self.active) else self.failures + 1
        if self.failures < self.threshold or self.cooldown_observations:
            return None
        candidate = next((route for route, count in self.healthy.items() if route != self.active and count >= 2), None)
        if candidate:
            previous, self.active, self.failures = self.active, candidate, 0
            self.cooldown_observations = 6
            return {"origin": self.origin, "from": previous, "to": candidate,
                    "requires": "DNS adapter confirmation; same internal primary and fencing"}
        return None
