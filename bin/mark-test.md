import numpy as np
from sklearn.linear_model import LinearRegression

# Load the data from the input file
data = []
with open('input.txt', 'r') as f:
    for line in f:
        data.append([int(x) for x in line.split()])

# Convert the data to a numpy array
X = np.array(data[:-1])
y = np.array(data[1:])

# Fit the model to the data
model = LinearRegression()
model.fit(X, y)

# Predict the next line using the model
next_line = model.predict(y[-1:])

# Print the predicted line to the console
print(next_line)



I got the following error:
Traceback (most recent call last):
  File "/home/david/.bin/predict3.py", line 23, in <module>
    prediction = model.predict(test_data)
  File "/home/david/.local/lib/python3.10/site-packages/sklearn/tree/_classes.py", line 426, in predict
    X = self._validate_X_predict(X, check_input)
  File "/home/david/.local/lib/python3.10/site-packages/sklearn/tree/_classes.py", line 392, in _validate_X_predict
    X = self._validate_data(X, dtype=DTYPE, accept_sparse="csr", reset=False)
  File "/home/david/.local/lib/python3.10/site-packages/sklearn/base.py", line 558, in _validate_data
    self._check_n_features(X, reset=reset)
  File "/home/david/.local/lib/python3.10/site-packages/sklearn/base.py", line 359, in _check_n_features
    raise ValueError(
ValueError: X has 6 features, but DecisionTreeClassifier is expecting 5 features as input.