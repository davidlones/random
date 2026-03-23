import argparse, sys, os.path, math

search_count = 53
max_search = 10
min_paged = 0
max_paged = min_paged + max_search

if search_count > max_search:
    pages = int(math.ceil(float(search_count) / float(max_search)))
    page = 1
    while page <= pages:
        for i in reversed(xrange(min_paged, max_paged)):
            # draw(t=rel[i], keyword=query)
            print(i)
        # printNicely('')
        userinput = raw_input('Page ' + str(page) + ' of ' + str(pages) + '. View next page? (y/n) ')
        if "n" in userinput.lower():
            break
        else:
            # printNicely('')
            page += 1
            min_paged += max_search
            max_paged += max_search