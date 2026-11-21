"""Real mouse input for driving apps on this machine's screen (System Events clicks don't reach
web pages). macOS only; needs pyobjc-framework-Quartz.
  mouse.py click X Y        left click at screen points (x, y)
  mouse.py scroll X Y N     scroll N lines at (x, y) (negative = up)
"""
import sys, time
import Quartz as Q

def post(e):
    Q.CGEventPost(Q.kCGHIDEventTap, e)

def click(x, y):
    p = (float(x), float(y))
    post(Q.CGEventCreateMouseEvent(None, Q.kCGEventMouseMoved, p, Q.kCGMouseButtonLeft)); time.sleep(0.05)
    post(Q.CGEventCreateMouseEvent(None, Q.kCGEventLeftMouseDown, p, Q.kCGMouseButtonLeft)); time.sleep(0.05)
    post(Q.CGEventCreateMouseEvent(None, Q.kCGEventLeftMouseUp, p, Q.kCGMouseButtonLeft))

def scroll(x, y, n):
    post(Q.CGEventCreateMouseEvent(None, Q.kCGEventMouseMoved, (float(x), float(y)), Q.kCGMouseButtonLeft))
    for _ in range(abs(int(n))):
        post(Q.CGEventCreateScrollWheelEvent(None, Q.kCGScrollEventUnitLine, 1, -5 if int(n) > 0 else 5)); time.sleep(0.02)

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "click": click(*sys.argv[2:4])
    elif cmd == "scroll": scroll(*sys.argv[2:5])
