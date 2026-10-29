# ADN DMR Peer Server - tests benchmarks bench dmrd
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

"""µs and function calls per voice frame through RoutingUseCases.dmrd_received.

Group voice from a hotspot on MASTER-A (TS2, TG 213) to MASTER-B and 6 OpenBridges, in calls of
50 frames 60 ms apart, on the deterministic harness with a no-op send and the stream trimmer run
between calls; only dmrd_received is timed. --masters is the number of HBP systems besides the
source (MASTER-B plus idle ones), which the HBP loop control walks on every frame. TRUNK-1 is a
plain MASTER off the TG in every run; --trunk makes it a Tier III system with one site logged in,
one radio registered and TG 214 subscribed, so the TG 213 fan-out stays the same. --mix private
sends a unit call to a SUB_MAP radio on MASTER-B instead. --profile also counts, with cProfile,
the function calls inside dmrd_received per frame: unlike the time, that count has no noise and
compares across machines. Each run checks before and after the timed part that every frame
reached every target, so it cannot measure a shorter path than it claims.

Not collected by pytest. Run it from any directory against this checkout or another one (an
upstream branch, an older commit), which it imports from without writing into it:

    python -B tests/benchmarks/bench_dmrd.py [--repo CHECKOUT] [--masters 1|10|30] [--trunk]
        [--mix group|private] [--profile]

Run the variants interleaved (each round runs every --masters value on every checkout) and
report the best per cell. --trunk needs a checkout with Tier III.
"""

from __future__ import annotations

import argparse
import collections
import cProfile
import functools
import gc
import platform
import pstats
import statistics
import sys
from pathlib import Path
from time import perf_counter_ns

TG = 213
OBPS = tuple(f"OBP-{i}" for i in range(6))
DST_RADIO = 7123456
FRAMES = 50

SITE_TG = 214
SITE_MS = 3141002
SITE_PEER = (3141001).to_bytes(4, "big")
SITE_ADDR = ("192.0.2.10", 62031)


def _enable_trunking(config: dict, sc) -> None:
    """TRUNK-1 becomes a Tier III system wired as bootstrap wires it; sets sc.trunk_verify."""
    import tempfile
    import types

    from twisted.internet.defer import maybeDeferred

    import tests.harness.deterministic as h
    from adn_server.application.subscription.subscription_table_ops import upsert_static_leg
    from adn_server.application.trunking.config import trunk_systems
    from adn_server.domain.subscription import AudioChannel, SubscriptionId, SystemId, TgId
    from adn_server.infrastructure.bootstrap.trunking import build_trunk_sites
    from adn_server.infrastructure.config_normalizer import ensure_system_runtime_config
    from adn_server.infrastructure.persistence.trunk_registration_store import JsonTrunkRegistrationStore
    from adn_server.infrastructure.twisted_adapters.udp_hbp_trunk import TrunkSiteProtocol
    from tests.support.dmrtc_wire import (
        GatewayLink, dmrt_registration, dmrt_subscription, read_registration_confirmation,
        read_subscription_confirmation,
    )
    from tests.support.hbp_repeat_stack import RecordingTransport

    cfg = config["SYSTEMS"]["TRUNK-1"]
    cfg.update(TRUNKING=True, SINGLE_MODE=False, PASSPHRASE=b"bench", MAX_PEERS=4, DEFAULT_UA_TIMER=10)
    ensure_system_runtime_config({"SYSTEMS": {"TRUNK-1": cfg}})  # the other systems stay as in "off" runs
    config["GLOBAL"]["REG_ACL"] = h.acl_permit_all(h.PEER_MAX)  # read at login; process_acls sets it in the server
    reporting = types.SimpleNamespace(send_routing_table=lambda *_a, **_k: None)
    tmp = tempfile.TemporaryDirectory(prefix="bench-trunk-")  # gone with this call: a run never writes the file
    path = str(Path(tmp.name) / "trunk_registrations.json")
    store = sc.subscription_store

    def to_site(data: bytes) -> list[bytes]:
        n = len(proto.transport.sent)
        proto.datagramReceived(data, SITE_ADDR)
        return [pkt for pkt, addr in proto.transport.sent[n:] if addr == SITE_ADDR]

    with h.patch_routing_wall_time(sc.clock):  # slot rows and the registry use the harness clock
        sites = build_trunk_sites(
            config, sc.protocols, store, sc.acl_router, sc.routing, reporting, path,
            JsonTrunkRegistrationStore(maybeDeferred),
        )
        sites.system_added("TRUNK-1", sc.clock.time())
        proto = TrunkSiteProtocol(
            "TRUNK-1", config, None, sites=sites, router=sc.acl_router,
            dmrd_received=sc.routing.dmrd_received,
            on_talker_alias_stream_end=sc.routing.clear_talker_alias_stream,
            routing_table_for_report=sc.routing.routing_table_for_report,
            get_subscription_store=lambda: store,
        )
        proto.transport = RecordingTransport()
        sc.protocols["TRUNK-1"] = proto
        sc.routing.set_concurrent_systems(trunk_systems(config["SYSTEMS"]))

        gateway = GatewayLink(SITE_PEER, b"bench")
        out = gateway.login()
        while out is not None:
            replies, out = to_site(out), None
            for reply in replies:
                out = gateway.received(reply) or out
        assert gateway.status == "RUNNING" and gateway.master_login, gateway.status
        reply = next(r for r in to_site(dmrt_registration(0x01, [SITE_MS], int(sc.clock.time()))) if r[4] == 0x41)
        assert read_registration_confirmation(reply)[1] == [(SITE_MS, True)], reply.hex()
        reply = next(r for r in to_site(dmrt_subscription(0x03, [SITE_TG])) if r[4] == 0x43)
        assert read_subscription_confirmation(reply) == (0x43, [SITE_TG]), reply.hex()
    assert sc.routing._concurrent_systems == frozenset({"TRUNK-1"})
    assert any(str(leg.system) == "TRUNK-1" for leg in store.legs_in_table(str(SITE_TG)))
    assert not any(str(leg.system) == "TRUNK-1" for leg in store.legs_in_table(str(TG)))

    hotspot_leg = SubscriptionId(AudioChannel(TgId(SITE_TG), 1), SystemId("MASTER-A"))

    def verify(stream: int) -> None:
        """A hotspot call on TG 214 reaches the site, through bootstrap's send, as 71-byte frames of one call UUID."""
        assert proto.site_connected(SITE_PEER)
        idle = store.get(hotspot_leg)  # created INBAND idle by the site's subscription
        others = collections.Counter()

        def send(system: str, packet: bytes, **kwargs) -> None:
            target = sc.protocols.get(system)
            if isinstance(target, TrunkSiteProtocol):
                target.send_system(packet, **kwargs)
            else:
                others[system] += 1

        with h.patch_routing_wall_time(sc.clock):
            upsert_static_leg(
                store, system="MASTER-A", tg=SITE_TG, slot=1, active=True,
                timeout_sec=600.0, timer_at=sc.clock.time() + 600.0,
            )
            saved, sc.routing._send_to_system = sc.routing._send_to_system, send
            n = len(proto.transport.sent)
            _run(sc, _calls(h, 1, FRAMES, "group", stream, dst=SITE_TG, slot=1))
            sc.routing._send_to_system = saved
            store.upsert(idle)
        got = [pkt for pkt, addr in proto.transport.sent[n:] if addr == SITE_ADDR]
        # OpenBridge legs are active in every table, so the OBPs carry TG 214 too; no HBP system does.
        assert set(others) <= set(OBPS) and len(proto.transport.sent) - n == len(got), (dict(others), len(got))
        assert len(got) == FRAMES and {len(p) for p in got} == {71}, [len(p) for p in got]
        assert all(p[:4] == b"DMRD" and p[8:11] == SITE_TG.to_bytes(3, "big") for p in got)
        assert len({p[55:] for p in got}) == 1

    verify(0x7D000000)
    sc.trunk_verify = verify


def _assert_off(sc) -> None:
    # getattr: a checkout without Tier III has no _concurrent_systems.
    assert getattr(sc.routing, "_concurrent_systems", frozenset()) == frozenset()
    assert not any(type(p).__name__ == "TrunkSiteProtocol" for p in sc.protocols.values())
    assert not sc.config["SYSTEMS"]["TRUNK-1"].get("TRUNKING")


def _scenario(h, masters: int, trunk: bool, mix: str):
    extra = tuple(f"MASTER-X{i}" for i in range(masters - 1))
    config = h.minimal_config(("MASTER-A", "MASTER-B", "TRUNK-1") + extra)
    for i, name in enumerate(OBPS):
        h.add_openbridge_system(config, name)
        config["SYSTEMS"][name]["NETWORK_ID"] = (100 + i).to_bytes(4, "big")
    if mix == "private":
        config["_SUB_MAP"] = {DST_RADIO.to_bytes(3, "big"): ("MASTER-B", 2, 1e12)}
    table = h.active_routing_table(
        TG, (("MASTER-A", 2), ("MASTER-B", 2)) + tuple((n, 1) for n in OBPS), timeout_minutes=10**6,
    )
    sc = h.DeterministicScenario(config=config, routing_table=table)
    sc.routing.apply_startup_subscriptions()
    for i, name in enumerate(extra):  # an idle MASTER still holds an old, unrelated stream on each slot
        for slot in (1, 2):
            sc.seed_hbp_slot_stream(name, slot, 0x0DEAD000 + 2 * i + slot, tgid=9000 + i, rf_src=3100000 + i)
    if trunk:
        _enable_trunking(sc.config, sc)
    else:
        _assert_off(sc)
    return sc


def _calls(h, n: int, frames: int, mix: str, first_stream: int, dst: int = TG, slot: int = 2) -> list[list[dict]]:
    unit = mix == "private"
    out = []
    for c in range(n):
        base = h.PacketSpec(
            dst_id=DST_RADIO if unit else dst, stream_id=first_stream + c, slot=slot,
            call_type="unit" if unit else "group",
        )
        spec = h.DeterministicScenario
        head = spec.unit_voice_head_spec(base) if unit else spec.voice_head_spec(base)
        bursts = [spec.voice_burst_spec(base, seq=s, dtype_vseq=(s - 1) % 6) for s in range(1, frames - 1)]
        term = spec.voice_term_spec(base, seq=frames - 1)
        out.append([s.decoded_hbp_args() for s in [head, *bursts, term]])
    return out


def _run(sc, calls: list[list[dict]], route=None) -> int:
    route, clock, total = route or sc.routing.dmrd_received, sc.clock, 0
    for frames in calls:
        for args in frames:
            now = clock.advance(0.06)
            t0 = perf_counter_ns()
            ok = route("MASTER-A", ingress_pkt_time=now, **args)
            total += perf_counter_ns() - t0
            if ok is not False:
                sc._sync_hbp_slot("MASTER-A", args)
        clock.advance(10.0)
        sc.routing.stream_trimmer_loop()
    return total


def _calls_per_frame(sc, h, frames: int, mix: str, n: int = 20) -> float:
    prof = cProfile.Profile()
    _run(sc, _calls(h, n, frames, mix, 0x7C000000), functools.partial(prof.runcall, sc.routing.dmrd_received))
    return sum(
        nc for (_file, _line, name), (_cc, nc, *_) in pstats.Stats(prof).stats.items()
        if name != "<method 'disable' of '_lsprof.Profiler' objects>"  # runcall's own exit, once per frame
    ) / (n * frames)


def _check(sc, h, frames: int, mix: str, stream: int) -> None:
    sent = collections.Counter()
    sc.routing._send_to_system = lambda target, *_a, **_k: sent.__setitem__(target, sent[target] + 1)
    _run(sc, _calls(h, 1, frames, mix, stream))
    want = {"MASTER-B": frames} if mix == "private" else {t: frames for t in ("MASTER-B", *OBPS)}
    if dict(sent) != want:
        raise SystemExit(f"wrong path: sent {dict(sent)}, expected {want}")
    sc.routing._send_to_system = lambda *_a, **_k: None


def main() -> None:
    ap = argparse.ArgumentParser(description="µs and function calls per voice frame through dmrd_received")
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2], help="checkout to measure")
    ap.add_argument("--masters", type=int, default=1, help="HBP systems besides the source")
    ap.add_argument("--trunk", action="store_true", help="TRUNK-1 is a Tier III system with one site")
    ap.add_argument("--mix", choices=("group", "private"), default="group")
    ap.add_argument("--calls", type=int, default=200, help="calls per repetition")
    ap.add_argument("--frames", type=int, default=FRAMES, help="frames per call")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--profile", action="store_true", help="also count function calls per frame with cProfile")
    a = ap.parse_args()
    sys.path[:0] = [str(a.repo / "src"), str(a.repo)]

    import adn_server
    import tests.harness.deterministic as h

    sc = _scenario(h, a.masters, a.trunk, a.mix)
    with h.patch_routing_wall_time(sc.clock):  # one clock for routing, timers and the trimmer
        _check(sc, h, a.frames, a.mix, 0x7F000000)
        _run(sc, _calls(h, 20, a.frames, a.mix, 0x7E000000))  # warm caches
        per_frame = []
        for rep in range(a.reps):
            calls = _calls(h, a.calls, a.frames, a.mix, 0x10000000 + rep * 0x100000)
            gc.collect()
            per_frame.append(_run(sc, calls) / (a.calls * a.frames) / 1000)
        counted = _calls_per_frame(sc, h, a.frames, a.mix) if a.profile else None
        _check(sc, h, a.frames, a.mix, 0x7F100000)
        if a.trunk:
            sc.trunk_verify(0x7D100000)  # the site is still logged in and still reached on its TG
        else:
            _assert_off(sc)
    print(
        f"{a.mix} masters={a.masters} trunk={'on' if a.trunk else 'off'}: "
        f"best {min(per_frame):.1f} µs/frame, median {statistics.median(per_frame):.1f}"
        + (f", {counted:.2f} calls/frame" if counted is not None else "")
        + f" ({a.reps} x {a.calls} calls x {a.frames} frames; Python {platform.python_version()} "
        f"{platform.machine()}; {Path(adn_server.__file__).parents[2]})"
    )


if __name__ == "__main__":
    main()
