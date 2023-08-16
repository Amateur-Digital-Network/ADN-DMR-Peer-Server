
from twisted.internet import reactor
from twisted.web.xmlrpc import Proxy


def printValue(value):
    print(repr(value))
    reactor.stop()


def printError(error):
    print("error", error)
    reactor.stop()


def capitalize(value):
    print(value)


proxy = Proxy(b"http://localhost:7080/xmlrpc")
# The callRemote method accepts a method name and an argument list.
proxy.callRemote("FD_API.reset", '2', '55555').addCallbacks(capitalize, printError)
reactor.run()
