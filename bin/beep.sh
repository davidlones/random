#!/bin/bash

# Set the frequency, duration, and delay from the command line arguments
frequency=${1:-800}
duration=${2:-0.1}

# Generate and play the sine wave using ffmpeg and aplay
ffmpeg -loglevel quiet -f lavfi -i "sine=frequency=$frequency:duration=$duration" -c:a pcm_s16le -f wav - | aplay - 2> /dev/null
