#!/usr/bin/env python
# _0853RV3R

from PyQt4 import QtGui, QtCore
import sys, os, subprocess
AbuseIPDB = "/home/davidlones/.bin/abuseipdb"

class Window(QtGui.QWidget):
    def __init__(self):
        QtGui.QWidget.__init__(self)
        self.textbox = QtGui.QLineEdit()
        self.textbox.setObjectName('ipaddr')
        self.textbox.setText('8.8.8.8')
        self.button1 = QtGui.QPushButton('Test', self)
        self.button1.clicked.connect(self.handleButton)
        self.button2 = QtGui.QPushButton('Test', self)
        self.button2.clicked.connect(self.handleButton)
        layout = QtGui.QVBoxLayout(self)
        layout.addWidget(self.textbox)
        layout.addWidget(self.button1)
        layout.addWidget(self.button2)

    def handleButton(self):
    	print(self.textbox.text())
        # FNULL = open(os.devnull, 'w')
        # subprocess.Popen([AbuseIPDB, str(self.textbox.text()), "-n", "-x"], stdout=FNULL, stderr=subprocess.STDOUT)




app = QtGui.QApplication(sys.argv)
window = Window()
window.resize(500,200)
window.show()
sys.exit(app.exec_())
