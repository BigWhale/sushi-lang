def gcd(a, b):
    x, y = a, b
    while y != 0:
        x, y = y, x % y
    return x

def main():
    total = 0
    for i in range(1, 1200):
        for j in range(1, 1200):
            total += gcd(i, j)
    print(total)
main()
