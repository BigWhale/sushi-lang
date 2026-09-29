def main():
    n = 2500
    a = [0] * n
    seed = 12345
    for i in range(n):
        seed = (seed * 1103 + 12345) % 65536
        a[i] = seed
    i = 0
    while i < n:
        j = 0
        while j < n - 1 - i:
            if a[j] > a[j + 1]:
                a[j], a[j + 1] = a[j + 1], a[j]
            j += 1
        i += 1
    print(f"{a[0]} {a[n // 2]} {a[n - 1]}")
main()
