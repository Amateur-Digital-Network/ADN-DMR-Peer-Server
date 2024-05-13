#
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

#Some utilty functions from dmr_utils3 have been modified. These live here.

# Also new ADN specific functions.

import ssl
from time import time
from os.path import isfile, getmtime
from urllib.request import urlopen
from json import load as jload, dump as jdump
import hashlib



#Use this try_download instead of that from dmr_utils3
def try_download(_path, _file, _url, _stale,):
    no_verify = ssl._create_unverified_context()
    now = time()
    file_exists = isfile(''.join([_path,_file])) == True
    if file_exists:
        file_old = (getmtime(''.join([_path,_file])) + _stale) < now
    if not file_exists or (file_exists and file_old):
        try:
            with urlopen(_url, context=no_verify) as response:
                data = response.read()
                #outfile.write(data)
                response.close()
            result = 'ID ALIAS MAPPER: \'{}\' successfully downloaded'.format(_file)
        except IOError:
            result = 'ID ALIAS MAPPER: \'{}\' could not be downloaded due to an IOError'.format(_file)
        else:
            if data and (data != b'{}'):
                try:
                    with open(''.join([_path,_file]), 'wb') as outfile:
                        outfile.write(data)
                        outfile.close()
                except IOError:
                    result = 'ID ALIAS mapper \'{}\' file could not be written due to an IOError'.format(_file)
            else:
                result = 'ID ALIAS mapper \'{}\' file not written because downloaded data is empty for some reason'.format(_file)

    else:
        result = 'ID ALIAS MAPPER: \'{}\' is current, not downloaded'.format(_file)

    return result

# SHORT VERSION - MAKES A SIMPLE {INTEGER ID: 'CALLSIGN'} DICTIONARY
def mk_id_dict(_path, _file):
    _dict = {}
    try:
        with open(_path+_file, 'r', encoding='latin1') as _handle:
            records = jload(_handle)
            if 'count' in [*records]:
                records.pop('count')
            records = records[[*records][0]]
            _handle.close
            for record in records:
                try:
                    _dict[int(record['id'])] = record['callsign']
                except:
                    pass
        return _dict
    except:
        raise

#Read JSON from file
def load_json(filename):
    try:
        with open(filename) as f:
            data = jload(f)
    except:
        raise
    else:
        return(data)

def save_json(filename,data):
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            jdump(data, f, ensure_ascii=False, indent=4)
    except:
        raise
    else:
        return(True)



#Calculate blake2b checksum of file
def blake2bsum(filename):
    blake2b_hash = hashlib.blake2b()
    try:
        with open(filename,"rb") as f:
            for byte_block in iter(lambda: f.read(4096),b""):
                blake2b_hash.update(byte_block)
            return(blake2b_hash.hexdigest())
    except:
        raise
