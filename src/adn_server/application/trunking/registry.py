# ADN DMR Peer Server - application trunking registry
#
# Copyright (C) 2026  Rodrigo Pérez, CE5RPY <ce5rpy@qmd.cl>
# Copyright (C) 2026  Joaquin Madrid Belando, EA5GVK <ea5gvk@gmail.com>
#
###############################################################################
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software Foundation,
#   Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301  USA
###############################################################################

"""What Tier III sites told the server: registered radios, subscribed talkgroups, call UUIDs and parrot keyers."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from typing import NamedTuple

MAX_REGISTRATIONS_PER_SITE = 1024
# dmrtc does not resend registrations after a reconnect; 24 h is when the SUB_MAP trim drops their pin too.
REGISTRATION_TTL_S = 86400.0
MAX_TGS_PER_SITE = 512
# Longer than the 180 s source timeout.
CALL_UUID_KEEP_S = 300.0
# The parrot replays an over of up to 180 s, starting 2 s after it ends.
ECHO_HOLD_S = 185.0
# A refresh is worth a write once an hour, as SubMapSaver does for SUB_MAP times.
_REVISION_BUCKET_S = 3600


class Site(NamedTuple):
    system: str
    peer_id: bytes


class Registration(NamedTuple):
    ms_id: int
    site: Site
    t: float


class SiteRegistry:
    def __init__(self) -> None:
        self._regs: dict[int, Registration] = {}
        self._by_site: dict[Site, dict[int, None]] = {}
        self._subs: dict[Site, set[int]] = {}
        self._holders: dict[tuple[str, int], dict[bytes, None]] = {}
        self._uuids: dict[tuple[bytes, bytes], tuple[bytes, float]] = {}
        self._echo: dict[tuple[str, int, int], tuple[bytes, float]] = {}
        self._revision = 0

    @property
    def revision(self) -> int:
        """Changes whenever the registrations worth persisting change."""
        return self._revision

    def register(self, ms_id: int, site: Site, now: float) -> tuple[bool, Site | None]:
        """Whether the site may hold the radio, and the other site it was registered at."""
        current = self._regs.get(ms_id)
        if current is not None and current.site == site:
            self._refresh(current, now)
            return True, None
        if len(self._by_site.get(site, ())) >= MAX_REGISTRATIONS_PER_SITE:
            return False, None
        if current is not None:
            self._unlink(current)
        self._link(Registration(ms_id, site, now))
        self._revision += 1
        return True, current.site if current is not None else None

    def deregister(self, ms_id: int, site: Site) -> bool:
        current = self._regs.get(ms_id)
        if current is None or current.site != site:
            return False
        self._unlink(current)
        self._revision += 1
        return True

    def touch(self, ms_id: int, site: Site, now: float) -> None:
        current = self._regs.get(ms_id)
        if current is not None and current.site == site:
            self._refresh(current, now)

    def at_site(self, site: Site) -> list[Registration]:
        return [self._regs[ms_id] for ms_id in self._by_site.get(site, ())]

    def expire(self, now: float) -> list[Registration]:
        expired = [r for r in self._regs.values() if now - r.t > REGISTRATION_TTL_S]
        for registration in expired:
            self._unlink(registration)
        if expired:
            self._revision += 1
        return expired

    def registrations(self) -> list[Registration]:
        return list(self._regs.values())

    def restore(self, registrations: Iterable[Registration], systems: frozenset[str], now: float) -> None:
        """Load saved registrations, keeping the newest that are live, on a trunk system and under the cap."""
        for registration in sorted(registrations, key=lambda r: r.t, reverse=True):
            if (
                now - registration.t > REGISTRATION_TTL_S
                or registration.site.system not in systems
                or registration.ms_id in self._regs
                or len(self._by_site.get(registration.site, ())) >= MAX_REGISTRATIONS_PER_SITE
            ):
                continue
            self._link(registration)

    def subscribe(self, site: Site, tg: int) -> bool:
        """True when ``site`` is the first of its system to hold ``tg``."""
        tgs = self._subs.setdefault(site, set())
        if tg in tgs:
            return False
        tgs.add(tg)
        holders = self._holders.setdefault((site.system, tg), {})
        holders[site.peer_id] = None
        return len(holders) == 1

    def unsubscribe(self, site: Site, tg: int) -> bool:
        """True when ``site`` was the last of its system to hold ``tg``."""
        tgs = self._subs.get(site)
        if not tgs or tg not in tgs:
            return False
        tgs.discard(tg)
        if not tgs:
            del self._subs[site]
        return self._release(site, tg)

    def is_subscribed(self, site: Site, tg: int) -> bool:
        return tg in self._subs.get(site, ())

    def tg_count(self, site: Site) -> int:
        return len(self._subs.get(site, ()))

    def subscribers(self, system: str, tg: int) -> Collection[bytes]:
        holders = self._holders.get((system, tg))
        return holders.keys() if holders is not None else ()

    def held(self) -> list[tuple[str, int]]:
        return list(self._holders)

    def drop_site(self, site: Site) -> list[int]:
        """Forget the site's talkgroups and return those no other site of its system holds; keep its radios."""
        return [tg for tg in sorted(self._subs.pop(site, ())) if self._release(site, tg)]

    def drop_system(self, system: str) -> tuple[list[int], list[Registration]]:
        released = [tg for site in [s for s in self._subs if s.system == system] for tg in self.drop_site(site)]
        dropped = [r for r in self._regs.values() if r.site.system == system]
        for registration in dropped:
            self._unlink(registration)
        if dropped:
            self._revision += 1
        return released, dropped

    # With the source radio, which is in one call at a time: two sites may pick one stream ID at once.
    def note_uuid(self, stream_id: bytes, rf_src: bytes, uuid: bytes, now: float) -> None:
        self._uuids[(stream_id, rf_src)] = (uuid, now)

    def uuid(self, stream_id: bytes, rf_src: bytes) -> bytes | None:
        noted = self._uuids.get((stream_id, rf_src))
        return noted[0] if noted is not None else None

    def note_echo(self, system: str, tg: int, rf_src: int, peer_id: bytes, now: float) -> None:
        self._echo[(system, tg, rf_src)] = (peer_id, now)

    def echo_site(self, system: str, tg: int, rf_src: int, now: float) -> bytes | None:
        """The site where ``rf_src`` keyed the parrot ``tg``, while its playback can still come."""
        noted = self._echo.get((system, tg, rf_src))
        return noted[0] if noted is not None and now - noted[1] <= ECHO_HOLD_S else None

    def trim(self, now: float) -> None:
        for key in [k for k, (_, t) in self._uuids.items() if now - t > CALL_UUID_KEEP_S]:
            del self._uuids[key]
        for key in [k for k, (_, t) in self._echo.items() if now - t > ECHO_HOLD_S]:
            del self._echo[key]

    def _link(self, registration: Registration) -> None:
        self._regs[registration.ms_id] = registration
        self._by_site.setdefault(registration.site, {})[registration.ms_id] = None

    def _unlink(self, registration: Registration) -> None:
        del self._regs[registration.ms_id]
        at_site = self._by_site[registration.site]
        del at_site[registration.ms_id]
        if not at_site:
            del self._by_site[registration.site]

    def _refresh(self, current: Registration, now: float) -> None:
        self._regs[current.ms_id] = current._replace(t=now)
        if int(current.t // _REVISION_BUCKET_S) != int(now // _REVISION_BUCKET_S):
            self._revision += 1

    def _release(self, site: Site, tg: int) -> bool:
        holders = self._holders[(site.system, tg)]
        del holders[site.peer_id]
        if holders:
            return False
        del self._holders[(site.system, tg)]
        return True
