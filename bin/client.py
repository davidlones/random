#!/usr/bin/env python
# _0853RV3R
import subprocess, logging, socket, sys, time, os, shelve, traceback, errno
from thread import *


import urllib2, urllib
from M2Crypto import BIO, RSA, Rand

from BaseHTTPServer import BaseHTTPRequestHandler,HTTPServer

def generate_RSA(bits=4096):
	print('generating ' + bits + '-bit key pair')
	new_key = RSA.gen_key(bits, 65537)
	memory = BIO.MemoryBuffer()
	new_key.save_key_bio(memory, cipher=None)
	private_key = memory.getvalue()
	new_key.save_pub_key_bio(memory)
	return private_key, memory.getvalue()

def notetime():
	return time.strftime("%I:%M:%S %p")

def client():
	host = '0.0.0.0'
	filename = '!'
	listener_port = 8988
	html_port =8080

	message = 'this is a test'

	url = 'http://' + host + ':' + str(html_port) + '/' + filename

	loadkey = urllib.urlopen(url)
	public_key = loadkey.read()

	bio = BIO.MemoryBuffer(public_key)
	rsa = RSA.load_pub_key_bio(bio)

	encrypted = rsa.public_encrypt(message, RSA.pkcs1_oaep_padding)
	packet = encrypted.encode('base64')

	try:
		s = socket.socket()
		s.connect((host, listener_port))

		s.send(packet)

	finally:
		s.close

if __name__ == "__main__":
	client()

#test()