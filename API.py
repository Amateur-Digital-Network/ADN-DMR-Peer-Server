
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


class FD_API(ServiceBase):
    _version = 0.1

    #return API version
    @rpc(Unicode, _returns=Decimal())
    def version(ctx, sessionid):
        return(FD_API._version)

    @rpc()
    def dummy(ctx):
        pass

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

    @rpc(UnsignedInteger32)
    def killserver(ctx,killkey):
        pass

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

