import numpy as np
from sklearn.linear_model import LogisticRegression

# Read the input file and split each line into a list of numbers
input_data = []
with open('input.txt', 'r') as f:
    for line in f:
        input_data.append([int(x) for x in line.split()])
print(input_data)
# # Convert the input data into a numpy array
# X = np.array(input_data)

# # Define the target variable y
# # For example, you could define y as a list of labels indicating whether each line in the input file should be classified as "positive" or "negative"
# y = [1, 1, 1, 1, 1, 0, 0]

# # Train a logistic regression model using the input data and target variable
# model = LogisticRegression()
# model.fit(X, y)

# # Predict the next line that should follow the lines in the input file
# prediction = model.predict(X)

# # Output the predicted line to the console
# print(prediction)
