from graphics import *

def main():
	win = GraphWin('session', 500, 500)
	win.setBackground('black')

	box = Rectangle(Point(100,100), Point(400,400))
	box.setOutline('white')
	box.draw(win)

	win.getMouse()
	win.close()

main()