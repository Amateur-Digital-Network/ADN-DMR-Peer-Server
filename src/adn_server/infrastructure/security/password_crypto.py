# ADN DMR Peer Server - password encryption (legacy password_crypto)
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

"""Fernet-based encrypt/decrypt for stored passwords. Legacy password_crypto."""

from __future__ import annotations

import os
from typing import Optional

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None  # type: ignore[misc, assignment]


def get_or_create_key(key_path: str) -> bytes:
    """Load encryption key from file or create and save one."""
    if Fernet is None:
        raise RuntimeError("cryptography package required for password_crypto")
    if os.path.exists(key_path):
        with open(key_path, "rb") as f:
            return f.read()
    key = Fernet.generate_key()
    os.makedirs(os.path.dirname(key_path), exist_ok=True)
    with open(key_path, "wb") as f:
        f.write(key)
    return key


def get_fernet(key_path: str = "config/encryption_key.secret") -> "Fernet":
    """Return Fernet instance using key at key_path."""
    if Fernet is None:
        raise RuntimeError("cryptography package required for password_crypto")
    key = get_or_create_key(key_path)
    return Fernet(key)


def decrypt_password(encrypted_password: Optional[str], key_path: str = "config/encryption_key.secret") -> Optional[str]:
    """Decrypt a stored password; return as-is if empty or on failure."""
    if not encrypted_password:
        return encrypted_password
    try:
        fernet = get_fernet(key_path)
        decrypted = fernet.decrypt(encrypted_password.encode("utf-8"))
        return decrypted.decode("utf-8")
    except Exception:
        return encrypted_password


def decrypt_passwords(
    encrypted: dict[str, str], key_path: str = "config/encryption_key.secret"
) -> dict[str, str]:
    """Decrypt a whole table on one Fernet: the key is read once, not once per entry."""
    fernet = get_fernet(key_path)
    decrypted: dict[str, str] = {}
    for radio_id, password in encrypted.items():
        if not password:
            decrypted[str(radio_id)] = ""
            continue
        try:
            decrypted[str(radio_id)] = fernet.decrypt(password.encode("utf-8")).decode("utf-8")
        except Exception:
            decrypted[str(radio_id)] = password
    return decrypted
