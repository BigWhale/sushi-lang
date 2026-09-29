def main():
    n = 500000
    m = {}
    for i in range(n):
        m[i * 7] = i
    total = 0
    for i in range(n * 2):
        v = m.get(i * 7)
        if v is not None:
            total += v
    print(f"{len(m)} {total}")
main()
