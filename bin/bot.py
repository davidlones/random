#!/usr/bin/env python
# _0853RV3R
import subprocess, logging, socket, sys, time, os, shelve, traceback, errno, pyotp, argparse
from thread import *
import urllib2
from M2Crypto import BIO, RSA, Rand
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer

parser = argparse.ArgumentParser(description = \
"\n\
PyBot v0.2\n\
\n\
Generates obfuscated method of fetching RSA keys and sending an encrypted message. Does not yet execute payloads on client. \n\
\n\
_0853RV3R",
formatter_class=argparse.RawTextHelpFormatter)

parser.add_argument('-s', '--server', action="store_true", dest="server", help="server mde")
parser.add_argument('-c', '--client', action="store_true", dest="client", help="client mode")
parser.add_argument('-m', '--message', action="store", dest="message", help="message to send")

if len(sys.argv[1:])==0:
	parser.print_help()		
	parser.exit()

argument = parser.parse_args()

message = "hello world"
if argument.message:
	message = argument.message


host = '0.0.0.0'

httpsecret = 'thisisthelastone'
httptotp = pyotp.TOTP(httpsecret)

commsecret = 'butwhynotfirsttw'
commtotp = pyotp.TOTP(commsecret)

class CommunicationServer():
	def stop(self):
		self.run = False

	def thread(self, c, addr, private_key):
		thetime = notetime()
		data = c.recv(5000)
		if data:
			packet = data.decode('base64')
			decrypted = decrypt(packet, private_key)
			print('[' + thetime + '] ' + addr[0] + ' >> ' + str(decrypted))
			c.close()

	def start(self, host, commport, private_key):
		self.run = True
		s = socket.socket()
		s.bind((host, commport))
		s.listen(5)
		while self.run:
			c, addr = s.accept()
			start_new_thread(self.thread ,(c, addr, private_key))
		s.close()

class StoppableHTTPServer(HTTPServer):
	def server_bind(self):
		HTTPServer.server_bind(self)
		self.socket.settimeout(1)
		self.run = True

	def get_request(self):
		while self.run:
			try:
				sock, addr = self.socket.accept()
				sock.settimeout(None)
				return (sock, addr)
			except socket.timeout:
				pass

	def stop(self):
		self.run = False

	def start(self):
		while self.run:
			self.handle_request()

class httpHandler(BaseHTTPRequestHandler):
	def do_GET(self):
		if self.path==self.filepath:
			self.send_response(418)
			self.send_header('Content-type','text/html')
			self.end_headers()
			self.wfile.write(self.content)
			return

def generate_RSA(bits=4096):
	print('generating ' + str(bits) + '-bit key pair')
	new_key = RSA.gen_key(bits, 65537)
	memory = BIO.MemoryBuffer()
	new_key.save_key_bio(memory, cipher=None)
	private_key = memory.getvalue()
	new_key.save_pub_key_bio(memory)

	return private_key, memory.getvalue()

def decrypt(packet, private_key):
		rsa = RSA.load_key_string(private_key)
		decrypted = rsa.private_decrypt(packet, RSA.pkcs1_oaep_padding)
		return decrypted

def notetime():
	return time.strftime("%I:%M:%S %p")

def server():
	print('SERVER HAS STARTED')
	private_key, public_key = generate_RSA()

	try:
		while True:
			commotp = str(commtotp.now().replace('0', '1'))
			httpotp = str(httptotp.now().replace('0', '1'))

			commport = int(commotp[1:-1])
			httpport = int(httpotp[1:-1])
			filename = httpotp[0] + httpotp[-1]

			filepath = '/' + filename

			httpHandler.filepath = filepath
			httpHandler.content = public_key

			httpserver = StoppableHTTPServer(('', httpport), httpHandler)
			start_new_thread(httpserver.start, ())
			print('HTTPD [' + str(host) + ':' + str(httpport) + filepath + ']')

			commserver = CommunicationServer()
			start_new_thread(commserver.start, (host, commport, private_key))
			print('COMMD [' + str(host) + ':' + str(commport) + ']')	

			time.sleep(30)
			httpserver.stop()
			commserver.stop()

			print('RESETTING STATE')

	except KeyboardInterrupt:
		print(' user exited')

	finally:
		print('SERVER HAS STOPPED')

def client():
	print('CLIENT')
	private_key, public_key = generate_RSA()

	httpotp = str(httptotp.now().replace('0', '1'))
	commotp = str(commtotp.now().replace('0', '1'))
	s = socket.socket()
	commport = int(commotp[1:-1])
	httpport = int(httpotp[1:-1])
	filename = httpotp[0] + httpotp[-1]

	url = 'http://' + host + ':' + str(httpport) + '/' + filename
	print(url)

	try:
		loadkey = urllib2.urlopen(url)

	except urllib2.HTTPError as e:
		if e.code == 418:
		    public_key = e.read()

	bio = BIO.MemoryBuffer(public_key)
	rsa = RSA.load_pub_key_bio(bio)

	encrypted = rsa.public_encrypt(message, RSA.pkcs1_oaep_padding)
	packet = encrypted.encode('base64')

	print('encrypting message "' + message + '"')

	try:
		s.connect((host, commport))
		s.send(packet)
		print('packet sent')

	finally:
		s.close


if argument.server:
	server()

if argument.client:
	client()

#test()
