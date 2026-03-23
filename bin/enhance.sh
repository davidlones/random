#!/bin/bash

for file in *.png; do
 sudo convert -enhance $file "../${file%.png}.png"
done
