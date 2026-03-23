import feedparser, random

article_number = random.randint(1,21)

d = feedparser.parse('https://www.reddit.com/r/news/.rss')
article_link = d['entries'][article_number]['link']
article_title = d['entries'][article_number]['title']

print(article_title)
print(article_link)