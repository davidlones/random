from bs4 import BeautifulSoup
import csv

results = []
for pagenumber in range(1, 104):
    inputfile = str(pagenumber) + ".html"

    page = open(inputfile, 'r')

    soup = BeautifulSoup(page, "html.parser")

    # Find the table on the webpage
    table = soup.find('table', class_='results mm')

    # Extract the rows from the table
    rows = table.find_all('ul')

    for row in rows:
        data = [cell.text for cell in row.find_all('li')]
        results.append(data[:6])

print(results)
# Create an output file
output_file = open('output.csv', 'w')

# Create a CSV writer
csv_writer = csv.writer(output_file)

# Iterate through the rows and write the data to the CSV file
for row in reversed(results):
    csv_writer.writerow(row)

# Close the output file
output_file.close()
