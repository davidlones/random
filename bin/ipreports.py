import urllib2, sys
from bs4 import BeautifulSoup

ipaddr = sys.argv[1]

try:
    if sys.argv[2] == "-o":
        out = True
        thefile = sys.argv[3]
        file = open(thefile, 'w')
    else:
        out = False
except:
    out = False


# print(ipaddr)
# print(str(out))

quote_page = "https://www.abuseipdb.com/check/" + ipaddr

hdr = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.11 (KHTML, like Gecko) Chrome/23.0.1271.64 Safari/537.11',
       'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
       'Accept-Charset': 'ISO-8859-1,utf-8;q=0.7,*;q=0.3',
       'Accept-Encoding': 'none',
       'Accept-Language': 'en-US,en;q=0.8',
       'Connection': 'keep-alive'}

page = urllib2.Request(quote_page, headers=hdr)
data = urllib2.urlopen(page)

page = data.read()


soup1 = BeautifulSoup(page, 'html.parser')
name_box1 = soup1.find('section', attrs={'id': 'report-wrapper'})
report1 = name_box1.text.strip()

trim1 = report1.replace("\t", "")
trim1 = trim1.replace("\n\n\n", "\n")
report1 = trim1

soup2 = BeautifulSoup(page, 'html.parser')
name_box2 = soup2.find('table', attrs={'class': 'table table-striped responsive-table'})
report2 = name_box2.text.strip()


# print(report1)

trim2 = report2.replace("Reporter \n Date \n Comment \n Categories \n\n", "")
trim2 = trim2.replace("\n\n", "\n")
trim2 = trim2.replace("             \n", "\n")
trim2 = trim2.replace("\t", "")
trim2 = trim2.replace("\n\n", "\n")
trim2 = trim2.replace("\n ", "\n")

report2 = trim2

report = report1 + report2
print(report)

if out:
    file.write(report)
    file.close()


