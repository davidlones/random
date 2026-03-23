import time, threading
from thread import start_new_thread

# timeout = 7

# starttime = time.time() - 604800
# currenttime = time.time()
# dayselapsed = (currenttime - starttime)/60/60/24
# print(dayselapsed)
# if dayselapsed > timeout:
#     print("TIMEOUT1")





# class tester1(threading.Thread):
#      def run(self):
#         while cond == 1:
#             print("thread running")
#             time.sleep(1)
#         print("thread stops")

# def test1(pau):
#     global cond
#     cond = pau
#     if cond == 1:
#         testing1 = tester1 ()
#         testing1.daemon = True
#         print("thread starts")
#         testing1.start()


# print("Test 1:")
# print("stuff happens before thread starts")
# test1(1)
# print("stuff happens after thread starts")
# time.sleep(3)
# test1(0)

# time.sleep(3)





# def tester2():
#     while cond == 1:
#         time.sleep(1)
#         print("thread running")
#     print("thread stops")

# def test2(pau):
#     global cond
#     cond = pau
#     if cond == 1:
#         print("thread starts")
#         start_new_thread(tester2,())


# print("\n\nTest 2:")
# print("stuff happens before thread starts")
# test2(1)
# print("stuff happens after thread starts")
# time.sleep(3)
# test2(0)

# time.sleep(3)





class tester3(threading.Thread):
    def __init__(self):
    	threading.Thread.__init__(self)
        self.daemon = True

    def run(self):
        self.cont = True
        print("thread starts")
        while self.cont == True:
            print("thread running")
            time.sleep(1)

    def stop(self):
        self.cont = False
        print("thread stops")


testing3 = tester3()

print("\n\nTest 2:")
print("stuff happens before thread starts")
testing3.start()
print("stuff happens after thread starts")
time.sleep(3)
testing3.stop()

time.sleep(3)


