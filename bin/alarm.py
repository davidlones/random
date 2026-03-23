#!/usr/bin/env python
# _0853RV3R

from PyQt4 import QtGui, QtCore
import sys, os, subprocess, signal
alarmscript = "/home/davidlones/.bin/alarm"

class Window(QtGui.QWidget):
    def __init__(self):
        QtGui.QWidget.__init__(self)
        self.text = QtGui.QLabel()
        self.text.setText('Alarm started.')
        self.StopButton = QtGui.QPushButton('Stop', self)
        self.StopButton.clicked.connect(self.Stop)
        self.DismissButton = QtGui.QPushButton('Dismiss', self)
        self.DismissButton.clicked.connect(self.Dismiss)

        windowlayout = QtGui.QVBoxLayout(self)
        content = QtGui.QHBoxLayout(self)
        buttons = QtGui.QHBoxLayout(self)

        content.addWidget(self.text)

        buttons.addStretch(1)
        buttons.addWidget(self.StopButton)
        buttons.addWidget(self.DismissButton)

        windowlayout.addLayout(content)
        windowlayout.addLayout(buttons)

    def Stop(self):
        subprocess.Popen(["/home/davidlones/.bin/spot", "pause"], stdout=subprocess.PIPE, shell=False)
        try:
            os.killpg(os.getpgid(alarmthread.pid), signal.SIGTERM)
        except Exception:
            sys.exc_clear()
        sys.exit(app.exec_())

    def Dismiss(self):
        sys.exit(app.exec_())





app = QtGui.QApplication(sys.argv)
window = Window()
window.setWindowTitle('SoL v5.0.1-alpha')
window.resize(500,100)

alarmthread = subprocess.Popen([alarmscript], stdout=subprocess.PIPE, shell=True, preexec_fn=os.setsid) 
window.show()

sys.exit(app.exec_())
