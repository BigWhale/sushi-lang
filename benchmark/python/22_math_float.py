from math import sqrt, sin, cos, exp, log, pow, floor, atan2, hypot

def main():
    n = 2000000
    acc = 0.0
    for i in range(n):
        x = float(i % 1000) * 0.001 + 0.5
        acc = acc + sqrt(x) + sin(x) + cos(x) + exp(x) + log(x)
        acc = acc + pow(x, 1.5) + floor(x * 10.0) + atan2(x, 1.0) + hypot(x, 2.0)
    print(int(acc))
main()
