#!/bin/bash
REFRESH_RATE=${1:-1}

while true; do
  CPU_TEMP=$(sensors | sed -rn 's/.*id 0:\s+.([0-9]+).*/\1/p')

  if [[ $CPU_TEMP -lt 42 ]]; then
    FAN_SPEED=1
  elif [[ $CPU_TEMP -lt 69 ]]; then
    FAN_SPEED=$(echo "1+e((l($CPU_TEMP - 41)/l(2))*2.3)/640" | bc -l | awk '{printf "%.0f", $0}')
  else
    FAN_SPEED=100
  fi

  /usr/bin/liquidctl --match H115i set fan speed $FAN_SPEED

  tput clear
  echo "CPU Temperature: $CPU_TEMP C"
  echo "Fan Speed: $FAN_SPEED %"

  sleep $REFRESH_RATE
done

# FAN_SPEED=$(echo "e((l($CPU_TEMP - 41)/l(27))*4.55)" | bc -l | awk '{printf "%.0f", $0}')
# /usr/bin/liquidctl --match H115i set fan speed 20 20 30 30 40 60 55 80 70 100
