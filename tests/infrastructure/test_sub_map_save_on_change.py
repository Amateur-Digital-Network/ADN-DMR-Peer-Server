"""SUB_MAP is written when a route changes, not on every tick of the save loop."""

from __future__ import annotations

import pickle

from adn_server.infrastructure.persistence import PickleSubMapStore, SubMapSaver


class _CountingStore(PickleSubMapStore):
    def __init__(self):
        self.writes = 0

    def save(self, path, sub_map):
        self.writes += 1
        super().save(path, sub_map)


def _saver(tmp_path, sub_map):
    store = _CountingStore()
    return store, SubMapSaver(store, str(tmp_path / "sub_map.pkl"), sub_map)


def test_loaded_map_is_not_rewritten(tmp_path):
    sub_map = {b"\x00\x00\x01": ("MASTER-1", 2, 1000.0, b"\x00\x00\x00\x01")}
    store, saver = _saver(tmp_path, sub_map)
    assert saver.save_if_changed() is False
    assert store.writes == 0


def test_same_route_newer_frame_is_not_a_change(tmp_path):
    sub_map = {b"\x00\x00\x01": ("MASTER-1", 2, 1000.0, b"\x00\x00\x00\x01")}
    store, saver = _saver(tmp_path, sub_map)
    sub_map[b"\x00\x00\x01"] = ("MASTER-1", 2, 1030.0, b"\x00\x00\x00\x01")
    assert saver.save_if_changed() is False


def test_new_unit_moved_unit_and_trim_are_changes(tmp_path):
    sub_map = {}
    store, saver = _saver(tmp_path, sub_map)

    sub_map[b"\x00\x00\x01"] = ("MASTER-1", 2, 1000.0, b"\x00\x00\x00\x01")
    assert saver.save_if_changed() is True

    sub_map[b"\x00\x00\x01"] = ("MASTER-1", 2, 1010.0, b"\x00\x00\x00\x02")  # other hotspot
    assert saver.save_if_changed() is True

    del sub_map[b"\x00\x00\x01"]
    assert saver.save_if_changed() is True
    assert saver.save_if_changed() is False
    assert store.writes == 3


def test_timestamp_is_refreshed_once_per_hour(tmp_path):
    # Otherwise a unit that never moves keeps its first saved time and a
    # restart would trim it while it is still active.
    sub_map = {b"\x00\x00\x01": ("MASTER-1", 2, 3600.0, None)}
    store, saver = _saver(tmp_path, sub_map)
    sub_map[b"\x00\x00\x01"] = ("MASTER-1", 2, 7199.0, None)
    assert saver.save_if_changed() is False
    sub_map[b"\x00\x00\x01"] = ("MASTER-1", 2, 7200.0, None)
    assert saver.save_if_changed() is True


def test_forced_save_resets_the_baseline(tmp_path):
    sub_map = {}
    store, saver = _saver(tmp_path, sub_map)
    sub_map[b"\x00\x00\x01"] = ("MASTER-1", 1, 1000.0)  # legacy 3-tuple
    saver.save()
    assert saver.save_if_changed() is False
    assert store.writes == 1
    assert store.load(str(tmp_path / "sub_map.pkl")) == sub_map


def test_save_leaves_no_temp_file_and_replaces_atomically(tmp_path):
    path = tmp_path / "sub_map.pkl"
    path.write_bytes(pickle.dumps({b"old": ("X", 1, 0.0)}))
    PickleSubMapStore().save(str(path), {b"new": ("Y", 2, 1.0)})
    assert [p.name for p in tmp_path.iterdir()] == ["sub_map.pkl"]
    assert PickleSubMapStore().load(str(path)) == {b"new": ("Y", 2, 1.0)}
