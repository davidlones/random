#!/bin/bash

# Remove the encrypted virtual volume

# Unmount the encrypted volume if it is mounted
sudo umount /encrypted

# Close the encrypted volume if it is open
sudo cryptsetup luksClose crypt

sudo losetup -d /dev/loop42
sudo losetup -d encrypted.img
sudo losetup -d /dev/mapper/crypt
sudo losetup -d crypt

# Remove the encrypted file
sudo rmdir /encrypted
read -p "Wipe contents of '/encrypted'? "
sudo rm encrypted.img
sudo rm -r /encrypted