def main():
    n = 5000000
    acc = 0
    facc = 0.0
    for i in range(n):
        a = (i * 7919) % 1000 - 500
        b = (i * 104729) % 1000 - 500
        acc = acc + abs(a) + min(a, b) + max(a, b)
        x = float(a) * 0.5
        y = float(b) * 0.25
        facc = facc + abs(x) + min(x, y) + max(x, y)
    print(f"{acc} {int(facc)}")
main()
