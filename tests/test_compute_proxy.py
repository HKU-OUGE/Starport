import socket
import ssl
import unittest
from unittest.mock import patch
from isolation_toolkit import compute_remote


class Connection:
    def __init__(self, header=b'HTTP/1.1 200 Connection established\r\n\r\n'):
        self.header=header; self.sent=[]; self.connected=[]; self.timeouts=[]
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def connect(self,address):self.connected.append(address)
    def settimeout(self,timeout):self.timeouts.append(timeout)
    def sendall(self,data):self.sent.append(data)
    def recv(self,size):
        data=self.header[:size];self.header=self.header[size:];return data


class ProxyTests(unittest.TestCase):
    def run_probe(self,outcomes,headers=None,budget=12,clock=None):
        connections=[];hosts=[];factory_args=[]
        def factory(*args):
            factory_args.append(args)
            conn=Connection(headers[len(connections)] if headers else b'HTTP/1.1 200 Connection established\r\n\r\n')
            connections.append(conn);return conn
        class Context:
            def wrap_socket(self,conn,server_hostname):
                hosts.append(server_hostname);value=outcomes[len(hosts)-1]
                if isinstance(value,Exception):raise value
                return Connection(value)
        with patch.object(compute_remote.socket,'socket',side_effect=factory),patch.object(compute_remote.ssl,'create_default_context',return_value=Context()):
            if clock is None:result=compute_remote.proxy_https({'proxy_socket':'/test/proxy.sock'},budget)
            else:
                with patch.object(compute_remote.time,'monotonic',side_effect=clock):
                    result=compute_remote.proxy_https({'proxy_socket':'/test/proxy.sock'},budget)
        self.assertTrue(all(args==(socket.AF_UNIX,socket.SOCK_STREAM) for args in factory_args))
        self.assertTrue(all(c.connected==['/test/proxy.sock'] for c in connections))
        return result,connections,hosts

    def test_success_does_not_repeat(self):
        result,connections,hosts=self.run_probe([b'HTTP/1.1 204 No Content\r\n'])
        self.assertTrue(result);self.assertEqual(len(connections),1)
        self.assertEqual(hosts,['www.gstatic.com'])

    def test_transient_tls_failure_uses_second_https_target(self):
        result,connections,hosts=self.run_probe([TimeoutError(),b'HTTP/1.1 200 OK\r\n'])
        self.assertTrue(result);self.assertEqual(hosts,['www.gstatic.com','example.com'])

    def test_all_attempts_fail_closed(self):
        result,connections,hosts=self.run_probe([TimeoutError(),OSError(),ssl.SSLError()])
        self.assertFalse(result);self.assertEqual(len(connections),3)

    def test_certificate_rejection_never_disables_verification(self):
        result,connections,hosts=self.run_probe([ssl.SSLCertVerificationError()]*3)
        self.assertFalse(result);self.assertEqual(len(hosts),3)

    def test_proxy_rejection_does_not_attempt_origin_directly(self):
        result,connections,hosts=self.run_probe([],headers=[b'HTTP/1.1 403 Forbidden\r\n\r\n']*3)
        self.assertFalse(result);self.assertFalse(hosts)

    def test_missing_http_response_is_not_success(self):
        result,connections,hosts=self.run_probe([b'',b'garbage',b''])
        self.assertFalse(result)

    def test_total_budget_stops_retrying(self):
        result,connections,hosts=self.run_probe([],budget=0,clock=[0,0])
        self.assertFalse(result);self.assertFalse(connections)


if __name__=='__main__':unittest.main()
