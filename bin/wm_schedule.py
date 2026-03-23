#!/usr/bin/env python
# _0853RV3R

import webbrowser
import wx

wx.App()
link = "https://login.walmartone.com/StoreCheck/frontEnd/?win=219964471&store=3431"
webbrowser.get('firefox %s').open_new_tab(link)
screen = wx.ScreenDC()
size = screen.GetSize()
bmp = wx.EmptyBitmap(size[0], size[1])
mem = wx.MemoryDC(bmp)
mem.Blit(0, 0, size[0], size[1], screen, 0, 0)
del mem
bmp.SaveFile('Downloads/screenshot.png', wx.BITMAP_TYPE_PNG)