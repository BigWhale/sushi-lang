def main():
    M = 0xFFFFFFFFFFFFFFFF
    x = 88172645463325252
    acc = 0
    for _ in range(10000000):
        x ^= (x << 13) & M
        x ^= x >> 7
        x ^= (x << 17) & M
        acc = (acc + (x >> 56)) & M
    print(acc)
main()
