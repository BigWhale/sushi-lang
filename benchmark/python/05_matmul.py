def main():
    n = 160
    a = [0.0] * (n * n)
    b = [0.0] * (n * n)
    c = [0.0] * (n * n)
    for i in range(n):
        for j in range(n):
            a[i * n + j] = float((i * j) % 7)
            b[i * n + j] = float((i + j) % 5)
    for i in range(n):
        for k in range(n):
            aik = a[i * n + k]
            for j in range(n):
                c[i * n + j] = c[i * n + j] + aik * b[k * n + j]
    total = 0.0
    for i in range(n * n):
        total += c[i]
    print(int(total))
main()
