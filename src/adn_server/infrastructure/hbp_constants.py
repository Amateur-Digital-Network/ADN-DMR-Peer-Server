# ADN DMR Peer Server - HBP protocol constants
# Copyright (C) 2026  Rodrigo Pérez, CE5RPY <ce5rpy@qmd.cl>
#
# Derived from ADN DMR Server / FreeDMR  / HBlink. Original license:
###############################################################################
# Copyright (C) 2020 Simon Adlem, G7RZU <g7rzu@gb7fr.org.uk>
# Copyright (C) 2016-2019 Cortney T. Buffington, N0MJS <n0mjs@me.com>
#
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

"""Homebrew protocol opcodes and frame types (legacy const.py).

Frame types, the DMR and MASTER/PEER opcodes and protocol-level constants live
in domain.hbp_protocol (the canonical source); they are re-exported here so
existing infrastructure imports keep working.
"""

from ..domain.hbp_protocol import (  # noqa: F401 — re-export
    DMR,
    DMRA,
    DMRD,
    DMRE,
    DMRF,
    HBPF_DATA_SYNC,
    HBPF_SLT_VHEAD,
    HBPF_SLT_VTERM,
    HBPF_VOICE,
    HBPF_VOICE_SYNC,
    MSTC,
    MSTCL,
    MSTN,
    MSTNAK,
    MSTP,
    MSTPONG,
    PROTO_VER,
    RPTA,
    RPTACK,
    RPTC,
    RPTCL,
    RPTK,
    RPTL,
    RPTO,
    RPTP,
    RPTPING,
    STREAM_TO,
    VER,
)

# OpenBridge
EOBP = b"EOBP"
BC = b"BC"
BCKA = b"BCKA"
BCSQ = b"BCSQ"
BCST = b"BCST"
BCVE = b"BCVE"

# Proxy
PRIN = b"PRIN"
PRBL = b"PRBL"
