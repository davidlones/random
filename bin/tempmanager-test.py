import subprocess
import matplotlib.pyplot as plt

temps = []
speeds = []

for CPU_TEMP in range(42, 70):
    output = subprocess.run(["/home/david/.bin/tempmanager-test.sh", str(CPU_TEMP)], capture_output=True)
    output_str = output.stdout.decode("utf-8")
    lines = output_str.split("\n")
    temp_line = lines[0]
    temp = int(temp_line.split(": ")[1][:-2])
    speed_line = lines[1]
    speed = int(speed_line.split(": ")[1][:-1])
    temps.append(temp)
    speeds.append(speed)

plt.plot(temps, speeds)
plt.xlabel("Temperature (C)")
plt.ylabel("Fan Speed (%)")
plt.show()
