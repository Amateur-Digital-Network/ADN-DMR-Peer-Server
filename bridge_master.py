#!/usr/bin/env python
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

'''
This application, in conjuction with it's rule file (rules.py) will
work like a "conference bridge". This is similar to what most hams think of as a
reflector. You define conference bridges and any system joined to that conference
bridge will both receive traffic from, and send traffic to any other system
joined to the same conference bridge. It does not provide end-to-end connectivity
as each end system must individually be joined to a conference bridge (a name
you create in the configuraiton file) to pass traffic.

This program currently only works with group voice calls.
'''

# Python modules we need
import sys
from bitarray import bitarray
from time import time,sleep
import importlib.util
import re
import copy

# Twisted is pretty important, so I keep it separate
from twisted.internet.protocol import Factory, Protocol
from twisted.protocols.basic import NetstringReceiver
from twisted.internet import reactor, task

# Things we import from the main hblink module
from hblink import HBSYSTEM, OPENBRIDGE, systems, hblink_handler, reportFactory, REPORT_OPCODES, mk_aliases
from dmr_utils3.utils import bytes_3, int_id, get_alias, bytes_4
from dmr_utils3 import decode, bptc, const
import config
from config import acl_build
import log
from const import *
from mk_voice import pkt_gen
#from voice_lib import words

#Read voices
from read_ambe import readAMBE
#Remap some words for certain languages
from i8n_voice_map import voiceMap


#MySQL
from mysql_config import useMYSQL

# Stuff for socket reporting
import pickle
# REMOVE LATER from datetime import datetime
# The module needs logging, but handlers, etc. are controlled by the parent
import logging
logger = logging.getLogger(__name__)

#REGEX
import re


# Does anybody read this stuff? There's a PEP somewhere that says I should do this.
__author__     = 'Cortney T. Buffington, N0MJS, Forked by Simon Adlem - G7RZU'
__copyright__  = 'Copyright (c) 2016-2019 Cortney T. Buffington, N0MJS and the K0USY Group, Simon Adlem, G7RZU 2020'
__credits__    = 'Colin Durbridge, G4EML, Steve Zingman, N4IRS; Mike Zingman, N4IRR; Jonathan Naylor, G4KLX; Hans Barthen, DL5DI; Torsten Shultze, DG1HT; Jon Lee, G4TSN'
__license__    = 'GNU GPLv3'
__maintainer__ = 'Simon Adlem G7RZU'
__email__      = 'simon@gb7fr.org.uk'

# Module gobal varaibles

# Timed loop used for reporting HBP status
#
# REPORT BASED ON THE TYPE SELECTED IN THE MAIN CONFIG FILE
def config_reports(_config, _factory):
    if True: #_config['REPORTS']['REPORT']:
        def reporting_loop(logger, _server):
            logger.debug('(REPORT) Periodic reporting loop started')
            _server.send_config()
            _server.send_bridge()

        logger.info('(REPORT) HBlink TCP reporting server configured')

        report_server = _factory(_config)
        report_server.clients = []
        reactor.listenTCP(_config['REPORTS']['REPORT_PORT'], report_server)

        reporting = task.LoopingCall(reporting_loop, logger, report_server)
        reporting.start(_config['REPORTS']['REPORT_INTERVAL'])

    return report_server


# Import Bridging rules
# Note: A stanza *must* exist for any MASTER or CLIENT configured in the main
# configuration file and listed as "active". It can be empty,
# but it has to exist.
def make_bridges(_rules):
    # Convert integer GROUP ID numbers from the config into hex strings
    # we need to send in the actual data packets.
    for _bridge in _rules:
        for _system in _rules[_bridge]:
            if _system['SYSTEM'] not in CONFIG['SYSTEMS']:
                sys.exit('ERROR: Conference bridge "{}" references a system named "{}" that is not enabled in the main configuration'.format(_bridge, _system['SYSTEM']))

            _system['TGID']       = bytes_3(_system['TGID'])
            for i, e in enumerate(_system['ON']):
                _system['ON'][i]  = bytes_3(_system['ON'][i])
            for i, e in enumerate(_system['OFF']):
                _system['OFF'][i] = bytes_3(_system['OFF'][i])
            _system['TIMEOUT']    = _system['TIMEOUT']*60
            if _system['ACTIVE'] == True:
                _system['TIMER']  = time() + _system['TIMEOUT']
            else:
                _system['TIMER']  = time()
        
       # if _bridge[0:1] == '#':
        #    continue
        
        for _confsystem in CONFIG['SYSTEMS']:
            #if _confsystem[0:3] == 'OBP':
            if CONFIG['SYSTEMS'][_confsystem]['MODE'] != 'MASTER':
                continue
            ts1 = False 
            ts2 = False
            for i,e in enumerate(_rules[_bridge]):
                if e['SYSTEM'] == _confsystem and e['TS'] == 1:
                    ts1 = True
                if e['SYSTEM'] == _confsystem and e['TS'] == 2:
                    ts2 = True
            if _bridge[0:1] != '#':
                _tmout = CONFIG['SYSTEMS'][_confsystem]['DEFAULT_UA_TIMER']
                if ts1 == False:
                    _rules[_bridge].append({'SYSTEM': _confsystem, 'TS': 1, 'TGID': bytes_3(int(_bridge)),'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [bytes_3(int(_bridge)),],'RESET': [], 'TIMER': time()})
                if ts2 == False:
                    _rules[_bridge].append({'SYSTEM': _confsystem, 'TS': 2, 'TGID': bytes_3(int(_bridge)),'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [bytes_3(int(_bridge)),],'RESET': [], 'TIMER': time()})
            else:
                _tmout = CONFIG['SYSTEMS'][_confsystem]['DEFAULT_UA_TIMER']
                if ts2 == False:
                    _rules[_bridge].append({'SYSTEM': _confsystem, 'TS': 2, 'TGID': bytes_3(9),'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [bytes_3(4000)],'ON': [],'RESET': [], 'TIMER': time()})
    
    return _rules

#Make a single bridge - used for on-the-fly UA bridges
def make_single_bridge(_tgid,_sourcesystem,_slot,_tmout):
    _tgid_s = str(int_id(_tgid))
    #Always a 1 min timeout for Echo
    if _tgid_s == '9990':
        _tmout = 1
    BRIDGES[_tgid_s] = []
    for _system in CONFIG['SYSTEMS']:
        if _system[0:3] != 'OBP':
        #if CONFIG['SYSTEMS'][system]['MODE'] == 'MASTER':
            #_tmout = CONFIG['SYSTEMS'][_system]['DEFAULT_UA_TIMER']
            if _system == _sourcesystem:
                    if _slot == 1:
                        BRIDGES[_tgid_s].append({'SYSTEM': _system, 'TS': 1, 'TGID': _tgid,'ACTIVE': True,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [_tgid,],'RESET': [], 'TIMER': time() + (_tmout * 60)})
                        BRIDGES[_tgid_s].append({'SYSTEM': _system, 'TS': 2, 'TGID': _tgid,'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [_tgid,],'RESET': [], 'TIMER': time()})
                    else:
                        BRIDGES[_tgid_s].append({'SYSTEM': _system, 'TS': 2, 'TGID': _tgid,'ACTIVE': True,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [_tgid,],'RESET': [], 'TIMER': time() + (_tmout * 60)})
                        BRIDGES[_tgid_s].append({'SYSTEM': _system, 'TS': 1, 'TGID': _tgid,'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [_tgid,],'RESET': [], 'TIMER': time()})
            else:
                BRIDGES[_tgid_s].append({'SYSTEM': _system, 'TS': 1, 'TGID': _tgid,'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [_tgid,],'RESET': [], 'TIMER': time()})
                BRIDGES[_tgid_s].append({'SYSTEM': _system, 'TS': 2, 'TGID': _tgid,'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [_tgid,],'RESET': [], 'TIMER': time()})
                
        if _system[0:3] == 'OBP' and int_id(_tgid) >= 89:
            BRIDGES[_tgid_s].append({'SYSTEM': _system, 'TS': 1, 'TGID': _tgid,'ACTIVE': True,'TIMEOUT': '','TO_TYPE': 'NONE','OFF': [],'ON': [],'RESET': [], 'TIMER': time()})
        
#Make static bridge - used for on-the-fly relay bridges
def make_stat_bridge(_tgid):
    _tgid_s = str(int_id(_tgid))
    BRIDGES[_tgid_s] = []
    for _system in CONFIG['SYSTEMS']:
        if _system[0:3] != 'OBP':
            if CONFIG['SYSTEMS'][_system]['MODE'] == 'MASTER':
                _tmout = CONFIG['SYSTEMS'][_system]['DEFAULT_UA_TIMER']
                BRIDGES[_tgid_s].append({'SYSTEM': _system, 'TS': 1, 'TGID': _tgid,'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [_tgid,],'RESET': [], 'TIMER': time()})
                BRIDGES[_tgid_s].append({'SYSTEM': _system, 'TS': 2, 'TGID': _tgid,'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [_tgid,],'RESET': [], 'TIMER': time()})
                    
        if _system[0:3] == 'OBP':
            BRIDGES[_tgid_s].append({'SYSTEM': _system, 'TS': 1, 'TGID': _tgid,'ACTIVE': True,'TIMEOUT': '','TO_TYPE': 'STAT','OFF': [],'ON': [],'RESET': [], 'TIMER': time()})
        

def make_default_reflector(reflector,_tmout,system):
    bridge = '#'+str(reflector)
    #_tmout = CONFIG['SYSTEMS'][system]['DEFAULT_UA_TIMER']
    if bridge not in BRIDGES:
        BRIDGES[bridge] = []
        make_single_reflector(bytes_3(reflector),_tmout, system)
    bridgetemp = []
    for bridgesystem in BRIDGES[bridge]:
        if bridgesystem['SYSTEM'] == system and bridgesystem['TS'] == 2:
            bridgetemp.append({'SYSTEM': system, 'TS': 2, 'TGID': bytes_3(9),'ACTIVE': True,'TIMEOUT':  _tmout * 60,'TO_TYPE': 'OFF','OFF': [],'ON': [bytes_3(reflector),],'RESET': [], 'TIMER': time() + (_tmout * 60)})
        else:
            bridgetemp.append(bridgesystem)
            
        BRIDGES[bridge] = bridgetemp
        
def make_static_tg(tg,ts,_tmout,system):
    #_tmout = CONFIG['SYSTEMS'][system]['DEFAULT_UA_TIMER']
    if str(tg) not in BRIDGES:
        make_single_bridge(bytes_3(tg),system,ts,_tmout)
    bridgetemp = []
    for bridgesystem in BRIDGES[str(tg)]:
        if bridgesystem['SYSTEM'] == system and bridgesystem['TS'] == ts:
            bridgetemp.append({'SYSTEM': system, 'TS': ts, 'TGID': bytes_3(tg),'ACTIVE': True,'TIMEOUT':  _tmout * 60,'TO_TYPE': 'OFF','OFF': [],'ON': [bytes_3(tg),],'RESET': [], 'TIMER': time() + (_tmout * 60)})
        else:
            bridgetemp.append(bridgesystem)
        
    BRIDGES[str(tg)] = bridgetemp
    
def reset_static_tg(tg,ts,_tmout,system):
    #_tmout = CONFIG['SYSTEMS'][system]['DEFAULT_UA_TIMER']
    bridgetemp = []
    for bridgesystem in BRIDGES[str(tg)]:
        if bridgesystem['SYSTEM'] == system and bridgesystem['TS'] == ts:
            bridgetemp.append({'SYSTEM': system, 'TS': ts, 'TGID': bytes_3(tg),'ACTIVE': False,'TIMEOUT':  _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [bytes_3(tg),],'RESET': [], 'TIMER': time() + (_tmout * 60)})
        else:
            bridgetemp.append(bridgesystem)
        
    BRIDGES[str(tg)] = bridgetemp
        
def reset_default_reflector(reflector,_tmout,system):
    bridge = '#'+str(reflector)
    #_tmout = CONFIG['SYSTEMS'][system]['DEFAULT_UA_TIMER']
    if bridge not in BRIDGES:
        BRIDGES[bridge] = []
        make_single_reflector(bytes_3(reflector),_tmout, system)
    bridgetemp = []
    for bridgesystem in BRIDGES[bridge]:
        if bridgesystem['SYSTEM'] == system and bridgesystem['TS'] == 2:
            bridgetemp.append({'SYSTEM': system, 'TS': 2, 'TGID': bytes_3(9),'ACTIVE': False,'TIMEOUT':  _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [bytes_3(reflector),],'RESET': [], 'TIMER': time() + (_tmout * 60)})
        else:
            bridgetemp.append(bridgesystem)
        BRIDGES[bridge] = bridgetemp
            
def make_single_reflector(_tgid,_tmout,_sourcesystem):
    _tgid_s = str(int_id(_tgid))
    _bridge = '#' + _tgid_s
    #1 min timeout for echo
    if _tgid_s == '9990':
        _tmout = 1
    BRIDGES[_bridge] = []
    for _system in CONFIG['SYSTEMS']:
        #if _system[0:3] != 'OBP':
        if CONFIG['SYSTEMS'][_system]['MODE'] == 'MASTER':
            #_tmout = CONFIG['SYSTEMS'][_system]['DEFAULT_UA_TIMER']
            if _system == _sourcesystem:
                BRIDGES[_bridge].append({'SYSTEM': _system, 'TS': 2, 'TGID': bytes_3(9),'ACTIVE': True,'TIMEOUT':  _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [_tgid,],'RESET': [], 'TIMER': time() + (_tmout * 60)})
            else:
                BRIDGES[_bridge].append({'SYSTEM': _system, 'TS': 2, 'TGID': bytes_3(9),'ACTIVE': False,'TIMEOUT':  CONFIG['SYSTEMS'][_system]['DEFAULT_UA_TIMER'] * 60,'TO_TYPE': 'ON','OFF': [],'ON': [_tgid,],'RESET': [], 'TIMER': time()})
        if _system[0:3] == 'OBP' and int_id(_tgid) >= 89:
            BRIDGES[_bridge].append({'SYSTEM': _system, 'TS': 1, 'TGID': _tgid,'ACTIVE': True,'TIMEOUT': '','TO_TYPE': 'NONE','OFF': [],'ON': [],'RESET': [], 'TIMER': time()})
        
def remove_bridge_system(system):
    _bridgestemp = {}
    _bridgetemp = {}
    for _bridge in BRIDGES:
        for _bridgesystem in BRIDGES[_bridge]:
            if _bridgesystem['SYSTEM'] != system:
                if _bridge not in _bridgestemp:
                    _bridgestemp[_bridge] = []
                _bridgestemp[_bridge].append(_bridgesystem)
    BRIDGES.update(_bridgestemp)
                

# Run this every minute for rule timer updates
def rule_timer_loop():
    logger.debug('(ROUTER) routerHBP Rule timer loop started')
    _now = time()
    _remove_bridges = []
    for _bridge in BRIDGES:
        _bridge_used = False
        for _system in BRIDGES[_bridge]:
            if _system['TO_TYPE'] == 'ON':
                if _system['ACTIVE'] == True:
                    _bridge_used = True
                    if _system['TIMER'] < _now:
                        _system['ACTIVE'] = False
                        logger.info('(ROUTER) Conference Bridge TIMEOUT: DEACTIVATE System: %s, Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
                        if _bridge[0:1] == '#':
                            reactor.callInThread(disconnectedVoice,_system['SYSTEM'])
                    else:
                        timeout_in = _system['TIMER'] - _now
                        _bridge_used = True
                        logger.info('(ROUTER) Conference Bridge ACTIVE (ON timer running): System: %s Bridge: %s, TS: %s, TGID: %s, Timeout in: %.2fs,', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']),  timeout_in)
                elif _system['ACTIVE'] == False:
                    logger.debug('(ROUTER) Conference Bridge INACTIVE (no change): System: %s Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
            elif _system['TO_TYPE'] == 'OFF':
                if _system['ACTIVE'] == False:
                    if _system['TIMER'] < _now:
                        _system['ACTIVE'] = True
                        _bridge_used = True 
                        logger.info('(ROUTER) Conference Bridge TIMEOUT: ACTIVATE System: %s, Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
                    else:
                        timeout_in = _system['TIMER'] - _now
                        _bridge_used = True
                        logger.info('(ROUTER) Conference Bridge INACTIVE (OFF timer running): System: %s Bridge: %s, TS: %s, TGID: %s, Timeout in: %.2fs,', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']),  timeout_in)
                elif _system['ACTIVE'] == True:
                    _bridge_used = True
                    logger.debug('(ROUTER) Conference Bridge ACTIVE (no change): System: %s Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
            else:
                if _system['SYSTEM'][0:3] != 'OBP':
                    _bridge_used = True
                elif _system['SYSTEM'][0:3] == 'OBP' and _system['TO_TYPE'] == 'STAT':
                    _bridge_used = True
                logger.debug('(ROUTER) Conference Bridge NO ACTION: System: %s, Bridge: %s, TS: %s, TGID: %s', _system['SYSTEM'], _bridge, _system['TS'], int_id(_system['TGID']))
                
        if _bridge_used == False:
            _remove_bridges.append(_bridge)
                
    for _bridgerem in _remove_bridges:
        del BRIDGES[_bridgerem]
        logger.debug('(ROUTER) Unused conference bridge %s removed',_bridgerem)

    if CONFIG['REPORTS']['REPORT']:
        report_server.send_clients(b'bridge updated')

def statTrimmer():
    logger.debug('(ROUTER) STAT trimmer loop started')
    _remove_bridges = []
    for _bridge in BRIDGES:
        _bridge_stat = False
        _in_use = False
        for _system in BRIDGES[_bridge]:
            if _system['TO_TYPE'] == 'STAT':
                _bridge_stat = True
            if _system['TO_TYPE'] == 'ON' and _system['ACTIVE']:
                _in_use = True
            elif _system['TO_TYPE'] == 'OFF' and not _system['ACTIVE']:
                _in_use = True
        if _bridge_stat and not _in_use:
            _remove_bridges.append(_bridge)
    for _bridgerem in _remove_bridges:
        del BRIDGES[_bridgerem]
        logger.debug('(ROUTER) STAT bridge %s removed',_bridgerem)
    if CONFIG['REPORTS']['REPORT']:
        report_server.send_clients(b'bridge updated')


# run this every 10 seconds to trim orphaned stream ids
def stream_trimmer_loop():
    logger.debug('(ROUTER) Trimming inactive stream IDs from system lists')
    _now = time()

    for system in systems:
        # HBP systems, master and peer
        if CONFIG['SYSTEMS'][system]['MODE'] != 'OPENBRIDGE':
            for slot in range(1,3):
                _slot  = systems[system].STATUS[slot]

                # RX slot check
                if _slot['RX_TYPE'] != HBPF_SLT_VTERM and _slot['RX_TIME'] <  _now - 10:
                    _slot['RX_TYPE'] = HBPF_SLT_VTERM
                    logger.info('(%s) *TIME OUT*  RX STREAM ID: %s SUB: %s TGID %s, TS %s, Duration: %.2f', \
                        system, int_id(_slot['RX_STREAM_ID']), int_id(_slot['RX_RFS']), int_id(_slot['RX_TGID']), slot, _slot['RX_TIME'] - _slot['RX_START'])
                    if CONFIG['REPORTS']['REPORT']:
                        systems[system]._report.send_bridgeEvent('GROUP VOICE,END,RX,{},{},{},{},{},{},{:.2f}'.format(system, int_id(_slot['RX_STREAM_ID']), int_id(_slot['RX_PEER']), int_id(_slot['RX_RFS']), slot, int_id(_slot['RX_TGID']), _slot['RX_TIME'] - _slot['RX_START']).encode(encoding='utf-8', errors='ignore'))
                    #Null stream_id - for loop control 
                    _slot['RX_STREAM_ID'] = b'\x00'

                # TX slot check
                if _slot['TX_TYPE'] != HBPF_SLT_VTERM and _slot['TX_TIME'] <  _now - 10:
                    _slot['TX_TYPE'] = HBPF_SLT_VTERM
                    logger.info('(%s) *TIME OUT*  TX STREAM ID: %s SUB: %s TGID %s, TS %s, Duration: %.2f', \
                        system, int_id(_slot['TX_STREAM_ID']), int_id(_slot['TX_RFS']), int_id(_slot['TX_TGID']), slot, _slot['TX_TIME'] - _slot['TX_START'])
                    if CONFIG['REPORTS']['REPORT']:
                        systems[system]._report.send_bridgeEvent('GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}'.format(system, int_id(_slot['TX_STREAM_ID']), int_id(_slot['TX_PEER']), int_id(_slot['TX_RFS']), slot, int_id(_slot['TX_TGID']), _slot['TX_TIME'] - _slot['TX_START']).encode(encoding='utf-8', errors='ignore'))

        # OBP systems
        # We can't delete items from a dicationry that's being iterated, so we have to make a temporarly list of entrys to remove later
        if CONFIG['SYSTEMS'][system]['MODE'] == 'OPENBRIDGE':
            remove_list = []
            fin_list = []
            for stream_id in systems[system].STATUS:
                
                #if stream already marked as finished, just remove it
                if '_fin' in systems[system].STATUS[stream_id] and systems[system].STATUS[stream_id]['LAST'] < _now - 10:
                    logger.info('(%s) *FINISHED STREAM* STREAM ID: %s',system, int_id(stream_id))
                    fin_list.append(stream_id)
                    continue
                
                try:
                    if systems[system].STATUS[stream_id]['LAST'] < _now - 10:
                        remove_list.append(stream_id)
                except:
                    logger.warning("(%s) Keyerror - stream trimmer Stream ID: %s",system,stream_id)
                    systems[system].STATUS[stream_id]['LAST'] = _now
                    continue
                
            #remove finished     
            for stream_id in fin_list:
                removed = systems[system].STATUS.pop(stream_id)
                
            for stream_id in remove_list:
                if stream_id in systems[system].STATUS:
                    _stream = systems[system].STATUS[stream_id]
                    _sysconfig = CONFIG['SYSTEMS'][system]
                    logger.info('(%s) *TIME OUT*   STREAM ID: %s SUB: %s PEER: %s TGID: %s TS 1 Duration: %.2f', \
                        system, int_id(stream_id), get_alias(int_id(_stream['RFS']), subscriber_ids), get_alias(int_id(_sysconfig['NETWORK_ID']), peer_ids), get_alias(int_id(_stream['TGID']), talkgroup_ids), _stream['LAST'] - _stream['START'])
                    if CONFIG['REPORTS']['REPORT']:
                            systems[system]._report.send_bridgeEvent('GROUP VOICE,END,RX,{},{},{},{},{},{},{:.2f}'.format(system, int_id(stream_id), int_id(_sysconfig['NETWORK_ID']), int_id(_stream['RFS']), 1, int_id(_stream['TGID']), _stream['LAST'] - _stream['START']).encode(encoding='utf-8', errors='ignore'))
                    removed = systems[system].STATUS.pop(stream_id)
                else:
                    logger.error('(%s) Attemped to remove OpenBridge Stream ID %s not in the Stream ID list: %s', system, int_id(stream_id), [id for id in systems[system].STATUS])

def sendVoicePacket(self,pkt,_source_id,_dest_id,_slot):
    _stream_id = pkt[16:20]
    _pkt_time = time()
    if _stream_id not in systems[system].STATUS:
        systems[system].STATUS[_stream_id] = {
        'START':     _pkt_time,
        'CONTENTION':False,
        'RFS':       _source_id,
        'TGID':      _dest_id,
        'LAST':      _pkt_time
        }
        _slot['TX_TGID'] = _dest_id
    else:
        systems[system].STATUS[_stream_id]['LAST'] = _pkt_time
        _slot['TX_TIME'] = _pkt_time
                                            
    self.send_system(pkt)
    
def sendSpeech(self,speech):
    logger.debug('(%s) Inside sendspeech thread',self._system)
    sleep(1)
    _nine = bytes_3(9)
    _source_id = bytes_3(5000)
    _slot  = systems[system].STATUS[2]
    while True:
        try:
            pkt = next(speech)
        except StopIteration:
            break
        #Packet every 60ms
        sleep(0.058)
        reactor.callFromThread(sendVoicePacket,self,pkt,_source_id,_nine,_slot)

    logger.debug('(%s) Sendspeech thread ended',self._system)

def disconnectedVoice(system):
    _nine = bytes_3(9)
    _source_id = bytes_3(5000)
    logger.debug('(%s) Sending disconnected voice',system)
    _say = [words['silence']]
    _say.append(words['silence']) 
    if CONFIG['SYSTEMS'][system]['DEFAULT_REFLECTOR'] > 0:
        _say.append(words['silence'])
        _say.append(words['linkedto'])
        _say.append(words['silence'])
        _say.append(words['to'])
        _say.append(words['silence'])
        _say.append(words['silence']) 
        
        for number in str(CONFIG['SYSTEMS'][system]['DEFAULT_REFLECTOR']):
            _say.append(words[number])
            _say.append(words['silence'])
    else:
        _say.append(words['notlinked'])
    
    _say.append(words['silence']) 
    
    speech = pkt_gen(_source_id, _nine, bytes_4(9), 1, _say)

    sleep(1)
    _slot  = systems[system].STATUS[2]
    while True:
        try:
            pkt = next(speech)
        except StopIteration:
                break
        #Packet every 60ms
        sleep(0.058)
        _stream_id = pkt[16:20]
        _pkt_time = time()
        reactor.callFromThread(sendVoicePacket,self,pkt,_source_id,_nine,_slot)
        logger.debug('(%s) disconnected voice thread end',system)
    

def threadIdent():
    logger.debug('(IDENT) starting ident thread')
    reactor.callInThread(ident)
    
def threadedMysql():
    logger.debug('(MYSQL) Starting MySQL thread')
    reactor.callInThread(mysqlGetConfig)

def ident():
    for system in systems:
        if CONFIG['SYSTEMS'][system]['MODE'] != 'MASTER':
            continue
        if CONFIG['SYSTEMS'][system]['VOICE_IDENT'] == True:
            if CONFIG['SYSTEMS'][system]['MAX_PEERS'] > 1:
                logger.debug("(IDENT) %s System has MAX_PEERS > 1, skipping",system)
                continue
            _callsign = False
            for _peerid in CONFIG['SYSTEMS'][system]['PEERS']:
                _callsign = CONFIG['SYSTEMS'][system]['PEERS'][_peerid]['CALLSIGN'].decode()
            if not _callsign:
                logger.debug("(IDENT) %s System has no peers or no recorded callsign (%s), skipping",system,_callsign)
                continue
            _slot  = systems[system].STATUS[2]
            #If slot is idle for RX and TX
            #print("RX:"+str(_slot['RX_TYPE'])+" TX:"+str(_slot['TX_TYPE'])+" TIME:"+str(time() - _slot['TX_TIME']))
            if (_slot['RX_TYPE'] == HBPF_SLT_VTERM) and (_slot['TX_TYPE'] == HBPF_SLT_VTERM) and (time() - _slot['TX_TIME'] > CONFIG['SYSTEMS'][system]['GROUP_HANGTIME']):
                #_stream_id = hex_str_4(1234567)
                logger.info('(%s) System idle. Sending voice ident',system)
                _say = [words['silence']]
                _say.append(words['silence'])
                _systemcs = re.sub(r'\W+', '', _callsign)
                _systemcs.upper()
                for character in _systemcs:
                    _say.append(words[character])
                    _say.append(words['silence'])
                _say.append(words['silence'])
                #test 
                #_say.append(AMBEobj.readSingleFile('44xx.ambe'))
                _all_call = bytes_3(16777215)
                _source_id= bytes_3(5000)
                speech = pkt_gen(_source_id, _all_call, bytes_4(16777215), 1, _say)

                sleep(1)
                _slot  = systems[system].STATUS[2]
                while True:
                    try:
                        pkt = next(speech)
                    except StopIteration:
                            break
                    #Packet every 60ms
                    sleep(0.058)
                    
                    _stream_id = pkt[16:20]
                    _pkt_time = time()
                    reactor.callFromThread(sendVoicePacket,systems[system],pkt,_source_id,_all_call,_slot)

def options_config():
    logger.debug('(OPTIONS) Running options parser')
    for _system in CONFIG['SYSTEMS']:
        if CONFIG['SYSTEMS'][_system]['MODE'] != 'MASTER':
            continue
        if CONFIG['SYSTEMS'][_system]['ENABLED'] == True:
            if 'OPTIONS' in CONFIG['SYSTEMS'][_system]:
                _options = {}
                CONFIG['SYSTEMS'][_system]['OPTIONS'] = CONFIG['SYSTEMS'][_system]['OPTIONS'].rstrip('\x00')
                re.sub("\'","",CONFIG['SYSTEMS'][_system]['OPTIONS'])
                re.sub("\"","",CONFIG['SYSTEMS'][_system]['OPTIONS'])
                for x in CONFIG['SYSTEMS'][_system]['OPTIONS'].split(";"):
                    try:
                        k,v = x.split('=')
                    except ValueError:
                        logger.debug('(OPTIONS) Value error %s ignoring',_system)
                        continue
                    _options[k] = v
                logger.debug('(OPTIONS) Options found for %s',_system)
                
                if 'DIAL' in _options:
                    _options['DEFAULT_REFLECTOR'] = _options.pop('DIAL')
                if 'TIMER' in _options:
                    _options['DEFAULT_UA_TIMER'] = _options.pop('TIMER')
                if 'TS1' in _options:
                    _options['TS1_STATIC'] = _options.pop('TS1')
                if 'TS2' in _options:
                    _options['TS2_STATIC'] = _options.pop('TS2')
                    
                #DMR+ style options
                if 'StartRef' in _options:
                    _options['DEFAULT_REFLECTOR'] = _options.pop('StartRef')
                if 'RelinkTime' in _options:
                    _options['DEFAULT_UA_TIMER'] = _options.pop('RelinkTime')
                if 'TS1_1' in _options:
                    _options['TS1_STATIC'] = _options.pop('TS1_1')
                    if 'TS1_2' in _options:
                        _options['TS1_STATIC'] = _options['TS1_STATIC'] + ',' + _options.pop('TS1_2')
                    if 'TS1_3' in _options:
                        _options['TS1_STATIC'] = _options['TS1_STATIC'] + ',' + _options.pop('TS1_3')
                    if 'TS1_4' in _options:
                        _options['TS1_STATIC'] = _options['TS1_STATIC'] + ',' + _options.pop('TS1_4')
                    if 'TS1_4' in _options:
                        _options['TS1_STATIC'] = _options['TS1_STATIC'] + ',' + _options.pop('TS1_5')
                if 'TS2_2' in _options:
                    _options['TS2_STATIC'] = _options.pop('TS2_1')
                    if 'TS2_2' in _options:
                        _options['TS2_STATIC'] = _options['TS2_STATIC'] + ',' + _options.pop('TS2_2')
                    if 'TS2_3' in _options:
                        _options['TS2_STATIC'] = _options['TS2_STATIC'] + ',' + _options.pop('TS2_3')
                    if 'TS2_4' in _options:
                        _options['TS2_STATIC'] = _options['TS2_STATIC'] + ',' + _options.pop('TS2_4')
                    if 'TS2_4' in _options:
                        _options['TS2_STATIC'] = _options['TS2_STATIC'] + ',' + _options.pop('TS2_5')
                if 'UserLink' in _options:
                    _options.pop('UserLink')
                
                if 'TS1_STATIC' not in _options:
                    _options['TS1_STATIC'] = False
                
                if 'TS2_STATIC' not in _options:
                    _options['TS2_STATIC'] = False
                    
                if 'DEFAULT_REFLECTOR' not in _options:
                    _options['DEFAULT_REFLECTOR'] = 0
                    
                if 'DEFAULT_UA_TIMER' not in _options:
                    _options['DEFAULT_UA_TIMER'] = CONFIG['SYSTEMS'][_system]['DEFAULT_UA_TIMER']
                
                if 'VOICE' in _options and (CONFIG['SYSTEMS'][_system]['VOICE_IDENT'] != bool(int(_options['VOICE']))):
                    CONFIG['SYSTEMS'][_system]['VOICE_IDENT'] = bool(int(_options['VOICE']))
                    logger.debug("(OPTIONS) %s - Setting voice ident to %s",_system,CONFIG['SYSTEMS'][_system]['VOICE_IDENT'])
                    
                if 'SINGLE' in _options and (CONFIG['SYSTEMS'][_system]['SINGLE_MODE'] != bool(int(_options['SINGLE']))):
                    CONFIG['SYSTEMS'][_system]['SINGLE_MODE'] = bool(int(_options['SINGLE']))
                    logger.debug("(OPTIONS) %s - Setting SINGLE_MODE to %s",_system,CONFIG['SYSTEMS'][_system]['SINGLE_MODE'])
                
                if 'TS1_STATIC' not in _options or 'TS2_STATIC' not in _options or 'DEFAULT_REFLECTOR' not in _options or 'DEFAULT_UA_TIMER' not in _options:
                    logger.debug('(OPTIONS) %s - Required field missing, ignoring',_system)
                    continue
                
                if _options['TS1_STATIC'] == '':
                    _options['TS1_STATIC'] = False
                if _options['TS2_STATIC'] == '':
                    _options['TS2_STATIC'] = False
                    
                if _options['TS1_STATIC']:
                    re.sub("\s","",_options['TS1_STATIC'])
                    if re.search("![\d\,]",_options['TS1_STATIC']):
                        logger.debug('(OPTIONS) %s - TS1_STATIC contains characters other than numbers and comma, ignoring',_system)
                        continue
                
                if _options['TS2_STATIC']:
                    re.sub("\s","",_options['TS2_STATIC'])
                    if re.search("![\d\,]",_options['TS2_STATIC']):
                        logger.debug('(OPTIONS) %s - TS2_STATIC contains characters other than numbers and comma, ignoring',_system)
                        continue
                
                if isinstance(_options['DEFAULT_REFLECTOR'], str) and not _options['DEFAULT_REFLECTOR'].isdigit():
                    logger.debug('(OPTIONS) %s - DEFAULT_UA_TIMER is not an integer, ignoring',_system)
                    continue
                
                if isinstance(_options['DEFAULT_UA_TIMER'], str) and not _options['DEFAULT_UA_TIMER'].isdigit():
                    logger.debug('(OPTIONS) %s - DEFAULT_REFLECTOR is not an integer, ignoring',_system)
                    continue
                    
                _tmout = int(_options['DEFAULT_UA_TIMER'])
                
                if int(_options['DEFAULT_UA_TIMER']) != CONFIG['SYSTEMS'][_system]['DEFAULT_UA_TIMER']:
                    logger.debug('(OPTIONS) %s Updating DEFAULT_UA_TIMER for existing bridges.',_system)
                    remove_bridge_system(_system)
                    for _bridge in BRIDGES:
                        ts1 = False 
                        ts2 = False
                        for i,e in enumerate(BRIDGES[_bridge]):
                            if e['SYSTEM'] == _system and e['TS'] == 1:
                                ts1 = True
                            if e['SYSTEM'] == _system and e['TS'] == 2:
                                ts2 = True
                        if _bridge[0:1] != '#':
                            if ts1 == False:
                                BRIDGES[_bridge].append({'SYSTEM': _system, 'TS': 1, 'TGID': bytes_3(int(_bridge)),'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [bytes_3(int(_bridge)),],'RESET': [], 'TIMER': time()})
                            if ts2 == False:
                                BRIDGES[_bridge].append({'SYSTEM': _system, 'TS': 2, 'TGID': bytes_3(int(_bridge)),'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [bytes_3(int(_bridge)),],'RESET': [], 'TIMER': time()})
                        else:
                            if ts2 == False:
                                BRIDGES[_bridge].append({'SYSTEM': _system, 'TS': 2, 'TGID': bytes_3(9),'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [bytes_3(4000)],'ON': [],'RESET': [], 'TIMER': time()})
        
                if int(_options['DEFAULT_REFLECTOR']) != CONFIG['SYSTEMS'][_system]['DEFAULT_REFLECTOR']:
                    if int(_options['DEFAULT_REFLECTOR']) > 0:
                        logger.debug('(OPTIONS) %s default reflector changed, updating',_system) 
                        reset_default_reflector(CONFIG['SYSTEMS'][_system]['DEFAULT_REFLECTOR'],_tmout,_system)
                        make_default_reflector(int(_options['DEFAULT_REFLECTOR']),_tmout,_system)
                    else:
                        logger.debug('(OPTIONS) %s default reflector disabled, updating',_system) 
                        reset_default_reflector(int(_options['DEFAULT_REFLECTOR']),_tmout,_system)
                        
                if _options['TS1_STATIC'] != CONFIG['SYSTEMS'][_system]['TS1_STATIC']:
                    _tmout = int(_options['DEFAULT_UA_TIMER'])
                    logger.debug('(OPTIONS) %s TS1 static TGs changed, updating',_system)
                    ts1 = []
                    if CONFIG['SYSTEMS'][_system]['TS1_STATIC']:
                        ts1 = CONFIG['SYSTEMS'][_system]['TS1_STATIC'].split(',')
                        for tg in ts1:
                            if not tg:
                                continue
                            tg = int(tg)
                            reset_static_tg(tg,1,_tmout,_system)   
                    ts1 = []
                    if _options['TS1_STATIC']:
                        ts1 = _options['TS1_STATIC'].split(',')
                        for tg in ts1:
                            if not tg:
                                continue
                            tg = int(tg)
                            make_static_tg(tg,1,_tmout,_system)
                            
                if _options['TS2_STATIC'] != CONFIG['SYSTEMS'][_system]['TS2_STATIC']:
                    _tmout = int(_options['DEFAULT_UA_TIMER'])
                    logger.debug('(OPTIONS) %s TS2 static TGs changed, updating',_system)
                    ts2 = []
                    if CONFIG['SYSTEMS'][_system]['TS2_STATIC']:
                        ts2 = CONFIG['SYSTEMS'][_system]['TS2_STATIC'].split(',')
                        for tg in ts2:
                            if not tg:
                                continue
                            tg = int(tg)
                            reset_static_tg(tg,2,_tmout,_system)
                    ts2 = []
                    if _options['TS2_STATIC']:
                        ts2 = _options['TS2_STATIC'].split(',')
                        for tg in ts2:
                            if not tg:
                                continue
                            tg = int(tg)
                            make_static_tg(tg,2,_tmout,_system)
                
                CONFIG['SYSTEMS'][_system]['TS1_STATIC'] =  _options['TS1_STATIC']
                CONFIG['SYSTEMS'][_system]['TS2_STATIC'] = _options['TS2_STATIC']
                CONFIG['SYSTEMS'][_system]['DEFAULT_REFLECTOR'] = int(_options['DEFAULT_REFLECTOR'])
                CONFIG['SYSTEMS'][_system]['DEFAULT_UA_TIMER'] = int(_options['DEFAULT_UA_TIMER'])

def mysqlGetConfig():
    logger.debug('(MYSQL) Periodic config check')
    SQLGETCONFIG = {}
    if sql.con():
        logger.debug('(MYSQL) reading config from database')
        try:
            SQLGETCONFIG = sql.getConfig()
        except:
            logger.debug('(MYSQL) problem with SQL query, aborting')
            sql.close()
            return
    else:
        logger.debug('(MYSQL) problem connecting to SQL server, aborting')
        sql.close()
        return
    
    sql.close()
    reactor.callFromThread(mysql_config_check,SQLGETCONFIG)
    

def mysql_config_check(SQLGETCONFIG):

    SQLCONFIG = SQLGETCONFIG
    for system in SQLGETCONFIG:
        if system not in CONFIG['SYSTEMS']:
            if SQLCONFIG[system]['ENABLED']:
                logger.debug('(MYSQL) new enabled system %s, starting HBP listener',system)  
                CONFIG['SYSTEMS'][system] = SQLCONFIG[system]
                systems[system] = routerHBP(system, CONFIG, report_server)
                listeningPorts[system] = reactor.listenUDP(CONFIG['SYSTEMS'][system]['PORT'], systems[system], interface=CONFIG['SYSTEMS'][system]['IP'])
            else:
                logger.debug('(MYSQL) new disabled system %s',system) 
            _tmout = SQLCONFIG[system]['DEFAULT_UA_TIMER']
            #Do ACL processing
        # Subscriber and TGID ACLs
            logger.debug('(MYSQL) building ACLs')
            # Registration ACLs
            SQLCONFIG[system]['REG_ACL'] = acl_build(SQLCONFIG[system]['REG_ACL'], PEER_MAX)
            for acl in ['SUB_ACL', 'TG1_ACL', 'TG2_ACL']:
                SQLCONFIG[system][acl] = acl_build(SQLCONFIG[system][acl], ID_MAX)
            
            #Add system to bridges
            if SQLCONFIG[system]['ENABLED']:
                logger.debug('(MYSQL) adding new system to static bridges')
                for _bridge in BRIDGES:
                    ts1 = False 
                    ts2 = False
                    for i,e in enumerate(BRIDGES[_bridge]):
                        if e['SYSTEM'] == system and e['TS'] == 1:
                            ts1 = True
                        if e['SYSTEM'] == system and e['TS'] == 2:
                            ts2 = True
                    if _bridge[0:1] != '#':
                        if ts1 == False:
                            BRIDGES[_bridge].append({'SYSTEM': system, 'TS': 1, 'TGID': bytes_3(int(_bridge)),'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [bytes_3(int(_bridge)),],'RESET': [], 'TIMER': time()})
                        if ts2 == False:
                            BRIDGES[_bridge].append({'SYSTEM': system, 'TS': 2, 'TGID': bytes_3(int(_bridge)),'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [bytes_3(int(_bridge)),],'RESET': [], 'TIMER': time()})
                    else:
                        if ts2 == False:
                            BRIDGES[_bridge].append({'SYSTEM': system, 'TS': 2, 'TGID': bytes_3(9),'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [bytes_3(4000)],'ON': [],'RESET': [], 'TIMER': time()})
                
                if SQLCONFIG[system]['DEFAULT_REFLECTOR'] > 0:
                        logger.debug('(MYSQL) %s setting default reflector',system) 
                        make_default_reflector(SQLCONFIG[system]['DEFAULT_REFLECTOR'],_tmout,system)
                
                if SQLCONFIG[system]['TS1_STATIC']:
                    logger.debug('(MYSQL) %s setting static TGs on TS1',system) 
                    ts1 = SQLCONFIG[system]['TS1_STATIC'].split(',')
                    for tg in ts1:
                        if not tg:
                            continue
                        tg = int(tg)
                        make_static_tg(tg,1,_tmout,system)
                        
                if SQLCONFIG[system]['TS2_STATIC']:
                    logger.debug('(MYSQL) %s setting static TGs on TS2',system) 
                    ts2 = SQLCONFIG[system]['TS2_STATIC'].split(',')
                    for tg in ts2:
                        if not tg:
                            continue
                        tg = int(tg)
                        make_static_tg(tg,2,_tmout,system)
        
            continue
        
        #Preserve options line
        if 'OPTIONS' in CONFIG['SYSTEMS'][system]:
            SQLCONFIG[system]['OPTIONS'] = CONFIG['SYSTEMS'][system]['OPTIONS']
            SQLCONFIG[system]['TS1_STATIC'] = CONFIG['SYSTEMS'][system]['TS1_STATIC']
            SQLCONFIG[system]['TS2_STATIC'] = CONFIG['SYSTEMS'][system]['TS2_STATIC']
            SQLCONFIG[system]['DEFAULT_UA_TIMER'] = CONFIG['SYSTEMS'][system]['DEFAULT_UA_TIMER']
            SQLCONFIG[system]['DEFAULT_REFLECTOR'] = CONFIG['SYSTEMS'][system]['DEFAULT_REFLECTOR']
            
            #logger.debug('(MYSQL) %s has HBP Options line - skipping',system)
            #continue
            
        
        if SQLCONFIG[system]['ENABLED'] == False and CONFIG['SYSTEMS'][system]['ENABLED'] == True:
            logger.debug('(MYSQL) %s changed from enabled to disabled, killing HBP listener and removing from bridges',system)
            systems[system].master_dereg()
            if systems[system]._system_maintenance is not None and systems[system]._system_maintenance.running == True:
                systems[system]._system_maintenance.stop()
                systems[system]._system_maintenance = None
            remove_bridge_system(system)
            listeningPorts[system].stopListening()
            
        if CONFIG['SYSTEMS'][system]['ENABLED'] == False and SQLCONFIG[system]['ENABLED'] == True:
            logger.debug('(MYSQL) %s changed from disabled to enabled, starting HBP listener',system)
            systems[system] = routerHBP(system, CONFIG, report_server)
            listeningPorts[system] = reactor.listenUDP(CONFIG['SYSTEMS'][system]['PORT'], systems[system], interface=CONFIG['SYSTEMS'][system]['IP'])
            logger.debug('(GLOBAL) %s instance created: %s, %s', CONFIG['SYSTEMS'][system]['MODE'], system, systems[system])
            logger.debug('(MYSQL) adding new system to static bridges')
            _tmout = SQLCONFIG[system]['DEFAULT_UA_TIMER']
            for _bridge in BRIDGES:
                ts1 = False 
                ts2 = False
                for i,e in enumerate(BRIDGES[_bridge]):
                    if e['SYSTEM'] == system and e['TS'] == 1:
                        ts1 = True
                    if e['SYSTEM'] == system and e['TS'] == 2:
                        ts2 = True
                if _bridge[0:1] != '#':
                    if ts1 == False:
                        BRIDGES[_bridge].append({'SYSTEM': system, 'TS': 1, 'TGID': bytes_3(int(_bridge)),'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [bytes_3(int(_bridge)),],'RESET': [], 'TIMER': time()})
                    if ts2 == False:
                        BRIDGES[_bridge].append({'SYSTEM': system, 'TS': 2, 'TGID': bytes_3(int(_bridge)),'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [bytes_3(int(_bridge)),],'RESET': [], 'TIMER': time()})
                else:
                    if ts2 == False:
                        BRIDGES[_bridge].append({'SYSTEM': system, 'TS': 2, 'TGID': bytes_3(9),'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [bytes_3(4000)],'ON': [],'RESET': [], 'TIMER': time()})

            
            if SQLCONFIG[system]['DEFAULT_REFLECTOR'] > 0:
                if 'OPTIONS' not in SQLCONFIG[system]:
                    logger.debug('(MYSQL) %s setting default reflector',system) 
                    make_default_reflector(SQLCONFIG[system]['DEFAULT_REFLECTOR'],_tmout,system)
            
            if SQLCONFIG[system]['TS1_STATIC']:
                if 'OPTIONS' not in SQLCONFIG[system]:
                    logger.debug('(MYSQL) %s setting static TGs on TS1',system) 
                    ts1 = SQLCONFIG[system]['TS1_STATIC'].split(',')
                    for tg in ts1:
                        if not tg:
                            continue
                        tg = int(tg)
                        make_static_tg(tg,1,_tmout,system)
                        
                if SQLCONFIG[system]['TS2_STATIC']:
                    logger.debug('(MYSQL) %s setting static TGs on TS2',system) 
                    ts2 = SQLCONFIG[system]['TS2_STATIC'].split(',')
                    for tg in ts2:
                        if not tg:
                            continue
                        tg = int(tg)
                        make_static_tg(tg,2,_tmout,system)
                    
        if SQLCONFIG[system]['DEFAULT_UA_TIMER'] != CONFIG['SYSTEMS'][system]['DEFAULT_UA_TIMER']:
            if 'OPTIONS' not in CONFIG['SYSTEMS'][system]:
                logger.debug('(MYSQL) %s DEFAULT_UA_TIMER changed. Updating bridges.',system)
                remove_bridge_system(system)
                for _bridge in BRIDGES:
                    ts1 = False 
                    ts2 = False
                    _tmout = CONFIG['SYSTEMS'][system][DEFAULT_UA_TIMER]
                    for i,e in enumerate(BRIDGES[_bridge]):
                        if e['SYSTEM'] == system and e['TS'] == 1:
                            ts1 = True
                        if e['SYSTEM'] == system and e['TS'] == 2:
                            ts2 = True
                    if _bridge[0:1] != '#':
                        if ts1 == False:
                            BRIDGES[_bridge].append({'SYSTEM': system, 'TS': 1, 'TGID': bytes_3(int(_bridge)),'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [bytes_3(int(_bridge)),],'RESET': [], 'TIMER': time()})
                        if ts2 == False:
                            BRIDGES[_bridge].append({'SYSTEM': system, 'TS': 2, 'TGID': bytes_3(int(_bridge)),'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [],'ON': [bytes_3(int(_bridge)),],'RESET': [], 'TIMER': time()})
                    else:
                        if ts2 == False:
                            BRIDGES[_bridge].append({'SYSTEM': system, 'TS': 2, 'TGID': bytes_3(9),'ACTIVE': False,'TIMEOUT': _tmout * 60,'TO_TYPE': 'ON','OFF': [bytes_3(4000)],'ON': [],'RESET': [], 'TIMER': time()})

                
                if SQLCONFIG[system]['DEFAULT_REFLECTOR'] > 0:
                  #  if 'OPTIONS' not in SQLCONFIG[system]:
                    logger.debug('(MYSQL) %s setting default reflector',system) 
                    make_default_reflector(SQLCONFIG[system]['DEFAULT_REFLECTOR'],_tmout,system)
            
                if SQLCONFIG[system]['TS1_STATIC']:
                   # if 'OPTIONS' not in SQLCONFIG[system]:
                    logger.debug('(MYSQL) %s setting static TGs on TS1',system) 
                    ts1 = SQLCONFIG[system]['TS1_STATIC'].split(',')
                    for tg in ts1:
                        if not tg:
                            continue
                        tg = int(tg)
                        make_static_tg(tg,1,_tmout,system)
                            
                    if SQLCONFIG[system]['TS2_STATIC']:
                        logger.debug('(MYSQL) %s setting static TGs on TS2',system) 
                        ts2 = SQLCONFIG[system]['TS2_STATIC'].split(',')
                        for tg in ts2:
                            if not tg:
                                continue
                            tg = int(tg)
                            make_static_tg(tg,2,_tmout,system)
                

        
        if SQLCONFIG[system]['IP'] != CONFIG['SYSTEMS'][system]['IP'] and CONFIG['SYSTEMS'][system]['ENABLED'] == True:
            logger.debug('(MYSQL) %s IP binding changed on enabled system, killing HBP listener. Will restart in 1 minute',system)
            systems[system].master_dereg()
            if systems[system]._system_maintenance is not None and systems[system]._system_maintenance.running == True:
                systems[system]._system_maintenance.stop()
                systems[system]._system_maintenance = None
            listeningPorts[system].stopListening()
            SQLCONFIG[system]['ENABLED'] = False
            
        if SQLCONFIG[system]['PORT'] != CONFIG['SYSTEMS'][system]['PORT'] and CONFIG['SYSTEMS'][system]['ENABLED'] == True:
            logger.debug('(MYSQL) %s Port binding changed on enabled system, killing HBP listener. Will restart in 1 minute',system)
            systems[system].master_dereg()
            if systems[system]._system_maintenance is not None and systems[system]._system_maintenance.running == True:
                systems[system]._system_maintenance.stop()
                systems[system]._system_maintenance = None
            listeningPorts[system].stopListening()
            SQLCONFIG[system]['ENABLED'] = False
            
        if SQLCONFIG[system]['MAX_PEERS'] != CONFIG['SYSTEMS'][system]['MAX_PEERS'] and CONFIG['SYSTEMS'][system]['ENABLED'] == True:
            logger.debug('(MYSQL) %s MAX_PEERS changed on enabled system, killing HBP listener. Will restart in 1 minute',system)
            systems[system].master_dereg()
            if systems[system]._system_maintenance is not None and systems[system]._system_maintenance.running == True:
                systems[system]._system_maintenance.stop()
                systems[system]._system_maintenance = None
            listeningPorts[system].stopListening()
            SQLCONFIG[system]['ENABLED'] = False
            
        if SQLCONFIG[system]['PASSPHRASE'] != CONFIG['SYSTEMS'][system]['PASSPHRASE'] and CONFIG['SYSTEMS'][system]['ENABLED'] == True:
            logger.debug('(MYSQL) %s Passphrase changed on enabled system. Kicking peers',system)
            systems[system].master_dereg()
            
        if SQLCONFIG[system]['DEFAULT_REFLECTOR'] != CONFIG['SYSTEMS'][system]['DEFAULT_REFLECTOR']:
            if 'OPTIONS' not in SQLCONFIG[system]:
                _tmout = SQLCONFIG[system]['DEFAULT_UA_TIMER']
                if SQLCONFIG[system]['DEFAULT_REFLECTOR'] > 0:
                    logger.debug('(MYSQL) %s default reflector changed, updating',system) 
                    reset_default_reflector(CONFIG['SYSTEMS'][system]['DEFAULT_REFLECTOR'],_tmout,system)
                    make_default_reflector(SQLCONFIG[system]['DEFAULT_REFLECTOR'],_tmout,system)
                else:
                    logger.debug('(MYSQL) %s default reflector disabled, updating',system) 
                    reset_default_reflector(CONFIG['SYSTEMS'][system]['DEFAULT_REFLECTOR'],_tmout,system)
                
        if SQLCONFIG[system]['TS1_STATIC'] != CONFIG['SYSTEMS'][system]['TS1_STATIC']:
            if 'OPTIONS' not in CONFIG['SYSTEMS'][system]:
                _tmout = SQLCONFIG[system]['DEFAULT_UA_TIMER']
                logger.debug('(MYSQL) %s TS1 static TGs changed, updating',system)
                ts1 = []
                if CONFIG['SYSTEMS'][system]['TS1_STATIC']:
                    ts1 = CONFIG['SYSTEMS'][system]['TS1_STATIC'].split(',')
                    for tg in ts1:
                        if not tg:
                            continue
                        tg = int(tg)
                        reset_static_tg(tg,1,_tmout,system)   
                ts1 = []
                if SQLCONFIG[system]['TS1_STATIC']:
                    ts1 = SQLCONFIG[system]['TS1_STATIC'].split(',')
                    for tg in ts1:
                        if not tg:
                            continue
                        tg = int(tg)
                        make_static_tg(tg,1,_tmout,system)
                    
        if SQLCONFIG[system]['TS2_STATIC'] != CONFIG['SYSTEMS'][system]['TS2_STATIC']:
            if 'OPTIONS' not in CONFIG['SYSTEMS'][system]:
                _tmout = SQLCONFIG[system]['DEFAULT_UA_TIMER']
                logger.debug('(MYSQL) %s TS2 static TGs changed, updating',system)
                ts2 = []
                if CONFIG['SYSTEMS'][system]['TS2_STATIC']:
                    ts2 = CONFIG['SYSTEMS'][system]['TS2_STATIC'].split(',')
                    for tg in ts2:
                        if not tg:
                            continue
                        tg = int(tg)
                        reset_static_tg(tg,2,_tmout,system)
                ts2 = []
                if SQLCONFIG[system]['TS2_STATIC']:
                    ts2 = SQLCONFIG[system]['TS2_STATIC'].split(',')
                    for tg in ts2:
                        if not tg:
                            continue
                        tg = int(tg)
                        make_static_tg(tg,2,_tmout,system)
        
        #Rebuild ACLs
        SQLCONFIG[system]['REG_ACL'] = acl_build(SQLCONFIG[system]['REG_ACL'], PEER_MAX)
        SQLCONFIG[system]['SUB_ACL'] = acl_build(SQLCONFIG[system]['SUB_ACL'], ID_MAX)
        SQLCONFIG[system]['TG1_ACL'] = acl_build(SQLCONFIG[system]['TG1_ACL'], ID_MAX)
        SQLCONFIG[system]['TG2_ACL'] = acl_build(SQLCONFIG[system]['TG2_ACL'], ID_MAX)
        
        if SQLCONFIG[system]['REG_ACL'] != CONFIG['SYSTEMS'][system]['REG_ACL']:
            logger.debug('(MYSQL) registration ACL changed')
        if SQLCONFIG[system]['SUB_ACL'] != CONFIG['SYSTEMS'][system]['SUB_ACL']:
            logger.debug('(MYSQL) subscriber ACL changed')
        if SQLCONFIG[system]['TG1_ACL'] != CONFIG['SYSTEMS'][system]['TG1_ACL']:
            logger.debug('(MYSQL) TG1 ACL changed')
        if SQLCONFIG[system]['TG2_ACL'] != CONFIG['SYSTEMS'][system]['TG2_ACL']:
            logger.debug('(MYSQL) TG2 ACL changed')
            
        #Preserve peers list
        if system in CONFIG['SYSTEMS'] and CONFIG['SYSTEMS'][system]['ENABLED'] and 'PEERS' in CONFIG['SYSTEMS'][system] :
            SQLCONFIG[system]['PEERS'] = CONFIG['SYSTEMS'][system]['PEERS']
            CONFIG['SYSTEMS'][system].update(SQLCONFIG[system])
        else:
            CONFIG['SYSTEMS'][system].update(SQLCONFIG[system]) 
        
                
    #Add MySQL config data to config dict
    #CONFIG['SYSTEMS'].update(SQLCONFIG)
   
    SQLCONFIG = {} 

class routerOBP(OPENBRIDGE):

    def __init__(self, _name, _config, _report):
        OPENBRIDGE.__init__(self, _name, _config, _report)
        self.STATUS = {}
        
        #Store last sequence number
        self._lastSeq = False
        
    def to_target(self, _peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data, pkt_time, dmrpkt, _bits,_bridge,_system,_noOBP,sysIgnore):
        _sysIgnore = sysIgnore
        for _target in BRIDGES[_bridge]:
            if (_target['SYSTEM'] != self._system) and (_target['ACTIVE']):
                _target_status = systems[_target['SYSTEM']].STATUS
                _target_system = self._CONFIG['SYSTEMS'][_target['SYSTEM']]
                if (_target['SYSTEM'],_target['TS']) in _sysIgnore:
                    #logger.debug("(DEDUP) OBP Source Skipping system %s TS: %s",_target['SYSTEM'],_target['TS'])
                    continue
                if _target_system['MODE'] == 'OPENBRIDGE':
                    if _noOBP == True:
                        continue
                    #We want to ignore this system and TS combination if it's called again for this packet
                    _sysIgnore.append((_target['SYSTEM'],_target['TS']))
        
                    # Is this a new call stream on the target?
                    if (_stream_id not in _target_status):
                        # This is a new call stream on the target
                        _target_status[_stream_id] = {
                            'START':     pkt_time,
                            'CONTENTION':False,
                            'RFS':       _rf_src,
                            'TGID':      _dst_id,
                        }
                        # Generate LCs (full and EMB) for the TX stream
                        try:
                            dst_lc = b''.join([self.STATUS[_stream_id]['LC'][0:3], _target['TGID'], _rf_src])
                        except:
                            return
                        _target_status[_stream_id]['H_LC'] = bptc.encode_header_lc(dst_lc)
                        _target_status[_stream_id]['T_LC'] = bptc.encode_terminator_lc(dst_lc)
                        _target_status[_stream_id]['EMB_LC'] = bptc.encode_emblc(dst_lc)

                        logger.info('(%s) Conference Bridge: %s, Call Bridged to OBP System: %s TS: %s, TGID: %s', self._system, _bridge, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))
                        if CONFIG['REPORTS']['REPORT']:
                            systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,START,TX,{},{},{},{},{},{}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID'])).encode(encoding='utf-8', errors='ignore'))

                    # Record the time of this packet so we can later identify a stale stream
                    _target_status[_stream_id]['LAST'] = pkt_time
                    # Clear the TS bit -- all OpenBridge streams are effectively on TS1
                    _tmp_bits = _bits & ~(1 << 7)

                    # Assemble transmit HBP packet header
                    _tmp_data = b''.join([_data[:8], _target['TGID'], _data[11:15], _tmp_bits.to_bytes(1, 'big'), _data[16:20]])

                    # MUST TEST FOR NEW STREAM AND IF SO, RE-WRITE THE LC FOR THE TARGET
                    # MUST RE-WRITE DESTINATION TGID IF DIFFERENT
                    # if _dst_id != rule['DST_GROUP']:
                    dmrbits = bitarray(endian='big')
                    dmrbits.frombytes(dmrpkt)
                    # Create a voice header packet (FULL LC)
                    if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                        dmrbits = _target_status[_stream_id]['H_LC'][0:98] + dmrbits[98:166] + _target_status[_stream_id]['H_LC'][98:197]
                    # Create a voice terminator packet (FULL LC)
                    elif _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VTERM:
                        dmrbits = _target_status[_stream_id]['T_LC'][0:98] + dmrbits[98:166] + _target_status[_stream_id]['T_LC'][98:197]
                        if CONFIG['REPORTS']['REPORT']:
                            call_duration = pkt_time - _target_status[_stream_id]['START']
                            systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID']), call_duration).encode(encoding='utf-8', errors='ignore'))
                    # Create a Burst B-E packet (Embedded LC)
                    elif _dtype_vseq in [1,2,3,4]:
                        dmrbits = dmrbits[0:116] + _target_status[_stream_id]['EMB_LC'][_dtype_vseq] + dmrbits[148:264]
                    dmrpkt = dmrbits.tobytes()
                    _tmp_data = b''.join([_tmp_data, dmrpkt])

                else:
                    # BEGIN CONTENTION HANDLING
                    #
                    # The rules for each of the 4 "ifs" below are listed here for readability. The Frame To Send is:
                    #   From a different group than last RX from this HBSystem, but it has been less than Group Hangtime
                    #   From a different group than last TX to this HBSystem, but it has been less than Group Hangtime
                    #   From the same group as the last RX from this HBSystem, but from a different subscriber, and it has been less than stream timeout
                    #   From the same group as the last TX to this HBSystem, but from a different subscriber, and it has been less than stream timeout
                    # The "continue" at the end of each means the next iteration of the for loop that tests for matching rules
                    #
                    if ((_target['TGID'] != _target_status[_target['TS']]['RX_TGID']) and ((pkt_time - _target_status[_target['TS']]['RX_TIME']) < _target_system['GROUP_HANGTIME'])):
                        if self.STATUS[_stream_id]['CONTENTION'] == False:
                            self.STATUS[_stream_id]['CONTENTION'] = True
                            logger.info('(%s) Call not routed to TGID %s, target active or in group hangtime: HBSystem: %s, TS: %s, TGID: %s', self._system, int_id(_target['TGID']), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['RX_TGID']))
                        continue
                    if ((_target['TGID'] != _target_status[_target['TS']]['TX_TGID']) and ((pkt_time - _target_status[_target['TS']]['TX_TIME']) < _target_system['GROUP_HANGTIME'])):
                        if self.STATUS[_stream_id]['CONTENTION'] == False:
                            self.STATUS[_stream_id]['CONTENTION'] = True
                            logger.info('(%s) Call not routed to TGID%s, target in group hangtime: HBSystem: %s, TS: %s, TGID: %s', self._system, int_id(_target['TGID']), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['TX_TGID']))
                        continue
                    if (_target['TGID'] == _target_status[_target['TS']]['RX_TGID']) and ((pkt_time - _target_status[_target['TS']]['RX_TIME']) < STREAM_TO):
                        if self.STATUS[_stream_id]['CONTENTION'] == False:
                            self.STATUS[_stream_id]['CONTENTION'] = True
                            logger.info('(%s) Call not routed to TGID%s, matching call already active on target: HBSystem: %s, TS: %s, TGID: %s', self._system, int_id(_target['TGID']), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['RX_TGID']))
                        continue
                    if (_target['TGID'] == _target_status[_target['TS']]['TX_TGID']) and (_rf_src != _target_status[_target['TS']]['TX_RFS']) and ((pkt_time - _target_status[_target['TS']]['TX_TIME']) < STREAM_TO):
                        if self.STATUS[_stream_id]['CONTENTION'] == False:
                            self.STATUS[_stream_id]['CONTENTION'] = True
                            logger.info('(%s) Call not routed for subscriber %s, call route in progress on target: HBSystem: %s, TS: %s, TGID: %s, SUB: %s', self._system, int_id(_rf_src), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['TX_TGID']), int_id(_target_status[_target['TS']]['TX_RFS']))
                        continue

                    # Is this a new call stream?
                    if (_target_status[_target['TS']]['TX_STREAM_ID'] != _stream_id):
                        # Record the DST TGID and Stream ID
                        _target_status[_target['TS']]['TX_START'] = pkt_time
                        _target_status[_target['TS']]['TX_TGID'] = _target['TGID']
                        _target_status[_target['TS']]['TX_STREAM_ID'] = _stream_id
                        _target_status[_target['TS']]['TX_RFS'] = _rf_src
                        _target_status[_target['TS']]['TX_PEER'] = _peer_id
                        # Generate LCs (full and EMB) for the TX stream
                        dst_lc = b''.join([self.STATUS[_stream_id]['LC'][0:3], _target['TGID'], _rf_src])
                        _target_status[_target['TS']]['TX_H_LC'] = bptc.encode_header_lc(dst_lc)
                        _target_status[_target['TS']]['TX_T_LC'] = bptc.encode_terminator_lc(dst_lc)
                        _target_status[_target['TS']]['TX_EMB_LC'] = bptc.encode_emblc(dst_lc)
                        logger.debug('(%s) Generating TX FULL and EMB LCs for HomeBrew destination: System: %s, TS: %s, TGID: %s', self._system, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))
                        logger.info('(%s) Conference Bridge: %s, Call Bridged to HBP System: %s TS: %s, TGID: %s', self._system, _bridge, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))
                        if CONFIG['REPORTS']['REPORT']:
                            systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,START,TX,{},{},{},{},{},{}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID'])).encode(encoding='utf-8', errors='ignore'))

                    # Set other values for the contention handler to test next time there is a frame to forward
                    _target_status[_target['TS']]['TX_TIME'] = pkt_time
                    _target_status[_target['TS']]['TX_TYPE'] = _dtype_vseq

                    # Handle any necessary re-writes for the destination
                    if _system['TS'] != _target['TS']:
                        _tmp_bits = _bits ^ 1 << 7
                    else:
                        _tmp_bits = _bits

                    # Assemble transmit HBP packet header
                    _tmp_data = b''.join([_data[:8], _target['TGID'], _data[11:15], _tmp_bits.to_bytes(1, 'big'), _data[16:20]])

                    # MUST TEST FOR NEW STREAM AND IF SO, RE-WRITE THE LC FOR THE TARGET
                    # MUST RE-WRITE DESTINATION TGID IF DIFFERENT
                    # if _dst_id != rule['DST_GROUP']:
                    dmrbits = bitarray(endian='big')
                    dmrbits.frombytes(dmrpkt)
                    # Create a voice header packet (FULL LC)
                    if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                        dmrbits = _target_status[_target['TS']]['TX_H_LC'][0:98] + dmrbits[98:166] + _target_status[_target['TS']]['TX_H_LC'][98:197]
                    # Create a voice terminator packet (FULL LC)
                    elif _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VTERM:
                        dmrbits = _target_status[_target['TS']]['TX_T_LC'][0:98] + dmrbits[98:166] + _target_status[_target['TS']]['TX_T_LC'][98:197]
                        if CONFIG['REPORTS']['REPORT']:
                            call_duration = pkt_time - _target_status[_target['TS']]['TX_START']
                            systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID']), call_duration).encode(encoding='utf-8', errors='ignore'))
                    # Create a Burst B-E packet (Embedded LC)
                    elif _dtype_vseq in [1,2,3,4]:
                        dmrbits = dmrbits[0:116] + _target_status[_target['TS']]['TX_EMB_LC'][_dtype_vseq] + dmrbits[148:264]
                    dmrpkt = dmrbits.tobytes()
                    _tmp_data = b''.join([_tmp_data, dmrpkt, b'\x00\x00']) # Add two bytes of nothing since OBP doesn't include BER & RSSI bytes #_data[53:55]

                # Transmit the packet to the destination system
                systems[_target['SYSTEM']].send_system(_tmp_data)
                    #logger.debug('(%s) Packet routed by bridge: %s to system: %s TS: %s, TGID: %s', self._system, _bridge, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))
                #Ignore this system and TS pair if it's called again on this packet
        return(_sysIgnore)


    def dmrd_received(self, _peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data):
        pkt_time = time()
        dmrpkt = _data[20:53]
        _bits = _data[15]
        
        #Handle inbound duplicates
        if _seq == True and _seq == self._lastSeq:
            logger.debug("%s) Duplicate sequence number %s, disgarding",self._system,_seq)
            return
        #Inbound out-of-order packets
        elif _seq == True and (_seq != 1) and (_seq < self._lastSeq):
            logger.debug("%s) Out of order packet - last sequence number %s, this sequence number %s,  disgarding",self._system,self._lastSeq,_seq)
            return
        #Inbound missed packets
        elif _seq == True and _seq > (self._lastSeq+1):
             logger.debug("(%s) Missed packet - last sequence number %s, this sequence number %s",self._system,self._lastSeq,_seq)
    

        if _call_type == 'group':
            # Is this a new call stream?
            if (_stream_id not in self.STATUS):
                
                # This is a new call stream
                self.STATUS[_stream_id] = {
                    'START':     pkt_time,
                    'CONTENTION':False,
                    'RFS':       _rf_src,
                    'TGID':      _dst_id,
                    '1ST': True
                }

                # If we can, use the LC from the voice header as to keep all options intact
                if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                    decoded = decode.voice_head_term(dmrpkt)
                    self.STATUS[_stream_id]['LC'] = decoded['LC']

                # If we don't have a voice header then don't wait to decode the Embedded LC
                # just make a new one from the HBP header. This is good enough, and it saves lots of time
                else:
                    self.STATUS[_stream_id]['LC'] = LC_OPT + _dst_id + _rf_src


                logger.info('(%s) *CALL START* STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s', \
                        self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot)
                if CONFIG['REPORTS']['REPORT']:
                    self._report.send_bridgeEvent('GROUP VOICE,START,RX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))


            else:
                
                if '_fin' in self.STATUS[_stream_id]:
                   logger.warning("(%s) OBP *LoopControl* STREAM ID: %s ALREADY FINISHED FROM THIS SOURCE, IGNORING",self._system, int_id(_stream_id))
                   return
               
               # Loop Control

                for system in systems:                            
                    if system  == self._system:
                        continue
                    if CONFIG['SYSTEMS'][system]['MODE'] != 'OPENBRIDGE':
                        for _sysslot in systems[system].STATUS:
                            if 'RX_STREAM_ID' in systems[system].STATUS[_sysslot] and _stream_id == systems[system].STATUS[_sysslot]['RX_STREAM_ID']:
                                if 'LOOPLOG' not in self.STATUS[_stream_id] or not self.STATUS[_stream_id]['LOOPLOG']: 
                                    logger.warning("(%s) OBP *LoopControl* FIRST HBP: %s, STREAM ID: %s, TG: %s, TS: %s, IGNORE THIS SOURCE",self._system, system, int_id(_stream_id), int_id(_dst_id),_sysslot)
                                    self.STATUS[_stream_id]['LOOPLOG'] = True
                                self.STATUS[_stream_id]['LAST'] = pkt_time
                                return
                    else:
                        #if _stream_id in systems[system].STATUS and systems[system].STATUS[_stream_id]['START'] <= self.STATUS[_stream_id]['START']:
                        if _stream_id in systems[system].STATUS and '1ST' in systems[system].STATUS[_stream_id] and systems[system].STATUS[_stream_id]['TGID'] == _dst_id:
                            if 'LOOPLOG' not in self.STATUS[_stream_id] or not self.STATUS[_stream_id]['LOOPLOG']:
                                logger.warning("(%s) OBP *LoopControl* FIRST OBP %s, STREAM ID: %s, TG %s, IGNORE THIS SOURCE",self._system, system, int_id(_stream_id), int_id(_dst_id))
                                self.STATUS[_stream_id]['LOOPLOG'] = True
                            self.STATUS[_stream_id]['LAST'] = pkt_time
                            return


            self.STATUS[_stream_id]['LAST'] = pkt_time
            
            #Save this sequence number 
            self._lastSeq = _seq
            
            #Create STAT bridge for unknown TG
            if CONFIG['GLOBAL']['GEN_STAT_BRIDGES']:
                if int_id(_dst_id) >= 5 and int_id(_dst_id) != 9 and (str(int_id(_dst_id)) not in BRIDGES):
                    logger.info('(%s) Bridge for STAT TG %s does not exist. Creating',self._system, int_id(_dst_id))
                    make_stat_bridge(_dst_id)
            
            _sysIgnore = []
            for _bridge in BRIDGES:
                #if _bridge[0:1] != '#':
                #if True:
                    for _system in BRIDGES[_bridge]:
                        if _system['SYSTEM'] == self._system and _system['TGID'] == _dst_id and _system['TS'] == _slot and _system['ACTIVE'] == True:
                            _sysIgnore = self.to_target(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data, pkt_time, dmrpkt, _bits,_bridge,_system,False,_sysIgnore)


            # Final actions - Is this a voice terminator?
            if (_frame_type == HBPF_DATA_SYNC) and (_dtype_vseq == HBPF_SLT_VTERM):
                call_duration = pkt_time - self.STATUS[_stream_id]['START']
                logger.info('(%s) *CALL END*   STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s, Duration: %.2f', \
                        self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot, call_duration)
                if CONFIG['REPORTS']['REPORT']:
                   self._report.send_bridgeEvent('GROUP VOICE,END,RX,{},{},{},{},{},{},{:.2f}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id), call_duration).encode(encoding='utf-8', errors='ignore'))
                   self.STATUS[_stream_id]['_fin'] = True
                #removed = self.STATUS.pop(_stream_id)
                #logger.debug('(%s) OpenBridge sourced call stream end, remove terminated Stream ID: %s', self._system, int_id(_stream_id))
                #if not removed:
                    #selflogger.error('(%s) *CALL END*   STREAM ID: %s NOT IN LIST -- THIS IS A REAL PROBLEM', self._system, int_id(_stream_id))
                
                #Reset sequence number 
                self._lastSeq = False

class routerHBP(HBSYSTEM):

    def __init__(self, _name, _config, _report):
        HBSYSTEM.__init__(self, _name, _config, _report)
        # Status information for the system, TS1 & TS2
        # 1 & 2 are "timeslot"
        # In TX_EMB_LC, 2-5 are burst B-E
        self.STATUS = {
            1: {
                'RX_START':     time(),
                'TX_START':     time(),
                'RX_SEQ':       0,
                'RX_RFS':       b'\x00',
                'TX_RFS':       b'\x00',
                'RX_PEER':      b'\x00',
                'TX_PEER':      b'\x00',
                'RX_STREAM_ID': b'\x00',
                'TX_STREAM_ID': b'\x00',
                'RX_TGID':      b'\x00\x00\x00',
                'TX_TGID':      b'\x00\x00\x00',
                'RX_TIME':      time(),
                'TX_TIME':      time(),
                'RX_TYPE':      HBPF_SLT_VTERM,
                'TX_TYPE':      HBPF_SLT_VTERM,
                'RX_LC':        b'\x00',
                'TX_H_LC':      b'\x00',
                'TX_T_LC':      b'\x00',
                'TX_EMB_LC': {
                    1: b'\x00',
                    2: b'\x00',
                    3: b'\x00',
                    4: b'\x00',
                    }
                },
            2: {
                'RX_START':     time(),
                'TX_START':     time(),
                'RX_SEQ':       0,
                'RX_RFS':       b'\x00',
                'TX_RFS':       b'\x00',
                'RX_PEER':      b'\x00',
                'TX_PEER':      b'\x00',
                'RX_STREAM_ID': b'\x00',
                'TX_STREAM_ID': b'\x00',
                'RX_TGID':      b'\x00\x00\x00',
                'TX_TGID':      b'\x00\x00\x00',
                'RX_TIME':      time(),
                'TX_TIME':      time(),
                'RX_TYPE':      HBPF_SLT_VTERM,
                'TX_TYPE':      HBPF_SLT_VTERM,
                'RX_LC':        b'\x00',
                'TX_H_LC':      b'\x00',
                'TX_T_LC':      b'\x00',
                'TX_EMB_LC': {
                    1: b'\x00',
                    2: b'\x00',
                    3: b'\x00',
                    4: b'\x00',
                    }
                }
            }

    def to_target(self, _peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data, pkt_time, dmrpkt, _bits,_bridge,_system,_noOBP,sysIgnore):
        _sysIgnore = sysIgnore
        for _target in BRIDGES[_bridge]:
            #if _target['SYSTEM'] != self._system or (_target['SYSTEM'] == self._system and _target['TS'] != _slot):
            if _target['SYSTEM'] != self._system and _target['ACTIVE']:
                #if _target['ACTIVE']:
                    _target_status = systems[_target['SYSTEM']].STATUS
                    _target_system = self._CONFIG['SYSTEMS'][_target['SYSTEM']]

                    if (_target['SYSTEM'],_target['TS']) in _sysIgnore:
                        #logger.debug("(DEDUP) HBP Source - Skipping system %s TS: %s",_target['SYSTEM'],_target['TS'])
                        continue
                    if _target_system['MODE'] == 'OPENBRIDGE':
                        if _noOBP == True:
                            continue
                        #We want to ignore this system and TS combination if it's called again for this packet
                        _sysIgnore.append((_target['SYSTEM'],_target['TS']))
        
                        # Is this a new call stream on the target?
                        if (_stream_id not in _target_status):
                            # This is a new call stream on the target
                            _target_status[_stream_id] = {
                                'START':     pkt_time,
                                'CONTENTION':False,
                                'RFS':       _rf_src,
                                'TGID':      _dst_id,
                            }
                            # Generate LCs (full and EMB) for the TX stream
                            dst_lc = b''.join([self.STATUS[_slot]['RX_LC'][0:3], _target['TGID'], _rf_src])
                            _target_status[_stream_id]['H_LC'] = bptc.encode_header_lc(dst_lc)
                            _target_status[_stream_id]['T_LC'] = bptc.encode_terminator_lc(dst_lc)
                            _target_status[_stream_id]['EMB_LC'] = bptc.encode_emblc(dst_lc)

                            logger.info('(%s) Conference Bridge: %s, Call Bridged to OBP System: %s TS: %s, TGID: %s', self._system, _bridge, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))
                            if CONFIG['REPORTS']['REPORT']:
                                systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,START,TX,{},{},{},{},{},{}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID'])).encode(encoding='utf-8', errors='ignore'))
                            
                        # Record the time of this packet so we can later identify a stale stream
                        _target_status[_stream_id]['LAST'] = pkt_time
                        # Clear the TS bit -- all OpenBridge streams are effectively on TS1
                        _tmp_bits = _bits & ~(1 << 7)

                        # Assemble transmit HBP packet header
                        _tmp_data = b''.join([_data[:8], _target['TGID'], _data[11:15], _tmp_bits.to_bytes(1, 'big'), _data[16:20]])

                        # MUST TEST FOR NEW STREAM AND IF SO, RE-WRITE THE LC FOR THE TARGET
                        # MUST RE-WRITE DESTINATION TGID IF DIFFERENT
                        # if _dst_id != rule['DST_GROUP']:
                        dmrbits = bitarray(endian='big')
                        dmrbits.frombytes(dmrpkt)
                        # Create a voice header packet (FULL LC)
                        if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                            dmrbits = _target_status[_stream_id]['H_LC'][0:98] + dmrbits[98:166] + _target_status[_stream_id]['H_LC'][98:197]
                        # Create a voice terminator packet (FULL LC)
                        elif _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VTERM:
                            dmrbits = _target_status[_stream_id]['T_LC'][0:98] + dmrbits[98:166] + _target_status[_stream_id]['T_LC'][98:197]
                            if CONFIG['REPORTS']['REPORT']:
                                call_duration = pkt_time - _target_status[_stream_id]['START']
                                systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID']), call_duration).encode(encoding='utf-8', errors='ignore'))
                        # Create a Burst B-E packet (Embedded LC)
                        elif _dtype_vseq in [1,2,3,4]:
                            dmrbits = dmrbits[0:116] + _target_status[_stream_id]['EMB_LC'][_dtype_vseq] + dmrbits[148:264]
                        dmrpkt = dmrbits.tobytes()
                        _tmp_data = b''.join([_tmp_data, dmrpkt])

                    else:
                        # BEGIN STANDARD CONTENTION HANDLING
                        #
                        # The rules for each of the 4 "ifs" below are listed here for readability. The Frame To Send is:
                        #   From a different group than last RX from this HBSystem, but it has been less than Group Hangtime
                        #   From a different group than last TX to this HBSystem, but it has been less than Group Hangtime
                        #   From the same group as the last RX from this HBSystem, but from a different subscriber, and it has been less than stream timeout
                        #   From the same group as the last TX to this HBSystem, but from a different subscriber, and it has been less than stream timeout
                        # The "continue" at the end of each means the next iteration of the for loop that tests for matching rules
                        #
                        if ((_target['TGID'] != _target_status[_target['TS']]['RX_TGID']) and ((pkt_time - _target_status[_target['TS']]['RX_TIME']) < _target_system['GROUP_HANGTIME'])):
                            if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD and self.STATUS[_slot]['RX_STREAM_ID'] != _stream_id:
                                logger.info('(%s) Call not routed to TGID %s, target active or in group hangtime: HBSystem: %s, TS: %s, TGID: %s', self._system, int_id(_target['TGID']), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['RX_TGID']))
                            continue
                        if ((_target['TGID'] != _target_status[_target['TS']]['TX_TGID']) and ((pkt_time - _target_status[_target['TS']]['TX_TIME']) < _target_system['GROUP_HANGTIME'])):
                            if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD and self.STATUS[_slot]['RX_STREAM_ID'] != _stream_id:
                                logger.info('(%s) Call not routed to TGID%s, target in group hangtime: HBSystem: %s, TS: %s, TGID: %s', self._system, int_id(_target['TGID']), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['TX_TGID']))
                            continue
                        if (_target['TGID'] == _target_status[_target['TS']]['RX_TGID']) and ((pkt_time - _target_status[_target['TS']]['RX_TIME']) < STREAM_TO):
                            if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD and self.STATUS[_slot]['RX_STREAM_ID'] != _stream_id:
                                logger.info('(%s) Call not routed to TGID%s, matching call already active on target: HBSystem: %s, TS: %s, TGID: %s', self._system, int_id(_target['TGID']), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['RX_TGID']))
                            continue
                        if (_target['TGID'] == _target_status[_target['TS']]['TX_TGID']) and (_rf_src != _target_status[_target['TS']]['TX_RFS']) and ((pkt_time - _target_status[_target['TS']]['TX_TIME']) < STREAM_TO):
                            if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD and self.STATUS[_slot]['RX_STREAM_ID'] != _stream_id:
                                logger.info('(%s) Call not routed for subscriber %s, call route in progress on target: HBSystem: %s, TS: %s, TGID: %s, SUB: %s', self._system, int_id(_rf_src), _target['SYSTEM'], _target['TS'], int_id(_target_status[_target['TS']]['TX_TGID']), int_id(_target_status[_target['TS']]['TX_RFS']))
                            continue

                        # Is this a new call stream?
                        if (_stream_id != self.STATUS[_slot]['RX_STREAM_ID']):
                                # Record the DST TGID and Stream ID
                                _target_status[_target['TS']]['TX_START'] = pkt_time
                                _target_status[_target['TS']]['TX_TGID'] = _target['TGID']
                                _target_status[_target['TS']]['TX_STREAM_ID'] = _stream_id
                                _target_status[_target['TS']]['TX_RFS'] = _rf_src
                                _target_status[_target['TS']]['TX_PEER'] = _peer_id
                                # Generate LCs (full and EMB) for the TX stream
                                dst_lc = self.STATUS[_slot]['RX_LC'][0:3] + _target['TGID'] + _rf_src
                                _target_status[_target['TS']]['TX_H_LC'] = bptc.encode_header_lc(dst_lc)
                                _target_status[_target['TS']]['TX_T_LC'] = bptc.encode_terminator_lc(dst_lc)
                                _target_status[_target['TS']]['TX_EMB_LC'] = bptc.encode_emblc(dst_lc)
                                logger.debug('(%s) Generating TX FULL and EMB LCs for HomeBrew destination: System: %s, TS: %s, TGID: %s', self._system, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))
                                logger.info('(%s) Conference Bridge: %s, Call Bridged to HBP System: %s TS: %s, TGID: %s', self._system, _bridge, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))
                                if CONFIG['REPORTS']['REPORT']:
                                    systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,START,TX,{},{},{},{},{},{}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID'])).encode(encoding='utf-8', errors='ignore'))

                        # Set other values for the contention handler to test next time there is a frame to forward
                        _target_status[_target['TS']]['TX_TIME'] = pkt_time
                        _target_status[_target['TS']]['TX_TYPE'] = _dtype_vseq

                        # Handle any necessary re-writes for the destination
                        if _system['TS'] != _target['TS']:
                            _tmp_bits = _bits ^ 1 << 7
                        else:
                            _tmp_bits = _bits

                        # Assemble transmit HBP packet header
                        _tmp_data = b''.join([_data[:8], _target['TGID'], _data[11:15], _tmp_bits.to_bytes(1, 'big'), _data[16:20]])

                        # MUST TEST FOR NEW STREAM AND IF SO, RE-WRITE THE LC FOR THE TARGET
                        # MUST RE-WRITE DESTINATION TGID IF DIFFERENT
                        # if _dst_id != rule['DST_GROUP']:
                        dmrbits = bitarray(endian='big')
                        dmrbits.frombytes(dmrpkt)
                        # Create a voice header packet (FULL LC)
                        if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                            dmrbits = _target_status[_target['TS']]['TX_H_LC'][0:98] + dmrbits[98:166] + _target_status[_target['TS']]['TX_H_LC'][98:197]
                        # Create a voice terminator packet (FULL LC)
                        elif _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VTERM:
                            dmrbits = _target_status[_target['TS']]['TX_T_LC'][0:98] + dmrbits[98:166] + _target_status[_target['TS']]['TX_T_LC'][98:197]
                            if CONFIG['REPORTS']['REPORT']:
                                call_duration = pkt_time - _target_status[_target['TS']]['TX_START']
                                systems[_target['SYSTEM']]._report.send_bridgeEvent('GROUP VOICE,END,TX,{},{},{},{},{},{},{:.2f}'.format(_target['SYSTEM'], int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _target['TS'], int_id(_target['TGID']), call_duration).encode(encoding='utf-8', errors='ignore'))
                        # Create a Burst B-E packet (Embedded LC)
                        elif _dtype_vseq in [1,2,3,4]:
                            dmrbits = dmrbits[0:116] + _target_status[_target['TS']]['TX_EMB_LC'][_dtype_vseq] + dmrbits[148:264]
                        try:
                            dmrpkt = dmrbits.tobytes()
                        except AttributeError:
                            logger.debug('(%s) Non-fatal AttributeError - dmrbits.tobytes()',self._system)
                            
                        _tmp_data = b''.join([_tmp_data, dmrpkt, _data[53:55]])

                    # Transmit the packet to the destination system
                    systems[_target['SYSTEM']].send_system(_tmp_data)
                    #logger.debug('(%s) Packet routed by bridge: %s to system: %s TS: %s, TGID: %s', self._system, _bridge, _target['SYSTEM'], _target['TS'], int_id(_target['TGID']))        
        return _sysIgnore

    def dmrd_received(self, _peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data):
        pkt_time = time()
        dmrpkt = _data[20:53]
        _bits = _data[15]
        
        _nine = bytes_3(9)
        
        _int_dst_id = int_id(_dst_id)
        
        #Handle private calls (for reflectors)
        if _call_type == 'unit' and _slot == 2:
            if (_stream_id != self.STATUS[_slot]['RX_STREAM_ID']):
                logger.warning('(%s) Reflector: Private call from %s to %s',self._system, int_id(_rf_src), _int_dst_id)
                #if _int_dst_id >= 4000 and _int_dst_id <= 5000:
                if _int_dst_id >= 5 and _int_dst_id <= 999999:
                    _bridgename = '#'+ str(_int_dst_id)
                    if _bridgename not in BRIDGES and not (_int_dst_id >= 4000 and _int_dst_id <= 5000):
                            logger.info('(%s) [A] Reflector for TG %s does not exist. Creating as User Activated. Timeout: %s',self._system, _int_dst_id,CONFIG['SYSTEMS'][self._system]['DEFAULT_UA_TIMER'])
                            make_single_reflector(_dst_id,CONFIG['SYSTEMS'][self._system]['DEFAULT_UA_TIMER'],self._system)
                    
                    if _int_dst_id > 5 and _int_dst_id != 9 and _int_dst_id != 5000:
                        for _bridge in BRIDGES:
                            if _bridge[0:1] != '#':
                                continue
                            for _system in BRIDGES[_bridge]:
                                _dehash_bridge = _bridge[1:]
                                if _system['SYSTEM'] == self._system:
                                    # TGID matches a rule source, reset its timer
                                    if _slot == _system['TS'] and _dst_id == _system['TGID'] and ((_system['TO_TYPE'] == 'ON' and (_system['ACTIVE'] == True)) or (_system['TO_TYPE'] == 'OFF' and _system['ACTIVE'] == False)):
                                        _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                        logger.info('(%s) [B] Transmission match for Reflector: %s. Reset timeout to %s', self._system, _bridge, _system['TIMER'])
                            
                                # TGID matches an ACTIVATION trigger
                                if _int_dst_id == int(_dehash_bridge) and _system['SYSTEM'] == self._system and  _slot == _system['TS']:
                                    # Set the matching rule as ACTIVE
                                    if _system['ACTIVE'] == False:
                                        _system['ACTIVE'] = True
                                        _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                        logger.info('(%s) [C] Reflector: %s, connection changed to state: %s', self._system, _bridge, _system['ACTIVE'])
                                        # Cancel the timer if we've enabled an "OFF" type timeout
                                        if _system['TO_TYPE'] == 'OFF':
                                            _system['TIMER'] = pkt_time
                                            logger.info('(%s) [D] Reflector: %s has an "OFF" timer and set to "ON": timeout timer cancelled', self._system, _bridge)
                                # Reset the timer for the rule
                                if _system['ACTIVE'] == True and _system['TO_TYPE'] == 'ON':
                                    _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                    logger.info('(%s) [E] Reflector: %s, timeout timer reset to: %s', self._system, _bridge, _system['TIMER'] - pkt_time)

                                # TGID matches an DE-ACTIVATION trigger
                                #Single TG mode
                                if (_dst_id in _system['OFF']  or _dst_id in _system['RESET'] or (_int_dst_id != int(_dehash_bridge)) and _system['SYSTEM'] == self._system and _slot == _system['TS']):
                                        # Set the matching rule as ACTIVE
                                        #Single TG mode
                                        if _dst_id in _system['OFF'] or _int_dst_id != int(_dehash_bridge) :
                                        #if _dst_id in _system['OFF']:
                                            if _system['ACTIVE'] == True:
                                                _system['ACTIVE'] = False
                                                logger.info('(%s) [F] Reflector: %s, connection changed to state: %s', self._system, _bridge, _system['ACTIVE'])
                                                # Cancel the timer if we've enabled an "ON" type timeout
                                                if _system['TO_TYPE'] == 'ON':
                                                    _system['TIMER'] = pkt_time
                                                    logger.info('(%s) [G] Reflector: %s has ON timer and set to "OFF": timeout timer cancelled', self._system, _bridge)
                                        # Reset the timer for the rule
                                        if _system['ACTIVE'] == False and _system['TO_TYPE'] == 'OFF':
                                            _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                            logger.info('(%s) [H] Reflector: %s, timeout timer reset to: %s', self._system, _bridge, _system['TIMER'] - pkt_time)
                                        # Cancel the timer if we've enabled an "ON" type timeout
                                        if _system['ACTIVE'] == True and _system['TO_TYPE'] == 'ON' and _dst_id in _system['OFF']:
                                            _system['TIMER'] = pkt_time
                                            logger.info('(%s) [I] Reflector: %s has ON timer and set to "OFF": timeout timer cancelled', self._system, _bridge)
            
            
            if (_frame_type == HBPF_DATA_SYNC) and (_dtype_vseq == HBPF_SLT_VTERM) and (self.STATUS[_slot]['RX_TYPE'] != HBPF_SLT_VTERM):
                
                #Speak callsign before message
                _say = [words['silence']]
        #        _systemcs = re.sub(r'\W+', '', self._system)
         #       _systemcs.upper()
          #      for character in _systemcs:
           #         _say.append(words[character])
            #        _say.append(words['silence'])
                
                #If disconnection called
                if _int_dst_id == 4000:
                    logger.info('(%s) Reflector: voice called - 4000 "not linked"', self._system)
                    _say.append(words['notlinked'])
                    _say.append(words['silence'])
                 
                 #If status called
                elif _int_dst_id == 5000:
                    _active = False
                    for _bridge in BRIDGES:
                        if _bridge[0:1] != '#':
                            continue
                        for _system in BRIDGES[_bridge]:
                            _dehash_bridge = _bridge[1:]
                            if _system['SYSTEM'] == self._system and _slot == _system['TS']:
                                    if _system['ACTIVE'] == True:
                                        logger.info('(%s) Reflector: voice called - 5000 status - "linked to %s"', self._system,_dehash_bridge)
                                        _say.append(words['silence'])
                                        _say.append(words['linkedto'])
                                        _say.append(words['silence'])
                                        _say.append(words['to'])
                                        _say.append(words['silence'])
                                        _say.append(words['silence']) 
                                        
                                        for num in str(_dehash_bridge):
                                            _say.append(words[num])
                                        
                                        _active = True
                                        break
                        
                    if _active == False:
                        logger.info('(%s) Reflector: voice called - 5000 status - "not linked"', self._system)
                        _say.append(words['notlinked'])
                
                #Speak what TG was requested to link
                else:
                    logger.info('(%s) Reflector: voice called (linking)  "linked to %s"', self._system,_int_dst_id)
                    _say.append(words['silence'])
                    _say.append(words['linkedto'])
                    _say.append(words['silence'])
                    _say.append(words['to'])
                    _say.append(words['silence'])
                    _say.append(words['silence'])
                    
                    for num in str(_int_dst_id):
                        _say.append(words[num])
     
                speech = pkt_gen(bytes_3(5000), _nine, bytes_4(9), 1, _say)
                
                #call speech in a thread as it contains sleep() and hence could block the reactor
                reactor.callInThread(sendSpeech,self,speech)

            # Mark status variables for use later
            self.STATUS[_slot]['RX_PEER']      = _peer_id
            self.STATUS[_slot]['RX_SEQ']       = _seq
            self.STATUS[_slot]['RX_RFS']       = _rf_src
            self.STATUS[_slot]['RX_TYPE']      = _dtype_vseq
            self.STATUS[_slot]['RX_TGID']      = _dst_id
            self.STATUS[_slot]['RX_TIME']      = pkt_time
            self.STATUS[_slot]['RX_STREAM_ID'] = _stream_id    
                            

        #Handle group calls
        if _call_type == 'group':

            # Is this a new call stream?
            if (_stream_id != self.STATUS[_slot]['RX_STREAM_ID']):
                if (self.STATUS[_slot]['RX_TYPE'] != HBPF_SLT_VTERM) and (pkt_time < (self.STATUS[_slot]['RX_TIME'] + STREAM_TO)) and (_rf_src != self.STATUS[_slot]['RX_RFS']):
                    logger.warning('(%s) Packet received with STREAM ID: %s <FROM> SUB: %s PEER: %s <TO> TGID %s, SLOT %s collided with existing call', self._system, int_id(_stream_id), int_id(_rf_src), int_id(_peer_id), int_id(_dst_id), _slot)
                    return

                # This is a new call stream
                self.STATUS[_slot]['RX_START'] = pkt_time
                logger.info('(%s) *CALL START* STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s', \
                        self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot)
                if CONFIG['REPORTS']['REPORT']:
                    self._report.send_bridgeEvent('GROUP VOICE,START,RX,{},{},{},{},{},{}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id)).encode(encoding='utf-8', errors='ignore'))

                # If we can, use the LC from the voice header as to keep all options intact
                if _frame_type == HBPF_DATA_SYNC and _dtype_vseq == HBPF_SLT_VHEAD:
                    decoded = decode.voice_head_term(dmrpkt)
                    self.STATUS[_slot]['RX_LC'] = decoded['LC']

                # If we don't have a voice header then don't wait to decode it from the Embedded LC
                # just make a new one from the HBP header. This is good enough, and it saves lots of time
                else:
                    self.STATUS[_slot]['RX_LC'] = LC_OPT + _dst_id + _rf_src

            #Create default bridge for unknown TG
                if int_id(_dst_id) >= 5 and int_id(_dst_id) != 9 and (str(int_id(_dst_id)) not in BRIDGES):
                    logger.info('(%s) Bridge for TG %s does not exist. Creating as User Activated. Timeout %s',self._system, int_id(_dst_id),CONFIG['SYSTEMS'][self._system]['DEFAULT_UA_TIMER'])
                    make_single_bridge(_dst_id,self._system,_slot,CONFIG['SYSTEMS'][self._system]['DEFAULT_UA_TIMER'])
                
                          
            _sysIgnore = []
            for _bridge in BRIDGES:
                #if _bridge[0:1] != '#':
                if True:
                    for _system in BRIDGES[_bridge]:
                        if _system['SYSTEM'] == self._system and _system['TGID'] == _dst_id and _system['TS'] == _slot and _system['ACTIVE'] == True:
                            _sysIgnore = self.to_target(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data, pkt_time, dmrpkt, _bits,_bridge,_system,False,_sysIgnore)
                        
                            #Send to reflector or TG too, if it exists
                            if _bridge[0:1] == '#':
                                _bridge = _bridge[1:]
                            else:
                                _bridge = '#'+_bridge
                            if _bridge in BRIDGES:
                                _sysIgnore = self.to_target(_peer_id, _rf_src, _dst_id, _seq, _slot, _call_type, _frame_type, _dtype_vseq, _stream_id, _data, pkt_time, dmrpkt, _bits,_bridge,_system,False,_sysIgnore)

            # Final actions - Is this a voice terminator?
            if (_frame_type == HBPF_DATA_SYNC) and (_dtype_vseq == HBPF_SLT_VTERM) and (self.STATUS[_slot]['RX_TYPE'] != HBPF_SLT_VTERM):
                call_duration = pkt_time - self.STATUS[_slot]['RX_START']
                logger.info('(%s) *CALL END*   STREAM ID: %s SUB: %s (%s) PEER: %s (%s) TGID %s (%s), TS %s, Duration: %.2f', \
                        self._system, int_id(_stream_id), get_alias(_rf_src, subscriber_ids), int_id(_rf_src), get_alias(_peer_id, peer_ids), int_id(_peer_id), get_alias(_dst_id, talkgroup_ids), int_id(_dst_id), _slot, call_duration)
                if CONFIG['REPORTS']['REPORT']:
                   self._report.send_bridgeEvent('GROUP VOICE,END,RX,{},{},{},{},{},{},{:.2f}'.format(self._system, int_id(_stream_id), int_id(_peer_id), int_id(_rf_src), _slot, int_id(_dst_id), call_duration).encode(encoding='utf-8', errors='ignore'))

                #
                # Begin in-band signalling for call end. This has nothign to do with routing traffic directly.
                #

                # Iterate the rules dictionary
                for _bridge in BRIDGES:
                    if (_bridge[0:1] == '#') and (_int_dst_id != 9):
                        continue
                    for _system in BRIDGES[_bridge]:
                        if _system['SYSTEM'] == self._system:

                            # TGID matches a rule source, reset its timer
                            if _slot == _system['TS'] and _dst_id == _system['TGID'] and ((_system['TO_TYPE'] == 'ON' and (_system['ACTIVE'] == True)) or (_system['TO_TYPE'] == 'OFF' and _system['ACTIVE'] == False)):
                                _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                logger.info('(%s) [1] Transmission match for Bridge: %s. Reset timeout to %s', self._system, _bridge, _system['TIMER'])

                            # TGID matches an ACTIVATION trigger
                            if (_dst_id in _system['ON'] or _dst_id in _system['RESET']) and _slot == _system['TS']:
                                # Set the matching rule as ACTIVE
                                if _dst_id in _system['ON']:
                                    if _system['ACTIVE'] == False:
                                        _system['ACTIVE'] = True
                                        _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                        logger.info('(%s) [2] Bridge: %s, connection changed to state: %s', self._system, _bridge, _system['ACTIVE'])
                                        # Cancel the timer if we've enabled an "OFF" type timeout
                                        if _system['TO_TYPE'] == 'OFF':
                                            _system['TIMER'] = pkt_time
                                            logger.info('(%s) [3] Bridge: %s set to "OFF" with an on timer rule: timeout timer cancelled', self._system, _bridge)
                                # Reset the timer for the rule
                                if _system['ACTIVE'] == True and _system['TO_TYPE'] == 'ON':
                                    _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                    logger.info('(%s) [4] Bridge: %s, timeout timer reset to: %s', self._system, _bridge, _system['TIMER'] - pkt_time)

                            # TGID matches an DE-ACTIVATION trigger
                            #Single TG mode
                            if (CONFIG['SYSTEMS'][self._system]['MODE'] == 'MASTER' and CONFIG['SYSTEMS'][self._system]['SINGLE_MODE']) == True:
                                if (_dst_id in _system['OFF']  or _dst_id in _system['RESET'] or _dst_id != _system['TGID']) and _slot == _system['TS']:
                                #if (_dst_id in _system['OFF']  or _dst_id in _system['RESET']) and _slot == _system['TS']:
                                    # Set the matching rule as ACTIVE
                                    #Single TG mode
                                    if _dst_id in _system['OFF'] or _dst_id != _system['TGID']:
                                    #if _dst_id in _system['OFF']:
                                        if _system['ACTIVE'] == True:
                                            _system['ACTIVE'] = False
                                            logger.info('(%s) [5] Bridge: %s, connection changed to state: %s', self._system, _bridge, _system['ACTIVE'])
                                            # Cancel the timer if we've enabled an "ON" type timeout
                                            if _system['TO_TYPE'] == 'ON':
                                                _system['TIMER'] = pkt_time
                                                logger.info('(%s) [6] Bridge: %s set to ON with an "OFF" timer rule: timeout timer cancelled', self._system, _bridge)
                                    # Reset the timer for the rule
                                    if _system['ACTIVE'] == False and _system['TO_TYPE'] == 'OFF':
                                        _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                        logger.info('(%s) [7] Bridge: %s, timeout timer reset to: %s', self._system, _bridge, _system['TIMER'] - pkt_time)
                                    # Cancel the timer if we've enabled an "ON" type timeout
                                    if _system['ACTIVE'] == True and _system['TO_TYPE'] == 'ON' and _dst_id in _system['OFF']:
                                        _system['TIMER'] = pkt_time
                                        logger.info('(%s) [8] Bridge: %s set to ON with and "OFF" timer rule: timeout timer cancelled', self._system, _bridge)
                            else:
                                
                                if (_dst_id in _system['OFF']  or _dst_id in _system['RESET']) and _slot == _system['TS']:
                                #if (_dst_id in _system['OFF']  or _dst_id in _system['RESET']) and _slot == _system['TS']:
                                    # Set the matching rule as ACTIVE
                                    if _dst_id in _system['OFF']:
                                    #if _dst_id in _system['OFF']:
                                        if _system['ACTIVE'] == True:
                                            _system['ACTIVE'] = False
                                            logger.info('(%s) [9] Bridge: %s, connection changed to state: %s', self._system, _bridge, _system['ACTIVE'])
                                            # Cancel the timer if we've enabled an "ON" type timeout
                                        if _system['TO_TYPE'] == 'ON':
                                            _system['TIMER'] = pkt_time
                                            logger.info('(%s) [10] Bridge: %s set to ON with and "OFF" timer rule: timeout timer cancelled', self._system, _bridge)
                                    # Reset the timer for the rule
                                    if _system['ACTIVE'] == False and _system['TO_TYPE'] == 'OFF':
                                        _system['TIMER'] = pkt_time + _system['TIMEOUT']
                                        logger.info('(%s) [11] Bridge: %s, timeout timer reset to: %s', self._system, _bridge, _system['TIMER'] - pkt_time)
                                    # Cancel the timer if we've enabled an "ON" type timeout
                                    if _system['ACTIVE'] == True and _system['TO_TYPE'] == 'ON' and _dst_id in _system['OFF']:
                                        _system['TIMER'] = pkt_time
                                        logger.info('(%s) [12] Bridge: %s set to ON with and "OFF" timer rule: timeout timer cancelled', self._system, _bridge)

            #
            # END IN-BAND SIGNALLING
            #


            # Mark status variables for use later
            self.STATUS[_slot]['RX_PEER']      = _peer_id
            self.STATUS[_slot]['RX_SEQ']       = _seq
            self.STATUS[_slot]['RX_RFS']       = _rf_src
            self.STATUS[_slot]['RX_TYPE']      = _dtype_vseq
            self.STATUS[_slot]['RX_TGID']      = _dst_id
            self.STATUS[_slot]['RX_TIME']      = pkt_time
            self.STATUS[_slot]['RX_STREAM_ID'] = _stream_id

#
# Socket-based reporting section
#
class bridgeReportFactory(reportFactory):

    def send_bridge(self):
        serialized = pickle.dumps(BRIDGES, protocol=2) #.decode("utf-8", errors='ignore')
        self.send_clients(REPORT_OPCODES['BRIDGE_SND']+serialized)

    def send_bridgeEvent(self, _data):
        if isinstance(_data, str):
            _data = _data.decode('utf-8', error='ignore')
        self.send_clients(REPORT_OPCODES['BRDG_EVENT']+_data)


#************************************************
#      MAIN PROGRAM LOOP STARTS HERE
#************************************************

if __name__ == '__main__':

    import argparse
    import sys
    import os
    import signal
    
    # Higheset peer ID permitted by HBP
    PEER_MAX = 4294967295
    
    ID_MAX = 16776415

    # Change the current directory to the location of the application
    os.chdir(os.path.dirname(os.path.realpath(sys.argv[0])))

    # CLI argument parser - handles picking up the config file from the command line, and sending a "help" message
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', action='store', dest='CONFIG_FILE', help='/full/path/to/config.file (usually hblink.cfg)')
    parser.add_argument('-r', '--rules', action='store', dest='RULES_FILE', help='/full/path/to/rules.file (usually rules.py)')
    parser.add_argument('-l', '--logging', action='store', dest='LOG_LEVEL', help='Override config file logging level.')
    cli_args = parser.parse_args()

    # Ensure we have a path for the config file, if one wasn't specified, then use the default (top of file)
    if not cli_args.CONFIG_FILE:
        cli_args.CONFIG_FILE = os.path.dirname(os.path.abspath(__file__))+'/hblink.cfg'

    # Call the external routine to build the configuration dictionary
    CONFIG = config.build_config(cli_args.CONFIG_FILE)

    # Ensure we have a path for the rules file, if one wasn't specified, then use the default (top of file)
    if not cli_args.RULES_FILE:
        cli_args.RULES_FILE = os.path.dirname(os.path.abspath(__file__))+'/rules.py'

    # Start the system logger
    if cli_args.LOG_LEVEL:
        CONFIG['LOGGER']['LOG_LEVEL'] = cli_args.LOG_LEVEL
    logger = log.config_logging(CONFIG['LOGGER'])
    logger.info('\n\nCopyright (c) 2013, 2014, 2015, 2016, 2018, 2019\n\tThe Regents of the K0USY Group. All rights reserved.\n')
    logger.debug('(GLOBAL) Logging system started, anything from here on gets logged')

    
    #If MySQL is enabled, read master config from MySQL too
    if CONFIG['MYSQL']['USE_MYSQL'] == True:
        logger.debug('(MYSQL) MySQL config enabled')
        SQLCONFIG = {}
        sql = useMYSQL(CONFIG['MYSQL']['SERVER'], CONFIG['MYSQL']['USER'], CONFIG['MYSQL']['PASS'], CONFIG['MYSQL']['DB'],CONFIG['MYSQL']['TABLE'],logger)
        #Run it once immediately
        if sql.con():
            logger.debug('(MYSQL) reading config from database')
            try:
                SQLCONFIG = sql.getConfig()
                #Add MySQL config data to config dict
            except:
                logger.debug('(MYSQL) problem with SQL query, aborting')
            sql.close()
            logger.debug('(MYSQL) building ACLs')
            # Build ACLs
            for system in SQLCONFIG:
                SQLCONFIG[system]['REG_ACL'] = acl_build(SQLCONFIG[system]['REG_ACL'], PEER_MAX)
                for acl in ['SUB_ACL', 'TG1_ACL', 'TG2_ACL']:
                    SQLCONFIG[system][acl] = acl_build(SQLCONFIG[system][acl], ID_MAX)
            
            CONFIG['SYSTEMS'].update(SQLCONFIG)
        else:
            logger.debug('(MYSQL) problem connecting to SQL server, aborting')
        

    # Set up the signal handler
    def sig_handler(_signal, _frame):
        logger.info('(GLOBAL) SHUTDOWN: CONFBRIDGE IS TERMINATING WITH SIGNAL %s', str(_signal))
        hblink_handler(_signal, _frame)
        logger.info('(GLOBAL) SHUTDOWN: ALL SYSTEM HANDLERS EXECUTED - STOPPING REACTOR')
        reactor.stop()

    # Set signal handers so that we can gracefully exit if need be
    for sig in [signal.SIGINT, signal.SIGTERM]:
        signal.signal(sig, sig_handler)

    # Create the name-number mapping dictionaries
    peer_ids, subscriber_ids, talkgroup_ids = mk_aliases(CONFIG)
    
    # Import the ruiles file as a module, and create BRIDGES from it
    spec = importlib.util.spec_from_file_location("module.name", cli_args.RULES_FILE)
    rules_module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(rules_module)
        logger.info('(ROUTER) Routing bridges file found and bridges imported: %s', cli_args.RULES_FILE)
    except (ImportError, FileNotFoundError):
        sys.exit('(ROUTER) TERMINATING: Routing bridges file not found or invalid: {}'.format(cli_args.RULES_FILE))

    # Build the routing rules file
    BRIDGES = make_bridges(rules_module.BRIDGES)
    
    # Default reflector
    logger.debug('(ROUTER) Setting default reflectors')
    for system in CONFIG['SYSTEMS']:
        if CONFIG['SYSTEMS'][system]['MODE'] != 'MASTER':
            continue
        if CONFIG['SYSTEMS'][system]['DEFAULT_REFLECTOR'] > 0:
            make_default_reflector(CONFIG['SYSTEMS'][system]['DEFAULT_REFLECTOR'],CONFIG['SYSTEMS'][system]['DEFAULT_UA_TIMER'],system)
            
    #static TGs 
    logger.debug('(ROUTER) setting static TGs')
    for system in CONFIG['SYSTEMS']:
        if CONFIG['SYSTEMS'][system]['MODE'] != 'MASTER':
            continue
        _tmout = CONFIG['SYSTEMS'][system]['DEFAULT_UA_TIMER']
        ts1 = []
        ts2 = []
        if CONFIG['SYSTEMS'][system]['TS1_STATIC']:
            ts1 = CONFIG['SYSTEMS'][system]['TS1_STATIC'].split(',')
        if CONFIG['SYSTEMS'][system]['TS2_STATIC']:
            ts2 = CONFIG['SYSTEMS'][system]['TS2_STATIC'].split(',')
            
        #if CONFIG['SYSTEMS'][system]['SINGLE_MODE'] == True:
            #if ts1:
                #make_static_tg(int(ts1[0]),1,system)
            #if ts2:
                #make_static_tg(int(ts2[0]),2,system)
        #else:
        for tg in ts1:
                if not tg:
                    continue
                tg = int(tg)
                make_static_tg(tg,1,_tmout,system)
        for tg in ts2:
                if not tg:
                    continue
                tg = int(tg)
                make_static_tg(tg,2,_tmout,system)

    # INITIALIZE THE REPORTING LOOP
    if CONFIG['REPORTS']['REPORT']:
        report_server = config_reports(CONFIG, bridgeReportFactory)
    else:
        report_server = None
        logger.info('(REPORT) TCP Socket reporting not configured')
        
    #Read AMBE
    AMBEobj = readAMBE(CONFIG['GLOBAL']['ANNOUNCEMENT_LANGUAGE'],'./Audio/')
    #global words
    words = AMBEobj.readfiles()
    logger.info('(AMBE) Read %s words into voice dict',len(words) - 1)

    #Remap words for internationalisation
    if CONFIG['GLOBAL']['ANNOUNCEMENT_LANGUAGE'] in voiceMap:
        logger.info('(AMBE) i8n voice map entry for language %s',CONFIG['GLOBAL']['ANNOUNCEMENT_LANGUAGE'])
        _map = voiceMap[CONFIG['GLOBAL']['ANNOUNCEMENT_LANGUAGE']]
        for _mapword in _map:
            logger.info('(AMBE) Mapping \"%s\" to \"%s\"',_mapword,_map[_mapword])
            words[_mapword] = words[_map[_mapword]]

    # HBlink instance creation
    logger.info('(GLOBAL) FreeDMR \'bridge_master.py\' -- SYSTEM STARTING...')

    
    listeningPorts = {}
    
    generator = {}
    systemdelete = []
    for system in CONFIG['SYSTEMS']:
        if CONFIG['SYSTEMS'][system]['ENABLED']:
            if CONFIG['SYSTEMS'][system]['MODE'] == 'MASTER' and (CONFIG['SYSTEMS'][system]['GENERATOR'] > 1):
                for count in range(CONFIG['SYSTEMS'][system]['GENERATOR']):
                    _systemname = system+'-'+str(count)
                    generator[_systemname] = copy.deepcopy(CONFIG['SYSTEMS'][system])
                    generator[_systemname]['PORT'] = generator[_systemname]['PORT'] + count
                    generator[_systemname]['_default_options'] = "TS1_STATIC={};TS2_STATIC={};SINGLE={};DEFAULT_UA_TIMER={};DEFAULT_REFLECTOR={};VOICE={}".format(generator[_systemname]['TS1_STATIC'],generator[_systemname]['TS2_STATIC'],int(generator[_systemname]['SINGLE_MODE']),generator[_systemname]['DEFAULT_UA_TIMER'],generator[_systemname]['DEFAULT_REFLECTOR'],int(generator[_systemname]['VOICE_IDENT']) )
                    logger.debug('(GLOBAL) Generator - generated system %s',_systemname)
                    generator[_systemname]['_default_options']
                systemdelete.append(system)
    
    for _system in generator:
        CONFIG['SYSTEMS'][_system] = generator[_system]
    for _system in systemdelete:
            CONFIG['SYSTEMS'].pop(_system)
    
    del generator
    del systemdelete
    
    for system in CONFIG['SYSTEMS']:
        if CONFIG['SYSTEMS'][system]['ENABLED']:
            if CONFIG['SYSTEMS'][system]['MODE'] == 'OPENBRIDGE':
                systems[system] = routerOBP(system, CONFIG, report_server)                
            else:
                systems[system] = routerHBP(system, CONFIG, report_server)
            listeningPorts[system] = reactor.listenUDP(CONFIG['SYSTEMS'][system]['PORT'], systems[system], interface=CONFIG['SYSTEMS'][system]['IP'])
            logger.debug('(GLOBAL) %s instance created: %s, %s', CONFIG['SYSTEMS'][system]['MODE'], system, systems[system])

    def loopingErrHandle(failure):
        logger.error('(GLOBAL) STOPPING REACTOR TO AVOID MEMORY LEAK: Unhandled error in timed loop.\n %s', failure)
        reactor.stop()

    # Initialize the rule timer -- this if for user activated stuff
    rule_timer_task = task.LoopingCall(rule_timer_loop)
    rule_timer = rule_timer_task.start(60)
    rule_timer.addErrback(loopingErrHandle)

    # Initialize the stream trimmer
    stream_trimmer_task = task.LoopingCall(stream_trimmer_loop)
    stream_trimmer = stream_trimmer_task.start(5)
    stream_trimmer.addErrback(loopingErrHandle)
   
    # Ident
    #This runs in a thread so as not to block the reactor
    ident_task = task.LoopingCall(threadIdent)
    identa = ident_task.start(900)
    identa.addErrback(loopingErrHandle)
    
    #Options parsing
    options_task = task.LoopingCall(options_config)
    options = options_task.start(30)
    options.addErrback(loopingErrHandle)
    
    #Mysql config checker
    #This runs in a thread so as not to block the reactor
    if CONFIG['MYSQL']['USE_MYSQL'] == True:
        mysql_task = task.LoopingCall(threadedMysql)
        mysql = mysql_task.start(30)
        mysql.addErrback(loopingErrHandle)
        
    #STAT trimmer - once every hour
    if CONFIG['GLOBAL']['GEN_STAT_BRIDGES']:
        stat_trimmer_task = task.LoopingCall(statTrimmer)
        stat_trimmer = stat_trimmer_task.start(3600)#3600
        stat_trimmer.addErrback(loopingErrHandle)
    
    #more threads
    reactor.suggestThreadPoolSize(100)
    
    reactor.run()
