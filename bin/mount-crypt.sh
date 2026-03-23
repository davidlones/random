#!/bin/bash

# Mount the encrypted virtual volume
sudo cryptsetup open --type luks encrypted.img crypt
sudo mount /dev/mapper/crypt /encrypted
