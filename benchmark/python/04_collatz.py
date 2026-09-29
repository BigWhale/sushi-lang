def main():
    best = 0
    best_n = 0
    for start in range(1, 300000):
        x = start
        steps = 0
        while x != 1:
            if x % 2 == 0:
                x = x // 2
            else:
                x = 3 * x + 1
            steps += 1
        if steps > best:
            best = steps
            best_n = start
    print(f"{best_n} {best}")
main()
