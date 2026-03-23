#!/bin/bash
# _0853RV3R

public_port='51228'
public_ip='74.221.101.29'
username='davidlones'

ssh -p $public_port $username@$public_ip 'screen -S vnc -d -m x11vnc' >/dev/null 2>&1 &
echo 'ctl-c to exit'
ssh -t -p $public_port $username@$public_ip -L 5999:localhost:5900 -N
ssh -p $public_port $username@$public_ip 'screen -X -S vnc quit' >/dev/null 2>&1 &
