#!/bin/bash

# Set the output file and plot file names
output_file="plot.txt"
plot_file="plot.png"

# Delete the output file if it already exists
rm -f "$output_file"

# Generate the data and save it to the output file
for CPU_TEMP in {30..80};
do
    echo "CPU Temperature: $CPU_TEMP C"

  if [[ $CPU_TEMP -lt 42 ]]; then
    FAN_SPEED=1
  elif [[ $CPU_TEMP -lt 69 ]]; then
    FAN_SPEED=$(echo "1+e((l($CPU_TEMP - 41)/l(2))*2.3)/640" | bc -l | awk '{printf "%.0f", $0}')
  else
    FAN_SPEED=100
  fi
    echo "Fan Speed: $FAN_SPEED"
done

# Use gnuplot to plot the data from the output file
# gnuplot -e "set term png; set output '$plot_file'; plot '$output_file' using 1:2 with lines"
