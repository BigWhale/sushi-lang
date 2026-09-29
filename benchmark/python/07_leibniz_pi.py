def main():
    s = 0.0
    sign = 1.0
    for k in range(20000000):
        s = s + sign / (2.0 * k + 1.0)
        sign = 0.0 - sign
    print(int(s * 4.0 * 1000000000.0))
main()
