import sys
sys.setrecursionlimit(10000)

def qsort(a, lo, hi):
    if lo >= hi:
        return
    p = a[(lo + hi) // 2]
    i, j = lo, hi
    while i <= j:
        while a[i] < p:
            i += 1
        while a[j] > p:
            j -= 1
        if i <= j:
            a[i], a[j] = a[j], a[i]
            i += 1
            j -= 1
    qsort(a, lo, j)
    qsort(a, i, hi)

def main():
    n = 500000
    a = [0] * n
    seed = 42
    for i in range(n):
        seed = (seed * 1103515245 + 12345) % 2147483648
        a[i] = seed % 1000000
    qsort(a, 0, n - 1)
    check = 0
    for i in range(1000):
        check += a[i * 499]
    print(f"{a[0]} {a[n - 1]} {check}")
main()
