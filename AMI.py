
from time import time
from random import randint

from twisted.internet import reactor,task
from twisted.internet.defer import Deferred
from twisted.internet.protocol import ClientFactory,ReconnectingClientFactory,Protocol
from twisted.protocols.basic import LineReceiver

class AMI():
    def __init__(self,host,port):
        self._AMIClient = self.AMIClient

        
        self.CF = reactor.connectTCP(host, port, self.CCClientFactory(self._AMIClient))
    
    def closeConnection(self):
        self.transport.loseConnection()
            
    class AMIClient(LineReceiver):
        
        delimiter = b'\r\n'
        
        end = b"Bye-bye!"
        
        def connectionMade(self):
            print('b')
            self.sendLine('Action: login')
            self.sendLine('Username: admin')
            self.sendLine('Secret: ilcgi')
            self.sendLine('\r\n')
            
        def lineReceived(self,line):
            
            print(line)
            
            if line == 'Asterisk Call Manager/1.0':
                print('OK')
                return
            
            k,v = line.split(':')
            
            if v == ' Success':
                    self.lastresponse = True
            elif k == 'Response' and v == ' Error':
                self.transport.loseConnection()
            
            else:
                self.sendLine('Action: command')
                self.sendLine('Command:' + self.command)
                self.sendline('\r\n')
                self.transport.loseConnection()
                    
            
            
    class AMIClientFactory(ReconnectingClientFactory):
        def __init__(self,AMIClient,command):
            self.command = command
            self.done = Deferred()
            self.protocol = AMIClient
            self.protocol.command = command
            print('a')

        def clientConnectionFailed(self, connector, reason):
            print("connection failed:", reason.getErrorMessage())
            ReconnectingClientFactory.clientConnectionLost(self, connector, reason)

        def clientConnectionLost(self, connector, reason):
            print("connection lost:", reason.getErrorMessage())
            ReconnectingClientFactory.clientConnectionLost(self, connector, reason)
        
            
if __name__ == '__main__':
    AMIc = AMI.AMIClientFactory(AMI.AMIClient,'rpt cmd 29177 ilink 3 2001')
    
