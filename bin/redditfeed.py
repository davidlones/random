#!/usr/bin/env python3

import argparse
import html
import io
import json
import hashlib
import os
import textwrap
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime

import requests
from PIL import Image, ImageTk

USER_AGENT="sol-reddit-client"
CACHE_DIR=os.path.expanduser("~/.cache/sol_reddit")
JSON_TTL=300

BG="#101418"
TEXT="#e6edf3"
ACCENT="#2f81f7"


class RedditClient:

    def __init__(self):

        self.session=requests.Session()
        self.session.headers.update({"User-Agent":USER_AGENT})
        self.ensure_cache_dirs()

    def ensure_cache_dirs(self):

        os.makedirs(self.cache_dir("json"),exist_ok=True)
        os.makedirs(self.cache_dir("image"),exist_ok=True)

    def cache_dir(self,kind):

        return os.path.join(CACHE_DIR,kind)

    def cache_path(self,kind,key,ext):

        digest=hashlib.sha256(key.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir(kind),f"{digest}.{ext}")

    def read_cached_json(self,path):

        with open(path,"r",encoding="utf-8") as fh:
            return json.load(fh)

    def write_cached_json(self,path,data):

        with open(path,"w",encoding="utf-8") as fh:
            json.dump(data,fh)

    def fetch_json_cached(self,url,ttl=JSON_TTL):

        path=self.cache_path("json",url,"json")
        cache_exists=os.path.exists(path)

        if cache_exists and (time.time()-os.path.getmtime(path))<ttl:
            return self.read_cached_json(path)

        try:

            data=self.session.get(url,timeout=15).json()
            self.write_cached_json(path,data)
            return data

        except:
            if cache_exists:
                return self.read_cached_json(path)
            raise

    def fetch_bytes_cached(self,url):

        ext=os.path.splitext(url.split("?",1)[0])[1].lower().strip(".") or "img"
        path=self.cache_path("image",url,ext)

        if os.path.exists(path):
            with open(path,"rb") as fh:
                return fh.read()

        data=self.session.get(url,timeout=20).content

        with open(path,"wb") as fh:
            fh.write(data)

        return data

    def format_timestamp(self,created_utc):

        if not created_utc:
            return "[unknown time]"

        dt=datetime.fromtimestamp(created_utc)
        month=dt.month
        day=dt.day
        year=dt.year
        hour=dt.strftime("%I").lstrip("0") or "0"
        return f"[{month}/{day}/{year} {hour}:{dt.strftime('%M:%S %p')}]"

    def fetch_posts(self,subreddit,limit=20,after=None):

        url=f"https://www.reddit.com/r/{subreddit}/hot.json?limit={limit}"

        if after:
            url=f"{url}&after={after}"

        data=self.fetch_json_cached(url)
        posts=[]

        for post in data["data"]["children"]:
            d=post["data"]
            image=None

            if "preview" in d:
                try:
                    image=html.unescape(
                        d["preview"]["images"][0]["source"]["url"]
                    )
                except:
                    pass

            post_url=d.get("url","")

            if not image and (
                "i.redd.it" in post_url or
                post_url.endswith(("jpg","png","webp"))
            ):
                image=post_url

            posts.append({
                "title":d["title"],
                "body":html.unescape(d.get("selftext","")),
                "image":image,
                "link":"https://reddit.com"+d["permalink"],
                "name":d.get("name"),
                "created":d.get("created_utc",0),
                "author":d.get("author","unknown"),
                "score":d.get("score",0),
            })

        return posts,data["data"].get("after")

    def fetch_comments(self,link):

        data=self.fetch_json_cached(link+".json")
        return data[1]["data"]["children"]


class RedditApp:

    def __init__(self,root,client,subreddit="all"):

        self.root=root
        self.client=client
        self.subreddit=subreddit
        self.posts=[]
        self.selected_index=None
        self.after_token=None
        self.loading_more=False
        self.page_size=20

        root.title("SOL Reddit")
        root.geometry("950x820")
        root.configure(bg=BG)

        self.build_top_bar()
        self.build_feed()

        self.load_posts(self.subreddit)

    def build_top_bar(self):

        bar=tk.Frame(self.root,bg=BG)
        bar.pack(fill="x",pady=8)

        tk.Label(bar,text="Subreddit:",bg=BG,fg=TEXT).pack(side="left",padx=8)

        self.sub_entry=tk.Entry(bar,width=20)
        self.sub_entry.insert(0,self.subreddit)
        self.sub_entry.pack(side="left")

        tk.Button(
            bar,
            text="Load",
            command=self.load_selected,
            bg=ACCENT,
            fg="white",
            relief="flat"
        ).pack(side="left",padx=6)

        tk.Button(
            bar,
            text="Front Page",
            command=lambda:self.load_posts("all"),
            bg="#1f6feb",
            fg="white",
            relief="flat"
        ).pack(side="left",padx=6)

        tk.Button(
            bar,
            text="Reload",
            command=lambda:self.load_posts(self.subreddit),
            bg="#1f6feb",
            fg="white",
            relief="flat"
        ).pack(side="left",padx=6)

    def build_feed(self):

        self.canvas=tk.Canvas(self.root,bg=BG,highlightthickness=0)
        self.scroll=tk.Scrollbar(self.root,command=self.canvas.yview)

        self.canvas.configure(yscrollcommand=self.scroll.set)

        self.scroll.pack(side="right",fill="y")
        self.canvas.pack(side="left",fill="both",expand=True)

        self.frame=tk.Frame(self.canvas,bg=BG)

        self.canvas.create_window((0,0),window=self.frame,anchor="nw")

        self.frame.bind(
            "<Configure>",
            lambda e:self.on_frame_configure()
        )

        self.root.bind_all("<MouseWheel>",self.scroll_event)
        self.root.bind_all("<Button-4>",self.scroll_event)
        self.root.bind_all("<Button-5>",self.scroll_event)
        self.root.bind_all("<Key-j>",lambda e:self.move_selection(1))
        self.root.bind_all("<Key-k>",lambda e:self.move_selection(-1))
        self.root.bind_all("<Key-o>",lambda e:self.open_selected_post())
        self.root.bind_all("<Key-r>",lambda e:self.reload_posts())
        self.root.bind_all("<Return>",lambda e:self.toggle_selected_comments())

    def on_frame_configure(self):

        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.check_load_more()

    def key_target_is_text_input(self):

        focused=self.root.focus_get()
        return isinstance(focused,tk.Entry)

    def scroll_event(self,event):

        if event.num==5 or event.delta<0:
            self.canvas.yview_scroll(1,"units")
        else:
            self.canvas.yview_scroll(-1,"units")

        self.check_load_more()

    def reload_posts(self):

        if self.key_target_is_text_input():
            return

        self.load_posts(self.subreddit)

    def move_selection(self,delta):

        if self.key_target_is_text_input() or not self.posts:
            return

        if self.selected_index is None:
            new_index=0 if delta>=0 else len(self.posts)-1
        else:
            new_index=max(0,min(len(self.posts)-1,self.selected_index+delta))

        self.select_post(new_index)

        if delta>0 and new_index>=len(self.posts)-3:
            self.check_load_more(force=True)

    def open_selected_post(self):

        if self.key_target_is_text_input() or self.selected_index is None:
            return

        webbrowser.open(self.posts[self.selected_index]["link"])

    def toggle_selected_comments(self):

        if self.key_target_is_text_input() or self.selected_index is None:
            return

        self.toggle_comments(self.posts[self.selected_index])

    def select_post(self,index):

        if index<0 or index>=len(self.posts):
            return

        if self.selected_index is not None and self.selected_index<len(self.posts):
            current=self.posts[self.selected_index]
            current["container"].configure(highlightbackground="#222",highlightthickness=1)

        self.selected_index=index
        post=self.posts[index]
        post["container"].configure(highlightbackground=ACCENT,highlightthickness=2)
        self.scroll_post_into_view(post["container"])

    def scroll_post_into_view(self,widget):

        self.root.update_idletasks()

        top=widget.winfo_y()
        bottom=top+widget.winfo_height()
        canvas_height=max(1,self.canvas.winfo_height())
        total_height=max(1,self.frame.winfo_height())
        visible_top=self.canvas.canvasy(0)
        visible_bottom=visible_top+canvas_height

        if top<visible_top:
            self.canvas.yview_moveto(max(0,top/total_height))
        elif bottom>visible_bottom:
            self.canvas.yview_moveto(max(0,(bottom-canvas_height)/total_height))

        self.check_load_more()

    def check_load_more(self,force=False):

        if self.loading_more or not self.after_token:
            return

        start,end=self.canvas.yview()

        if force or end>0.92:
            self.load_more_posts()

    def load_selected(self):

        sub=self.sub_entry.get().strip()

        if sub.startswith("r/"):
            sub=sub[2:]

        self.load_posts(sub)

    def clear_feed(self):

        for w in self.frame.winfo_children():
            w.destroy()

        self.posts=[]
        self.selected_index=None
        self.after_token=None
        self.loading_more=False

    def load_posts(self,sub):

        self.subreddit=sub
        self.clear_feed()
        self.load_more_posts(reset=True)

    def load_more_posts(self,reset=False):

        if self.loading_more:
            return

        after=None if reset else self.after_token
        self.loading_more=True

        def worker():

            try:
                posts,after_token=self.client.fetch_posts(
                    self.subreddit,
                    limit=self.page_size,
                    after=after
                )
            except:
                self.root.after(0,lambda:self.finish_loading_more([],after))
                return

            self.root.after(
                0,
                lambda:self.finish_loading_more(posts,after_token)
            )

        threading.Thread(target=worker,daemon=True).start()

    def finish_loading_more(self,posts,after_token):

        self.after_token=after_token
        self.loading_more=False

        for post in posts:
            self.render_post(post)

        if self.posts and self.selected_index is None:
            self.select_post(0)

        self.check_load_more()

    def render_post(self,post):

        title=post["title"]
        body=post["body"]
        img=post["image"]
        link=post["link"]
        created=post["created"]

        container=tk.Frame(
            self.frame,
            bg=BG,
            highlightbackground="#222",
            highlightthickness=1
        )
        container.pack(fill="x",pady=10)

        post["container"]=container

        title_label=tk.Label(
            container,
            text=title,
            fg=ACCENT,
            bg=BG,
            wraplength=860,
            justify="left",
            font=("Helvetica",14,"bold"),
            cursor="hand2"
        )

        title_label.pack(anchor="w",padx=20)
        title_label.bind("<Button-1>",lambda e,p=post:self.select_post_by_post(p))

        tk.Label(
            container,
            text=self.client.format_timestamp(created),
            fg="#8b949e",
            bg=BG,
            justify="left"
        ).pack(anchor="w",padx=20,pady=(2,0))

        if body.strip():

            tk.Label(
                container,
                text=body,
                fg=TEXT,
                bg=BG,
                wraplength=860,
                justify="left"
            ).pack(anchor="w",padx=20,pady=4)

        if img:

            img_label=tk.Label(container,bg=BG)
            img_label.pack(padx=20,pady=4)

            def load():

                try:

                    data=self.client.fetch_bytes_cached(img)
                    image=Image.open(io.BytesIO(data))
                    image.thumbnail((860,520))

                    tk_img=ImageTk.PhotoImage(image)

                    def apply():
                        img_label.configure(image=tk_img)
                        img_label.image=tk_img

                    self.root.after(0,apply)

                except:
                    pass

            threading.Thread(target=load,daemon=True).start()

        comment_frame=tk.Frame(container,bg=BG)
        post["comment_frame"]=comment_frame

        toggle=tk.Button(
            container,
            text="show comments",
            bg="#1f6feb",
            fg="white",
            relief="flat"
        )

        toggle.pack(anchor="w",padx=20,pady=4)
        toggle.config(command=lambda p=post:self.toggle_comments(p))
        post["toggle_button"]=toggle

        open_button=tk.Button(
            container,
            text="open in browser",
            command=lambda:webbrowser.open(link),
            bg="#444",
            fg="white",
            relief="flat"
        )
        open_button.pack(anchor="w",padx=20,pady=2)
        post["open_button"]=open_button

        tk.Frame(self.frame,height=2,bg="#222").pack(fill="x",padx=20,pady=10)
        self.posts.append(post)

    def select_post_by_post(self,post):

        index=self.posts.index(post)
        self.select_post(index)

    def toggle_comments(self,post):

        frame=post["comment_frame"]
        button=post["toggle_button"]
        link=post["link"]

        self.select_post_by_post(post)

        if frame.winfo_children():

            for w in frame.winfo_children():
                w.destroy()

            frame.pack_forget()
            button.config(text="show comments")
            return

        button.config(text="loading...")
        frame.pack(fill="x",padx=20)

        def worker():

            try:
                comments=self.client.fetch_comments(link)
            except:
                self.root.after(0,lambda:button.config(text="show comments"))
                return

            self.root.after(
                0,
                lambda:self.render_comments(frame,comments,0,button)
            )

        threading.Thread(target=worker,daemon=True).start()

    def render_comments(self,parent,comments,depth,button):

        if depth==0:
            button.config(text="hide comments")

            panel=tk.Frame(parent,bg="#0d1117",highlightbackground="#222",highlightthickness=1)
            panel.pack(fill="x",pady=6)

            header=tk.Frame(panel,bg="#0d1117")
            header.pack(fill="x",padx=8,pady=(8,4))

            tk.Label(
                header,
                text="Comments",
                bg="#0d1117",
                fg=TEXT,
                font=("Helvetica",11,"bold")
            ).pack(side="left")

            tk.Button(
                header,
                text="hide comments",
                command=lambda:self.toggle_comments(None,parent,button),
                bg="#444",
                fg="white",
                relief="flat"
            ).pack(side="right")

            body_parent=tk.Frame(panel,bg="#0d1117")
            body_parent.pack(fill="x",padx=8,pady=(0,8))
        else:
            body_parent=parent

        wraplength=max(500,820-(depth*40))

        for comment in comments:
            data=comment["data"]

            if "body" not in data:
                continue

            timestamp=self.client.format_timestamp(data.get("created_utc",0))
            body=html.unescape(data["body"])

            tk.Label(
                body_parent,
                text=f"{timestamp} {body}",
                bg="#0d1117",
                fg=TEXT,
                wraplength=wraplength,
                justify="left",
                anchor="w",
                padx=10*depth
            ).pack(fill="x",pady=3,anchor="w")

            replies=data.get("replies")

            if replies and isinstance(replies,dict):
                children=replies["data"]["children"]
                self.render_comments(body_parent,children,depth+1,button)


def print_comment_tree(client,comments,depth=0):

    for comment in comments:
        data=comment["data"]

        if "body" not in data:
            continue

        indent="  "*depth
        timestamp=client.format_timestamp(data.get("created_utc",0))
        author=data.get("author","unknown")
        score=data.get("score",0)
        body=html.unescape(data["body"]).replace("\r\n","\n")
        wrapped=textwrap.fill(
            body,
            width=96,
            initial_indent=f"{indent}- {timestamp} {author} ({score}): ",
            subsequent_indent=f"{indent}  "
        )
        print(wrapped)

        replies=data.get("replies")

        if replies and isinstance(replies,dict):
            print_comment_tree(client,replies["data"]["children"],depth+1)


def run_cli(client,args):

    posts,_=client.fetch_posts(args.subreddit,args.limit)

    if args.json:
        print(json.dumps(posts,indent=2))
        return

    for index,post in enumerate(posts,1):
        timestamp=client.format_timestamp(post["created"])
        author=post["author"]
        score=post["score"]
        print(f"{index}. {timestamp} {post['title']}")
        print(f"   by u/{author} | score {score}")

        if post["body"].strip():
            body=textwrap.shorten(
                post["body"].replace("\n"," "),
                width=180,
                placeholder="..."
            )
            print(f"   {body}")

        print(f"   {post['link']}")
        print("")

    if args.comments is not None:
        post_index=args.comments-1

        if post_index<0 or post_index>=len(posts):
            raise SystemExit(f"--comments index out of range: {args.comments}")

        post=posts[post_index]
        print(f"Comments for: {post['title']}")
        print(post["link"])
        print("")
        print_comment_tree(client,client.fetch_comments(post["link"]))


def parse_args():

    parser=argparse.ArgumentParser(
        description="Read Reddit from the terminal or launch the Tk GUI."
    )
    parser.add_argument("subreddit",nargs="?",default="all")
    parser.add_argument("--gui",action="store_true",help="launch the Tk GUI")
    parser.add_argument("--limit",type=int,default=20,help="number of posts to fetch in CLI mode")
    parser.add_argument("--comments",type=int,help="print comments for the numbered CLI post")
    parser.add_argument("--json",action="store_true",help="print CLI post data as JSON")
    return parser.parse_args()


def main():

    args=parse_args()
    client=RedditClient()

    if args.gui:
        root=tk.Tk()
        RedditApp(root,client,args.subreddit)
        root.mainloop()
        return

    run_cli(client,args)


if __name__=="__main__":
    main()
