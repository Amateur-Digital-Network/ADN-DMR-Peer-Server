from xmlrpc.client import Fault

from twisted.web import xmlrpc

from hashlib import blake2b

class FD_API_HELPERS():
    def __init__(self,CONFIG,APIQUEUE):
        self._CONFIG = CONFIG
        self._APIQUEUE = APIQUEUE

    def connected(self,_id):
        for system in self._CONFIG['SYSTEMS']:
             for peerid in self._CONFIG['SYSTEMS'][self._system]['PEERS']:
                 if peerid == _id:
                     return(system)
        return(False)


    def validateHMAC(_hmac,_system):
        self._config = self._CONFIG['SYSTEMS'][_system]
        _h = blake2b(key=self._config['_opt_key'], digest_size=16)
        _h.update('validate')
        _hash = _h.digest()
        if _hash == _hmac:
            return(True)
        else:
            return(False)


class FD_API(xmlrpc.XMLRPC(allow_none=True)):

    def __init__(self,CONFIG,APIQUEUE):
        self._CONFIG = CONFIG
        self._APIQUEUE = APIQUEUE
        self.helpers = FD_API_HELPERS(self._CONFIG,self._APIQUEUE)

    def reset(self,_id,_hmac):
        return('<xml></xml>')
        system = self.helpers.connected(_id)
        if result:
            if self.helpers.validateHMAC(_hmac,_system):
                self._CONFIG['SYSTEMS'][system]['_reset'] = True
            else:
                return Fault(2, "Authentication failed")
        else:
            return Fault(1, "ID not connected to this server")

        return('Z')


def main():
    from twisted.internet import reactor
    from twisted.web import server

    r = FD_API({},{})
    reactor.listenTCP(7080, server.Site(r))
    reactor.run()


if __name__ == "__main__":
    main()
