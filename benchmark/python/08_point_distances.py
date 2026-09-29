from math import sqrt

class Point:
    __slots__ = ("x", "y")
    def __init__(self, x, y):
        self.x = x
        self.y = y

def main():
    n = 1500
    pts = [Point(float((i * 37) % 1000), float((i * 91) % 1000)) for i in range(n)]
    total = 0.0
    for i in range(n):
        xi = pts[i].x
        yi = pts[i].y
        j = i + 1
        while j < n:
            dx = xi - pts[j].x
            dy = yi - pts[j].y
            total += sqrt(dx * dx + dy * dy)
            j += 1
    print(int(total))
main()
