#!/bin/bash

# This script creates a sparse file and sets up an encrypted LUKS volume on it.

FILE=encrypted.img
SIZE=64G

# Create the sparse file
truncate -s $SIZE $FILE

# Set up the encrypted volume on the sparse file
sudo cryptsetup luksFormat $FILE
sudo cryptsetup open --type luks $FILE crypt

# Format the newly created encrypted volume
sudo mkfs.ext4 /dev/mapper/crypt

# Mount the encrypted volume
sudo mkdir /encrypted
sudo mount /dev/mapper/crypt /encrypted


sudo touch /encrypted/test
echo "Line number: $LINENO"
sudo umount /encrypted
echo "Line number: $LINENO"
sudo cryptsetup luksClose crypt
echo "Line number: $LINENO"
