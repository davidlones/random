#!/bin/bash

# Run the bash script and save the output to a file
/home/david/.bin/tempmanager-test.sh > output.txt

# Use gnuplot to plot the data from the output file
gnuplot -e "set term png; set output 'plot.png'; plot 'output.txt' using 1:2 with lines"
