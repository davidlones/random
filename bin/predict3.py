import numpy as np
from sklearn.linear_model import LinearRegression

# Load the data from the input file
data = []
with open('output.csv', 'r') as f:
    for line in f:
        data.append([int(x) for x in line.split(",")])

# Convert the data to a numpy array
X = np.array(data[:-1])
y = np.array(data[1:])

# Fit the model to the data
model = LinearRegression()
model.fit(X, y)

# Predict the next line using the model
raw_prediction = model.predict(y[-1:])
print(raw_prediction)

# Round the predicted values to the nearest integer
rounded_prediction = np.round(raw_prediction)

# Convert the predicted values to integers
next_line = rounded_prediction.astype(int)

# Print the predicted line to the console
print(next_line)