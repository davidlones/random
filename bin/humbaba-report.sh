#!/bin/bash

user=$1
outfile=$2

nmap -T4 -A -Pn $user -o $outfile

python ~/.bin/AbuseIPDB.py $user >> $outfile