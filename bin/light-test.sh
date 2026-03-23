#!/bin/bash
for i in {0..359}; do kasa --host 10.0.1.3 hsv $i 100 100; done