# ADN DMR Peer Server - tests plugin send guards
#
# Copyright (C) 2026  Rodrigo Pérez, CE5RPY <ce5rpy@qmd.cl>
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

"""ServerContext.send_dmrd: opt-in, source allowlist, unit data only, rate limit."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.harness.deterministic import PacketSpec

from adn_server.application.plugins.application.bus import PluginBus
from adn_server.application.plugins.application.context import ServerContext
from adn_server.application.plugins.application.manager import PluginManager
from adn_server.application.plugins.application.sender import PluginDmrdSender
from adn_server.domain import HBPF_DATA_SYNC, HBPF_VOICE

GATEWAY_ID = 900999


def _config(**send) -> dict:
    return {"PLUGINS": {"send": {"d-aprs": {"allowed_src_ids": [GATEWAY_ID], "max_frames_per_s": 5, **send}}}}


def _frame(src: int = GATEWAY_ID, call_type: str = "unit", frame_type: int = HBPF_DATA_SYNC, dtype: int = 6) -> bytes:
    return PacketSpec(rf_src=src, dst_id=7140023, call_type=call_type, frame_type=frame_type, dtype_vseq=dtype).data()


class _Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def _sender(config: dict, clock=None):
    delivered: list = []
    sender = PluginDmrdSender(
        "d-aprs", config, lambda pkt, name: delivered.append((pkt, name)),
        call_from_reactor=lambda fn, *a: fn(*a), clock=clock or _Clock(),
    )
    return sender, delivered


def test_an_allowed_frame_is_delivered_on_the_reactor_with_the_plugin_name() -> None:
    sender, delivered = _sender(_config())
    assert sender(_frame()) is True
    assert delivered == [(_frame(), "d-aprs")]


def test_nothing_is_sent_without_a_plugins_send_entry() -> None:
    sender, delivered = _sender({"PLUGINS": {}})
    assert sender(_frame()) is False and delivered == []


def test_an_entry_without_source_ids_grants_nothing() -> None:
    sender, delivered = _sender(_config(allowed_src_ids=[]))
    assert sender(_frame()) is False and delivered == []


def test_a_plugin_cannot_send_as_a_radio() -> None:
    sender, delivered = _sender(_config())
    assert sender(_frame(src=7140023)) is False and delivered == []


def test_private_voice_and_non_dmrd_are_refused() -> None:
    sender, delivered = _sender(_config(group_voice_tgs=[213]))
    assert sender(_frame(frame_type=HBPF_VOICE, dtype=1)) is False  # unit call type, voice frame
    assert sender(b"not a dmrd frame") is False
    assert delivered == []


def test_group_voice_only_on_the_granted_talkgroups() -> None:
    sender, delivered = _sender(_config(group_voice_tgs=[213]))
    voice = PacketSpec(rf_src=GATEWAY_ID, dst_id=213, call_type="group", frame_type=HBPF_VOICE, dtype_vseq=1).data()
    other = PacketSpec(rf_src=GATEWAY_ID, dst_id=214, call_type="group", frame_type=HBPF_VOICE, dtype_vseq=1).data()
    assert sender(voice) is True
    assert sender(other) is False
    assert [pkt for pkt, _ in delivered] == [voice]


def test_group_voice_needs_a_grant_even_with_a_source_id() -> None:
    sender, delivered = _sender(_config())
    voice = PacketSpec(rf_src=GATEWAY_ID, dst_id=213, call_type="group", frame_type=HBPF_VOICE, dtype_vseq=1).data()
    assert sender(voice) is False and delivered == []


def test_on_the_reactor_thread_the_result_is_the_routing_result() -> None:
    results = iter([True, False])
    queued: list = []
    sender = PluginDmrdSender(
        "d-aprs", _config(), lambda pkt, name: next(results),
        call_from_reactor=lambda *a: queued.append(a), in_reactor_thread=lambda: True,
    )
    assert sender(_frame()) is True
    assert sender(_frame()) is False  # e.g. the slot was taken: the plugin should stop
    assert queued == []


def test_rate_limit_per_plugin() -> None:
    clock = _Clock()
    sender, delivered = _sender(_config(max_frames_per_s=5), clock)
    assert [sender(_frame()) for _ in range(7)] == [True] * 5 + [False] * 2  # starts full
    clock.t += 0.2  # one more token
    assert sender(_frame()) is True
    assert len(delivered) == 6 and sender.dropped == 2


def test_revoking_the_permission_takes_effect_on_the_next_frame() -> None:
    config = _config()
    sender, delivered = _sender(config)
    assert sender(_frame()) is True
    del config["PLUGINS"]["send"]["d-aprs"]  # what a SIGHUP reload leaves behind
    assert sender(_frame()) is False
    assert len(delivered) == 1


def test_master_kill_stops_sending() -> None:
    config = _config()
    sender, delivered = _sender(config)
    config["PLUGINS"]["master_kill"] = True
    assert sender(_frame()) is False and delivered == []


def _plugin_dir(root: Path, name: str) -> None:
    pkg = root / name / "plugin"
    pkg.mkdir(parents=True)
    (root / name / "config.yaml").write_text("enabled: true\n")
    (pkg / "__init__.py").write_text(
        "class _P:\n"
        f"    name = {name!r}\n"
        "    ctx = None\n"
        "    def on_load(self, bus, config, ctx):\n"
        "        type(self).ctx = ctx\n"
        "    def on_event(self, event): pass\n"
        "    def on_reload(self, config): pass\n"
        "    def on_shutdown(self): pass\n"
        "def create_plugin():\n"
        "    return _P()\n"
    )


def test_only_the_granted_plugin_gets_send_dmrd(tmp_path) -> None:
    _plugin_dir(tmp_path / "plugins", "d-aprs")
    _plugin_dir(tmp_path / "plugins", "logger")
    ctx = ServerContext(config={}, project_root=str(tmp_path), defer_to_thread=None, call_from_reactor=None, call_later=None)
    made: list[str] = []
    manager = PluginManager(PluginBus(), ctx, tmp_path, sender_factory=lambda name: made.append(name) or (lambda pkt: True))
    loaded = {p.name: p for p in manager.discover_and_load(_config())}
    assert type(loaded["d-aprs"]).ctx.send_dmrd is not None
    assert type(loaded["logger"]).ctx.send_dmrd is None
    assert made == ["d-aprs"]


# --- through the real SIGHUP reload path (review of #104) ---

from adn_server.application.runtime_context import (  # noqa: E402
    ConfigProxy,
    RuntimeContext,
    RuntimeContextHolder,
    prepare_reload_config,
    swap_runtime_config,
)
from adn_server.infrastructure.config_reload import merge_top_level_config  # noqa: E402


def _reload(holder: RuntimeContextHolder, incoming: dict) -> None:
    """What a SIGHUP does with a freshly parsed adn-server.yaml (``incoming``)."""
    new_config = prepare_reload_config(holder)
    merge_top_level_config(new_config, incoming)
    swap_runtime_config(holder, new_config)


@pytest.mark.parametrize(
    "incoming",
    [
        {"GLOBAL": {}, "PLUGINS": {"send": {}}},  # entry removed
        {"GLOBAL": {}, "PLUGINS": {"master_kill": True, **_config()["PLUGINS"]}},  # master_kill
        {"GLOBAL": {}},  # the whole PLUGINS section removed
    ],
    ids=["entry-removed", "master-kill", "section-removed"],
)
def test_a_sighup_reload_revokes_sending(incoming) -> None:
    holder = RuntimeContextHolder(RuntimeContext(config={"GLOBAL": {}, **_config()}))
    sender, delivered = _sender(ConfigProxy(holder))
    assert sender(_frame()) is True
    _reload(holder, incoming)
    assert sender(_frame()) is False
    assert len(delivered) == 1


def test_a_sighup_reload_can_grant_a_new_talkgroup() -> None:
    holder = RuntimeContextHolder(RuntimeContext(config={"GLOBAL": {}, **_config()}))
    sender, _ = _sender(ConfigProxy(holder))
    voice = PacketSpec(rf_src=GATEWAY_ID, dst_id=213, call_type="group", frame_type=HBPF_VOICE, dtype_vseq=1).data()
    assert sender(voice) is False
    _reload(holder, {"GLOBAL": {}, **_config(group_voice_tgs=[213])})
    assert sender(voice) is True


def test_plugins_section_is_read_from_adn_server_yaml_and_followed_on_reload(tmp_path) -> None:
    """The whole chain with the real loader: PLUGINS used to be dropped by YamlConfigLoader,
    so neither master_kill, overrides nor send permissions ever reached the server."""
    import logging
    from pathlib import Path

    from adn_server.infrastructure.config_loader import YamlConfigLoader
    from adn_server.infrastructure.config_reload import prepare_incoming_config

    example = Path(__file__).resolve().parents[2] / "adn-server.example.yaml"
    base = example.read_text(encoding="utf-8")
    path = tmp_path / "adn-server.yaml"
    grant = "\nPLUGINS:\n  send:\n    d-aprs:\n      allowed_src_ids: [900999]\n"
    path.write_text(base + grant, encoding="utf-8")
    log = logging.getLogger("test")

    boot = prepare_incoming_config(YamlConfigLoader(), str(path), log)
    assert boot["PLUGINS"]["send"]["d-aprs"]["allowed_src_ids"] == [900999]
    holder = RuntimeContextHolder(RuntimeContext(config=boot))
    sender, _ = _sender(ConfigProxy(holder))
    assert sender(_frame()) is True

    path.write_text(base + "\nPLUGINS:\n  master_kill: true\n" + grant[len("\nPLUGINS:\n"):], encoding="utf-8")
    _reload(holder, prepare_incoming_config(YamlConfigLoader(), str(path), log))
    assert sender(_frame()) is False


@pytest.mark.parametrize("rate", [float("inf"), float("nan"), 0, -5])
def test_a_rate_that_is_not_a_positive_number_grants_nothing(rate) -> None:
    from adn_server.application.plugins.domain.send import send_permission

    assert send_permission(_config(max_frames_per_s=rate), "d-aprs") is None


def test_voice_slot_query_only_for_granted_talkgroups() -> None:
    sender = PluginDmrdSender(
        "d-aprs", _config(group_voice_tgs=[213]), lambda *a: True, call_from_reactor=lambda *a: None,
        slot_for_tg=lambda tg: 2,
    )
    assert sender.voice_slot_for_tg(213) == 2
    assert sender.voice_slot_for_tg(214) is None
