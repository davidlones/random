#!/bin/bash

# Unmount the encrypted virtual volume

# Unmount the encrypted volume from the /encrypted directory
sudo umount /encrypted

# Close the encrypted volume
sudo cryptsetup luksClose crypt
