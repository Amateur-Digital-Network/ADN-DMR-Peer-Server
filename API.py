
from hashlib import blake2b
from spyne import ServiceBase, rpc, Integer, Decimal, UnsignedInteger32, Unicode, Iterable, error
from dmr_utils3.utils import bytes_3


class FD_APIUserDefinedContext(object):
    def __init__(self,CONFIG,APIQUEUE,BRIDGES):
        self.CONFIG = CONFIG
        self.APIQUEUE = APIQUEUE
        self.BRIDGES = BRIDGES

    def getconfig(self):
        return self.CONFIG

    def getapiqueue(self):
        return self.APIQUEUE

    def getbridges(self):
        return self.BRIDGES

    def validateKey(self,dmrid,key):
        systems = self.CONFIG['SYSTEMS']
        dmrid = bytes_3(dmrid)
        for system in systems:
            for peerid in systems[system]['PEERS']:
                if peerid == dmrid:
                    if key == _hash:
                        return(systems[system]['_opt_key'])
                    else:
                        return(False)

    def reset(self,system):
        self.CONFIG['SYSTEMS'][system]['_reset'] = True

    def queue(self,system,options):
        self.APIQUEUE.append((system,options))


class FD_API(ServiceBase):
    _version = 0.1

    #def validateHMAC(_hmac,_system):
    #    self._config = self._CONFIG['SYSTEMS'][_system]
    #    _h = blake2b(key=self._config['_opt_key'], digest_size=16)
    #    _h.update('validate')
    #    _hash = _h.digest()
    #    if _hash == _hmac:
    #        return(True)
    #    else:
    #        return(False)


    #return API version
    @rpc(Unicode, _returns=Decimal())
    def version(ctx, sessionid):
        return(FD_API._version)

    @rpc(Unicode,Unicode, _returns=Unicode())
    def reset(ctx,dmrid,key):
        system = ctx.udc.validateKey(dmrid,key)
        if system:
            ctx.udc.reset(system)
        else:
            raise error.InvalidCredentialsError()

    @rpc(UnsignedInteger32,UnsignedInteger32,Unicode,_returns=Unicode())
    def setoptions(ctx,dmrid,key,options):
        system = ctx.udc.validateKey(dmrid,key)
        if system:
            ctx.udc.queue(system,options)
        else:
            raise error.InvalidCredentialsError()

    @rpc(UnsignedInteger32,_returns=(Unicode()))
    def killserver(ctx,killkey):
        pass

    @rpc(_returns=Unicode())
    def getconfig(ctx):
        return ctx.udc.getconfig()

    @rpc(_returns=Unicode())
    def getbridges(ctx):
        return ctx.udc.getbridges()
