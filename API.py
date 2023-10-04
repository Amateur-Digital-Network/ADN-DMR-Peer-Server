#
###############################################################################
# Copyright (C) 2023 Simon Adlem, G7RZU <g7rzu@gb7fr.org.uk>
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

from spyne import ServiceBase, rpc, Integer, Decimal, UnsignedInteger32, Unicode, Iterable, error
from dmr_utils3.utils import bytes_3, bytes_4


class FD_APIUserDefinedContext(object):
    def __init__(self,CONFIG,BRIDGES):
        self.CONFIG = CONFIG
        self.BRIDGES = BRIDGES

    def getconfig(self):
        return self.CONFIG

    def getbridges(self):
        return self.BRIDGES

    def validateKey(self,dmrid,key):
        systems = self.CONFIG['SYSTEMS']
        dmrid = bytes_4(dmrid)
        print(dmrid)
        for system in systems:
            if systems[system]['MODE'] == 'MASTER':
                for peerid in systems[system]['PEERS']:
                    print(peerid)
                    if peerid == dmrid:
                        if key == systems[system]['_opt_key']:
                            return(system)
                        else:
                            return(False)
        return(False)

    def validateSystemKey(self,systemkey):
        if systemkey == self.CONFIG['GLOBAL']['SYSTEM_API_KEY']:
            return True
        else:
            return False

    def reset(self,system):
        self.CONFIG['SYSTEMS'][system]['_reset'] = True

    def options(self,system,options):
        self.CONFIG['SYSTEMS'][system]['OPTIONS'] = options

    def killserver(self):
        self.CONFIG['GLOBAL']['_KILL_SERVER'] = True



class FD_API(ServiceBase):
    _version = 0.1

    #return API version
    @rpc(Unicode, _returns=Decimal())
    def version(ctx, sessionid):
        return(FD_API._version)

    @rpc()
    def dummy(ctx):
        pass

    ######################
    #User level API calls#
    ######################
    @rpc(Unicode,Unicode)
    def reset(ctx,dmrid,key):
        system = ctx.udc.validateKey(int(dmrid),key)
        if system:
            ctx.udc.reset(system)
        else:
            raise error.InvalidCredentialsError()

    @rpc(UnsignedInteger32,Unicode,Unicode)
    def setoptions(ctx,dmrid,key,options):
        system = ctx.udc.validateKey(int(dmrid),key)
        if system:
            ctx.udc.options(system,options)
        else:
            raise error.InvalidCredentialsError()



    ########################
    #System level API calls#
    ########################
    @rpc(Unicode)
    def killserver(ctx,systemkey):
        if ctx.udc.validateSystemKey(systemkey):
            return ctx.udc.killserver()
        else:
            raise error.InvalidCredentialsError()

    @rpc(Unicode,_returns=Unicode())
    def getconfig(ctx,systemkey):
        if ctx.udc.validateSystemKey(systemkey):
            return ctx.udc.getconfig()
        else:
            raise error.InvalidCredentialsError()

    @rpc(Unicode,_returns=Unicode())
    def getbridges(ctx,systemkey):
        if ctx.udc.validateSystemKey(systemkey):
            return ctx.udc.getbridges()
        else:
            raise error.InvalidCredentialsError()


