# ADN DMR Peer Server - load and decrypt user passwords (legacy hblink load_user_passwords, get_user_password)
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

"""Load user_passwords.json, decrypt with password_crypto; expose get_user_password(radio_id) for auth."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class UserPasswordsLoader:
    """Load and cache decrypted user passwords from GLOBAL.USERS_PASS (JSON with 'passwords' dict).

    Loaded at startup and again whenever the security downloader replaces the file,
    which is the only way it changes. Re-reading it on a timer decrypted the whole
    table ~30 times between downloads for an identical result.
    """

    def __init__(self, project_root: str) -> None:
        self._project_root = project_root
        self._passwords: dict[str, str] = {}
        self._config_dir = os.path.join(project_root, "config")
        self._key_path = os.path.join(self._config_dir, "encryption_key.secret")

    def load(self, config: dict[str, Any]) -> dict[str, str]:
        """Load user_passwords.json from data dir, decrypt each; return passwords dict. Legacy load_user_passwords."""
        previous = dict(self._passwords)
        data_dir = os.path.join(
            self._project_root,
            (config.get("ALIASES", {}).get("PATH") or "data").rstrip("/"),
        )
        users_pass = (config.get("GLOBAL", {}).get("USERS_PASS") or "user_passwords.json").strip()
        path = os.path.join(data_dir, users_pass)
        key_path = os.path.join(
            self._project_root,
            (config.get("GLOBAL", {}).get("CONFIG_PATH") or "config").rstrip("/"),
        )
        hash_encrypt = (config.get("GLOBAL", {}).get("HASH_ENCRYPT") or "encryption_key.secret").strip()
        self._key_path = os.path.join(key_path, hash_encrypt)
        if not os.path.exists(path):
            if previous:
                logger.warning("(AUTH) user passwords file missing, keeping cached passwords")
                return self._passwords
            self._passwords = {}
            return self._passwords
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get("passwords"), dict):
                raise ValueError("invalid user_passwords.json shape")
            from .password_crypto import decrypt_passwords

            self._passwords = decrypt_passwords(data.get("passwords", {}), self._key_path)
            logger.debug("(AUTH) Loaded %d individual passwords from %s", len(self._passwords), path)
        except (FileNotFoundError, json.JSONDecodeError, ValueError, Exception) as e:
            logger.warning("(AUTH) Could not load user passwords: %s", e)
            if previous:
                logger.warning("(AUTH) keeping previous cached passwords (%d entries)", len(previous))
                self._passwords = previous
            else:
                self._passwords = {}
        return self._passwords

    def get_user_password(self, radio_id: int) -> bytes | None:
        """Return password for radio_id (for login auth); 7-char prefix match like legacy. Legacy get_user_password."""
        radio_id_str = str(radio_id)
        if not radio_id_str.isdigit():
            return None
        if radio_id_str in self._passwords:
            pwd = self._passwords[radio_id_str]
            return pwd.encode("utf-8") if isinstance(pwd, str) else pwd
        if len(radio_id_str) == 9:
            base_id = radio_id_str[:7]
            if base_id in self._passwords:
                logger.debug("(AUTH) Radio ID %s using base ID %s password", radio_id_str, base_id)
                pwd = self._passwords[base_id]
                return pwd.encode("utf-8") if isinstance(pwd, str) else pwd
        return None
