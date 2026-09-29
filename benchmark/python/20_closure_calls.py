def apply_all(f, n):
    total = 0
    for i in range(n):
        total += f(i)
    return total

def main():
    k = 3
    f = lambda x: (x % 100) * k + 1
    print(apply_all(f, 10000000))
main()
